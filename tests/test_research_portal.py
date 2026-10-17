"""Executable v0.2 portal, persistent identity, and cancellation acceptance tests."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from asset_agent.research_contracts import validate_search_request, validate_search_result
from asset_agent.research_portal import ProviderError, ResearchMaterialPortal
from asset_agent.portal import MaterialPortal


_FIXTURES = Path(__file__).resolve().parents[1] / "examples" / "webmcp-v02"


def fixture(name):
    return json.loads((_FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def bare(*, candidates=None, limitations=None, **overrides):
    value = {
        "candidates": deepcopy(candidates if candidates is not None else fixture("mechanic-response")["candidates"]),
        "limitations": list(limitations if limitations is not None else ["Synthetic test provider; not production evidence"]),
        "isMock": True,
    }
    value.update(overrides)
    return value


class ResearchPortalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.request = fixture("mechanic-request")
        # Dependency/schema initialization is independent of provider timing;
        # cancellation coordination starts after that one-time setup.
        validate_search_request(self.request)

    def portal(self, provider, **kwargs):
        settings = {
            "provider": provider, "provider_name": "acceptance-test-provider",
            "cache_path": self.root / "requests.sqlite3", "allow_mock": True,
            "timeout_seconds": 1,
        }
        settings.update(kwargs)
        portal = ResearchMaterialPortal(**settings)
        self.addCleanup(portal.close)
        return portal

    def pool(self, workers=2):
        pool = ThreadPoolExecutor(max_workers=workers)
        self.addCleanup(pool.shutdown)
        return pool

    def assert_failure(self, result, code):
        validate_search_result(result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], code)
        self.assertFalse(result["isMock"])
        self.assertNotIn("candidates", result)

    def wait_for_state(self, portal, request_id, expected, timeout=1):
        until = time.monotonic() + timeout
        tick = threading.Event()
        while time.monotonic() < until:
            record = portal.get_request_status(request_id)
            if record and record["state"] == expected:
                return record
            tick.wait(0.005)
        self.fail("request {} did not become {}".format(request_id, expected))

    def test_bare_provider_result_echoes_request_and_preserves_input(self):
        observed = []

        def provider(request):
            observed.append(deepcopy(request))
            request["description"] = "provider mutation"
            return bare()

        portal = self.portal(provider)
        original = deepcopy(self.request)
        result = portal.search_sync(self.request)
        validate_search_result(result, original)
        self.assertEqual(self.request, original)
        self.assertEqual(observed, [original])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["requestId"], original["requestId"])
        self.assertEqual(result["discussion"], {key: original["discussion"][key] for key in ("runId", "threadId", "round")})
        self.assertEqual(result["provider"], "acceptance-test-provider")
        self.assertTrue(result["isMock"])
        self.assertEqual(portal.get_request_status(original["requestId"])["state"], "finished")

    def test_schema_valid_whitespace_identifiers_are_echoed_verbatim(self):
        request = deepcopy(self.request)
        request["requestId"] = " "
        request["discussion"]["runId"] = "  "
        request["discussion"]["threadId"] = "\t"
        validate_search_request(request)
        portal = self.portal(lambda request: bare(candidates=[], limitations=[]))
        result = portal.search_sync(request)
        validate_search_result(result, request)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["requestId"], " ")
        self.assertEqual(result["discussion"]["runId"], "  ")
        self.assertEqual(result["discussion"]["threadId"], "\t")

    def test_full_official_provider_result_is_accepted_without_rewriting_metadata(self):
        expected = fixture("mechanic-response")
        portal = self.portal(lambda request: deepcopy(expected))
        self.assertEqual(portal.search_sync(self.request), expected)

    def test_images_not_processed_produce_explicit_partial_limitations(self):
        request = fixture("archetype-request")
        portal = self.portal(lambda request: bare(candidates=[]))
        result = portal.search_sync(request)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any("not visually analyzed" in note for note in result["limitations"]))
        validate_search_result(result, request)

    def test_processed_images_do_not_force_partial(self):
        portal = self.portal(lambda request: bare(candidates=[], imagesProcessed=True))
        result = portal.search_sync(fixture("archetype-request"))
        self.assertEqual(result["status"], "completed")

    def test_successful_zero_candidates_is_completed_and_never_invents_fallback(self):
        portal = self.portal(lambda request: bare(candidates=[], limitations=[]))
        result = portal.search_sync(self.request)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["limitations"], [])

    def test_partial_result_keeps_explicit_provider_limitation(self):
        portal = self.portal(lambda request: bare(candidates=[], partial=True, limitations=["Search index unavailable"]))
        result = portal.search_sync(self.request)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["limitations"], ["Search index unavailable"])

    def test_invalid_candidate_kind_duplicate_id_and_overlimit_are_structured_failures(self):
        for invalidity in ("kind", "duplicate", "overlimit"):
            with self.subTest(invalidity=invalidity):
                request = deepcopy(self.request)
                request["requestId"] += "-" + invalidity
                raw = bare()
                if invalidity == "kind":
                    raw["candidates"][0]["kind"] = "game_repository"
                elif invalidity == "duplicate":
                    raw["candidates"][1]["candidateId"] = raw["candidates"][0]["candidateId"]
                else:
                    request["limit"] = 1
                portal = self.portal(lambda request, raw=raw: deepcopy(raw))
                result = portal.search_sync(request)
                self.assert_failure(result, "UNAVAILABLE")
                self.assertEqual(portal.get_request_status(request["requestId"])["state"], "finished")

    def test_wrong_full_result_correlation_is_rejected(self):
        for field in ("requestId", "runId", "threadId", "round"):
            with self.subTest(field=field):
                request = deepcopy(self.request)
                request["requestId"] += "-" + field
                raw = fixture("mechanic-response")
                raw["requestId"] = request["requestId"]
                target = raw if field == "requestId" else raw["discussion"]
                target[field] = 1 if field == "round" else "wrong"
                portal = self.portal(lambda request, raw=raw: deepcopy(raw))
                self.assert_failure(portal.search_sync(request), "UNAVAILABLE")

    def test_mock_provider_requires_explicit_opt_in_and_failure_is_not_mock(self):
        portal = self.portal(lambda request: bare(), allow_mock=False)
        self.assert_failure(portal.search_sync(self.request), "UNSUPPORTED_INPUT")

    def test_production_mode_also_rejects_previously_cached_fixture_results(self):
        development = self.portal(lambda request: bare())
        self.assertTrue(development.search_sync(self.request)["isMock"])

        def forbidden_dispatch(request):
            self.fail("a cached fixture must not cause another dispatch")

        production = self.portal(forbidden_dispatch, allow_mock=False)
        result = production.search_sync(self.request)
        self.assert_failure(result, "UNSUPPORTED_INPUT")
        self.assertTrue(development.search_sync(self.request)["isMock"])

    def test_missing_mock_marker_is_invalid_provider_data(self):
        raw = bare()
        raw.pop("isMock")
        portal = self.portal(lambda request: deepcopy(raw))
        self.assert_failure(portal.search_sync(self.request), "UNAVAILABLE")

    def test_expected_provider_error_codes_are_structured_and_correlated(self):
        for code in ("UNAVAILABLE", "TIMEOUT", "RATE_LIMITED", "INVALID_INPUT", "UNSUPPORTED_INPUT", "AUTH_REQUIRED"):
            with self.subTest(code=code):
                request = deepcopy(self.request)
                request["requestId"] += "-" + code

                def provider(request, code=code):
                    raise ProviderError(code, "A safe provider message", retryable=code == "RATE_LIMITED", retry_after_ms=120 if code == "RATE_LIMITED" else None)

                result = self.portal(provider).search_sync(request)
                self.assert_failure(result, code)
                validate_search_result(result, request)
                self.assertEqual(result["error"]["message"], "A safe provider message")

    def test_http_429_and_auth_errors_are_mapped_without_leaking_error_text(self):
        for status, code in ((429, "RATE_LIMITED"), (401, "AUTH_REQUIRED"), (403, "AUTH_REQUIRED"), (503, "UNAVAILABLE")):
            with self.subTest(status=status):
                request = deepcopy(self.request)
                request["requestId"] += "-http-" + str(status)

                def provider(request, status=status):
                    raise HTTPError("https://example.org/?token=secret", status, "credential-secret", {"Retry-After": "2"}, None)

                result = self.portal(provider).search_sync(request)
                self.assert_failure(result, code)
                self.assertNotIn("secret", json.dumps(result))
                self.assertEqual(result["error"]["retryAfterMs"], 2000)
                self.assertEqual(result["error"]["retryable"], status in (429, 503))

    def test_timeout_and_permission_exceptions_are_expected_failures(self):
        for exception, code in ((TimeoutError, "TIMEOUT"), (PermissionError, "AUTH_REQUIRED")):
            with self.subTest(exception=exception):
                request = deepcopy(self.request)
                request["requestId"] += "-" + code

                def provider(request, exception=exception):
                    raise exception("internal detail")

                self.assert_failure(self.portal(provider).search_sync(request), code)

    def test_same_request_id_with_changed_input_is_an_idempotency_conflict(self):
        calls = []
        portal = self.portal(lambda request: calls.append(request) or bare())
        original = portal.search_sync(self.request)
        changed = dict(self.request, description="A different mechanic")
        self.assert_failure(portal.search_sync(changed), "INVALID_INPUT")
        self.assertEqual(portal.search_sync(self.request), original)
        self.assertEqual(len(calls), 1)

    def test_identical_retryable_failure_can_retry_but_nonretryable_failure_is_cached(self):
        for retryable in (False, True):
            with self.subTest(retryable=retryable):
                calls = []
                request = deepcopy(self.request)
                request["requestId"] += "-retry-" + str(retryable)

                def provider(request):
                    calls.append(1)
                    if len(calls) == 1:
                        raise ProviderError("UNAVAILABLE", "A temporary source failure", retryable=retryable)
                    return bare()

                portal = self.portal(provider)
                first = portal.search_sync(request)
                self.assert_failure(first, "UNAVAILABLE")
                second = portal.search_sync(request)
                self.assertEqual(second["status"], "completed" if retryable else "failed")
                self.assertEqual(len(calls), 2 if retryable else 1)

    def test_cross_instance_concurrent_same_sqlite_request_dispatches_once(self):
        entered, release, second_reserved = threading.Event(), threading.Event(), threading.Event()
        calls = []
        lock = threading.Lock()

        def provider(request):
            with lock:
                calls.append(request["requestId"])
            entered.set()
            release.wait(1)
            return bare()

        first, second = self.portal(provider), self.portal(provider)
        observed_acquisitions = []
        reserve = second.store.reserve

        def observe_second_reservation(*args, **kwargs):
            outcome = reserve(*args, **kwargs)
            observed_acquisitions.append(outcome[0])
            second_reserved.set()
            return outcome

        second.store.reserve = observe_second_reservation
        pool = self.pool()
        self.addCleanup(release.set)
        a = pool.submit(first.search_sync, self.request)
        self.assertTrue(entered.wait(1), "provider was not entered")
        b = pool.submit(second.search_sync, self.request)
        self.assertTrue(second_reserved.wait(1), "second caller did not reserve while the first operation was pending")
        self.assertEqual(observed_acquisitions, [False])
        self.assertEqual(first.get_request_status(self.request["requestId"])["state"], "pending")
        release.set()
        self.assertEqual(a.result(1), b.result(1))
        self.assertEqual(calls, [self.request["requestId"]])

    def test_completed_result_survives_restart_with_same_timestamp_without_redispatch(self):
        original = self.portal(lambda request: bare())
        first = original.search_sync(self.request)
        original.close()
        calls = []
        restarted = self.portal(lambda request: calls.append(1) or bare(candidates=[]))
        second = restarted.search_sync(self.request)
        self.assertEqual(first, second)
        self.assertEqual(first["retrievedAt"], second["retrievedAt"])
        self.assertEqual(calls, [])

    def test_timeout_keeps_pending_and_identical_retry_observes_original_operation(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def provider(request):
            calls.append(request["requestId"])
            entered.set()
            release.wait(1)
            return bare()

        portal = self.portal(provider, timeout_seconds=0.04)
        self.addCleanup(release.set)
        first = portal.search_sync(self.request)
        self.assertTrue(entered.is_set())
        self.assert_failure(first, "TIMEOUT")
        self.assertTrue(first["error"]["retryable"])
        self.assertEqual(portal.get_request_status(self.request["requestId"])["state"], "pending")
        self.assert_failure(portal.search_sync(self.request), "TIMEOUT")
        self.assertEqual(calls, [self.request["requestId"]])
        release.set()
        persisted = self.wait_for_state(portal, self.request["requestId"], "finished")
        final = portal.search_sync(self.request)
        self.assertEqual(final, persisted["result"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(calls, [self.request["requestId"]])

    def test_event_cancellation_reaches_provider_and_remains_cancelled(self):
        entered, observed_cancel, cleanup_stop = threading.Event(), threading.Event(), threading.Event()
        cancellation = threading.Event()

        class Provider:
            def search(self, request, *, cancel_event):
                entered.set()
                while not cleanup_stop.wait(0.005):
                    if cancel_event.is_set():
                        observed_cancel.set()
                        break
                return bare()

        portal = self.portal(Provider())
        pool = self.pool()
        self.addCleanup(cleanup_stop.set)
        pending = pool.submit(portal.search_sync, self.request, {"signal": cancellation})
        self.assertTrue(entered.wait(1))
        cancellation.set()
        with self.assertRaises(asyncio.CancelledError):
            pending.result(1)
        self.assertTrue(observed_cancel.wait(1))
        self.wait_for_state(portal, self.request["requestId"], "cancelled")
        with self.assertRaises(asyncio.CancelledError):
            portal.search_sync(self.request)

    def test_async_task_cancel_propagates_and_never_persists_success(self):
        entered, observed_cancel, cleanup_stop = threading.Event(), threading.Event(), threading.Event()

        class Provider:
            def search(self, request, *, cancel_event):
                entered.set()
                while not cleanup_stop.wait(0.005):
                    if cancel_event.is_set():
                        observed_cancel.set()
                        break
                return bare()

        portal = self.portal(Provider())
        self.addCleanup(cleanup_stop.set)

        async def scenario():
            task = asyncio.create_task(portal.search(self.request))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(await asyncio.to_thread(observed_cancel.wait, 1))

        asyncio.run(scenario())
        self.wait_for_state(portal, self.request["requestId"], "cancelled")

    def test_precancelled_event_avoids_dispatch(self):
        cancellation = threading.Event()
        cancellation.set()
        calls = []
        portal = self.portal(lambda request: calls.append(1) or bare())
        with self.assertRaises(asyncio.CancelledError):
            portal.search_sync(self.request, {"signal": cancellation})
        self.assertEqual(calls, [])
        self.assertIsNone(portal.get_request_status(self.request["requestId"]))

    def test_clarification_chain_is_persisted_and_valid_reply_is_accepted(self):
        calls = []

        def provider(request):
            calls.append(request["requestId"])
            return fixture("portal-clarification-response" if request["discussion"]["round"] == 0 else "portal-follow-up-response")

        portal = self.portal(provider)
        initial = portal.search_sync(self.request)
        self.assertEqual(initial["status"], "needs_clarification")
        follow_up = fixture("portal-follow-up-request")
        result = portal.search_sync(follow_up)
        self.assertEqual(result["status"], "completed")
        validate_search_result(result, follow_up)
        self.assertEqual(calls, [self.request["requestId"], follow_up["requestId"]])

    def test_invalid_clarification_answers_fail_before_dispatch(self):
        calls = []
        portal = self.portal(lambda request: calls.append(request["requestId"]) or fixture("portal-clarification-response"))
        portal.search_sync(self.request)
        for field, replacement in (("questionId", "not-asked"), ("optionId", "not-offered")):
            with self.subTest(field=field):
                invalid = fixture("portal-follow-up-request")
                invalid["requestId"] += "-" + field
                invalid["discussion"]["followUp"]["answers"][0][field] = replacement
                self.assert_failure(portal.search_sync(invalid), "INVALID_INPUT")
                self.assertIsNone(portal.get_request_status(invalid["requestId"]))
        self.assertEqual(calls, [self.request["requestId"]])

    def test_stateless_full_snapshot_followup_returns_explicit_parent_limitation(self):
        portal = self.portal(lambda request: bare())
        result = portal.search_sync(fixture("portal-follow-up-request"))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(any("parent request" in note.lower() and "upstream" in note.lower() for note in result["limitations"]))

    def test_stateful_strict_followup_requires_a_persisted_parent(self):
        calls = []
        portal = self.portal(lambda request: calls.append(1) or bare(), require_parent=True)
        result = portal.search_sync(fixture("portal-follow-up-request"))
        self.assert_failure(result, "INVALID_INPUT")
        self.assertEqual(calls, [])

    def test_round_four_is_rejected_before_reservation_or_provider_dispatch(self):
        calls = []
        portal = self.portal(lambda request: calls.append(1) or bare())
        invalid = fixture("portal-follow-up-request")
        invalid["discussion"]["round"] = 4
        self.assert_failure(portal.search_sync(invalid), "INVALID_INPUT")
        self.assertEqual(calls, [])
        self.assertIsNone(portal.get_request_status(invalid["requestId"]))

    def test_request_status_is_readonly_and_result_copies_are_independent(self):
        calls = []
        portal = self.portal(lambda request: calls.append(1) or bare())
        self.assertIsNone(portal.get_request_status("unknown"))
        first = portal.search_sync(self.request)
        first["candidates"].clear()
        record = portal.get_request_status(self.request["requestId"])
        self.assertEqual(len(record["result"]["candidates"]), 2)
        record["result"]["candidates"].clear()
        self.assertEqual(len(portal.search_sync(self.request)["candidates"]), 2)
        self.assertEqual(calls, [1])

    def test_closed_instance_returns_structured_failure(self):
        portal = self.portal(lambda request: bare())
        portal.close()
        self.assert_failure(portal.search_sync(self.request), "UNAVAILABLE")

    def test_executor_shutdown_before_dispatch_finishes_reservation_with_retryable_failure(self):
        calls = []
        portal = self.portal(lambda request: calls.append(1) or bare())
        with patch.object(portal._executor, "submit", side_effect=RuntimeError("cannot schedule after shutdown")):
            result = portal.search_sync(self.request)
        self.assert_failure(result, "UNAVAILABLE")
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(calls, [])
        record = portal.get_request_status(self.request["requestId"])
        self.assertEqual(record["state"], "finished")
        self.assertEqual(record["result"], result)
        self.assertEqual(portal.search_sync(self.request)["status"], "completed")
        self.assertEqual(calls, [1])

    def test_invalid_async_options_return_structured_invalid_input_without_dispatch(self):
        calls = []
        portal = self.portal(lambda request: calls.append(1) or bare())

        async def scenario():
            for options in ({"unexpected": True}, {"signal": object()}, "invalid"):
                with self.subTest(options=options):
                    result = await portal.search(self.request, options)
                    self.assert_failure(result, "INVALID_INPUT")
                    validate_search_result(result, self.request)

        asyncio.run(scenario())
        self.assertEqual(calls, [])
        self.assertIsNone(portal.get_request_status(self.request["requestId"]))

    def test_material_facade_research_does_not_require_any_art_or_reference_catalog(self):
        art_catalog = self.root / "no-art-catalog"
        reference_catalog = self.root / "no-reference-catalog"
        portal = MaterialPortal(
            art_catalog, reference_catalog_dir=reference_catalog,
            research_provider=lambda request: bare(),
            research_cache_path=self.root / "facade.sqlite3", allow_mock_research=True,
        )
        self.addCleanup(portal.close)
        result = portal.search_sync(self.request)
        validate_search_result(result, self.request)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(art_catalog.exists())
        self.assertFalse(reference_catalog.exists())

    def test_material_facade_async_dispatch_preserves_formal_and_legacy_response_shapes(self):
        art_catalog = self.root / "art"
        art_catalog.mkdir()
        (art_catalog / "pause.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><path d="M4 4h4v16H4zM16 4h4v16h-4z"/></svg>',
            encoding="utf-8",
        )
        record = {
            "id": "pause", "type": "icon", "name": "Pause", "description": "Pause and resume icon",
            "tags": ["pause"], "style": "generic", "pack": "tests", "path": "pause.svg", "format": "svg",
            "license": "CC0-1.0", "author": "Test fixture", "source_url": "https://example.invalid/pause",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        }
        (art_catalog / "catalog.json").write_text(json.dumps({"schema_version": "1.0", "assets": [record]}), encoding="utf-8")
        portal = MaterialPortal(
            art_catalog, research_provider=lambda request: bare(),
            research_cache_path=self.root / "facade.sqlite3", allow_mock_research=True,
        )
        self.addCleanup(portal.close)

        async def scenario():
            formal = await portal.search(self.request)
            validate_search_result(formal, self.request)
            self.assertEqual(formal["status"], "completed")
            legacy = await portal.search({
                "purpose": "mechanic", "description": "Pause icon",
                "constraints": {"platform": "web", "engine": "Phaser", "language": "TypeScript"},
                "limit": 5,
            })
            self.assertEqual(set(legacy), {"candidates", "limitations"})
            self.assertEqual(legacy["candidates"][0]["id"], "pause")
            self.assertNotIn("contractVersion", legacy)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
