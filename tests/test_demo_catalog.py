"""Regression queries and end-to-end handoff using the bundled real assets."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from asset_agent import AssetAgent


ROOT = Path(__file__).resolve().parents[1]


class DemoCatalogTests(unittest.TestCase):
    def setUp(self):
        self.agent = AssetAgent(ROOT / "catalogs/demo")
        self.addCleanup(self.agent.close)

    def test_bundled_assets_match_pinned_source_hashes(self):
        self.assertEqual(18, len(self.agent.catalog.assets))
        self.assertEqual([], self.agent.catalog.validate_files())

    def test_chinese_queries_find_expected_real_icons(self):
        cases = {
            "暂停按钮": "guard13007/pause-button",
            "金币计数": "delapouite/coins",
            "氮气加速": "lorc/heavy-lightning",
            "生命值": "delapouite/heart-beats",
            "静音": "delapouite/sound-off",
            "瞄准准星": "delapouite/crosshair",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                hits = self.agent.search(query, filters={"type": "icon"}, top_k=1)
                self.assertEqual("game-icons/" + expected, hits[0]["asset"]["id"])

    def test_real_bundle_is_ready_and_integrity_checked(self):
        request = json.loads((ROOT / "examples/racing-ui.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            result = self.agent.run(request, output)
            self.assertEqual("ready", result["status"])
            self.assertEqual(4, result["bundle"]["asset_count"])
            self.assertEqual(result, json.loads((output / "manifest.json").read_text(encoding="utf-8")))
            for selection in result["selections"]:
                data = (output / selection["file"]["path"]).read_bytes()
                self.assertEqual(selection["file"]["sha256"], hashlib.sha256(data).hexdigest())
                self.assertIn(selection["source"]["author"], (output / "CREDITS.txt").read_text(encoding="utf-8"))

    def test_missing_animated_model_is_explicitly_partial(self):
        request = json.loads((ROOT / "examples/missing-character.json").read_text(encoding="utf-8"))
        result = self.agent.plan(request)
        self.assertEqual("partial", result["status"])
        self.assertEqual("player", result["unresolved"][0]["slot_id"])
        self.assertEqual([], result["unresolved"][0]["candidate_ids"])


if __name__ == "__main__":
    unittest.main()
