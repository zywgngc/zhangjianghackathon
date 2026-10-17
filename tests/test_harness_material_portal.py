"""Independent contract tests for the relocatable game-harness portal file.

HTTP uses MockTransport only. The optional harness HTTP/schema dependencies
are deliberately separate from the zero-dependency art-agent test suite.
"""

import ast
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

try:
    import httpx
    import jsonschema  # noqa: F401 -- explicitly check the optional dependency
except ImportError:
    httpx = None


ROOT = Path(__file__).resolve().parents[1]
MODULE_FILE = ROOT / "game_harness/material_portal.py"
SCHEMA_FILE = ROOT / "asset_agent/specs/material-portal-0.2.schema.json"
EXAMPLES = ROOT / "examples/webmcp-v02"


def load_portal(path):
    name = "standalone_material_portal_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def fixture(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def correlated_result(request, *, is_mock=False, status="completed"):
    # These source identities are real curated references, but the response is
    # supplied by a unit-test HTTP stub and never asserts tested behavior.
    catalog = json.loads((ROOT / "catalogs/references/materials.json").read_text(encoding="utf-8"))
    candidate = next(item["candidate"] for item in catalog["materials"] if item["candidate"]["candidateId"] == "ref-pathfinding-grid-astar")
    result = {
        "contractVersion": "0.2", "requestId": request["requestId"],
        "discussion": {key: request["discussion"][key] for key in ("runId", "threadId", "round")},
        "provider": "http-unit-test-provider", "retrievedAt": "2026-10-17T00:00:00Z",
        "isMock": is_mock, "status": status, "candidates": [copy.deepcopy(candidate)],
        "limitations": ["Unit-test response: source behavior was not executed or verified."],
    }
    if status == "needs_clarification":
        result.update(clarificationId="clarification-1", questions=[{
            "questionId": "reverse-traversal", "prompt": "Is reverse traversal required?",
            "options": [{"optionId": "forward-only", "label": "Forward only"}, {"optionId": "both", "label": "Both directions"}],
            "required": False, "defaultOptionId": "forward-only",
        }])
    return result


def correlated_failure(request, *, retryable, retry_after_ms=None):
    result = correlated_result(request)
    del result["candidates"], result["limitations"]
    result.update(status="failed", error={
        "code": "UNAVAILABLE", "message": "Unit-test provider failure",
        "retryable": retryable, "retryAfterMs": retry_after_ms,
    })
    return result


class HarnessSingleFileSourceTests(unittest.TestCase):
    def test_module_has_no_asset_agent_or_relative_runtime_imports(self):
        tree = ast.parse(MODULE_FILE.read_text(encoding="utf-8"), filename=str(MODULE_FILE))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse(any(alias.name == "asset_agent" or alias.name.startswith("asset_agent.") for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "The handoff must remain importable as a standalone file")
                self.assertFalse(node.module == "asset_agent" or (node.module or "").startswith("asset_agent."))


@unittest.skipUnless(httpx is not None, "Optional harness dependencies httpx and jsonschema are not installed")
class HarnessMaterialPortalTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.portal_module = load_portal(MODULE_FILE)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop(cls.portal_module.__name__, None)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "state"
        self.settings = SimpleNamespace(
            data_dir=self.data_dir, portal_url="local://references", portal_token=None,
            portal_schema=SCHEMA_FILE,
        )

    def client(self, *, transport=None, settings=None):
        return self.portal_module.MaterialPortalClient(settings or self.settings, transport=transport)

    def mechanic_request(self, engine="phaser", *, request_id=None):
        request = fixture("mechanic-request.json")
        request["requestId"] = request_id or "harness-navigation-" + engine
        request["constraints"]["engine"] = engine
        request["discussion"]["runId"] = "harness-run"
        return request

    async def test_copied_single_file_runs_local_catalog_without_asset_agent(self):
        standalone = Path(self.temporary.name) / "standalone/material_portal.py"
        standalone.parent.mkdir()
        shutil.copyfile(MODULE_FILE, standalone)
        original_import = __import__

        def isolated_import(name, *args, **kwargs):
            if name == "asset_agent" or name.startswith("asset_agent.") or name == "game_harness" or name.startswith("game_harness."):
                raise AssertionError("Standalone portal attempted an application-package import")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=isolated_import):
            module = load_portal(standalone)
            try:
                portal = module.MaterialPortalClient(self.settings)
                result = await portal.search_request(self.mechanic_request())
            finally:
                sys.modules.pop(module.__name__, None)
        self.assertFalse(result["isMock"])
        self.assertIn("ref-phaser-path-follower", {item["candidateId"] for item in result["candidates"]})

    async def test_typed_local_engine_filters_keep_html_phaser_and_babylon_separate(self):
        expected = {
            "html": {"ref-pathfinding-grid-astar"},
            "phaser": {"ref-phaser-path-follower", "ref-pathfinding-grid-astar"},
            "babylon": {"ref-babylon-recast-navigation", "ref-pathfinding-grid-astar"},
        }
        portal = self.client()
        for engine, candidates in expected.items():
            with self.subTest(engine=engine):
                request = self.mechanic_request(engine)
                original = copy.deepcopy(request)
                result = await portal.search_request(request)
                portal.validate_result(request, result)
                self.assertEqual(request, original)
                self.assertFalse(result["isMock"])
                self.assertIn(result["status"], {"completed", "partial"})
                self.assertEqual({item["candidateId"] for item in result["candidates"]}, candidates)
                for candidate in result["candidates"]:
                    uri = urlsplit(candidate["source"]["uri"])
                    self.assertEqual(uri.scheme, "https")
                    self.assertIn(uri.hostname, {"github.com", "raw.githubusercontent.com"})
                    self.assertTrue(candidate["source"]["revision"])
                    self.assertTrue(candidate["evidence"])
                    notes = " ".join(candidate["limitations"]).casefold()
                    self.assertTrue("unverified" in notes or "not verified" in notes)

    async def test_local_legacy_html_archetype_does_not_relax_engine_or_invent_candidates(self):
        portal = self.client()
        result = await portal.search("survivors automatic combat enemy waves", run_id="html-archetype-run")
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["trace"]["request"]["constraints"]["engine"], "html")
        self.assertEqual(result["trace"]["request"]["purpose"], "archetype")
        self.assertEqual(result["trace"]["result"]["candidates"], [])
        self.assertFalse(result["trace"]["result"]["isMock"])

    async def test_named_archetype_does_not_fill_limit_with_other_game_genres(self):
        portal = self.client()
        for query, expected in (("2048数字合并，方向键手机滑动，计分胜负", ["ref-html-2048"]),
                                ("俄罗斯方块消行，方向键控制，网格计分", ["ref-html-tetris"]),
                                ("手机触控赛车游戏，有方向键和得分", [])):
            with self.subTest(query=query):
                result = await portal.search(query, run_id="named-archetype-" + query)
                self.assertEqual([item["candidate_id"] for item in result["documents"]], expected)

    async def test_legacy_chinese_queries_find_the_native_html_game_archetypes(self):
        portal = self.client()
        repositories = {
            "ref-html-2048": "https://github.com/gabrielecirulli/2048/",
            "ref-html-tetris": "https://github.com/jakesgordon/javascript-tetris/",
        }
        for query, expected in (("2048数字合并滑动棋盘", "ref-html-2048"), ("俄罗斯方块消行下落旋转", "ref-html-tetris")):
            with self.subTest(query=query):
                result = await portal.search(query, run_id="native-html-" + expected)
                self.assertTrue(result["documents"])
                self.assertEqual(result["documents"][0]["candidate_id"], expected)
                self.assertEqual(result["trace"]["request"]["constraints"]["engine"], "html")
                self.assertFalse(result["trace"]["result"]["isMock"])
                for document in result["documents"]:
                    self.assertIn(document["candidate_id"], repositories)
                    self.assertTrue(document["source"].startswith(repositories[document["candidate_id"]]))
                    self.assertEqual(document["verification"], "unverified_candidate")

    async def test_typed_local_image_reference_reports_text_only_without_http_download(self):
        request = self.mechanic_request(request_id="local-image-reference")
        request["images"] = [{"uri": "asset://reference/waypoints", "mimeType": "image/png", "description": "路径截图"}]
        before = copy.deepcopy(request)
        with patch.object(httpx, "AsyncClient", side_effect=AssertionError("Local image references must not construct an HTTP client")):
            result = await self.client().search_request(request)
        self.assertEqual(request, before)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["isMock"])
        notes = " ".join(result["limitations"]).casefold()
        self.assertIn("text only", notes)
        self.assertIn("not resolved", notes)

    async def test_local_unrelated_query_and_enemy_wave_results_are_honest(self):
        portal = self.client()
        request = self.mechanic_request(request_id="empty-query")
        request.update(mechanicId="opaque-9284", description="unobtainium zebra dirigible", requiredBehavior=["quartz zeppelin"])
        empty = await portal.search_request(request)
        self.assertEqual(empty["candidates"], [])
        self.assertEqual(empty["status"], "completed")
        request = self.mechanic_request(request_id="wave-query")
        request.update(mechanicId="enemy-wave-scheduling", description="enemy wave scheduling", requiredBehavior=["Schedule enemy spawns using elapsed simulation time"])
        waves = await portal.search_request(request)
        self.assertEqual([item["candidateId"] for item in waves["candidates"]], ["ref-wukong-wave-spawning"])

    def test_request_ids_are_stable_and_run_isolated(self):
        portal = self.client()
        first = portal.request_for("同一查询", 4, "run-1")
        self.assertEqual(first, portal.request_for("同一查询", 4, "run-1"))
        self.assertNotEqual(first["requestId"], portal.request_for("同一查询", 4, "run-2")["requestId"])
        self.assertEqual(first["discussion"]["round"], 0)
        self.assertEqual(first["discussion"]["runId"], "run-1")

    def test_validation_rejects_invalid_correlation_candidates_and_clarification(self):
        portal = self.client()
        request = self.mechanic_request()
        with self.assertRaises(self.portal_module.PortalContractError):
            portal.validate_request(dict(request, limit=11))
        for issue in ("request", "run", "duplicate", "kind", "limit", "default", "duplicate-option"):
            with self.subTest(issue=issue):
                result = correlated_result(request, status="needs_clarification" if issue in {"default", "duplicate-option"} else "completed")
                tested_request = copy.deepcopy(request)
                if issue == "request":
                    result["requestId"] = "unrelated"
                elif issue == "run":
                    result["discussion"]["runId"] = "another-run"
                elif issue == "duplicate":
                    result["candidates"].append(copy.deepcopy(result["candidates"][0]))
                elif issue == "kind":
                    result["candidates"][0]["kind"] = "reference_media"
                elif issue == "limit":
                    tested_request["limit"] = 1
                    extra = copy.deepcopy(result["candidates"][0])
                    extra["candidateId"] = "another-candidate"
                    result["candidates"].append(extra)
                elif issue == "default":
                    result["questions"][0]["defaultOptionId"] = "missing"
                else:
                    result["questions"][0]["options"].append(copy.deepcopy(result["questions"][0]["options"][0]))
                with self.assertRaises(self.portal_module.PortalContractError):
                    portal.validate_result(tested_request, result)

    async def test_http_typed_request_preserves_payload_and_headers(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        self.settings.portal_token = "unit-test-token"
        received = []

        def respond(http_request):
            payload = json.loads(http_request.content)
            received.append(payload)
            self.assertEqual(http_request.headers["Idempotency-Key"], payload["requestId"])
            self.assertEqual(http_request.headers["Authorization"], "Bearer unit-test-token")
            return httpx.Response(200, json=correlated_result(payload))

        request = self.mechanic_request()
        result = await self.client(transport=httpx.MockTransport(respond)).search_request(request)
        self.assertEqual(received, [request])
        self.assertEqual(result["requestId"], request["requestId"])

    async def test_real_http_documents_keep_source_and_unverified_marker(self):
        self.settings.portal_url = "https://unit-test.invalid/search"

        def respond(http_request):
            return httpx.Response(200, json=correlated_result(json.loads(http_request.content)))

        result = await self.client(transport=httpx.MockTransport(respond)).search("pathfinding reference", run_id="document-run")
        self.assertEqual(len(result["documents"]), 1)
        document = result["documents"][0]
        self.assertEqual(document["verification"], "unverified_candidate")
        self.assertEqual(document["origin"], "material_portal")
        self.assertEqual(document["candidate_id"], "ref-pathfinding-grid-astar")
        self.assertTrue(document["source"].startswith("https://github.com/"))
        self.assertTrue(document["revision"])
        self.assertTrue(document["license"])
        candidate = result["trace"]["result"]["candidates"][0]
        self.assertEqual(document["paths"], candidate["source"]["paths"])
        self.assertEqual(document["symbols"], candidate["source"]["symbols"])
        self.assertEqual(document["evidence"], candidate["evidence"])

    async def test_mock_and_clarification_claim_cache_never_promotes_documents(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        for status, is_mock in (("completed", True), ("needs_clarification", False)):
            with self.subTest(status=status, is_mock=is_mock):
                received = []

                def respond(http_request):
                    payload = json.loads(http_request.content)
                    received.append(payload)
                    return httpx.Response(200, json=correlated_result(payload, status=status, is_mock=is_mock))

                portal = self.client(transport=httpx.MockTransport(respond))
                run_id = "mock-run" if is_mock else "clarification-run"
                first = await portal.search("pathfinding", run_id=run_id)
                second = await portal.search("pathfinding", run_id=run_id)
                self.assertEqual(first["documents"], [])
                self.assertEqual(second["documents"], [])
                self.assertEqual(len(received), 1)
                self.assertTrue(first["warnings"])
                self.assertEqual(first["trace"]["result"], second["trace"]["result"])
                self.assertEqual(first["trace"]["result"]["isMock"], is_mock)
                if status == "needs_clarification":
                    self.assertEqual(first["trace"]["discussion_outcome"], "unresolved_clarification")
                    self.assertEqual(first["trace"]["result"]["questions"][0]["defaultOptionId"], "forward-only")
                    self.assertTrue(first["trace"]["result"]["candidates"])

    async def test_http_network_failure_is_raised_low_level_and_warned_by_legacy(self):
        self.settings.portal_url = "https://unit-test.invalid/search"

        def respond(http_request):
            raise httpx.ReadTimeout("unit-test timeout", request=http_request)

        portal = self.client(transport=httpx.MockTransport(respond))
        with self.assertRaises(httpx.ReadTimeout):
            await portal.search_request(self.mechanic_request())
        result = await portal.search("pathfinding", run_id="timeout-run")
        self.assertEqual(result["documents"], [])
        self.assertTrue(result["warnings"])
        self.assertNotIn("result", result["trace"])

    async def test_uncertain_remote_timeout_is_not_automatically_redispatched(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        received = []

        def respond(http_request):
            received.append(json.loads(http_request.content))
            raise httpx.ReadTimeout("unknown remote job state", request=http_request)

        portal = self.client(transport=httpx.MockTransport(respond))
        request = self.mechanic_request()
        with self.assertRaises(httpx.ReadTimeout):
            await portal.search_request(request)
        with self.assertRaises(self.portal_module.PortalContractError):
            await self.client(transport=httpx.MockTransport(respond)).search_request(request)
        self.assertEqual(received, [request])

    async def test_local_reference_revision_cannot_bypass_uncertain_remote_dispatch(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        self.settings.portal_token = "unchanged-unit-test-token"
        received = []

        def respond(http_request):
            received.append(json.loads(http_request.content))
            raise httpx.ReadTimeout("unknown remote job state", request=http_request)

        transport = httpx.MockTransport(respond)
        request = self.mechanic_request(request_id="remote-timeout-before-catalog-change")
        with self.assertRaises(httpx.ReadTimeout):
            await self.client(transport=transport).search_request(request)
        changed_local_catalog = list(self.portal_module._REFERENCES) + [{"namespace-only-unit-test-marker": True}]
        with patch.object(self.portal_module, "_REFERENCES", changed_local_catalog):
            with self.assertRaises(self.portal_module.PortalContractError):
                await self.client(transport=transport).search_request(request)
        self.assertEqual(received, [request])

    async def test_formal_retryable_failure_allows_a_later_explicit_same_id_call(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        for retry_after in (0, None):
            with self.subTest(retry_after=retry_after):
                received = []

                def respond(http_request):
                    payload = json.loads(http_request.content)
                    received.append(payload)
                    result = correlated_failure(payload, retryable=True, retry_after_ms=retry_after) if len(received) == 1 else correlated_result(payload)
                    return httpx.Response(200, json=result)

                portal = self.client(transport=httpx.MockTransport(respond))
                request = self.mechanic_request(request_id="retryable-" + str(retry_after))
                first = await portal.search_request(request)
                self.assertEqual(first["status"], "failed")
                self.assertEqual(len(received), 1, "The adapter must not retry a provider failure automatically")
                second = await portal.search_request(request)
                self.assertEqual(second["status"], "completed")
                self.assertEqual(received, [request, request])

    async def test_retry_after_and_nonretryable_failures_do_not_redispatch(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        for retryable, retry_after in ((True, 60000), (False, None)):
            with self.subTest(retryable=retryable, retry_after=retry_after):
                received = []

                def respond(http_request):
                    payload = json.loads(http_request.content)
                    received.append(payload)
                    return httpx.Response(200, json=correlated_failure(payload, retryable=retryable, retry_after_ms=retry_after))

                transport = httpx.MockTransport(respond)
                request = self.mechanic_request(request_id="held-failure-" + str(retryable))
                first = await self.client(transport=transport).search_request(request)
                second = await self.client(transport=transport).search_request(request)
                self.assertEqual(first["status"], "failed")
                self.assertEqual(second, first)
                self.assertEqual(received, [request])

    async def test_unknown_parent_cannot_reference_its_own_request_id(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        received = []

        def respond(http_request):
            payload = json.loads(http_request.content)
            received.append(payload)
            return httpx.Response(200, json=correlated_result(payload))

        request = self.mechanic_request(request_id="self-parent")
        request["discussion"].update(round=1, inReplyToRequestId=request["requestId"], followUp={
            "message": "Refine the same behavior", "clarificationId": None, "answers": [],
        })
        with self.assertRaises(self.portal_module.PortalContractError):
            await self.client(transport=httpx.MockTransport(respond)).search_request(request)
        self.assertEqual(received, [])

    async def test_concurrent_same_request_uses_one_remote_dispatch(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        received = []

        async def respond(http_request):
            payload = json.loads(http_request.content)
            received.append(payload)
            await asyncio.sleep(0.01)
            return httpx.Response(200, json=correlated_result(payload))

        portal = self.client(transport=httpx.MockTransport(respond))
        request = self.mechanic_request()
        results = await asyncio.gather(*(portal.search_request(copy.deepcopy(request)) for _ in range(4)))
        self.assertEqual(len(received), 1)
        self.assertTrue(all(result == results[0] for result in results))

    def test_uri_validation_survives_base_jsonschema_without_optional_format_packages(self):
        checker_type = self.portal_module.FormatChecker
        with patch.object(self.portal_module, "FormatChecker", side_effect=lambda: checker_type(formats=[])):
            portal = self.client()
        request = self.mechanic_request()
        for uri in ("not a uri", "https://", "https://example.invalid:bad/source"):
            with self.subTest(uri=uri):
                invalid = copy.deepcopy(request)
                invalid["images"] = [{"uri": uri, "mimeType": "image/png", "description": "Input reference"}]
                with self.assertRaises(self.portal_module.PortalContractError):
                    portal.validate_request(invalid)
        result = correlated_result(request)
        result["candidates"][0]["source"]["uri"] = "../relative/source.js"
        with self.assertRaises(self.portal_module.PortalContractError):
            portal.validate_result(request, result)

    def test_datetime_validation_survives_base_jsonschema_without_optional_format_packages(self):
        checker_type = self.portal_module.FormatChecker
        with patch.object(self.portal_module, "FormatChecker", side_effect=lambda: checker_type(formats=[])):
            portal = self.client()
        request = self.mechanic_request()
        for timestamp in ("2026-10-17", "2026-02-30T00:00:00Z", "2026-10-17T00:00:00"):
            with self.subTest(timestamp=timestamp):
                result = correlated_result(request)
                result["retrievedAt"] = timestamp
                with self.assertRaises(self.portal_module.PortalContractError):
                    portal.validate_result(request, result)

    async def clarification_exchange(self, *, required_question=False):
        self.settings.portal_url = "https://unit-test.invalid/search"
        parent = self.mechanic_request()
        received = []

        def respond(http_request):
            payload = json.loads(http_request.content)
            received.append(payload)
            is_parent = payload["requestId"] == parent["requestId"]
            result = correlated_result(payload, status="needs_clarification" if is_parent else "completed")
            if is_parent and required_question:
                result["questions"].append({"questionId": "path-scale", "prompt": "How many path points?", "options": [], "required": True, "defaultOptionId": None})
            return httpx.Response(200, json=result)

        transport = httpx.MockTransport(respond)
        portal = self.client(transport=transport)
        await portal.search_request(parent)
        child = copy.deepcopy(parent)
        child["requestId"] = "clarification-follow-up"
        child["discussion"].update(round=1, inReplyToRequestId=parent["requestId"], followUp={
            "message": "Forward traversal is sufficient", "clarificationId": "clarification-1",
            "answers": [{"questionId": "reverse-traversal", "source": "developer_choice", "optionId": "forward-only", "text": None}],
        })
        return portal, transport, received, child

    async def test_known_parent_rejects_invalid_clarification_answers_before_dispatch(self):
        portal, _, received, child = await self.clarification_exchange()
        for issue in ("question", "option", "duplicate", "default"):
            with self.subTest(issue=issue):
                invalid = copy.deepcopy(child)
                invalid["requestId"] += "-" + issue
                answers = invalid["discussion"]["followUp"]["answers"]
                if issue == "question":
                    answers[0]["questionId"] = "unknown-question"
                elif issue == "option":
                    answers[0]["optionId"] = "unknown-option"
                elif issue == "duplicate":
                    answers.append(copy.deepcopy(answers[0]))
                else:
                    answers[0].update(source="system_default", optionId="both")
                with self.assertRaises(self.portal_module.PortalContractError):
                    await portal.search_request(invalid)
        self.assertEqual(len(received), 1)

    async def test_known_parent_rejects_cross_run_or_thread_follow_up(self):
        portal, _, received, child = await self.clarification_exchange()
        for field in ("runId", "threadId"):
            with self.subTest(field=field):
                invalid = copy.deepcopy(child)
                invalid["requestId"] += "-" + field
                invalid["discussion"][field] = "another-conversation"
                with self.assertRaises(self.portal_module.PortalContractError):
                    await portal.search_request(invalid)
        self.assertEqual(len(received), 1)

    async def test_known_parent_required_question_cannot_be_omitted(self):
        portal, _, received, child = await self.clarification_exchange(required_question=True)
        with self.assertRaises(self.portal_module.PortalContractError):
            await portal.search_request(child)
        self.assertEqual(len(received), 1)

    async def test_valid_typed_follow_up_uses_persisted_parent_clarification(self):
        _, transport, received, child = await self.clarification_exchange()
        result = await self.client(transport=transport).search_request(child)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["requestId"], child["requestId"])
        self.assertEqual(received[-1], child)
        self.assertEqual(len(received), 2)

    async def test_mismatched_response_raises_low_level_and_warns_legacy(self):
        self.settings.portal_url = "https://unit-test.invalid/search"

        def respond(http_request):
            result = correlated_result(json.loads(http_request.content))
            result["requestId"] = "wrong-request"
            return httpx.Response(200, json=result)

        portal = self.client(transport=httpx.MockTransport(respond))
        with self.assertRaises(self.portal_module.PortalContractError):
            await portal.search_request(self.mechanic_request())
        result = await portal.search("pathfinding", run_id="invalid-result-run")
        self.assertEqual(result["documents"], [])
        self.assertTrue(result["warnings"])

    async def test_oversized_stream_is_rejected_before_all_chunks_are_buffered(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        streams = []

        class LargeStream(httpx.AsyncByteStream):
            def __init__(self):
                self.chunks_read = 0
                self.closed = False

            async def __aiter__(self):
                for _ in range(3):
                    self.chunks_read += 1
                    yield b" " * 600000

            async def aclose(self):
                self.closed = True

        def respond(http_request):
            stream = LargeStream()
            streams.append(stream)
            return httpx.Response(200, stream=stream, headers={"Content-Type": "application/json"})

        portal = self.client(transport=httpx.MockTransport(respond))
        with self.assertRaises(self.portal_module.PortalContractError):
            await portal.search_request(self.mechanic_request())
        self.assertTrue(streams[0].closed)
        self.assertLessEqual(streams[0].chunks_read, 2)
        result = await portal.search("pathfinding", run_id="oversized-run")
        self.assertEqual(result["documents"], [])
        self.assertTrue(result["warnings"])

    async def test_explicit_http_cancellation_propagates(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        started = asyncio.Event()

        async def respond(http_request):
            started.set()
            await asyncio.Event().wait()

        portal = self.client(transport=httpx.MockTransport(respond))
        task = asyncio.create_task(portal.search("cancelled search", run_id="cancelled-run"))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_success_cache_is_persistent_deep_copied_and_run_isolated(self):
        self.settings.portal_url = "https://unit-test.invalid/search"
        received = []

        def respond(http_request):
            payload = json.loads(http_request.content)
            received.append(payload)
            return httpx.Response(200, json=correlated_result(payload))

        transport = httpx.MockTransport(respond)
        portal = self.client(transport=transport)
        request = self.mechanic_request()
        first = await portal.search_request(request)
        first["candidates"][0]["source"]["paths"].append("caller-mutation")
        second = await portal.search_request(request)
        third = await self.client(transport=transport).search_request(request)
        self.assertEqual(len(received), 1)
        self.assertEqual(second, third)
        self.assertNotIn("caller-mutation", third["candidates"][0]["source"]["paths"])
        await portal.search("same query", run_id="run-a")
        await portal.search("same query", run_id="run-b")
        self.assertEqual(len(received), 3)
        self.assertNotEqual(received[-1]["requestId"], received[-2]["requestId"])

    async def test_same_request_id_with_changed_input_is_rejected(self):
        self.settings.portal_url = "https://unit-test.invalid/search"

        def respond(http_request):
            return httpx.Response(200, json=correlated_result(json.loads(http_request.content)))

        portal = self.client(transport=httpx.MockTransport(respond))
        request = self.mechanic_request()
        await portal.search_request(request)
        changed = copy.deepcopy(request)
        changed["description"] += " changed behavior"
        with self.assertRaises(self.portal_module.PortalContractError):
            await portal.search_request(changed)

    async def test_success_cache_is_isolated_by_provider_endpoint(self):
        received = []

        def respond(http_request):
            payload = json.loads(http_request.content)
            received.append(str(http_request.url))
            result = correlated_result(payload)
            result["provider"] = http_request.url.host
            return httpx.Response(200, json=result)

        transport = httpx.MockTransport(respond)
        first_settings = SimpleNamespace(**vars(self.settings))
        first_settings.portal_url = "https://first-unit-test.invalid/search"
        second_settings = SimpleNamespace(**vars(self.settings))
        second_settings.portal_url = "https://second-unit-test.invalid/search"
        request = self.mechanic_request()
        first = await self.client(transport=transport, settings=first_settings).search_request(request)
        second = await self.client(transport=transport, settings=second_settings).search_request(request)
        self.assertEqual(len(received), 2)
        self.assertNotEqual(first["provider"], second["provider"])


if __name__ == "__main__":
    unittest.main()
