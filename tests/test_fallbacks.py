"""Missing-asset reports describe requests, never invented completed assets."""

import copy
import json
import unittest

from asset_agent.fallbacks import build_missing_report


class MissingReportTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "request_id": "pelican-demo",
            "prompt": "A pelican cycles along the coast and catches fish.",
            "style": "soft-cartoon",
            "layout": {"viewport": {"width": 1280, "height": 720}},
            "constraints": {"licenses": ["CC0-1.0"], "max_asset_bytes": 4096,
                            "max_total_bytes": 8192, "max_triangles": 3000,
                            "allow_reuse": False},
        }
        self.slot = {
            "slot_id": "player", "type": "model", "required": True,
            "description": "Pelican riding a bicycle", "tags": ["pelican", "bicycle"],
            "formats": ["glb"], "required_animations": ["ride", "jump"],
            "bounds": {"x": 100, "y": 200, "width": 320, "height": 240},
            "anchor": "bottom-center", "fit": "contain",
        }

    def report(self, reason="no_matching_asset", **kwargs):
        return build_missing_report(self.slot, reason, self.request, **kwargs)

    def test_real_slot_animation_and_constraints_are_preserved(self):
        result = self.report(attempted_queries=["pelican ride", "bicycle bird"])
        brief = result["generation_brief"]
        self.assertEqual(result["slot_id"], "player")
        self.assertTrue(result["required"])
        self.assertEqual(brief["status"], "requested")
        self.assertEqual(brief["asset_type"], "model")
        self.assertEqual(brief["required_animations"], ["ride", "jump"])
        self.assertEqual(brief["formats"], ["glb"])
        self.assertEqual(brief["dimensions"], {"width": 320, "height": 240})
        self.assertEqual(brief["context"]["slot"]["bounds"], self.slot["bounds"])
        self.assertEqual(brief["context"]["constraints"], self.request["constraints"])
        self.assertIn(self.request["prompt"], brief["prompt"])
        self.assertIn(self.slot["description"], brief["prompt"])
        self.assertIn("ride", brief["prompt"])
        self.assertEqual(brief["style"], "soft-cartoon")
        self.assertNotIn("omit_optional", [action["type"] for action in result["actions"]])

    def test_input_lists_are_deduplicated_without_inventing_ids(self):
        result = self.report(attempted_queries=["bird", "bird", " pelican "],
                             candidate_ids=["real-a", "real-a", "real-b"])
        self.assertEqual(result["attempted_queries"], ["bird", "pelican"])
        self.assertEqual(result["candidate_ids"], ["real-a", "real-b"])
        opaque = self.report(candidate_ids=[" real-id ", " real-id "])
        self.assertEqual(opaque["candidate_ids"], [" real-id "])

    def test_resize_precedes_bounds_and_slot_style_overrides_game(self):
        self.slot.update(resize={"width": 512, "height": 512}, style="pixel")
        brief = self.report()["generation_brief"]
        self.assertEqual(brief["dimensions"], {"width": 512, "height": 512})
        self.assertEqual(brief["style"], "pixel")
        self.assertEqual(brief["context"]["slot"]["bounds"], self.slot["bounds"])

    def test_icon_requests_transparency_and_suggests_svg_without_generating_it(self):
        self.slot = {"slot_id": "pause", "type": "icon", "description": "Pause icon",
                     "required": True, "formats": ["svg"], "bounds": {"width": 48, "height": 48}}
        result = self.report()
        self.assertTrue(result["generation_brief"]["transparent_background"])
        self.assertEqual(result["generation_brief"]["method_hint"], "procedural_svg")
        action = next(action for action in result["actions"] if action["type"] == "request_generation")
        self.assertEqual(action["status"], "suggested")
        self.assertEqual(action["dispatch_status"], "not_dispatched")

    def test_png_only_icon_does_not_change_requested_format(self):
        self.slot.update(type="icon", formats=["png"], required_animations=[])
        brief = self.report()["generation_brief"]
        self.assertEqual(brief["formats"], ["png"])
        self.assertNotIn("method_hint", brief)

    def test_budget_problem_prioritizes_size_and_budget_decisions(self):
        result = self.report("total_byte_budget", candidate_ids=["existing"])
        self.assertEqual(result["reason"], "total_byte_budget")
        self.assertEqual([action["type"] for action in result["actions"]],
                         ["choose_smaller_candidate", "request_budget_change"])
        self.assertIsNone(result["generation_brief"])
        self.assertEqual(result["actions"][1]["current_max_total_bytes"], 8192)
        self.assertEqual(result["actions"][0]["candidate_ids"], ["existing"])

    def test_selector_refusal_keeps_candidate_review_distinct(self):
        result = self.report("selector_left_unassigned", candidate_ids=["existing"])
        self.assertEqual(result["reason"], "selector_left_unassigned")
        self.assertEqual(result["actions"][0]["type"], "review_selection")
        generation = next(action for action in result["actions"] if action["type"] == "request_generation")
        self.assertIn("prerequisite", generation)
        self.assertEqual(result["candidate_ids"], ["existing"])

    def test_reuse_disabled_requests_distinct_asset_without_policy_change(self):
        result = self.report("reuse_disabled", candidate_ids=["used"])
        retry = next(action for action in result["actions"] if action["type"] == "retry_query")
        self.assertTrue(retry["require_distinct_asset"])
        self.assertFalse(result["generation_brief"]["context"]["constraints"]["allow_reuse"])

    def test_optional_is_only_suggested_for_omission_and_never_marked_ready(self):
        self.slot["required"] = False
        result = self.report()
        self.assertFalse(result["required"])
        omit = next(action for action in result["actions"] if action["type"] == "omit_optional")
        self.assertEqual(omit["status"], "suggested")
        self.assertNotIn("status", result)
        self.assertNotEqual(result["generation_brief"]["status"], "ready")

    def test_brief_contains_no_fabricated_file_identity_or_url(self):
        forbidden = {"file", "path", "asset_id", "url", "source_url", "license_url"}

        def check(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        check(self.report()["generation_brief"])

    def test_is_pure_and_returns_independent_copies(self):
        original_slot, original_request = copy.deepcopy(self.slot), copy.deepcopy(self.request)
        queries, candidates = ["pelican"], ["real"]
        result = self.report(attempted_queries=queries, candidate_ids=candidates)
        result["generation_brief"]["context"]["constraints"]["licenses"].append("MIT")
        result["generation_brief"]["context"]["slot"]["bounds"]["x"] = 0
        result["candidate_ids"].append("new")
        self.assertEqual(self.slot, original_slot)
        self.assertEqual(self.request, original_request)
        self.assertEqual(queries, ["pelican"])
        self.assertEqual(candidates, ["real"])
        json.dumps(result, allow_nan=False)

    def test_missing_dimensions_remain_unspecified(self):
        self.slot.pop("bounds")
        self.assertIsNone(self.report()["generation_brief"]["dimensions"])

    def test_invalid_input_is_rejected_without_side_effects(self):
        with self.assertRaises(ValueError):
            self.report("unknown_reason")
        with self.assertRaises(ValueError):
            self.report(candidate_ids="not-a-list")
        self.slot["required"] = "false"
        with self.assertRaises(ValueError):
            self.report()


if __name__ == "__main__":
    unittest.main()
