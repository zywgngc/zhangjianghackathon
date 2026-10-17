"""Exercise real image handoff through the host pipeline without paid calls."""

import copy
import hashlib
import importlib
import json
from pathlib import Path
import struct
import sys
from types import ModuleType
import uuid
import zlib

import pytest


pytest.importorskip("openai")
pytest.importorskip("pydantic")
ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "game_harness"


@pytest.fixture
def host():
    # Isolate the host module instances from other integration tests without
    # starting the web server or altering the checked-in source.
    name = "asset_pipeline_test_" + uuid.uuid4().hex
    package = ModuleType(name)
    package.__path__ = [str(HOST)]
    sys.modules[name] = package
    loaded = {part: importlib.import_module(name + "." + part)
              for part in ("runner", "config", "store", "asset_material_portal", "validation")}
    yield loaded
    for module in list(sys.modules):
        if module == name or module.startswith(name + "."):
            del sys.modules[module]


def tiny_png(color):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 3, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + bytes(color) * 2) * 3))
            + chunk(b"IEND", b""))


def bundles(host, tmp_path):
    documents, asset_ids = [], []
    for index, color in enumerate(((32, 74, 192, 255), (112, 32, 24, 255))):
        directory = tmp_path / ("art-" + str(index))
        manifest = host["asset_material_portal"].collect_asset_bytes(
            tiny_png(color), directory,
            source_url="https://kenney.nl/assets/test-pack-" + str(index),
            license_info={"identifier": "CC0-1.0", "author": "Test Author"},
            original_filename="soldier-" + str(index) + ".png")
        asset_ids.append(manifest["assets"][0]["assetId"])
        documents.append({"id": "art-" + str(index), "source": "licensed test fixture",
                          "content": "独立士兵图片", "asset_manifest": str(directory / "manifest.json"),
                          "paths": [str(directory / "manifest.json")],
                          "verification": "downloaded_hash_checked_art",
                          "assets": [{"dataUri": "data:image/png;base64,never-send-preview"}]})
    return documents, asset_ids


def game_html(asset_ids=()):
    images = "".join('<img alt="士兵" src="asset://' + identifier + '">' for identifier in asset_ids)
    return ('<!doctype html><html><head><meta charset="utf-8"></head><body>'
            '<main data-testid="game-root">游戏' + images + '</main><span data-testid="score">0</span>'
            '<button type="button" data-action="start">开始</button>'
            '<button type="button" data-action="pause">暂停</button>'
            '<button type="button" data-action="resume">继续</button>'
            '<button type="button" data-action="restart">重开</button>'
            '<script>window.__GAME__={state:"ready",score:0,ticks:0};</script></body></html>')


class FakeAgents:
    def __init__(self, html, *, repair=False):
        self.html, self.repair = html, repair
        self.inputs, self.review_count = [], 0

    async def structured(self, rid, stage, role, instruction, data, schema, images=None):
        self.inputs.append((role, copy.deepcopy(data)))
        if role == "rewrite":
            result = {"title": "数字门", "rewritten_prompt": "数字门枪手，左右移动收集队员并战胜终点首领，支持手机触控。",
                      "core_loop": ["左右移动", "收集队员"], "user_requirements": ["数字门枪手"],
                      "assumptions": [], "acceptance_criteria": ["可开始", "可暂停", "可重开"]}
        elif role == "design":
            result = {"engine": "canvas", "engine_reason": "动作玩法", "mechanics": ["移动", "数字门", "首领"],
                      "controls": ["左右移动"], "win_condition": "击败首领", "lose_condition": "队员归零",
                      "visual_style": "统一像素素材", "responsive_strategy": "横竖屏适配",
                      "acceptance_criteria": ["可开始", "可暂停", "可重开"], "knowledge_ids": []}
        else:
            self.review_count += 1
            needs_repair = self.repair and self.review_count == 1
            result = {"verdict": "repair" if needs_repair else "pass", "summary": "仅模拟 QA",
                      "defects": [{"severity": "major", "evidence": "模拟一项缺陷", "fix": "修复"}] if needs_repair else [],
                      "limitations": ["此单测不证明游戏玩法运行"]}
        return schema.model_validate(result)

    async def complete(self, rid, stage, role, instruction, data, **kwargs):
        self.inputs.append((role, copy.deepcopy(data)))
        return "```html\n" + self.html + "\n```"


class FakeRetriever:
    def __init__(self, documents):
        self.documents = documents

    async def search(self, query, run_id=None):
        return {"documents": copy.deepcopy(self.documents), "warnings": [],
                "material_portal": {"result": {"debug_path": "C:/private/materials/manifest.json",
                    "preview": "data:image/png;base64,private-transport-preview"}}}


class StaticValidator:
    def __init__(self, host):
        self.host, self.htmls = host, []

    async def validate(self, rid, version, html):
        self.htmls.append(html)
        errors = self.host["validation"].inspect_html(html)
        return {"passed": not errors, "sha256": hashlib.sha256(html.encode()).hexdigest(),
                "static_errors": errors, "viewports": [], "browser_errors": []}


async def run_pipeline(host, tmp_path, documents, html, *, repair=False, max_repairs=1, retriever=None):
    settings = host["config"].Settings(data_dir=tmp_path / "state", api_key="unused-test-key",
                                        max_repairs=max_repairs, vision_qa=False)
    store = host["store"].Store(settings.data_dir)
    run = store.create({"description": "数字门枪手游戏，使用合适的已核验士兵素材。", "engine": "canvas"})
    store.claim()
    agents, validator = FakeAgents(html, repair=repair), StaticValidator(host)
    harness = host["runner"].Harness(settings, store, agents, retriever or FakeRetriever(documents), validator)
    await harness.execute(run["id"])
    return harness, store, run["id"], agents, validator


@pytest.mark.asyncio
async def test_two_real_bundles_reach_final_html_without_model_base64(host, tmp_path):
    documents, asset_ids = bundles(host, tmp_path)
    _, store, rid, agents, validator = await run_pipeline(host, tmp_path, documents, game_html(asset_ids))
    assert store.get(rid)["status"] == "passed"
    final_html = store.path(rid, "v1.html").read_text(encoding="utf-8")
    assert final_html.count("data:image/png;base64,") == 2
    assert "asset://" not in final_html
    assert "CC0-1.0" in final_html
    assert not host["validation"].inspect_html(final_html)
    assert validator.htmls == [final_html]
    source = store.path(rid, "v1.source.html").read_text(encoding="utf-8")
    assert "```" not in source and "asset://" in source and "base64" not in source
    usage = store.read_json(rid, "v1.asset-usage.json")
    assert usage["asset_ids"] == asset_ids and usage["count"] == 2
    assert usage["embedded_html_bytes"] == len(final_html.encode("utf-8"))
    for role, data in agents.inputs:
        if role in {"design", "build", "repair"}:
            serialized = json.dumps(data)
            assert "data:image/png;base64" not in serialized
            assert "asset_manifest" not in serialized
            assert str(tmp_path) not in serialized.replace("\\\\", "\\")
            assert "private/materials" not in serialized
            assert "private-transport-preview" not in serialized
            assert "material_portal" not in data.get("retrieval", {})
            assert all(identifier in serialized for identifier in asset_ids)
    qa = next(data for role, data in agents.inputs if role == "qa")
    assert qa["html"] == final_html
    # Persisted evidence, used for embedding, retains the original manifests.
    assert "asset_manifest" in store.read_json(rid, "retrieval.json")["documents"][0]
    assert documents[0]["assets"][0]["dataUri"].endswith("never-send-preview")


@pytest.mark.asyncio
async def test_repair_uses_unembedded_source_and_embeds_again(host, tmp_path):
    documents, asset_ids = bundles(host, tmp_path)
    _, store, rid, agents, validator = await run_pipeline(host, tmp_path, documents, game_html(asset_ids), repair=True)
    assert store.get(rid)["status"] == "passed"
    assert len(validator.htmls) == 2
    data = next(data for role, data in agents.inputs if role == "repair")
    assert "asset://" in data["previous_html"]
    assert "base64" not in data["previous_html"]
    assert store.read_json(rid, "v2.asset-usage.json")["count"] == 2
    assert store.path(rid, "v2.html").read_text(encoding="utf-8").count("material-assets-credits:start") == 1


@pytest.mark.asyncio
async def test_unknown_asset_fails_before_candidate_or_browser(host, tmp_path):
    documents, _ = bundles(host, tmp_path)
    _, store, rid, _, validator = await run_pipeline(host, tmp_path, documents, game_html(["png-unknown"]))
    assert store.get(rid)["status"] == "failed"
    assert "unknown" in store.get(rid)["error"].lower()
    assert not store.path(rid, "v1.html").exists()
    assert store.path(rid, "v1.source.html").exists()
    assert not validator.htmls


@pytest.mark.asyncio
async def test_embedded_size_budget_fails_before_browser(host, tmp_path):
    documents, asset_ids = bundles(host, tmp_path)
    html = game_html(asset_ids).replace("</body>", "<!--" + "a" * 300000 + "--></body>")
    _, store, rid, _, validator = await run_pipeline(host, tmp_path, documents, html)
    assert store.get(rid)["status"] == "failed"
    assert "byte limit" in store.get(rid)["error"]
    assert not validator.htmls and not store.path(rid, "v1.html").exists()


@pytest.mark.asyncio
async def test_games_without_art_still_build_and_reuse_checkpoints(host, tmp_path):
    harness, store, rid, agents, validator = await run_pipeline(host, tmp_path, [], game_html())
    assert store.get(rid)["status"] == "passed"
    assert store.read_json(rid, "v1.asset-usage.json")["count"] == 0
    assert store.path(rid, "v1.source.html").read_bytes() == store.path(rid, "v1.html").read_bytes()
    agents.inputs.clear()
    store.update(rid, status="running")
    await harness.execute(rid)
    assert not agents.inputs and len(validator.htmls) == 1


@pytest.mark.asyncio
async def test_art_enablement_preserves_external_resource_block(host, tmp_path):
    documents, asset_ids = bundles(host, tmp_path)
    html = game_html(asset_ids).replace("</main>", '<img src="https://example.invalid/external.png"></main>')
    _, store, rid, _, validator = await run_pipeline(host, tmp_path, documents, html, max_repairs=0)
    assert store.get(rid)["status"] == "needs_attention"
    assert store.get(rid)["approved_version"] is None
    assert any("外部" in error for error in store.read_json(rid, "v1.report.json")["static_errors"])
    assert len(validator.htmls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["web://assets", "local://references"])
async def test_project_retrieval_never_dispatches_html_asset_provider(tmp_path, url):
    from game_harness.config import Settings
    from game_harness.rag import Retriever

    retriever = Retriever(Settings(data_dir=tmp_path, portal_url=url, rag_url=""))
    calls = []

    async def art_search(query, limit, run_id=None):
        calls.append((query, run_id))
        return {"documents": [{"id": "asset-test", "title": "Test", "content": "Sprite",
                    "asset_manifest": "C:/private/materials/manifest.json"}],
                "warnings": [], "trace": {"private_path": "C:/private/materials/manifest.json"}}

    retriever.asset_portal.search = art_search
    ordinary = await retriever.search("键盘触屏游戏", run_id="project-test")
    assert calls == []
    assert "material_portal" not in ordinary
    assert "asset_manifest" not in json.dumps(ordinary)
    assert any("仅支持单文件 HTML" in warning for warning in ordinary["warnings"])
    html = await retriever.search_html("键盘触屏游戏", run_id="html-test")
    assert calls == [("键盘触屏游戏", "html-test")]
    assert html["documents"][0]["id"] == "asset-test"
    assert html["material_portal"]["private_path"].endswith("manifest.json")


@pytest.mark.asyncio
async def test_original_http_portal_remains_shared_by_project_and_html(tmp_path):
    from game_harness.config import Settings
    from game_harness.material_portal import MaterialPortalClient
    from game_harness.rag import Retriever

    retriever = Retriever(Settings(data_dir=tmp_path, portal_url="https://example.invalid/materials", rag_url=""))
    assert isinstance(retriever.portal, MaterialPortalClient)
    assert retriever.asset_portal is None
    calls = []

    async def http_search(query, limit, run_id=None):
        calls.append(run_id)
        return {"documents": [{"id": "http-original", "title": "HTTP reference", "content": "Data"}],
                "warnings": [], "trace": {"request": {"requestId": run_id}}}

    retriever.portal.search = http_search
    for method, rid in ((retriever.search, "project-test"), (retriever.search_html, "html-test")):
        result = await method("同一HTTP契约", run_id=rid)
        assert result["documents"][0]["id"] == "http-original"
        assert result["material_portal"]["request"]["requestId"] == rid
        assert result["warnings"] == []
    assert calls == ["project-test", "html-test"]


@pytest.mark.asyncio
async def test_html_runner_explicitly_selects_asset_aware_retrieval(host, tmp_path):
    documents, asset_ids = bundles(host, tmp_path)

    class HtmlRetriever(FakeRetriever):
        def __init__(self, documents):
            super().__init__(documents)
            self.html_calls = []

        async def search(self, query, run_id=None):
            pytest.fail("HTML builds must opt into the asset-aware retrieval path")

        async def search_html(self, query, run_id=None):
            self.html_calls.append(run_id)
            return await super().search(query, run_id=run_id)

    retriever = HtmlRetriever(documents)
    _, store, rid, _, _ = await run_pipeline(host, tmp_path, documents,
                                             game_html(asset_ids), retriever=retriever)
    assert store.get(rid)["status"] == "passed"
    assert retriever.html_calls == [rid]
    assert store.path(rid, "v1.html").read_text(encoding="utf-8").count("data:image/png;base64,") == 2
