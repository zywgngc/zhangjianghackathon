import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from game_harness.app import create_app
from game_harness.rag import Retriever
from game_harness.runner import Harness
from game_harness.validation import inspect_html


class Agents:
    def __init__(self, contracts):
        self.contracts = contracts
        self.roles = []

    async def structured(self, rid, stage, role, instruction, data, schema, images=None):
        self.roles.append(role)
        return schema.model_validate(self.contracts[{"rewrite": "brief", "design": "design", "qa": "review"}[role]])

    async def complete(self, rid, stage, role, instruction, data, **kwargs):
        self.roles.append(role)
        return "<html>version " + str(len(self.roles)) + "</html>"

    async def close(self):
        pass


class Validator:
    def __init__(self, passed):
        self.passed = passed
        self.calls = 0

    async def validate(self, rid, version, html):
        self.calls += 1
        return {"passed": self.passed, "sha256": hashlib.sha256(html.encode()).hexdigest(),
                "static_errors": [], "viewports": [], "browser_errors": []}


def test_claim_recovery_and_budget(store, request_data):
    rid = store.create(request_data)["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.claim(), range(4)))
    assert results.count(rid) == 1
    store.recover()
    assert store.get(rid)["status"] == "queued"
    assert store.claim() == rid
    store.reserve_call(rid, 1, 1000)
    store.charge(rid, 90)
    with pytest.raises(RuntimeError, match="预算"):
        store.reserve_call(rid, 1, 1000)
    assert store.get(rid)["tokens"] == 90
    store.update(rid, status="cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        store.reserve_call(rid, 10, 1000)


async def test_failed_browser_cannot_be_overruled_by_qa(settings, store, request_data, contracts):
    rid = store.create(request_data)["id"]
    store.claim()
    agents = Agents(contracts)
    validator = Validator(False)
    h = Harness(settings, store, agents, Retriever(settings), validator)
    await h.execute(rid)
    assert store.get(rid)["status"] == "needs_attention"
    assert store.get(rid)["approved_version"] is None
    assert validator.calls == 2  # One build plus exactly one allowed repair.
    assert agents.roles.count("repair") == 1
    assert not store.path(rid, "manifest.json").exists()


async def test_recovery_reuses_completed_stages(settings, store, request_data, contracts):
    rid = store.create(request_data)["id"]
    store.claim()
    agents, validator = Agents(contracts), Validator(True)
    h = Harness(settings, store, agents, Retriever(settings), validator)
    await h.execute(rid)
    assert store.get(rid)["status"] == "passed"
    manifest = store.read_json(rid, "manifest.json")
    assert manifest["sha256"] == hashlib.sha256(store.path(rid, "v1.html").read_bytes()).hexdigest()
    agents.roles.clear()
    store.update(rid, status="running")
    await h.execute(rid)
    assert agents.roles == []
    assert validator.calls == 1
    # A changed candidate must be re-tested and re-reviewed, never reuse stale QA.
    store.write(rid, "v1.html", "<html>changed candidate</html>")
    store.update(rid, status="running")
    await h.execute(rid)
    assert agents.roles == ["qa"]
    assert validator.calls == 2


async def test_critical_defect_prevents_publication(settings, store, request_data, contracts):
    contracts["review"]["defects"] = [{"severity": "critical", "evidence": "碰撞未实现", "fix": "实现碰撞"}]
    rid = store.create(request_data)["id"]
    store.claim()
    await Harness(settings, store, Agents(contracts), Retriever(settings), Validator(True)).execute(rid)
    assert store.get(rid)["status"] == "needs_attention"


async def test_cancel_interrupts_active_generation(settings, store, request_data, contracts):
    started = asyncio.Event()
    agents = Agents(contracts)
    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    agents.structured = blocked
    h = Harness(settings, store, agents, Retriever(settings), Validator(True))
    rid = store.create(request_data)["id"]
    await h.start()
    await asyncio.wait_for(started.wait(), 2)
    h.cancel(rid)
    await asyncio.sleep(.03)
    assert store.get(rid)["status"] == "cancelled"
    await h.stop()


async def test_browser_timeout_is_bounded_and_does_not_publish(settings, store, request_data, contracts):
    class HungValidator:
        async def validate(self, *args):
            await asyncio.Event().wait()
    settings.browser_timeout = .01
    rid = store.create(request_data)["id"]
    store.claim()
    await Harness(settings, store, Agents(contracts), Retriever(settings), HungValidator()).execute(rid)
    assert store.get(rid)["status"] == "needs_attention"
    assert "超时" in store.read_json(rid, "v1.report.json")["browser_errors"][0]


def test_preview_hash_auth_and_artifact_download(settings, request_data):
    settings.api_token = "test-secret"
    app = create_app(settings)
    store = app.state.store
    rid = store.create(request_data)["id"]
    store.update(rid, status="cancelled")  # Keep this fixture out of the live worker queue.
    with TestClient(app, raise_server_exceptions=True) as client:
        assert client.get('/api/runs').status_code == 401
        headers = {"Authorization": "Bearer test-secret"}
        preview = f"/api/runs/{rid}/preview/1"
        assert client.get(preview, headers=headers).status_code == 409
        digest = store.write(rid, "v1.html", "<html>verified</html>")
        store.write(rid, "manifest.json", {"sha256": digest})
        store.update(rid, status="passed", approved_version=1)
        response = client.get(preview, headers=headers)
        assert response.status_code == 200
        assert "sandbox allow-scripts" in response.headers["content-security-policy"]
        attachment = client.get(f"/api/runs/{rid}/artifacts/v1.html", headers=headers)
        assert "attachment" in attachment.headers["content-disposition"]
        store.write(rid, "v1.html", "<html>changed</html>")
        assert client.get(preview, headers=headers).status_code == 409
        assert client.get('/api/runs/not-a-run', headers=headers).status_code == 404
    with pytest.raises(ValueError):
        store.path(rid, "../.env")


def test_retrieval_is_chinese_and_traceable(settings):
    retriever = Retriever(settings)
    docs = retriever.local_search("接星星碰撞生命飞船键盘触屏")
    assert docs and "碰撞" in docs[0]["content"]
    assert all(d["id"] and d["source"] for d in docs)


def test_static_check_blocks_external_execution():
    errors = inspect_html('<html><meta><script src="https://evil.invalid/a.js"></script><iframe src="file:///etc/passwd"></iframe></html>')
    assert any("外部" in e for e in errors)
    assert any("iframe" in e for e in errors)
