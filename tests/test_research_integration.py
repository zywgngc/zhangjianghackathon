import asyncio
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from game_harness.app import create_app
from game_harness.locking import owner_lock
from game_harness.rag import Retriever
from game_harness.research import ResearchClient, ResearchError, ResearchReference
from game_harness.runner import Harness
from scripts.run_integrated import research_environment, select_research_node
from scripts import run_integrated
from test_harness import Agents, Validator

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "aa000000-0000-0000-0000-000000000001"


def configure(settings):
    settings.research_url = "http://research.invalid:4174"
    settings.research_token = "backend-only-research-token"
    settings.api_token = "harness-user-token"
    return {"Authorization": "Bearer " + settings.api_token}


def state_fixture():
    # This is a future compatible-provider fixture, never a claim about the current partial Planner.
    blueprint = {"version": 1, "projectRevision": 1, "readiness": "ready", "gaps": [],
        "plan": {"engine": "html", "mechanics": [{"id": "movement", "behavior": ["Move with elapsed time"]}]},
        "candidates": [{"mechanicId": "movement", "isMock": False, "evidence": "behavior_verified", "projectRevision": 1}],
        "acceptance": [{"id": "move-1", "requirement": "Frame-independent movement", "status": "not_executed"}]}
    return {"id": RUN_ID, "projectId": "project-1", "revision": 1, "status": "completed", "blueprints": [blueprint]}


def reference():
    return ResearchReference(runId=RUN_ID, projectRevision=1, blueprintVersion=1)


def test_harness_proxy_uses_backend_credentials_and_preserves_commands(settings):
    headers = configure(settings)
    seen = []
    def upstream(request):
        seen.append(request)
        if request.url.path.endswith("follow-ups"):
            return httpx.Response(409, json={"error": {"code": "FOLLOW_UP_LIMIT_REACHED", "message": "main allowance exhausted"}})
        return httpx.Response(200, json={"runId": RUN_ID, "status": "queued"})
    app = create_app(settings, research_factory=lambda s: ResearchClient(s, httpx.MockTransport(upstream)))
    command = {"requestId": "request-1", "projectId": "project-1", "caseId": "GAME-TEST-001",
               "images": [{"uri": "https://images.example/ref.png", "mimeType": "image/png", "description": "reference"}]}
    with TestClient(app) as client:
        assert client.post("/api/research/runs", json=command).status_code == 401
        assert not seen
        assert client.post("/api/research/runs", json=command, headers=headers).status_code == 202
        assert json.loads(seen[-1].content) == command
        assert seen[-1].headers["Authorization"] == "Bearer " + settings.research_token
        result = client.post(f"/api/research/runs/{RUN_ID}/follow-ups", json={"requestId": "round-four"}, headers=headers)
        assert result.status_code == 409 and result.json()["error"]["code"] == "FOLLOW_UP_LIMIT_REACHED"
        assert len(seen) == 2  # No invisible write retries or budget handling in the gateway.
        assert client.post("/api/research/runs", content="{" + " " * 65536, headers={**headers, "Content-Type": "application/json"}).status_code == 413
        config = client.get("/api/config", headers=headers).json()
        assert config["research_configured"]
        assert settings.research_token not in json.dumps(config)
        page = client.get("/research")
        assert 'data-api-base="/api/research"' in page.text
        assert settings.research_token not in page.text
        assert client.get("/research-assets/request-schema.mjs").status_code == 200
        assert client.get("/research-assets/ui-zh.mjs").status_code == 200
        assert client.get("/research-assets/secret.txt").status_code == 404


async def test_unconfigured_timeout_invalid_response_and_cancellation(settings):
    with pytest.raises(ResearchError, match="尚未配置"):
        await ResearchClient(settings).request("GET", "/api/runs")
    configure(settings)
    def timeout(request):
        raise httpx.ReadTimeout("secret URL must not leak", request=request)
    with pytest.raises(ResearchError) as error:
        await ResearchClient(settings, httpx.MockTransport(timeout)).request("GET", "/api/runs")
    assert error.value.code == "RESEARCH_TIMEOUT" and "secret" not in error.value.message
    for response in [httpx.Response(200, text="not json"), httpx.Response(302, json={}), httpx.Response(401, json={})]:
        with pytest.raises(ResearchError):
            await ResearchClient(settings, httpx.MockTransport(lambda _: response)).request("GET", "/api/runs")
    started = asyncio.Event()
    async def block(_request):
        started.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(ResearchClient(settings, httpx.MockTransport(block)).request("GET", "/api/runs"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("mutation,code", [("revision", "STALE_REVISION"), ("version", "STALE_BLUEPRINT"), ("run", "INVALID_RESEARCH_RESPONSE")])
async def test_handoff_rejects_stale_or_unrelated_snapshots(settings, mutation, code):
    configure(settings)
    state = state_fixture()
    if mutation == "revision": state["revision"] = 2
    if mutation == "version": state["blueprints"][0]["version"] = 2
    if mutation == "run": state["id"] = "other"
    with pytest.raises(ResearchError) as error:
        await ResearchClient(settings, httpx.MockTransport(lambda _: httpx.Response(200, json=state))).handoff(reference())
    assert error.value.code == code


def test_partial_engine_and_mock_gates_prevent_queueing_or_paid_calls(settings, request_data):
    headers = configure(settings)
    state = state_fixture()
    state["status"] = "partial"
    bp = state["blueprints"][0]
    bp["readiness"] = "partial"
    bp["plan"]["engine"] = "babylon"
    bp["candidates"][0].update(isMock=True, evidence="discovered")
    app = create_app(settings, research_factory=lambda s: ResearchClient(s, httpx.MockTransport(lambda _: httpx.Response(200, json=state))))
    with TestClient(app) as client:
        report = client.post(f"/api/research/runs/{RUN_ID}/handoff", json=reference().model_dump(), headers=headers).json()
        assert not report["eligible"] and "quick_run_handoff_engine_not_supported:babylon" in report["blockers"]
        assert "synthetic_or_unknown_source_evidence" in report["blockers"]
        result = client.post("/api/runs", json={**request_data, "research": reference().model_dump()}, headers=headers)
        assert result.status_code == 409
        assert app.state.store.list() == []


async def test_pinned_handoff_reaches_builder_and_qa_without_a_second_material_search(settings, store, request_data, contracts):
    configure(settings)
    state = state_fixture()
    client = ResearchClient(settings, httpx.MockTransport(lambda _: httpx.Response(200, json=state)))
    handoff = await client.handoff(reference(), "canvas")
    assert handoff["eligible"]
    data = {**request_data, "research": reference().model_dump()}
    rid = store.create(data, artifacts={"research.blueprint.json": handoff["blueprint"],
        "research.handoff.json": {k: v for k, v in handoff.items() if k != "blueprint"}})["id"]
    assert store.claim() == rid
    retriever = Retriever(settings)
    async def forbidden_search(*args, **kwargs):
        pytest.fail("Attached research must not restart material searches")
    retriever.search = forbidden_search
    await Harness(settings, store, Agents(contracts), retriever, Validator(True)).execute(rid)
    assert store.get(rid)["status"] == "passed"
    assert store.read_json(rid, "v1.build-input.json")["data"]["research"]["sha256"] == handoff["sha256"]
    assert store.read_json(rid, "v1.review.input.json")["data"]["original_request"]["research"]["reference"] == reference().model_dump()
    assert store.read_json(rid, "manifest.json")["research"]["sha256"] == handoff["sha256"]
    state["revision"] = 2  # Existing builds stay pinned, never silently refresh their research.
    tampered = copy.deepcopy(handoff["blueprint"])
    tampered["gaps"] = ["modified after handoff"]
    store.write(rid, "research.blueprint.json", tampered)
    agents = Agents(contracts)
    store.update(rid, status="running")
    await Harness(settings, store, agents, retriever, Validator(True)).execute(rid)
    assert store.get(rid)["status"] == "failed" and not agents.roles


async def test_full_project_entry_retains_harness_ownership(settings):
    headers = configure(settings)
    def unexpected_research(_request):
        pytest.fail("Full-spec projects must not implicitly start or consume research")
    app = create_app(settings, research_factory=lambda s: ResearchClient(s, httpx.MockTransport(unexpected_research)))
    # No lifespan/worker: this verifies admission without executing a project or calling a model.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        command = {"case_id": "GAME-TEST-002"}
        unsupported = await api.post("/api/projects", json={**command, "research": reference().model_dump()}, headers=headers)
        assert unsupported.status_code == 422
        assert app.state.store.list() == []
        created = await api.post("/api/projects", json=command, headers=headers)
        assert created.status_code == 202
        saved = app.state.store.request(created.json()["id"])
        assert saved["mode"] == "project" and saved["engine"] == "phaser"
        assert len(saved["specification"]) > 8000 and "research" not in saved


def test_server_lock_excludes_another_owner_and_releases(tmp_path):
    path = tmp_path / "owner.lock"
    with owner_lock(path):
        with pytest.raises(RuntimeError):
            with owner_lock(path):
                pytest.fail("Second owner acquired lock")
    with owner_lock(path):
        pass


def test_sidecar_environment_does_not_inherit_host_credentials_or_node_hooks(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "RAG_API_TOKEN", "MATERIAL_PORTAL_TOKEN", "HARNESS_API_TOKEN",
                "SSH_AUTH_SOCK", "NODE_OPTIONS", "NODE_PATH", "HTTP_PROXY", "npm_config_token",
                "WEBMCP_RESEARCH_TOKEN", "WEBMCP_TOKEN", "WEBMCP_UNRECOGNIZED"):
        monkeypatch.setenv(key, "host-only-value")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.setenv("PATH", "/trusted/node22/bin:/usr/bin")
    before = dict(os.environ)
    child = research_environment(4174, "fresh-private-sidecar-token", tmp_path / "research.sqlite")
    assert child == {key: value for key, value in child.items() if key in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
        "PORT", "WEBMCP_TOKEN", "WEBMCP_DB"}}
    assert "host-only-value" not in child.values()
    assert child["WEBMCP_TOKEN"] == "fresh-private-sidecar-token"
    assert child["PORT"] == "4174" and child["WEBMCP_DB"] == str(tmp_path / "research.sqlite")
    assert child["LANG"] == "zh_CN.UTF-8" and child["PATH"] == before["PATH"]
    assert dict(os.environ) == before


@pytest.mark.parametrize("version", ["v22.22.1", "v24.9.0", "v25.0.0", "not-a-version"])
def test_research_node_rejects_incompatible_versions_without_changing_builder_path(tmp_path, monkeypatch, version):
    path = os.environ.get("PATH")
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: version)
    with pytest.raises(SystemExit, match="Node.js >=24.10"):
        select_research_node(tmp_path / "node24")
    assert os.environ.get("PATH") == path


def test_explicit_research_node_uses_clean_probe_and_keeps_builder_node22(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/trusted/node22/bin:/usr/bin")
    monkeypatch.setenv("OPENAI_API_KEY", "host-only-value")
    seen = []
    def version(command, **kwargs):
        seen.append((command, kwargs))
        return "v24.10.0\n"
    monkeypatch.setattr(subprocess, "check_output", version)
    explicit = tmp_path / "isolated-node24"
    assert select_research_node(explicit) == str(explicit.resolve())
    assert seen[0][0] == [str(explicit.resolve()), "--version"]
    assert "OPENAI_API_KEY" not in seen[0][1]["env"]
    assert seen[0][1]["env"]["PATH"] == "/trusted/node22/bin:/usr/bin"
    assert os.environ["PATH"] == "/trusted/node22/bin:/usr/bin"


def test_integrated_launcher_routes_explicit_node_only_to_sidecar(settings, tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(sys, "argv", ["run_integrated.py", "--port", "18291", "--research-port", "14174",
        "--research-node", str(tmp_path / "node24")])
    monkeypatch.setenv("OPENAI_API_KEY", "host-only-value")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/host/ssh-agent")
    monkeypatch.setenv("WEBMCP_DB", str(tmp_path / "research.sqlite"))
    monkeypatch.setattr(run_integrated.os, "chdir", lambda _: None)
    monkeypatch.setattr(run_integrated, "load_dotenv", lambda _: None)
    selected = []
    monkeypatch.setattr(run_integrated, "select_research_node", lambda path: selected.append(path) or str(path))
    captured = {}
    class Child:
        def poll(self): return None
        def terminate(self): captured["terminated"] = True
        def wait(self, **kwargs): return 0
    def spawn(command, **kwargs):
        captured.update(command=command, child=kwargs)
        return Child()
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs): return SimpleNamespace(status_code=200)
    monkeypatch.setattr(run_integrated.subprocess, "Popen", spawn)
    monkeypatch.setattr(run_integrated.httpx, "Client", Client)
    monkeypatch.setattr("game_harness.config.load_settings", lambda: settings)
    monkeypatch.setattr("game_harness.app.create_app", lambda settings: "host-app")
    monkeypatch.setattr(run_integrated.uvicorn, "run", lambda app, **kwargs: captured.update(app=app, host=kwargs))
    path_before = os.environ.get("PATH")
    run_integrated.main()
    assert selected == [tmp_path / "node24"]
    assert captured["command"] == [str(tmp_path / "node24"), "src/server.ts"]
    assert "OPENAI_API_KEY" not in captured["child"]["env"] and "SSH_AUTH_SOCK" not in captured["child"]["env"]
    assert settings.research_url == "http://127.0.0.1:14174"
    assert settings.research_token == captured["child"]["env"]["WEBMCP_TOKEN"]
    assert captured["host"] == {"host": "127.0.0.1", "port": 18291, "workers": 1}
    assert captured["terminated"] and os.environ.get("PATH") == path_before


@pytest.mark.skipif(os.getenv("RUN_RESEARCH_INTEGRATION") != "1", reason="Requires Node 24 and webmcp dependencies")
def test_real_node_runtime_through_fastapi_gateway(settings, tmp_path, request_data):
    node = select_research_node(os.getenv("RESEARCH_NODE"))
    assert node
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    headers = configure(settings)
    settings.research_url = f"http://127.0.0.1:{port}"
    env = {**research_environment(port, settings.research_token, tmp_path / "research.sqlite"),
           "WEBMCP_RESEARCH_MODE": "fixtures"}
    process = subprocess.Popen([node, "src/server.ts"], cwd=ROOT / "webmcp", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic() + 15
        with httpx.Client(trust_env=False, timeout=1) as http:
            while time.monotonic() < deadline:
                assert process.poll() is None, process.stderr.read().decode()
                try:
                    if http.get(settings.research_url + "/health").status_code == 200: break
                except httpx.HTTPError: pass
                time.sleep(.05)
            else: pytest.fail("Node runtime did not start")
        app = create_app(settings)
        with TestClient(app) as api:
            assert api.get('/api/research/providers', headers=headers).json() == {
                'archetypes': 'synthetic-reference-portal', 'materials': 'synthetic-reference-portal'}
            start = {"requestId": "gateway-live", "projectId": "gateway-project", "caseId": "GAME-TEST-001",
                     "images": [{"uri": "https://ref.example/input.png", "mimeType": "image/png", "description": "fixture"}]}
            r = api.post("/api/research/runs", json=start, headers=headers)
            assert r.status_code == 202, r.text
            rid = r.json()["runId"]
            assert api.post("/api/research/runs", json=start, headers=headers).json()["runId"] == rid
            for _ in range(100):
                state = api.get(f"/api/research/runs/{rid}", headers=headers).json()
                if state["status"] == "partial": break
                time.sleep(.05)
            assert state["status"] == "partial"
            assert state["jobs"][0]["request"]["images"] == start["images"]
            assert len(state["blueprints"][-1]["acceptance"]) == 54
            ref = {"runId": rid, "projectRevision": state["revision"], "blueprintVersion": state["blueprints"][-1]["version"]}
            assert not api.post(f"/api/research/runs/{rid}/handoff", json=ref, headers=headers).json()["eligible"]
            for round_number in range(1, 4):
                command = {"contractVersion": "0.2", "requestId": f"follow-{round_number}", "projectId": state["projectId"],
                    "expectedProjectRevision": state["revision"], "discussion": {"runId": rid, "threadId": state["mainThreadId"],
                    "round": round_number, "inReplyToRequestId": state["lastMainRequestId"],
                    "followUp": {"message": "Keep the current constraints", "clarificationId": None, "answers": []}}}
                assert api.post(f"/api/research/runs/{rid}/follow-ups", json=command, headers=headers).status_code == 202
                state = api.get(f"/api/research/runs/{rid}", headers=headers).json()
            command["requestId"] = "follow-four"
            command["discussion"]["round"] = 4
            assert api.post(f"/api/research/runs/{rid}/follow-ups", json=command, headers=headers).json()["error"]["code"] == "FOLLOW_UP_LIMIT_REACHED"
            assert state["mainUsed"] == 3 and state["materialUsed"] <= 3
            assert api.post(f"/api/research/runs/{rid}/cancel", json={}, headers=headers).json()["status"] == "cancelled"
            assert app.state.store.list() == []
            # Sparse input must not silently load a benchmark or consume a follow-up for first choices.
            idea = {"requestId": "sparse-live", "projectId": "sparse-project", "mode": "idea", "brief": "猫咪生存小游戏"}
            response = api.post("/api/research/runs", json=idea, headers=headers)
            assert response.status_code == 202, response.text
            idea_id = response.json()["runId"]
            for _ in range(100):
                draft = api.get(f"/api/research/runs/{idea_id}", headers=headers).json()
                if draft["stage"] == "decisions": break
                time.sleep(.05)
            assert draft["stage"] == "decisions" and draft["plan"] is None
            selection = {"requestId": "select-live", "expectedProjectRevision": draft["revision"],
                         "clarificationId": draft["decisionBatch"]["clarificationId"],
                         "answers": [{"questionId": "difficulty", "optionId": "easy", "text": None, "source": "developer_choice"}]}
            assert api.post(f"/api/research/runs/{idea_id}/decisions", json=selection).status_code == 401
            assert api.post(f"/api/research/runs/{idea_id}/decisions", json=selection, headers=headers).status_code == 202
            for _ in range(100):
                draft = api.get(f"/api/research/runs/{idea_id}", headers=headers).json()
                if draft["status"] == "partial": break
                time.sleep(.05)
            assert draft["status"] == "partial"
            assert draft["mainUsed"] == 0 and draft["materialUsed"] == 0
            blueprint = draft["blueprints"][-1]
            assert blueprint["caseId"] is None and blueprint["source"] is None
            assert all(c["provisional"] and c["id"].startswith("IDEA/") for c in blueprint["acceptance"])
            assert any("5 点生命" in c["requirement"] for c in blueprint["acceptance"])
            assert app.state.store.list() == []  # No Builder/model job was started.
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()
