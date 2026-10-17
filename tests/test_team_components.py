import asyncio
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from game_harness.benchmarks import inventory
from game_harness.material_portal import MaterialPortalClient, PortalContractError


ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return json.loads((ROOT / "webmcp/examples" / name).read_text(encoding="utf-8"))


def test_team_case_inventory_preserves_explicit_engine_locks():
    cases = inventory(ROOT)
    assert [c["case_id"] for c in cases] == ["GAME-TEST-001", "GAME-TEST-002", "GAME-TEST-003"]
    assert [c["locked_engine"] for c in cases] == ["babylon", "phaser", "babylon"]
    assert all(c["runnable"] and c["execution"] == "not_run" for c in cases)
    assert all(c["has_acceptance_tests"] and c["reference_images"] for c in cases)
    assert all(c["blockers"] == [] for c in cases)
    assert len(cases[2]["names"]) == 2  # Header and body have different game names.
    assert all(i["width"] > 0 and i["height"] > 0 for c in cases for i in c["reference_images"])


def test_all_team_portal_examples_satisfy_their_canonical_schema(settings):
    portal = MaterialPortalClient(settings)
    for name in ["archetype-request.json", "mechanic-request.json", "portal-follow-up-request.json"]:
        portal.validate_request(fixture(name))
    request = fixture("mechanic-request.json")
    for name in ["mechanic-response.json", "portal-clarification-response.json"]:
        portal.validate_result(request, fixture(name))
    portal.validate_result(fixture("portal-follow-up-request.json"), fixture("portal-follow-up-response.json"))
    failure = fixture("failure-response.json")
    # The standalone outage fixture has its own search thread.
    request["discussion"]["threadId"] = failure["discussion"]["threadId"]
    portal.validate_result(request, failure)
    schema = json.loads(settings.portal_schema.read_text(encoding="utf-8"))
    Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/MainFollowUpRequest"}).validate(fixture("main-follow-up.json"))


@pytest.mark.parametrize("issue", ["stale_id", "duplicate", "wrong_kind", "limit", "runId", "threadId", "round"])
def test_portal_rejects_semantically_invalid_results(settings, issue):
    portal = MaterialPortalClient(settings)
    request, result = fixture("mechanic-request.json"), fixture("mechanic-response.json")
    if issue == "stale_id":
        result["requestId"] = "another-request"
    elif issue == "duplicate":
        result["candidates"][1]["candidateId"] = result["candidates"][0]["candidateId"]
    elif issue == "wrong_kind":
        result["candidates"][0]["kind"] = "reference_media"
    elif issue == "limit":
        request["limit"] = 1
    else:
        result["discussion"][issue] = 1 if issue == "round" else "unrelated"
    with pytest.raises(PortalContractError):
        portal.validate_result(request, result)


@pytest.mark.parametrize("is_mock", [True, False])
async def test_portal_mock_never_becomes_production_evidence(settings, is_mock):
    settings.portal_url = "https://mock.invalid/search"
    received = []
    def respond(request):
        data = json.loads(request.content)
        received.append(data)
        assert request.headers["Idempotency-Key"] == data["requestId"]
        result = fixture("mechanic-response.json")
        result["requestId"] = data["requestId"]
        result["discussion"] = {key: data["discussion"][key] for key in ["runId", "threadId", "round"]}
        result["isMock"] = is_mock
        return httpx.Response(200, json=result)
    portal = MaterialPortalClient(settings, transport=httpx.MockTransport(respond))
    result = await portal.search("实现中文游戏的移动机制", run_id="actual-run-123")
    assert received[0]["constraints"]["engine"] == "html"
    assert received[0]["discussion"]["runId"] == "actual-run-123"
    assert received[0]["discussion"]["round"] == 0
    assert portal.request_for("同一查询", 4, "run-1")["requestId"] == portal.request_for("同一查询", 4, "run-1")["requestId"]
    assert portal.request_for("同一查询", 4, "run-1")["requestId"] != portal.request_for("同一查询", 4, "run-2")["requestId"]
    if is_mock:
        assert result["documents"] == []
        assert any("mock" in w for w in result["warnings"])
    else:
        assert len(result["documents"]) == 2
        assert all(d["verification"] == "unverified_candidate" for d in result["documents"])
        assert all(d["license"] is None for d in result["documents"])


async def test_portal_timeout_does_not_substitute_mock_results(settings):
    settings.portal_url = "https://mock.invalid/search"
    def respond(request):
        raise httpx.ReadTimeout("timeout", request=request)
    result = await MaterialPortalClient(settings, transport=httpx.MockTransport(respond)).search("测试查询")
    assert result["documents"] == []
    assert "result" not in result["trace"]
    assert result["warnings"]


async def test_portal_cancellation_propagates(settings):
    settings.portal_url = "https://mock.invalid/search"
    started = asyncio.Event()
    async def respond(request):
        started.set()
        await asyncio.Event().wait()
    portal = MaterialPortalClient(settings, transport=httpx.MockTransport(respond))
    task = asyncio.create_task(portal.search("测试取消"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("issue", ["duplicate_question", "duplicate_option", "unknown_default"])
def test_portal_clarification_semantics(settings, issue):
    portal = MaterialPortalClient(settings)
    request, result = fixture("mechanic-request.json"), fixture("portal-clarification-response.json")
    question = result["questions"][0]
    if issue == "duplicate_question":
        result["questions"].append(dict(question))
    elif issue == "duplicate_option":
        question["options"].append(dict(question["options"][0]))
    else:
        question["defaultOptionId"] = "no-such-option"
    with pytest.raises(PortalContractError):
        portal.validate_result(request, result)


async def test_real_portal_clarification_is_preserved_without_fabricated_followups(settings):
    settings.portal_url = "https://mock.invalid/search"
    received = []
    def respond(request):
        data = json.loads(request.content)
        received.append(data)
        result = fixture("portal-clarification-response.json")
        result["isMock"] = False
        result["requestId"] = data["requestId"]
        result["discussion"] = {key: data["discussion"][key] for key in ["runId", "threadId", "round"]}
        return httpx.Response(200, json=result)
    portal = MaterialPortalClient(settings, transport=httpx.MockTransport(respond))
    result = await portal.search("测试需要澄清的检索", run_id="actual-run-123")
    assert len(received) == 1
    assert result["documents"] == []
    assert result["trace"]["discussion_outcome"] == "unresolved_clarification"
    assert result["trace"]["result"]["questions"][0]["defaultOptionId"] == "forward-only"
    assert result["trace"]["result"]["candidates"]
    assert any("澄清" in warning for warning in result["warnings"])
