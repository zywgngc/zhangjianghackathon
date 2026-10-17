import asyncio
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import pytest
from jsonschema import Draft202012Validator

from game_harness.benchmarks import inventory
from game_harness.acceptance import PLAN_SOURCE_HASHES
from game_harness.material_portal import MaterialPortalClient, PortalContractError
from game_harness.specifications import load_case


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


@pytest.mark.parametrize("case_id,count,engine", [
    ("GAME-TEST-001", 54, "babylon"),
    ("GAME-TEST-002", 88, "phaser"),
    ("GAME-TEST-003", 83, "babylon"),
])
def test_research_profiles_preserve_every_official_acceptance_requirement(case_id, count, engine):
    """The new research runtime and Python harness share the original 225 checks."""
    profile = json.loads((ROOT / "webmcp/benchmarks" / (case_id + ".json")).read_text())
    spec = load_case(case_id, ROOT)
    assert profile["caseId"] == spec.case_id
    assert profile["engine"] == spec.engine == engine
    assert len(profile["checks"]) == len(spec.acceptance_tests) == count
    assert [check["id"] for check in profile["checks"]] == [test["id"] for test in spec.acceptance_tests]
    for check, test in zip(profile["checks"], spec.acceptance_tests):
        # Profiles flatten line wrapping, but cannot change words or numeric rules.
        assert " ".join(check["requirement"].split()) == " ".join(test["text"].split())
    source = profile["source"]
    # The immutable reference URL belongs to the original commit. Only the
    # current checkout directory was renamed for portable filesystem support.
    assert source["path"].replace(":", "-", 1) == spec.source_path
    assert spec.sha256 == PLAN_SOURCE_HASHES[case_id]
    assert source["commit"] == "81fc5366aa417754ccdba07d5d3dc08daa8f6a02"
    url = urlparse(source["url"])
    assert url.scheme == "https" and url.netloc == "github.com"
    assert unquote(url.path) == "/zywgngc/zhangjianghackathon/blob/" + source["commit"] + "/" + source["path"]


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


@pytest.mark.parametrize("status,payload", [
    (202, {"runId": "research-run", "projectRevision": 0, "status": "queued"}),
    (200, {"version": 1, "caseId": "GAME-TEST-001", "readiness": "partial",
           "projectRevision": 0, "agentMode": "reference-planner", "candidates": [],
           "acceptance": [{"id": "GAME-TEST-001/T01", "status": "not_executed"}]}),
])
async def test_research_api_ack_or_partial_blueprint_cannot_become_material_search_evidence(settings, status, payload):
    """The research StartRequest API is not the v0.2 MaterialSearchResult bridge."""
    settings.portal_url = "https://mock.invalid/api/runs"
    portal = MaterialPortalClient(settings, transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json=payload)))
    result = await portal.search("从研究服务返回值中检索素材", run_id="actual-game-run")
    assert result["documents"] == []
    assert result["warnings"]
    assert "result" not in result["trace"]


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
