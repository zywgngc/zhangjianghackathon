"""Integration coverage for bounded query retries and honest missing reports."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from asset_agent import AssetAgent
from asset_agent.contracts import validate_request


class MissingRetryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.catalog = self.root / "catalog"
        self.catalog.mkdir()
        assets = []
        for name, tags in (("pause", ["pause", "暂停"]), ("coin", ["coin", "金币"])):
            (self.catalog / (name + ".svg")).write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2 2h20v20H2z"/></svg>',
                encoding="utf-8",
            )
            assets.append({
                "id": name, "type": "icon", "name": name, "description": name,
                "tags": tags, "style": "silhouette", "pack": "demo",
                "path": name + ".svg", "format": "svg", "license": "CC-BY-3.0",
                "author": "Test artist", "source_url": "https://example.test/" + name,
                "license_url": "https://creativecommons.org/licenses/by/3.0/",
            })
        runner = dict(assets[0], id="runner", type="model", name="runner",
                      description="Running character", tags=["runner"],
                      path="runner.gltf", format="gltf", animations=["Run"], triangles=100)
        (self.catalog / "runner.gltf").write_text(
            json.dumps({"asset": {"version": "2.0"}, "animations": [{"name": "Run"}]}),
            encoding="utf-8",
        )
        assets.append(runner)
        (self.catalog / "catalog.json").write_text(
            json.dumps({"schema_version": "1.0", "assets": assets}), encoding="utf-8"
        )
        self.request = {
            "request_id": "bounded-retry",
            "prompt": "A racing game needs a pause icon and a collectible counter.",
            "style": "silhouette",
            "layout": {"viewport": {"width": 320, "height": 240}, "slots": [
                {"slot_id": "pause", "type": "icon", "description": "pause",
                 "bounds": {"x": 8, "y": 8, "width": 24, "height": 24}},
                {"slot_id": "missing", "type": "icon", "description": "zzzxunknownasset"},
            ]},
        }

    def agent(self, **kwargs):
        agent = AssetAgent(self.catalog, **kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_default_does_not_make_a_second_query_builder_call(self):
        contexts = []

        def query_builder(context):
            contexts.append(copy.deepcopy(context))
            return {"queries": []}

        plan = self.agent(query_builder=query_builder).plan(self.request)
        self.assertEqual([context["phase"] for context in contexts], ["initial"])
        self.assertEqual(contexts[0]["retry_round"], 0)
        self.assertEqual(plan["missing_policy"], {"retry_rounds": 0})
        self.assertEqual(plan["status"], "partial")
        self.assertEqual(len(plan["retrieval"]["slots"]), 2)
        self.assertEqual(plan["unresolved"][0]["attempted_queries"], ["zzzxunknownasset"])

    def test_one_retry_only_receives_missing_slots_and_full_context(self):
        self.request["missing_policy"] = {"retry_rounds": 1}
        original = copy.deepcopy(self.request)
        contexts = []

        def query_builder(context):
            contexts.append(copy.deepcopy(context))
            if context["phase"] == "initial":
                return {"queries": []}
            return {"queries": [{"slot_id": "missing", "query": "coin 金币"}]}

        agent = self.agent(query_builder=query_builder)
        with patch.object(agent.search_engine, "search", wraps=agent.search_engine.search) as search:
            plan = agent.plan(self.request)
        self.assertEqual(self.request, original)
        self.assertEqual([context["phase"] for context in contexts], ["initial", "retry"])
        retry = contexts[1]
        self.assertEqual(retry["retry_round"], 1)
        self.assertEqual(retry["request"], validate_request(self.request))
        self.assertEqual([slot["slot_id"] for slot in retry["missing_slots"]], ["missing"])
        self.assertEqual(retry["previous_queries"], {"missing": ["zzzxunknownasset"]})
        self.assertEqual(search.call_count, 3)
        self.assertEqual([call.args[0] for call in search.call_args_list], ["pause", "zzzxunknownasset", "coin 金币"])
        self.assertEqual(plan["status"], "ready")
        self.assertEqual([selection["asset_id"] for selection in plan["selections"]], ["pause", "coin"])
        self.assertEqual([entry["retry_round"] for entry in plan["retrieval"]["slots"]], [0, 0, 1])

    def test_two_rounds_preserve_query_history_and_stop_at_the_limit(self):
        self.request["layout"]["slots"] = [self.request["layout"]["slots"][1]]
        self.request["missing_policy"] = {"retry_rounds": 2}
        contexts = []

        def query_builder(context):
            contexts.append(copy.deepcopy(context))
            if context["phase"] == "initial":
                return {"queries": []}
            return {"queries": [{"slot_id": "missing", "query": "zzzstillunknown" if context["retry_round"] == 1 else "coin"}]}

        plan = self.agent(query_builder=query_builder).plan(self.request)
        self.assertEqual([context["retry_round"] for context in contexts], [0, 1, 2])
        self.assertEqual(contexts[2]["previous_queries"], {"missing": ["zzzxunknownasset", "zzzstillunknown"]})
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["selections"][0]["asset_id"], "coin")
        self.assertEqual([entry["query"] for entry in plan["retrieval"]["slots"]],
                         ["zzzxunknownasset", "zzzstillunknown", "coin"])

    def test_two_round_configuration_stops_after_first_success(self):
        self.request["missing_policy"] = {"retry_rounds": 2}
        contexts = []

        def query_builder(context):
            contexts.append(context["phase"])
            return {"queries": []} if context["phase"] == "initial" else {"queries": [{"slot_id": "missing", "query": "coin"}]}

        self.assertEqual(self.agent(query_builder=query_builder).plan(self.request)["status"], "ready")
        self.assertEqual(contexts, ["initial", "retry"])

    def test_repeated_query_is_not_searched_again(self):
        self.request["layout"]["slots"] = [self.request["layout"]["slots"][1]]
        self.request["missing_policy"] = {"retry_rounds": 2}
        contexts = []

        def query_builder(context):
            contexts.append(context["phase"])
            return {"queries": []} if context["phase"] == "initial" else {"queries": [{"slot_id": "missing", "query": " zzzxunknownasset "}]}

        agent = self.agent(query_builder=query_builder)
        with patch.object(agent.search_engine, "search", wraps=agent.search_engine.search) as search:
            plan = agent.plan(self.request)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(contexts, ["initial", "retry"])
        self.assertEqual(plan["status"], "unresolved")
        self.assertEqual(plan["unresolved"][0]["attempted_queries"], ["zzzxunknownasset"])

    def test_retry_requires_a_configured_callback(self):
        self.request["missing_policy"] = {"retry_rounds": 1}
        agent = self.agent()
        with patch.object(agent.search_engine, "search", wraps=agent.search_engine.search) as search:
            with self.assertRaisesRegex(ValueError, "requires a query_builder"):
                agent.plan(self.request)
        search.assert_not_called()

    def test_invalid_retry_policy_is_rejected_before_search(self):
        for policy in ({"retry_rounds": -1}, {"retry_rounds": 3}, {"retry_rounds": True}, {"retry_rounds": 1.5}, {"retry_rounds": 0, "generate": True}):
            request = dict(self.request, missing_policy=policy)
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.agent().plan(request)

    def test_retry_cannot_use_resolved_unknown_or_duplicate_slot_ids(self):
        self.request["missing_policy"] = {"retry_rounds": 1}
        responses = [
            [{"slot_id": "pause", "query": "coin"}],
            [{"slot_id": "invented", "query": "coin"}],
            [{"slot_id": "missing", "query": "zzzfirst"}, {"slot_id": "missing", "query": "zzzsecond"}],
        ]
        for response in responses:
            def query_builder(context, response=response):
                return {"queries": []} if context["phase"] == "initial" else {"queries": response}
            with self.subTest(response=response), self.assertRaisesRegex(ValueError, "unknown, duplicate, or already-resolved"):
                self.agent(query_builder=query_builder).plan(self.request)

    def test_malformed_retry_queries_are_rejected(self):
        self.request["missing_policy"] = {"retry_rounds": 1}
        for response in ({}, {"queries": "coin"}, {"queries": ["coin"]}, {"queries": [{"slot_id": "missing", "query": ""}]}):
            def query_builder(context, response=response):
                return {"queries": []} if context["phase"] == "initial" else response
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.agent(query_builder=query_builder).plan(self.request)

    def test_retries_preserve_each_hard_filter_and_cannot_resolve_wrong_assets(self):
        base_slot = {"slot_id": "player", "type": "model", "description": "zzzxunknownasset",
                     "formats": ["gltf"], "packs": ["demo"], "required_animations": ["Run"]}
        cases = [
            ("license", {}, {"licenses": ["CC0-1.0"]}),
            ("style", {"style": "pixel"}, {}),
            ("format", {"formats": ["glb"]}, {}),
            ("pack", {"packs": ["other-pack"]}, {}),
            ("animation", {"required_animations": ["Ride"]}, {}),
            ("max_bytes", {}, {"max_asset_bytes": 1}),
            ("triangles", {}, {"max_triangles": 99}),
            ("type", {"type": "icon", "formats": ["svg"], "required_animations": []}, {}),
        ]
        for name, slot_changes, constraints in cases:
            request = copy.deepcopy(self.request)
            request["layout"]["slots"] = [dict(base_slot, **slot_changes)]
            request["constraints"] = constraints
            request["missing_policy"] = {"retry_rounds": 1}

            def query_builder(context):
                return {"queries": []} if context["phase"] == "initial" else {"queries": [{"slot_id": "player", "query": "runner"}]}

            agent = self.agent(query_builder=query_builder)
            with self.subTest(constraint=name), patch.object(agent.search_engine, "search", wraps=agent.search_engine.search) as search:
                plan = agent.plan(request)
                self.assertEqual(plan["status"], "unresolved")
                self.assertEqual(plan["selections"], [])
                self.assertEqual(search.call_count, 2)
                self.assertEqual(search.call_args_list[0].kwargs["filters"], search.call_args_list[1].kwargs["filters"])
                brief = plan["unresolved"][0]["generation_brief"]
                self.assertEqual(brief["required_animations"], request["layout"]["slots"][0]["required_animations"])
                self.assertEqual(brief["context"]["constraints"], validate_request(request)["constraints"])

    def test_selector_refusal_and_budget_failure_do_not_trigger_search_retries(self):
        for mode in ("selector", "budget"):
            request = copy.deepcopy(self.request)
            request["layout"]["slots"] = [request["layout"]["slots"][0]]
            request["missing_policy"] = {"retry_rounds": 2}
            phases = []

            def query_builder(context):
                phases.append(context["phase"])
                return {"queries": []}

            kwargs = {"query_builder": query_builder}
            if mode == "selector":
                kwargs["selector"] = lambda context: {"assignments": []}
            else:
                request["constraints"] = {"max_total_bytes": 1}
            with self.subTest(mode=mode):
                plan = self.agent(**kwargs).plan(request)
                self.assertEqual(phases, ["initial"])
                self.assertEqual(plan["status"], "unresolved")
                self.assertEqual(plan["unresolved"][0]["reason"], "selector_left_unassigned" if mode == "selector" else "total_byte_budget")

    def test_callback_context_mutation_cannot_relax_hard_constraints(self):
        self.request["layout"]["slots"] = [self.request["layout"]["slots"][1]]
        self.request["constraints"] = {"licenses": ["CC0-1.0"]}
        self.request["missing_policy"] = {"retry_rounds": 1}
        original = copy.deepcopy(self.request)

        def query_builder(context):
            context["request"]["constraints"]["licenses"] = ["CC-BY-3.0"]
            if context["phase"] == "initial":
                return {"queries": []}
            context["missing_slots"][0]["style"] = "generic"
            return {"queries": [{"slot_id": "missing", "query": "coin"}]}

        plan = self.agent(query_builder=query_builder).plan(self.request)
        self.assertEqual(self.request, original)
        self.assertEqual(plan["status"], "unresolved")
        self.assertEqual(plan["constraints"]["licenses"], ["CC0-1.0"])
        self.assertEqual(plan["selections"], [])

    def test_missing_statuses_keep_honest_briefs_and_optional_omission_suggestions(self):
        for known, required, expected in ((True, True, "partial"), (False, True, "unresolved"), (True, False, "ready"), (False, False, "ready")):
            request = copy.deepcopy(self.request)
            request["layout"]["slots"][1]["required"] = required
            if not known:
                request["layout"]["slots"] = [request["layout"]["slots"][1]]
            before_files = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
            with self.subTest(known=known, required=required):
                plan = self.agent().plan(request)
                self.assertEqual(plan["status"], expected)
                gap = plan["unresolved"][0]
                self.assertEqual(gap["required"], required)
                self.assertNotIn("file", gap)
                self.assertNotIn("asset_id", gap)
                self.assertTrue(gap["actions"])
                brief = gap["generation_brief"]
                self.assertEqual(brief["status"], "requested")
                self.assertEqual(brief["context"]["dispatch_status"], "not_dispatched")
                for forbidden in ("file", "path", "url", "asset_id"):
                    self.assertNotIn(forbidden, brief)
                self.assertEqual("omit_optional" in [action["type"] for action in gap["actions"]], not required)
                after_files = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
                self.assertEqual(before_files, after_files)


if __name__ == "__main__":
    unittest.main()
