import copy
import json
import tempfile
import unittest
from pathlib import Path

from asset_agent import AssetAgent
from asset_agent.contracts import validate_request


class AssetAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root / "catalog"
        self.catalog.mkdir()
        assets = []
        for name, tags, size in (("pause", ["pause", "暂停"], 20), ("coin", ["coin", "金币"], 30), ("heart", ["health", "heart", "血量"], 40)):
            (self.catalog / (name + ".svg")).write_bytes(b"x" * size)
            assets.append({"id": name, "type": "icon", "name": name, "description": name, "tags": tags, "style": "silhouette", "pack": "demo", "path": name + ".svg", "format": "svg", "license": "CC-BY-3.0", "author": "Test author", "source_url": "https://example.test/" + name, "license_url": "https://creativecommons.org/licenses/by/3.0/"})
        (self.catalog / "catalog.json").write_text(json.dumps({"schema_version": "1.0", "assets": assets}), encoding="utf-8")
        self.request = {"request_id": "unit", "prompt": "暂停、金币和血量", "style": "silhouette", "layout": {"viewport": {"width": 100, "height": 100}, "slots": [{"slot_id": "pause", "type": "icon", "description": "暂停", "bounds": {"x": 0, "y": 0, "width": 20, "height": 20}}, {"slot_id": "coins", "type": "icon", "description": "金币"}]}}

    def make_agent(self, **kwargs):
        agent = AssetAgent(self.catalog, **kwargs)
        self.addCleanup(agent.close)
        return agent

    def test_bilingual_plan_preserves_layout_and_does_not_modify_input(self):
        original = copy.deepcopy(self.request)
        plan = self.make_agent().plan(self.request)
        self.assertEqual("ready", plan["status"])
        self.assertEqual(["pause", "coin"], [item["asset_id"] for item in plan["selections"]])
        self.assertEqual(original["layout"]["slots"][0]["bounds"], plan["selections"][0]["usage"]["bounds"])
        self.assertEqual(original, self.request)
        self.assertEqual("ranked_policy", plan["retrieval"]["selection_mode"])

    def test_global_prompt_cannot_make_an_unknown_slot_match_pause(self):
        self.request["layout"]["slots"] = [{"slot_id": "unknown", "type": "icon", "description": "zzzznotanasset"}]
        plan = self.make_agent().plan(self.request)
        self.assertEqual("unresolved", plan["status"])
        self.assertEqual([], plan["selections"])

    def test_license_and_style_are_hard_constraints(self):
        for changes in ({"constraints": {"licenses": ["CC0-1.0"]}}, {"style": "pixel"}):
            request = dict(self.request, **changes)
            self.assertEqual("unresolved", self.make_agent().plan(request)["status"])

    def test_unknown_model_reports_a_gap_with_no_fabricated_file(self):
        self.request["layout"]["slots"].append({"slot_id": "player", "type": "model", "description": "pelican riding a bike", "required_animations": ["ride"]})
        plan = self.make_agent().plan(self.request)
        self.assertEqual("partial", plan["status"])
        self.assertEqual("player", plan["unresolved"][0]["slot_id"])
        self.assertNotIn("file", plan["unresolved"][0])

    def test_total_budget_and_required_priority(self):
        self.request["layout"]["slots"][0]["required"] = False
        self.request["constraints"] = {"max_total_bytes": 30}
        plan = self.make_agent().plan(self.request)
        self.assertEqual("ready", plan["status"])
        self.assertEqual(["coin"], [item["asset_id"] for item in plan["selections"]])
        self.assertEqual("total_byte_budget", plan["unresolved"][0]["reason"])

    def test_reused_asset_counts_towards_budget_once(self):
        self.request["layout"]["slots"][1]["description"] = "暂停"
        self.request["constraints"] = {"max_total_bytes": 20}
        plan = self.make_agent().plan(self.request)
        self.assertEqual("ready", plan["status"])
        self.assertEqual(20, plan["estimated_total_bytes"])
        self.assertEqual(2, len(plan["selections"]))

    def test_callback_receives_context_and_may_only_choose_candidates(self):
        seen = []
        def selector(context):
            seen.append(context)
            return {"assignments": [{"slot_id": entry["slot"]["slot_id"], "asset_id": entry["candidates"][0]["asset"]["id"], "reason": "Fits this slot"} for entry in context["slots"]]}
        plan = self.make_agent(selector=selector).plan(self.request)
        self.assertEqual("ready", plan["status"])
        self.assertEqual("rag_callback", plan["retrieval"]["selection_mode"])
        self.assertEqual(self.request["prompt"], seen[0]["request"]["prompt"])
        self.assertEqual("Fits this slot", plan["selections"][0]["reason"])

    def test_callback_hallucinated_or_cross_slot_ids_are_rejected(self):
        for asset_id in ("nonexistent", "coin"):
            selector = lambda context, asset_id=asset_id: {"assignments": [{"slot_id": "pause", "asset_id": asset_id}]}
            with self.assertRaisesRegex(ValueError, "not a retrieved candidate"):
                self.make_agent(selector=selector).plan(self.request)

    def test_callback_cannot_override_byte_budget(self):
        self.request["constraints"] = {"max_total_bytes": 20}
        def selector(context):
            return {"assignments": [{"slot_id": "pause", "asset_id": "pause"}, {"slot_id": "coins", "asset_id": "coin"}]}
        plan = self.make_agent(selector=selector).plan(self.request)
        self.assertEqual("partial", plan["status"])
        self.assertEqual(20, plan["estimated_total_bytes"])

    def test_query_builder_uses_game_prompt_to_expand_an_ambiguous_slot(self):
        self.request["layout"]["slots"] = [{"slot_id": "main_action", "type": "icon", "description": "按钮"}]
        self.request["prompt"] = "主要动作是暂停比赛"
        contexts = []
        def query_builder(context):
            contexts.append(context)
            return {"queries": [{"slot_id": "main_action", "query": "pause 暂停"}]}
        plan = self.make_agent(query_builder=query_builder).plan(self.request)
        self.assertEqual("pause", plan["selections"][0]["asset_id"])
        self.assertEqual(self.request["prompt"], contexts[0]["request"]["prompt"])
        self.assertEqual("callback", plan["retrieval"]["query_mode"])

    def test_query_builder_cannot_change_slot_identity(self):
        for bad in ({"queries": [{"slot_id": "invented", "query": "pause"}]}, {"queries": [{"slot_id": "pause", "query": ""}]}):
            with self.assertRaises(ValueError):
                self.make_agent(query_builder=lambda context, bad=bad: bad).plan(self.request)

    def test_animation_handoff_uses_actual_case_sensitive_clip_name(self):
        manifest_path = self.catalog / "catalog.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        asset = dict(manifest["assets"][0], id="runner", type="model", format="gltf", path="runner.gltf", name="runner", description="running character", tags=["runner"], animations=["Run"])
        (self.catalog / "runner.gltf").write_text('{"asset":{"version":"2.0"},"animations":[{"name":"Run"}]}', encoding="utf-8")
        manifest["assets"].append(asset)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.request["layout"]["slots"] = [{"slot_id": "player", "type": "model", "description": "runner", "required_animations": ["run"]}]
        plan = self.make_agent().plan(self.request)
        self.assertEqual(["Run"], plan["selections"][0]["usage"]["animations"])

    def test_planning_rejects_a_selected_asset_with_a_changed_source_hash(self):
        import hashlib
        manifest_path = self.catalog / "catalog.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"][0]["sha256"] = hashlib.sha256(b"original upstream bytes").hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            self.make_agent().plan(self.request)

    def test_uppercase_catalog_hash_is_accepted_through_delivery(self):
        import hashlib
        manifest_path = self.catalog / "catalog.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"][0]["sha256"] = hashlib.sha256((self.catalog / "pause.svg").read_bytes()).hexdigest().upper()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = self.make_agent().run(self.request, self.root / "uppercase-bundle")
        self.assertEqual("ready", result["status"])

    def test_duplicate_slots_and_invalid_bounds_fail_before_retrieval(self):
        invalid = copy.deepcopy(self.request)
        invalid["layout"]["slots"][1]["slot_id"] = "pause"
        with self.assertRaises(ValueError):
            validate_request(invalid)
        for value in (float("nan"), True, -1, 101):
            invalid = copy.deepcopy(self.request)
            invalid["layout"]["slots"][0]["bounds"]["width"] = value
            with self.assertRaises(ValueError):
                validate_request(invalid)


if __name__ == "__main__":
    unittest.main()
