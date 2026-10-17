"""Offline relevance regressions for the standalone material portal."""

import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]

try:
    import httpx  # noqa: F401 -- optional harness runtime dependencies
    import jsonschema  # noqa: F401
except ImportError:
    PORTAL = None
else:
    spec = importlib.util.spec_from_file_location("portal_relevance_regression", ROOT / "game_harness/material_portal.py")
    PORTAL = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(PORTAL)


@unittest.skipUnless(PORTAL is not None, "Requires optional harness httpx/jsonschema dependencies")
class HarnessPortalRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.index = PORTAL._ReferenceIndex(PORTAL._REFERENCES)

    def request(self, description, *, engine="html", purpose="archetype"):
        request = {
            "contractVersion": "0.2", "requestId": "relevance-regression", "projectRevision": 0,
            "discussion": {"runId": "relevance-run", "threadId": "archetypes", "round": 0,
                           "inReplyToRequestId": None, "followUp": None},
            "purpose": purpose, "description": description, "images": [],
            "constraints": {"engine": engine, "engineVersion": None, "platform": "web",
                            "language": "any", "inputModes": ["keyboard", "pointer", "touch"]},
            "materialKinds": ["game_repository", "source_code", "algorithm_documentation"], "limit": 10,
        }
        if purpose == "mechanic":
            request.update(mechanicId="navigation-waypoints", requiredBehavior=["Follow ordered waypoints and find routes around obstacles."])
        return request

    def ids(self, request):
        return {candidate["candidateId"] for candidate in self.index.search(request)[0]}

    def test_real_power_comparison_core_request_has_no_unrelated_html_archetype(self):
        core = json.loads((ROOT / "examples/game-harness-core/power-hunter.request.json").read_text(encoding="utf-8"))
        self.assertEqual(self.ids(self.request(core["description"])), set())

    def test_controls_score_lifecycle_and_technical_terms_are_insufficient(self):
        for description in (
            "原生HTML Canvas小游戏，键盘方向键、触摸操作，实时显示分数，开始暂停继续重开，胜负和计时",
            "Native browser JavaScript Canvas game with keyboard touch input, score updates, win/loss conditions, pause, resume and restart.",
            "显示唯一整数数字10和150，方向键移动光标，Enter确认，实时得分，适配手机触屏与电脑屏幕。",
            "Swipe and rotate with keyboard and touch, display score and a timer, restart after game over.",
        ):
            with self.subTest(description=description):
                self.assertEqual(self.ids(self.request(description)), set())

    def test_power_comparison_family_is_not_tied_to_a_specific_game_name(self):
        for description in (
            "比较战力，只能吞噬比自己弱的敌人，成功后把对方战力加到自己身上，点击自动接近目标。",
            "A combat power comparison game: absorb weaker enemies to increase strength; stronger targets cannot be consumed. Keyboard touch score pause restart.",
            "吞噬成长玩法：选择弱小目标并吸收它的力量，继续挑战更强目标。",
        ):
            with self.subTest(description=description):
                self.assertEqual(self.ids(self.request(description)), set())

    def test_matching_archetypes_survive_verbose_runtime_requirements(self):
        controls = " Canvas HTML keyboard touch score pause resume restart game over 1280x800."
        for description, engine, expected in (
            ("2048数字合并滑动棋盘", "html", {"ref-html-2048"}),
            ("合并相同数字，每次滑动棋盘后产生新的数字", "html", {"ref-html-2048"}),
            ("俄罗斯方块消行下落旋转", "html", {"ref-html-tetris"}),
            ("Falling blocks fill rows and clear complete lines", "html", {"ref-html-tetris"}),
            ("幸存者割草，自动攻击敌潮，拾取经验升级", "phaser", {"ref-wukong-survivors"}),
        ):
            with self.subTest(description=description):
                self.assertEqual(self.ids(self.request(description + controls, engine=engine)), expected)

    def test_unnamed_gameplay_theme_can_match_without_controls_as_evidence(self):
        self.assertEqual(self.ids(self.request("Merge equal-valued tiles on a grid to combine numbers.")), {"ref-html-2048"})

    def test_mechanic_navigation_and_engine_filters_are_preserved(self):
        request = self.request("Find a waypoint path implementation, with keyboard and score UI.", engine="phaser", purpose="mechanic")
        self.assertEqual(self.ids(request), {"ref-phaser-path-follower", "ref-pathfinding-grid-astar"})
        self.assertEqual(self.ids(self.request("survivors automatic combat and enemy waves", engine="html")), set())

    def test_updated_local_relevance_does_not_replay_pre_fix_cached_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(data_dir=Path(directory), portal_url="local://references", portal_token="",
                                       portal_schema=ROOT / "asset_agent/specs/material-portal-0.2.schema.json")
            client = PORTAL.MaterialPortalClient(settings)
            request = client.request_for("Native HTML Canvas keyboard touch score pause resume restart", 4, "cached-relevance")
            old_namespace = PORTAL._digest(PORTAL._json([
                "material-portal-v0.2-local",
                json.loads(Path(settings.portal_schema).read_text(encoding="utf-8-sig")),
                PORTAL._REFERENCES,
            ]))
            current_namespace = client._namespace
            client._namespace = old_namespace
            stale = client._local_search(request)
            stale["candidates"] = [deepcopy(next(record["candidate"] for record in PORTAL._REFERENCES
                                                if record["candidate"]["candidateId"] == "ref-html-2048"))]
            client.validate_result(request, stale)
            client._reserve(request)
            client._save(request, stale, "complete")
            client._namespace = current_namespace
            result = asyncio.run(client.search_request(request))
            self.assertEqual(result["candidates"], [])


if __name__ == "__main__":
    unittest.main()
