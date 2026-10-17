import asyncio
import json
from pathlib import Path

import httpx
import pytest

from game_harness.benchmarks import inventory
from game_harness.material_portal import MaterialPortalClient, PortalContractError


ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return json.loads((ROOT / "webmcp/examples" / name).read_text())


def test_team_case_inventory_preserves_explicit_engine_locks():
    cases = inventory(ROOT)
    assert [c["case_id"] for c in cases] == ["GAME-TEST-001", "GAME-TEST-002", "GAME-TEST-003"]
    assert [c["locked_engine"] for c in cases] == ["babylon", "phaser", "babylon"]
    assert all(not c["runnable"] and c["execution"] == "not_run" for c in cases)
    assert all(c["has_acceptance_tests"] and c["reference_images"] for c in cases)
    assert all("long_spec_input_not_supported" in c["blockers"] for c in cases)
    assert len(cases[2]["names"]) == 2  # Header and body have different game names.
    assert all(i["width"] > 0 and i["height"] > 0 for c in cases for i in c["reference_images"])


def test_all_team_portal_examples_satisfy_their_canonical_schema(settings):
    portal = MaterialPortalClient(settings)
    for name in ["archetype-request.json", "mechanic-request.json"]:
        portal.validate_request(fixture(name))
    request = fixture("mechanic-request.json")
    for name in ["mechanic-response.json", "failure-response.json"]:
        portal.validate_result(request, fixture(name))


@pytest.mark.parametrize("issue", ["stale_id", "duplicate", "wrong_kind", "limit"])
def test_portal_rejects_semantically_invalid_results(settings, issue):
    portal = MaterialPortalClient(settings)
    request, result = fixture("mechanic-request.json"), fixture("mechanic-response.json")
    if issue == "stale_id":
        result["requestId"] = "another-request"
    elif issue == "duplicate":
        result["candidates"][1]["candidateId"] = result["candidates"][0]["candidateId"]
    elif issue == "wrong_kind":
        result["candidates"][0]["kind"] = "reference_media"
    else:
        request["limit"] = 1
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
        result["isMock"] = is_mock
        return httpx.Response(200, json=result)
    portal = MaterialPortalClient(settings, transport=httpx.MockTransport(respond))
    result = await portal.search("实现中文游戏的移动机制")
    assert received[0]["constraints"]["engine"] == "html"
    assert portal.request_for("同一查询", 4)["requestId"] == portal.request_for("同一查询", 4)["requestId"]
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
