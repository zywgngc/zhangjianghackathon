import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from game_harness.agents import AgentClient
from game_harness.coding_agent import CodingAction, CodingAgent, DiagnosticCodingAction, FocusedCodingAction


class Workspace:
    """A controlled execution boundary; integration builds test the real runtime."""
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.builds, self.writes = 0, 0
        self.fail_builds = 0
        self.crash_after_write = False

    def resolve(self, relative, writable=False):
        path = self.root / relative
        if not relative or path.is_absolute() and not path.is_relative_to(self.root):
            raise ValueError("unsafe path")
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("path traversal")
        if relative != "index.html" and not relative.startswith(("src/", "public/")):
            raise ValueError("protected project configuration")
        return path

    def read_file(self, relative):
        return self.resolve(relative).read_text()

    def write_file(self, relative, content):
        path = self.resolve(relative, writable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.writes += 1
        if self.crash_after_write:
            self.crash_after_write = False
            raise asyncio.CancelledError("crash after mutation before checkpoint")

    def apply_patch(self, relative, old, new, expected_sha256=None):
        text = self.read_file(relative)
        assert hashlib.sha256(text.encode()).hexdigest() == expected_sha256
        assert text.count(old) == 1
        self.write_file(relative, text.replace(old, new, 1))

    def delete_file(self, relative, expected_sha256=None):
        path = self.resolve(relative, writable=True)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
        path.unlink()

    def list_files(self):
        return [item["path"] for item in self.snapshot()["files"]]

    def snapshot(self):
        files = [{"path": str(p.relative_to(self.root)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                  "bytes": p.stat().st_size}
                 for p in sorted(self.root.rglob("*")) if p.is_file()
                 and not any(part.startswith(".") or part in {"dist", "node_modules"}
                             for part in p.relative_to(self.root).parts)]
        return {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}

    async def build(self, install=True, timeout=180):
        self.builds += 1
        if self.builds <= self.fail_builds:
            return {"passed": False, "errors": ["TS2322: number is not assignable to string"]}
        return {"passed": True, "stages": ["typecheck", "vite build"], "errors": [],
                "entrypoint": "index.html", "artifact_manifest": [{"path": "index.html", "sha256": "a" * 64}]}


class ScriptedClient:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.inputs = []

    async def structured(self, rid, stage, role, instruction, data, schema, **kwargs):
        self.inputs.append({"stage": stage, "role": role, "data": data, **kwargs})
        return schema.model_validate(next(self.actions))


def run_id(store, request_data):
    rid = store.create(request_data)["id"]
    store.claim()
    return rid


async def test_gateway_actions_use_real_structured_client_and_do_not_claim_acceptance(settings, store, request_data, tmp_path):
    rid = run_id(store, request_data)
    proposals = iter([
        {"tool": "write_file", "path": "src/types.ts", "content": "export type Phase = 'ready' | 'running';"},
        {"tool": "build"},
        {"tool": "finish", "summary": "新增共享状态类型，主机已构建。"},
    ])
    captured = []

    def response(request):
        payload = json.loads(request.content)
        captured.append(payload)
        return httpx.Response(200, json={"id": "coding", "object": "chat.completion", "created": 0,
            "model": "qwen3.8-max", "choices": [{"index": 0, "message": {"role": "assistant",
            "content": json.dumps(next(proposals), ensure_ascii=False)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100}})

    client = AgentClient(settings, store)
    await client.client.close()
    client.client = AsyncOpenAI(api_key="test", base_url="https://mock.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
    workspace = Workspace(tmp_path / "game")
    try:
        result = await CodingAgent(client, store, workspace).run_feature(rid,
            {"id": "shared-types", "owned_paths": ["src/types.ts"], "objective": "定义游戏状态"}, {},
            role="design", owner="architecture-agent")
    finally:
        await client.close()
    assert result["status"] == "implemented"
    assert result["stop_reason"] == "implemented"
    assert result["acceptance"]["status"] == "not_run"
    assert result["changed_files"] == ["src/types.ts"]
    assert result["owner"] == "architecture-agent"
    assert workspace.builds == 1
    assert len(captured) == 3
    assert all(p["model"] == "qwen3.8-max" and p["max_tokens"] == 16000 for p in captured)
    assert store.get(rid)["calls"] == 3


async def test_exact_patch_and_build_error_are_fed_back_without_losing_source(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/combat.ts", "const hp = 2;\nconst damage = 2;\n")
    workspace.fail_builds = 1
    client = ScriptedClient([
        {"tool": "apply_patch", "path": "src/combat.ts", "old": "2", "new": "3"},
        {"tool": "apply_patch", "path": "src/combat.ts", "old": "const hp = 2;", "new": "const hp = 3;"},
        {"tool": "finish", "summary": "完成生命调整。"},
        {"tool": "apply_patch", "path": "src/combat.ts", "old": "const damage = 2;", "new": "const damage = 1;"},
        {"tool": "finish", "summary": "已修复构建并实现战斗规则。"},
    ])
    result = await CodingAgent(client, store, workspace).run_feature(rid,
        {"id": "combat", "owned_paths": ["src/combat.ts"]}, {"review_feedback": "保留既有战斗规则"})
    assert result["status"] == "implemented"
    assert workspace.read_file("src/combat.ts") == "const hp = 3;\nconst damage = 1;\n"
    first_feedback = client.inputs[1]["data"]["recent_observations"][-1]["result"]
    assert first_feedback["ok"] is False
    assert "exactly once" in first_feedback["error"]
    build_feedback = client.inputs[3]["data"]["recent_observations"][-1]["result"]
    assert build_feedback["accepted"] is False
    assert "TS2322" in json.dumps(build_feedback)


async def test_pending_file_mutation_replays_idempotently_after_crash(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.crash_after_write = True
    feature = {"id": "movement", "owned_paths": ["src/movement.ts"]}
    agent = CodingAgent(ScriptedClient([
        {"tool": "write_file", "path": "src/movement.ts", "content": "export const speed = 120;"}
    ]), store, workspace)
    with pytest.raises(asyncio.CancelledError):
        await agent.run_feature(rid, feature, {})
    checkpoint = store.read_json(rid, agent._prefix(feature["id"]) + ".checkpoint.json")
    assert checkpoint["pending"]["prepared"]["after_sha256"]
    client = ScriptedClient([{"tool": "finish", "summary": "恢复已落盘的移动模块并构建。"}])
    result = await CodingAgent(client, store, workspace).run_feature(rid, feature, {})
    assert result["status"] == "implemented"
    assert result["steps"] == 2
    assert workspace.writes == 1
    assert result["changed_files"] == ["src/movement.ts"]
    observation = store.read_json(rid, agent._prefix(feature["id"]) + ".step-0001.result.json")
    assert observation["recovered_already_applied"] is True


async def test_recovery_refuses_to_overwrite_a_concurrent_edit(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.crash_after_write = True
    feature = {"id": "movement"}
    agent = CodingAgent(ScriptedClient([
        {"tool": "write_file", "path": "src/movement.ts", "content": "export const speed = 120;"}
    ]), store, workspace)
    with pytest.raises(asyncio.CancelledError):
        await agent.run_feature(rid, feature, {})
    workspace.write_file("src/movement.ts", "export const speed = 240;")
    client = ScriptedClient([{"tool": "blocked", "summary": "其他作者修改了待恢复文件，需要集成。"}])
    result = await CodingAgent(client, store, workspace).run_feature(rid, feature, {})
    assert result["status"] == "blocked"
    assert workspace.read_file("src/movement.ts") == "export const speed = 240;"
    assert "workspace drift" in client.inputs[0]["data"]["recent_observations"][-1]["result"]["error"]


async def test_context_is_bounded_and_long_spec_remains_fully_retrievable(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    text = "机制定义及正式验收。" * 20_000 + "末尾硬性要求：不得降级 Babylon。"
    context = {"project_summary": "指定 Babylon 引擎", "spec_excerpts": text, "interfaces": {"tick": "fixed dt"}}
    client = ScriptedClient([
        {"tool": "read_context", "section": "spec_excerpts", "offset": len(text) - 22, "limit": 256},
        {"tool": "blocked", "summary": "已读取完整存档末段，等待上游接口。"},
    ])
    agent = CodingAgent(client, store, Workspace(tmp_path / "game"), context_chars=28_000)
    result = await agent.run_feature(rid, {"id": "long-spec"}, context)
    assert result["status"] == "blocked"
    assert all(len(json.dumps(call["data"], ensure_ascii=False, separators=(",", ":"))) < 28_000
               for call in client.inputs)
    assert "不得降级 Babylon" not in json.dumps(client.inputs[0]["data"], ensure_ascii=False)
    assert "不得降级 Babylon" in json.dumps(client.inputs[1]["data"], ensure_ascii=False)
    assert store.read_json(rid, agent._prefix("long-spec") + ".context.json")["spec_excerpts"] == text


async def test_file_ownership_protected_configs_and_read_before_replace(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/owner.ts", "existing implementation")
    client = ScriptedClient([
        {"tool": "write_file", "path": "src/other.ts", "content": "overwrite another owner"},
        {"tool": "write_file", "path": "package.json", "content": "disable typecheck"},
        {"tool": "write_file", "path": "src/owner.ts", "content": "discard existing code"},
        {"tool": "blocked", "summary": "需先读取当前实现。"},
    ])
    result = await CodingAgent(client, store, workspace).run_feature(rid,
        {"id": "owned", "owned_paths": ["src/owner.ts"]}, {})
    assert result["status"] == "blocked"
    assert workspace.read_file("src/owner.ts") == "existing implementation"
    assert not (workspace.root / "src/other.ts").exists()
    assert not (workspace.root / "package.json").exists()
    errors = [call["data"]["recent_observations"][-1]["result"]["error"] for call in client.inputs[1:]]
    assert "owned by another" in errors[0]
    assert "protected" in errors[1]
    assert "read the entire" in errors[2]


async def test_failed_host_acceptance_cannot_be_overridden_by_model_finish(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    client = ScriptedClient([
        {"tool": "finish", "summary": "我认为所有技能已经通过。"},
        {"tool": "blocked", "summary": "独立验收发现技能缺失。"},
    ])

    async def verifier(feature, workspace, build):
        return {"passed": False, "checks": [{"id": "T-31", "passed": False, "reason": "missing active skills"}]}

    result = await CodingAgent(client, store, Workspace(tmp_path / "game"), verifier=verifier).run_feature(
        rid, {"id": "skills"}, {})
    assert result["status"] == "blocked"
    observation = client.inputs[1]["data"]["recent_observations"][-1]["result"]
    assert observation["accepted"] is False
    assert observation["acceptance"]["checks"][0]["id"] == "T-31"


async def test_session_limit_can_resume_with_explicit_larger_budget_and_cached_result(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    feature = {"id": "progress"}
    first = ScriptedClient([{"tool": "note", "summary": "明确接口，仍有实现工作。", "memory": "下一步实现状态类型。"}])
    result = await CodingAgent(first, store, workspace, max_steps=1).run_feature(rid, feature, {})
    assert result["status"] == "needs_work"
    assert result["stop_reason"] == "action_limit"
    second = ScriptedClient([
        {"tool": "write_file", "path": "src/state.ts", "content": "export const phase = 'ready';"},
        {"tool": "finish", "summary": "状态已实现并构建。"},
    ])
    agent = CodingAgent(second, store, workspace, max_steps=3)
    result = await agent.run_feature(rid, feature, {})
    assert result["status"] == "implemented" and result["steps"] == 3
    assert second.inputs[0]["data"]["memory"] == "下一步实现状态类型。"
    assert await agent.run_feature(rid, feature, {}) == result
    assert len(second.inputs) == 2


async def test_workspace_has_one_live_coding_owner(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    entered, release = asyncio.Event(), asyncio.Event()

    class WaitingClient:
        async def structured(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return CodingAction(tool="blocked", summary="完成锁隔离检查。")

    task = asyncio.create_task(CodingAgent(WaitingClient(), store, workspace).run_feature(rid, {"id": "one"}, {}))
    await entered.wait()
    try:
        other = ScriptedClient([])
        with pytest.raises(RuntimeError, match="coding owner"):
            await CodingAgent(other, store, workspace).run_feature(rid, {"id": "two"}, {})
        assert not other.inputs
    finally:
        release.set()
        await task


async def test_incomplete_provider_response_never_mutates_workspace(settings, store, request_data, tmp_path):
    rid = run_id(store, request_data)

    def respond(request):
        return httpx.Response(200, json={"id": "truncated", "object": "chat.completion", "created": 0,
            "model": settings.model, "choices": [{"index": 0, "message": {"role": "assistant",
            "content": '{"tool":"write_file","path":"src/engine.ts","content":"partial'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}})

    client = AgentClient(settings, store)
    await client.client.close()
    client.client = AsyncOpenAI(api_key="test", base_url="https://mock.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    workspace = Workspace(tmp_path / "game")
    try:
        with pytest.raises(RuntimeError, match="被截断"):
            await CodingAgent(client, store, workspace).run_feature(rid, {"id": "truncation"}, {})
    finally:
        await client.close()
    assert workspace.writes == 0
    assert workspace.builds == 0


async def test_delete_is_scoped_and_requires_a_current_file_digest(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/obsolete.ts", "export const obsolete = true;")
    workspace.write_file("src/current.ts", "export const current = true;")
    digest = hashlib.sha256(workspace.read_file("src/obsolete.ts").encode()).hexdigest()
    client = ScriptedClient([
        {"tool": "delete_file", "path": "src/obsolete.ts"},
        {"tool": "delete_file", "path": "src/obsolete.ts", "expected_sha256": digest},
        {"tool": "finish", "summary": "删除错误的旧模块并重新构建。"},
    ])
    result = await CodingAgent(client, store, workspace).run_feature(rid,
        {"id": "cleanup", "owned_paths": ["src/**"]}, {})
    assert result["status"] == "implemented"
    assert not (workspace.root / "src/obsolete.ts").exists()
    assert "requires a current full read" in client.inputs[1]["data"]["recent_observations"][-1]["result"]["error"]


async def test_project_aliases_expose_review_and_complete_source_archive(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    source = "不可丢失的长规格。" * 10_000 + "最终门槛：八种被动技能。"
    store.write(rid, "specification.txt", source)
    client = ScriptedClient([
        {"tool": "read_context", "section": "__specification__", "offset": len(source) - 15, "limit": 256},
        {"tool": "blocked", "summary": "保留完整原文，并根据审查修复。"},
    ])
    agent = CodingAgent(client, store, Workspace(tmp_path / "game"), context_chars=60_000)
    await agent.run_feature(rid, {"id": "repair"}, {"source_archive": "specification.txt",
        "production_contract": "真实生产 Simulation 和测试实例使用同一代码。",
        "validation_report": {"passed": False, "defects": ["W12未实现"]},
        "acceptance": [{"id": "T88", "text": "全部原始技能可用。"}]})
    packet = client.inputs[0]["data"]
    assert packet["context"]["validation_report"]["defects"] == ["W12未实现"]
    assert "production_contract" in packet["context"] and "acceptance" in packet["context"]
    assert packet["context_sections"]["__specification__"]["immutable_source"] is True
    assert "八种被动技能" in client.inputs[1]["data"]["recent_observations"][-1]["result"]["content"]


async def test_reference_images_are_seen_once_and_retained_by_digest_on_resume(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    image = Path(__file__).parent / "fixtures" / "vision-probe.png"
    client = ScriptedClient([
        {"tool": "write_file", "path": "src/scene.ts", "content": "export const theme = 'military';"},
        {"tool": "finish", "summary": "场景模块按参考图实现并构建。"},
    ])
    workspace = Workspace(tmp_path / "game")
    feature = {"id": "visual"}
    agent = CodingAgent(client, store, workspace, images=[image])
    result = await agent.run_feature(rid, feature, {})
    assert result["status"] == "implemented"
    assert client.inputs[0]["images"] == [image]
    assert "images" not in client.inputs[1]
    expected = hashlib.sha256(image.read_bytes()).hexdigest()
    assert client.inputs[1]["data"]["reference_images"] == [{"name": image.name, "sha256": expected}]
    assert await agent.run_feature(rid, feature, {}) == result
    assert len(client.inputs) == 2


async def test_complete_source_read_preserves_crlf_offsets_and_digest(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    source = "第一行\r\n第二行：必须保留精确数值47。\r\n"
    store.write(rid, "specification.txt", source)
    client = ScriptedClient([
        {"tool": "read_context", "section": "__specification__"},
        {"tool": "blocked", "summary": "原文边界已核实。"},
    ])
    await CodingAgent(client, store, Workspace(tmp_path / "game")).run_feature(
        rid, {"id": "crlf-spec"}, {"source_archive": "specification.txt"})
    observed = client.inputs[1]["data"]["recent_observations"][-1]["result"]
    assert observed["content"] == source
    assert observed["sha256"] == hashlib.sha256(source.encode()).hexdigest()


async def test_eight_thousand_character_file_reads_in_one_complete_page_and_authorizes_replacement(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    source = "// 共享接口与原始数值必须保留\n" * 400
    source = (source + " " * 8_000)[:8_000]
    workspace.write_file("src/types.ts", source)
    specification = "原文技能参数\n" * 2_000
    store.write(rid, "specification.txt", specification)
    prefix = CodingAgent._prefix("larger-file-page")

    class InspectingClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            assert len(json.dumps(packet, ensure_ascii=False)) <= 32_000
            if len(self.inputs) == 1:
                observed = packet["recent_observations"][-1]["result"]
                assert observed["source"] == "file:src/types.ts"
                assert observed["content"] == source
                assert observed["offset"] == 0 and observed["next_offset"] == 8_000
                assert observed["characters"] == 8_000 and observed["truncated"] is False
                assert not observed.get("content_view_truncated")
                state = store.read_json(rid, prefix + ".checkpoint.json")
                assert state["full_reads"]["src/types.ts"] == hashlib.sha256(source.encode()).hexdigest()
            if len(self.inputs) == 3:
                observed = packet["recent_observations"][-1]["result"]
                assert observed["source"] == "context:__specification__"
                assert observed["next_offset"] == 6_000
                assert observed["characters"] == len(specification) and observed["truncated"] is True
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)

    client = InspectingClient([
        {"tool": "read_file", "path": "src/types.ts", "limit": 16_000},
        {"tool": "write_file", "path": "src/types.ts", "content": "export type Phase = 'ready';"},
        {"tool": "read_context", "section": "__specification__", "limit": 16_000},
        {"tool": "finish", "summary": "完整读过已有共享类型后替换，原始规格仍按6000字符分页。"},
    ])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(
        rid, {"id": "larger-file-page", "owned_paths": ["src/types.ts"]},
        {"source_archive": "specification.txt", "interfaces": specification * 5, "production_contract": specification})
    assert result["status"] == "implemented"
    assert workspace.read_file("src/types.ts") == "export type Phase = 'ready';"
    assert result["changed_files"] == ["src/types.ts"]


@pytest.mark.parametrize("limit", [8_000, 60_000])
async def test_serialized_context_budget_handles_nested_code_escapes_quotes_and_cjk(store, request_data, tmp_path, limit):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    code = ('const line = "中文\\\\路径\\n\\\"引号\\\"";\n' * 3_000)
    workspace.write_file("src/escaped.ts", code)
    client = ScriptedClient([
        {"tool": "read_file", "path": "src/escaped.ts", "limit": 16_000},
        {"tool": "read_context", "section": "spec_excerpts", "limit": 16_000},
        {"tool": "blocked", "summary": "已验证序列化上下文压缩，完整原始代码和规格仍可分页。"},
    ])
    context = {"spec_excerpts": [{"text": code, "source": {"path": "中文\\\\路径\\\".txt"}}],
               "interfaces": code, "production_contract": code, "review_feedback": code}
    result = await CodingAgent(client, store, workspace, context_chars=limit).run_feature(
        rid, {"id": "quoted-context", "objective": code}, context)
    assert result["status"] == "blocked"
    for call in client.inputs:
        assert len(json.dumps(call["data"], ensure_ascii=False)) <= limit
    stored = store.read_json(rid, CodingAgent._prefix("quoted-context") + ".context.json")
    assert stored["spec_excerpts"][0]["text"] == code


async def test_real_spec_pagination_preserves_source_metadata_and_escapes_read_loop(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    source = ('章节："技能配置"\\n\\\\path\n' * 5_000)
    store.write(rid, "specification.txt", source)
    workspace = Workspace(tmp_path / "game")

    class PagingClient:
        def __init__(self):
            self.inputs = []

        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            self.inputs.append(packet)
            assert len(json.dumps(packet, ensure_ascii=False)) <= 32_000
            if len(self.inputs) == 1:
                assert "approved_configuration" in packet["context"]
                return schema(tool="read_context", section="__specification__", offset=17_400, limit=16_000)
            observation = packet["recent_observations"][-1]["result"]
            if len(self.inputs) == 2:
                assert observation["characters"] == len(source)
                assert observation["offset"] == 17_400
                assert 17_400 < observation["next_offset"] <= 23_400
                assert len(observation["content"]) == observation["next_offset"] - 17_400
                assert observation.get("content_view_truncated") is not True
                receipt = packet["read_receipts"][-1]
                assert receipt["source"] == "context:__specification__"
                assert receipt["characters"] == len(source) and receipt["next_offset"] == observation["next_offset"]
                return schema(tool="read_context", section="__specification__", offset=observation["next_offset"], limit=16_000)
            if len(self.inputs) == 3:
                assert observation["offset"] > 17_400
                return schema(tool="write_file", path="src/types.ts", content="export type State = 'READY';")
            return schema(tool="finish", summary="根据相关章节开始实现，而非反复读取同一页。")

    client = PagingClient()
    result = await CodingAgent(client, store, workspace, context_chars=32_000, history_limit=3).run_feature(
        rid, {"id": "case2-foundation"}, {"source_archive": "specification.txt",
            "approved_configuration": {"activeSkillCount": 12, "passiveSkillCount": 8},
            "interfaces": source, "production_contract": source, "spec_excerpts": source})
    assert result["status"] == "implemented" and len(client.inputs) == 4
    assert result["changed_files"] == ["src/types.ts"]


async def test_five_identical_nonprogress_reads_stop_before_spending_the_session(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    store.write(rid, "specification.txt", "固定章节和真实规则。" * 10_000)
    client = ScriptedClient([{"tool": "read_context", "section": "__specification__", "offset": 17_400,
                              "limit": 16_000} for _ in range(36)])
    result = await CodingAgent(client, store, Workspace(tmp_path / "game"), context_chars=32_000).run_feature(
        rid, {"id": "stuck-reader"}, {"source_archive": "specification.txt"})
    assert result["status"] == "needs_work" and result["steps"] == 5
    assert result["stop_reason"] == "non_progress"
    assert result["read_receipts"][0]["source"] == "context:__specification__"
    assert len(client.inputs) == 5 and not result["changed_files"]
    assert "read_guidance" in client.inputs[2]["data"]["recent_observations"][-1]["result"]
    checkpoint = store.read_json(rid, result["checkpoint"])
    assert checkpoint["history"][-1]["result"]["non_progress_stop"] is True
    assert list(checkpoint["read_receipts"].values())[0]["characters"] == 100_000


@pytest.mark.parametrize("owned_path", ["src/game/InputManager.ts", "src/game/skills/ActiveSkillSystem.ts", "src/game/enemies/AI.ts"])
async def test_shared_types_and_interfaces_remain_complete_when_configuration_and_history_compete(store, request_data, tmp_path, owned_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    types = "export type PRNG = () => number;\nexport type EventSink = (event: string) => void;\n"
    types = (types + "// 真实共享接口字段\n" * 1_000)[:7_978]
    workspace.write_file("src/game/types.ts", types)
    interfaces = "export function updatePlayerNavigation(state: GameState, dt: number, rng: PRNG, emit: EventSink): void;\n" * 15
    configuration = {"unrelatedSkillRows": "全部技能数值" * 1_000}
    page = "必须按当前功能实现移动。" * 600

    class InspectingClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            assert len(json.dumps(packet, ensure_ascii=False)) <= 32_000
            assert packet["context"]["interfaces"] == interfaces
            resident = packet["resident_files"][0]
            assert resident["path"] == "src/game/types.ts" and resident["content"] == types
            assert resident["complete"] and resident["characters"] == 7_978
            assert resident["sha256"] == hashlib.sha256(types.encode()).hexdigest()
            assert resident["exports"] == ["PRNG", "EventSink"]
            if len(self.inputs) == 1:
                observed = packet["recent_observations"][-1]["result"]
                assert observed["content"] == page[:6_000]
                assert observed["next_offset"] == 6_000 and not observed.get("content_view_truncated")
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)

    client = InspectingClient([
        {"tool": "read_context", "section": "spec_excerpts", "limit": 16_000},
        {"tool": "note", "summary": "共享字段已经确认。", "memory": "下一文件InputManager.ts，PRNG/EventSink使用resident_files真实定义。"},
        {"tool": "write_file", "path": owned_path, "content": "export const input = { dx: 0, dy: 0, active: false };"},
        {"tool": "finish", "summary": "不再反复读取共享类型。"},
    ])
    result = await CodingAgent(client, store, workspace, context_chars=32_000, history_limit=3).run_feature(rid,
        {"id": "player-navigation", "owned_paths": [owned_path], "objective": "实现移动输入"},
        {"interfaces": interfaces, "approved_configuration": configuration, "spec_excerpts": page,
         "review_feedback": page * 5, "production_contract": page * 5, "architecture": page * 5})
    assert result["status"] == "implemented" and result["steps"] == 4


async def test_large_resident_contracts_preserve_the_latest_escape_heavy_page_within_serialized_budget(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    types = "export type Phase = 'ready';\n" + "中" * 7_950
    workspace.write_file("src/game/types.ts", types)
    interfaces = "接口签名" + "值" * 7_975
    source = '"\\\n中文' * 5_000

    class InspectingClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            assert len(json.dumps(packet, ensure_ascii=False)) <= 32_000
            assert packet["context"]["interfaces"] == interfaces
            assert packet["resident_files"][0]["content"] == types
            if self.inputs:
                result = store.read_json(rid, CodingAgent._prefix("dense-resident-packet") + ".step-0001.result.json")
                latest = packet["recent_observations"][-1]["result"]
                assert latest["content"] == result["content"]
                assert latest["next_offset"] == len(result["content"])
                assert not latest.get("content_view_truncated")
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)

    client = InspectingClient([
        {"tool": "read_context", "section": "spec_excerpts", "limit": 16_000},
        {"tool": "blocked", "summary": "序列化极限回归，不宣称实现。"},
    ])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "dense-resident-packet", "owned_paths": ["src/game/InputManager.ts"], "objective": "保留需求" * 10_000},
        {"interfaces": interfaces, "spec_excerpts": source, "project_summary": {"notes": "元数据" * 5_000},
         "approved_configuration": source, "production_contract": source, "review_feedback": source})
    assert result["status"] == "blocked"


@pytest.mark.parametrize("action,expected", [
    ({"tool": "write_file", "path": "src/game/InputManager.ts", "content": "export const input = { dx: 0 };"}, "implemented"),
    ({"tool": "blocked", "summary": "共享GameState缺少input字段，需上游增加input:InputState。"}, "blocked"),
    ({"tool": "build"}, "implemented"),
])
async def test_nonprogress_handoff_allows_one_concrete_recovery_action_not_more_reads(store, request_data, tmp_path, action, expected):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/game/types.ts", "export interface InputState { dx: number; dy: number; active: boolean; }")
    if action["tool"] == "build":
        workspace.write_file("src/game/InputManager.ts", "export const input = { dx: 0, dy: 0, active: false };")

    class FocusClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            if not self.inputs:
                assert schema is FocusedCodingAction
                assert schema.model_json_schema()["properties"]["tool"]["enum"] == ["write_file", "apply_patch", "build", "blocked"]
                assert packet["focused_recovery"]["target_file"] == "src/game/InputManager.ts"
                assert packet["resident_files"][0]["complete"]
                assert packet["context"]["interfaces"] == "InputState is the real shared input contract."
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)

    client = FocusClient([action, {"tool": "finish", "summary": "已提交真实源码，主机构建确认。"}])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "focused-navigation", "owned_paths": ["src/game/InputManager.ts"]},
        {"interfaces": "InputState is the real shared input contract.",
         "continuation": {"stop_reason": "non_progress", "memory": "types已读完整，再读interfaces确认一次。"}})
    assert result["status"] == expected
    assert len(client.inputs) == (2 if expected == "implemented" else 1)
    if expected == "needs_work":
        assert result["stop_reason"] == "non_progress" and not result["changed_files"]


async def test_repeated_complete_interface_reads_trigger_typed_recovery_before_five_read_stop(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/game/types.ts", "export type EventSink = (event: string) => void;")

    class InspectingClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            if len(self.inputs) == 2:
                assert schema is FocusedCodingAction
                assert packet["focused_recovery"]["source"] == "context:interfaces"
                assert packet["resident_files"][0]["exports"] == ["EventSink"]
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)

    client = InspectingClient([
        {"tool": "read_context", "section": "interfaces"},
        {"tool": "read_context", "section": "interfaces"},
        {"tool": "write_file", "path": "src/game/InputManager.ts", "content": "export const active = false;"},
        {"tool": "finish", "summary": "主机约束第二次重复后提交改动。"},
    ])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "repeat-interfaces", "owned_paths": ["src/game/InputManager.ts"]}, {"interfaces": "No PRNG declaration here; see actual shared types."})
    assert result["status"] == "implemented" and result["steps"] == 4
    assert [item["data"].get("focused_recovery") is not None for item in client.inputs] == [False, False, True, False]


async def test_host_rejects_read_even_if_custom_client_ignores_focused_schema(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/game/types.ts", "export interface InputState { dx: number; }")

    class IgnoringClient:
        calls = 0
        async def structured(self, *args, **kwargs):
            self.calls += 1
            assert args[5] is FocusedCodingAction
            return CodingAction(tool="read_context", section="interfaces")

    client = IgnoringClient()
    feature = {"id": "untrusted-focused-action", "owned_paths": ["src/game/InputManager.ts"]}
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid, feature,
        {"interfaces": "Already available.", "continuation": {"stop_reason": "non_progress"}})
    assert result["status"] == "needs_work" and result["stop_reason"] == "non_progress" and client.calls == 1
    raw = store.read_json(rid, CodingAgent._prefix(feature["id"]) + ".step-0001.result.json")
    assert raw["ok"] is False and "not another read" in raw["error"]


async def test_focused_write_crash_replays_once_and_releases_gate_only_on_verified_mutation(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/game/types.ts", "export interface InputState { dx: number; }")
    workspace.crash_after_write = True
    feature = {"id": "focused-write-crash", "owned_paths": ["src/game/InputManager.ts"]}
    context = {"interfaces": "Use the real InputState.", "continuation": {"stop_reason": "non_progress"}}
    client = ScriptedClient([
        {"tool": "write_file", "path": "src/game/InputManager.ts", "content": "export const dx = 0;"},
        {"tool": "finish", "summary": "同一write经崩溃恢复只执行一次。"},
    ])
    agent = CodingAgent(client, store, workspace, context_chars=32_000)
    with pytest.raises(asyncio.CancelledError):
        await agent.run_feature(rid, feature, context)
    result = await agent.run_feature(rid, feature, context)
    assert result["status"] == "implemented" and len(client.inputs) == 2
    assert workspace.writes == 2  # Initial types fixture plus exactly one proposed write.


@pytest.mark.parametrize("tool", ["read_file", "read_lines"])
async def test_compiler_line_numbers_have_explicit_source_reads_and_small_limits_are_valid(store, request_data, tmp_path, tool):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    lines = [f"// 第{number}行的真实内容\n" for number in range(1, 301)]
    source = "".join(lines)
    workspace.write_file("src/game/levels.ts", source)
    client = ScriptedClient([
        {"tool": "read_file", "path": "src/game/levels.ts", "offset": 240, "limit": 30},
        {"tool": tool, "path": "src/game/levels.ts", "start_line": 245, "end_line": 265},
        {"tool": "read_file", "path": "src/game/levels.ts", "start_line": 255, "end_line": 255, "limit": 1},
        {"tool": "blocked", "summary": "本回归核对字符偏移与tsc行号，不宣称功能完成。"},
    ])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "line-coordinate-"+tool, "owned_paths": ["src/game/levels.ts"]}, {})
    first = client.inputs[1]["data"]["recent_observations"][-1]["result"]
    assert first["content"] == source[240:270] and first["offset"] == 240
    assert first["start_line"] != 240 and "characters" in first["coordinate_unit"]
    line_read = client.inputs[2]["data"]["recent_observations"][-1]["result"]
    assert line_read["content"] == "".join(lines[244:265])
    assert line_read["start_line"] == 245 and line_read["end_line"] == 265
    assert line_read["offset"] == sum(map(len, lines[:244])) and line_read["selected_lines_complete"] is True
    tiny = client.inputs[3]["data"]["recent_observations"][-1]["result"]
    assert tiny["content"] == lines[254][0] and tiny["selected_lines_complete"] is False
    assert result["status"] == "blocked"


class CompilerWorkspace(Workspace):
    """Compiler-output control fixture reproducing the observed case3 positions."""
    async def build(self, **kwargs):
        if "const rng: {}" in self.read_file("src/game/levels.ts"):
            self.builds += 1
            return {"passed": False, "errors": ["typecheck failed"], "stdout":
                "src/game/levels.ts(255,30): error TS2339: Property 'next' does not exist on type '{}'.\n"
                "src/game/levels.ts:258:30 - error TS2339: Property 'next' does not exist on type '{}'.\n"
                "../../outside.ts(1,1): error TS2339: Untrusted outside path.\n", "stderr": ""}
        return await super().build(**kwargs)


def compiler_source():
    lines = [f"// Context line {number}\n" for number in range(1, 301)]
    lines[253] = "const rng: {} = { next: () => 0.5 };\n"
    lines[254] = "const first = rng.next();\n"
    lines[257] = "const second = rng.next();\n"
    return "".join(lines)


async def test_fresh_host_compiler_locations_release_focus_for_one_precise_read_then_patch(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = CompilerWorkspace(tmp_path / "game")
    source = compiler_source()
    workspace.write_file("src/game/levels.ts", source)
    feature = {"id": "compiler-focused-recovery", "owned_paths": ["src/game/levels.ts"]}

    class InspectingClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            assert len(json.dumps(packet, ensure_ascii=False)) <= 32_000
            if not self.inputs:
                assert schema is FocusedCodingAction
            elif len(self.inputs) == 1:
                assert schema is DiagnosticCodingAction and "focused_recovery" not in packet
                assert packet["new_build_diagnostic"]["remaining"] == 3
                assert packet["new_build_diagnostic"]["reported_paths"] == ["src/game/levels.ts"]
                diagnostics = packet["build_diagnostics"]
                assert [row["line"] for row in diagnostics] == [255, 258]
                assert diagnostics[0]["start_line"] == 245 and diagnostics[0]["end_line"] == 265
                assert diagnostics[0]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
                assert next(row["text"] for row in diagnostics[0]["excerpt"] if row["line"] == 255) == "const first = rng.next();"
                state = store.read_json(rid, CodingAgent._prefix(feature["id"]) + ".checkpoint.json")
                assert state["read_counts"] == {}
            elif len(self.inputs) == 2:
                assert schema is DiagnosticCodingAction and packet["new_build_diagnostic"]["remaining"] == 2
                read = packet["recent_observations"][-1]["result"]
                assert read["start_line"] == 245 and read["end_line"] == 265
                assert "const rng: {}" in read["content"]
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)

    client = InspectingClient([
        {"tool": "build"},
        {"tool": "read_lines", "path": "src/game/levels.ts", "start_line": 245, "end_line": 265},
        {"tool": "apply_patch", "path": "src/game/levels.ts", "old": "const rng: {}", "new": "const rng: { next(): number }"},
        {"tool": "finish", "summary": "已根据主机真实诊断行修复类型并重新构建。"},
    ])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid, feature,
        {"continuation": {"stop_reason": "non_progress"}, "approved_configuration": "规则" * 5_000,
         "review_feedback": "旧上下文" * 5_000, "interfaces": "interface contract"})
    assert result["status"] == "implemented" and result["steps"] == 4
    assert len(client.inputs) == 4
    stored = store.read_json(rid, CodingAgent._prefix(feature["id"]) + ".build-0001.json")
    assert stored["diagnostic_progress"] is True and len(stored["diagnostics"]) == 2


async def test_same_compiler_diagnostic_cannot_reset_nonprogress_by_rebuilding_forever(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = CompilerWorkspace(tmp_path / "game")
    workspace.write_file("src/game/levels.ts", compiler_source())
    client = ScriptedClient([{"tool": "build"}] * 36)
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "repeat-compiler-errors", "owned_paths": ["src/game/levels.ts"]},
        {"continuation": {"stop_reason": "non_progress"}})
    assert result["status"] == "needs_work" and result["stop_reason"] == "non_progress"
    assert result["steps"] == len(client.inputs) == 2 and not result["changed_files"]


@pytest.mark.parametrize("stale", [False, True])
async def test_resume_hydrates_current_host_build_before_the_first_focused_proposal(store, request_data, tmp_path, stale):
    rid = run_id(store, request_data)
    workspace = CompilerWorkspace(tmp_path / "game")
    workspace.write_file("src/game/levels.ts", compiler_source())
    report = await workspace.build()
    report["source_sha256"] = "b" * 64 if stale else workspace.snapshot()["sha256"]
    (workspace.root / ".harness").mkdir()
    (workspace.root / ".harness/build.json").write_text(json.dumps(report))

    class InspectingClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            if not self.inputs:
                if stale:
                    assert schema is FocusedCodingAction and "build_diagnostics" not in packet
                else:
                    assert schema is DiagnosticCodingAction and "focused_recovery" not in packet
                    assert packet["build_diagnostics"][0]["line"] == 255
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)

    actions = ([{"tool": "blocked", "summary": "旧构建与当前源码SHA不同，不能沿用诊断。"}] if stale else [
        {"tool": "read_lines", "path": "src/game/levels.ts", "start_line": 245, "end_line": 265},
        {"tool": "apply_patch", "path": "src/game/levels.ts", "old": "const rng: {}", "new": "const rng: { next(): number }"},
        {"tool": "finish", "summary": "重启前的真实构建诊断可安全恢复，不重复读全书。"}])
    client = InspectingClient(actions)
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "resume-current-compiler", "owned_paths": ["src/game/levels.ts"]},
        {"continuation": {"stop_reason": "non_progress"}})
    assert result["status"] == ("blocked" if stale else "implemented")
    assert len(client.inputs) == (1 if stale else 3)


async def test_missing_export_diagnostic_allows_three_source_and_dependency_reads_then_repair(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    lines = ["// Simulation context\n"] * 40
    lines[13] = "import { createPRNG } from './prng';\n"
    lines[14] = "import { buildGameConfig } from './config';\n"
    old = "".join(lines[13:15])
    workspace.write_file("src/game/Simulation.ts", "".join(lines))
    workspace.write_file("src/game/prng.ts", "export class PRNG { next() { return 0.5; } }\n")
    workspace.write_file("src/game/config.ts", "export const GAME_CONFIG = { forwardSpeed: 8 };\n")
    report = {"passed": False, "source_sha256": workspace.snapshot()["sha256"], "stdout":
        "src/game/Simulation.ts(14,10): error TS2305: Module './prng' has no exported member 'createPRNG'.\n"
        "src/game/Simulation.ts(15,10): error TS2724: Module './config' has no exported member 'buildGameConfig'.\n"}
    (workspace.root / ".harness").mkdir()
    (workspace.root / ".harness/build.json").write_text(json.dumps(report))

    class InspectingClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            if len(self.inputs) < 3:
                assert schema is DiagnosticCodingAction
                assert packet["new_build_diagnostic"]["remaining"] == 3-len(self.inputs)
                assert packet["new_build_diagnostic"]["reported_paths"] == ["src/game/Simulation.ts"]
            elif len(self.inputs) == 3:
                assert schema is FocusedCodingAction and "new_build_diagnostic" not in packet
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)

    client = InspectingClient([
        {"tool": "read_file", "path": "src/game/prng.ts"},
        {"tool": "read_file", "path": "src/game/config.ts"},
        {"tool": "read_lines", "path": "src/game/Simulation.ts", "start_line": 11, "end_line": 18},
        {"tool": "apply_patch", "path": "src/game/Simulation.ts", "old": old,
         "new": "import { PRNG } from './prng';\nimport { GAME_CONFIG } from './config';\n"},
        {"tool": "finish", "summary": "使用实际导出签名修复依赖接口，没有无关读取。"},
    ])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "missing-export-source-working-set", "owned_paths": ["src/game/Simulation.ts"]},
        {"continuation": {"stop_reason": "non_progress"}})
    assert result["status"] == "implemented" and len(client.inputs) == 5


def test_diagnostic_deduplication_and_file_diversity_include_seventh_status_effect_error(store, tmp_path):
    workspace = Workspace(tmp_path / "game")
    for path in ["src/game/ProjectilePool.ts", "src/game/StatusEffectSystem.ts"]:
        workspace.write_file(path, "".join(f"// Actual source line {line}\n" for line in range(1, 201)))
    first = [f"src/game/ProjectilePool.ts({line},7): error TS2554: Expected 9 arguments, but got 7.\n"
             for line in [164,168,179,185]]
    seventh = "src/game/StatusEffectSystem.ts(44,3): error TS1016: A required parameter cannot follow an optional parameter.\n"
    output = "".join(first+first[:2]+[seventh])
    report = {"stdout": output, "stderr": output, "stages": [{"stdout": output, "stderr": ""}]}
    rows = CodingAgent(None, store, workspace, context_chars=32_000)._build_diagnostics(report)
    assert len({(r["path"],r["line"],r["column"]) for r in rows}) == len(rows) <= 6
    assert [r["path"] for r in rows[:2]] == ["src/game/ProjectilePool.ts", "src/game/StatusEffectSystem.ts"]
    status = next(r for r in rows if r["path"].endswith("StatusEffectSystem.ts"))
    assert status["line"] == 44 and any(line["line"] == 44 for line in status["excerpt"])


async def test_diagnostic_dependency_same_page_repeat_does_not_renew_three_read_allowance(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = CompilerWorkspace(tmp_path / "game")
    workspace.write_file("src/game/levels.ts", compiler_source())
    workspace.write_file("src/game/prng.ts", "export class PRNG { next() { return 0.5; } }\n")
    client = ScriptedClient([
        {"tool": "build"}, {"tool": "read_file", "path": "src/game/prng.ts"},
        {"tool": "read_file", "path": "src/game/prng.ts"},
        {"tool": "blocked", "summary": "同一依赖已完整展示两次，需明确修复而非重新读取。"},
    ])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "repeat-diagnostic-dependency", "owned_paths": ["src/game/levels.ts"]}, {})
    assert result["status"] == "blocked" and len(client.inputs) == 4
    assert client.inputs[3]["data"]["focused_recovery"]["reason"] == "diagnostic source page repeated without new information"
    assert "new_build_diagnostic" not in client.inputs[3]["data"]


async def test_current_successful_host_build_releases_focus_without_unnecessary_source_edits(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/game/levels.ts", "export const levels = [1,2,3];")
    before = workspace.snapshot()["sha256"]
    (workspace.root / "dist").mkdir()
    artifact = b"<!doctype html><html lang='zh'><body>build fixture only</body></html>"
    (workspace.root / "dist/index.html").write_bytes(artifact)
    report = {"passed": True, "source_sha256": before, "entrypoint": "index.html",
              "artifact_manifest": [{"path": "index.html", "sha256": hashlib.sha256(artifact).hexdigest()}]}
    (workspace.root / ".harness").mkdir()
    (workspace.root / ".harness/build.json").write_text(json.dumps(report))

    async def no_rebuild(**kwargs):
        raise AssertionError("the current trusted successful build should be reused")

    workspace.build = no_rebuild
    client = ScriptedClient([{"tool": "finish", "summary": "外部修复已由同源码主机构建验证，当前功能完成，逐T验收尚未执行。"}])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {"id": "external-repair-completed", "owned_paths": ["src/game/levels.ts"]},
        {"continuation": {"stop_reason": "non_progress"}})
    assert result["status"] == "implemented" and result["acceptance"]["status"] == "not_run"
    assert workspace.snapshot()["sha256"] == before and workspace.writes == 1
    assert client.inputs[0]["data"]["latest_build"]["passed"] is True
    assert "focused_recovery" not in client.inputs[0]["data"]


async def test_repeating_successful_build_at_same_source_is_not_new_verification_progress(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/game/levels.ts", "export const levels = [1,2,3];")
    client = ScriptedClient([{"tool": "build"}, {"tool": "build"}])
    result = await CodingAgent(client, store, workspace, max_steps=2, context_chars=32_000).run_feature(rid,
        {"id": "verify-once-per-source", "owned_paths": ["src/game/levels.ts"]},
        {"continuation": {"stop_reason": "non_progress"}})
    prefix = CodingAgent._prefix("verify-once-per-source")
    assert store.read_json(rid, prefix + ".build-0001.json")["build_progress"] is True
    assert store.read_json(rid, prefix + ".build-0002.json")["build_progress"] is False
    assert not result["changed_files"] and result["status"] == "needs_work"


@pytest.mark.parametrize("output", ["missing", "empty", "whitespace", "unmatched_glob"])
async def test_successful_build_cannot_finish_without_declared_owned_outputs(store, request_data, tmp_path, output):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/template.ts", "export const template = true;")
    if output in {"empty", "whitespace"}:
        workspace.write_file("src/game/SkillSystem.ts", "" if output == "empty" else " \n\t")
    feature = {"id": "required-owned-output-" + output,
               "owned_paths": ["src/skills/**"] if output == "unmatched_glob" else ["src/game/SkillSystem.ts"]}
    client = ScriptedClient([{"tool": "build"}, {"tool": "finish", "summary": "模板已构建，所以声称功能完成。"},
                             {"tool": "blocked", "summary": "主机指出归属文件未交付，不能标记implemented。"}])
    result = await CodingAgent(client, store, workspace).run_feature(rid, feature, {})
    assert result["status"] == "blocked" and result["acceptance"]["status"] == "not_run"
    rejected = client.inputs[2]["data"]["recent_observations"][-1]["result"]
    assert rejected["accepted"] is False and "missing or empty" in rejected["reason"]
    assert rejected["missing_owned_paths"] or rejected["empty_owned_paths"]


async def test_finish_recovers_by_creating_missing_owned_file_within_the_same_session(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    client = ScriptedClient([
        {"tool": "finish", "summary": "先尝试完成。"},
        {"tool": "write_file", "path": "src/game/SkillSystem.ts", "content": "export const skillCount = 12;"},
        {"tool": "finish", "summary": "实际写入声明的模块再完成，独立验收未执行。"}])
    result = await CodingAgent(client, store, workspace).run_feature(rid,
        {"id": "finish-missing-output-repair", "owned_paths": ["src/game/SkillSystem.ts"]}, {})
    assert result["status"] == "implemented" and result["changed_files"] == ["src/game/SkillSystem.ts"]
    assert client.inputs[1]["data"]["recent_observations"][-1]["result"]["accepted"] is False
    assert workspace.builds == 1  # No compile spend for a known missing output.


async def test_glob_owner_can_finish_with_existing_safe_source_and_build_without_claiming_game_acceptance(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    workspace.write_file("src/game/SkillSystem.ts", "export const skillCount = 12;")
    client = ScriptedClient([{"tool": "finish", "summary": "归属glob有安全源码，主机确认可构建。"}])
    result = await CodingAgent(client, store, workspace).run_feature(rid,
        {"id": "glob-existing-output", "owned_paths": ["src/**", "public/**"]}, {})
    assert result["status"] == "implemented" and result["acceptance"]["status"] == "not_run"


async def test_legacy_implemented_checkpoint_cannot_bypass_a_missing_owned_output(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    feature = {"id": "legacy-empty-implementation", "owned_paths": ["src/game/SkillSystem.ts"]}
    first = await CodingAgent(ScriptedClient([{"tool": "blocked", "summary": "旧框架状态夹具，无实际功能文件。"}]),
                              store, workspace).run_feature(rid, feature, {})
    checkpoint = store.read_json(rid, first["checkpoint"])
    checkpoint.update(status="implemented", stop_reason="implemented")  # Simulate a pre-fix host checkpoint.
    store.write(rid, first["checkpoint"], checkpoint)
    client = ScriptedClient([
        {"tool": "finish", "summary": "旧缓存声称完成，但应重新检查文件。"},
        {"tool": "write_file", "path": "src/game/SkillSystem.ts", "content": "export const skillCount = 12;"},
        {"tool": "finish", "summary": "补交真实归属模块后才恢复implemented。"}])
    result = await CodingAgent(client, store, workspace).run_feature(rid, feature, {})
    assert result["status"] == "implemented" and len(client.inputs) == 3
    assert client.inputs[1]["data"]["recent_observations"][-1]["result"]["accepted"] is False
    assert workspace.resolve("src/game/SkillSystem.ts").is_file()


async def test_segment_result_preserves_precise_handoff_for_a_productive_continuation(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    client = ScriptedClient([
        {"tool": "read_context", "section": "approved_configuration", "memory": "下一文件 src/types.ts；导出Vec3{x,y,z:number}；speed=6。"},
        {"tool": "write_file", "path": "src/types.ts", "content": "export interface Vec3 { x:number; y:number; z:number }",
         "memory": "已完成src/types.ts导出Vec3；下一文件src/config.ts，speed=6，来源approved_configuration。"},
    ])
    workspace = Workspace(tmp_path / "game")
    result = await CodingAgent(client, store, workspace, max_steps=2).run_feature(
        rid, {"id": "foundation-lease"}, {"approved_configuration": {"speed": 6}})
    assert result["status"] == "needs_work" and result["stop_reason"] == "action_limit"
    assert result["changed_files"] == ["src/types.ts"]
    assert "src/config.ts" in result["memory"] and "speed=6" in result["memory"]
    assert result["read_receipts"][0]["source"] == "context:approved_configuration"
    from game_harness.coding_agent import CODING_INSTRUCTION
    assert "每读取1至2个必要资料页后" in CODING_INSTRUCTION
    assert "已经读完整的源不得再次从头通读" in CODING_INSTRUCTION
    continuation_client = ScriptedClient([
        {"tool": "write_file", "path": "src/config.ts", "content": "export const speed = 6;"},
        {"tool": "finish", "summary": "继承上一lease已知接口并完成下一配置文件。"},
    ])
    completed = await CodingAgent(continuation_client, store, workspace).run_feature(rid,
        {"id": "foundation-lease-2", "owned_paths": ["src/config.ts"]},
        {"approved_configuration": {"speed": 6}, "continuation": result})
    packet = continuation_client.inputs[0]["data"]
    assert packet["memory"] == result["memory"]
    assert packet["read_receipts"][0]["source"] == "context:approved_configuration"
    assert packet["next_unwritten_owned_files"] == ["src/config.ts"]
    assert completed["status"] == "implemented"


async def test_small_approved_configuration_stays_complete_and_file_frontier_updates(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / "game")
    config = {"tables": [{"id": f"W{i:02}", "effect": "真实技能参数与数值。" * 17, "damage": i * 12} for i in range(24)],
              "last_required_value": "末尾必须保留：E06范围20。"}
    assert 4_000 < len(json.dumps(config, ensure_ascii=False)) < 8_000
    client = ScriptedClient([
        {"tool": "write_file", "path": "src/types.ts", "content": "export type Id = string;"},
        {"tool": "write_file", "path": "src/config.ts", "content": "export const targetCount = 20;"},
        {"tool": "finish", "summary": "每次完成一个文件，未反复读取已常驻的配置。"},
    ])
    await CodingAgent(client, store, workspace, context_chars=32_000, history_limit=3).run_feature(rid,
        {"id": "data-frontier", "owned_paths": ["src/types.ts", "src/config.ts", "src/optional/**"]},
        {"approved_configuration": config, "spec_excerpts": "原文片段。" * 10_000,
         "production_contract": "真实共享接口。" * 5_000, "interfaces": "接口定义。" * 5_000})
    for call in client.inputs:
        assert call["data"]["context"]["approved_configuration"] == config
        assert len(json.dumps(call["data"], ensure_ascii=False)) <= 32_000
    assert client.inputs[0]["data"]["next_unwritten_owned_files"] == ["src/types.ts", "src/config.ts"]
    assert client.inputs[1]["data"]["next_unwritten_owned_files"] == ["src/config.ts"]
    assert client.inputs[2]["data"]["next_unwritten_owned_files"] == []


@pytest.mark.parametrize('prefer', ['compiler', 'latest_read', 'stale_compiler'])
def test_focused_working_file_uses_actual_owned_failure_or_latest_read_not_first_owner(store, tmp_path, prefer):
    workspace = Workspace(tmp_path / 'game')
    for name in ['CombatSystem', 'ProjectilePool', 'StatusEffectSystem', 'OtherOwner']:
        workspace.write_file('src/game/' + name + '.ts', 'export function ' + name + '(): void {}')
    feature = {'id': 'actual-error-file', 'owned_paths': ['src/game/CombatSystem.ts', 'src/game/ProjectilePool.ts', 'src/game/StatusEffectSystem.ts']}
    compiler_path = 'src/game/StatusEffectSystem.ts'
    read_path = 'src/game/ProjectilePool.ts'
    state = {'history': [{'tool': 'read_file', 'path': read_path,
        'result': {'ok': True, 'sha256': hashlib.sha256(workspace.read_file(read_path).encode()).hexdigest()}}]}
    if prefer != 'latest_read':
        state['build_diagnostics'] = [
            {'path': 'src/game/OtherOwner.ts', 'source_sha256': hashlib.sha256(workspace.read_file('src/game/OtherOwner.ts').encode()).hexdigest()},
            {'path': compiler_path, 'source_sha256': '0' * 64 if prefer == 'stale_compiler' else hashlib.sha256(workspace.read_file(compiler_path).encode()).hexdigest()}]
    expected = compiler_path if prefer == 'compiler' else read_path
    assert CodingAgent(None, store, workspace)._focus_target(feature, {}, state) == expected


def test_signature_headers_are_exact_source_and_ignore_documentation_or_literal_exports():
    source = '''// export function pretend(): number { return 0; }
const example = "export function fake(): string {}";
export function applyStatus(
  entityId: string,
  type: 'slow' | 'freeze' | 'burn' | 'poison' | 'stun' | 'knockback',
  duration: number,
  strength: number,
  damagePerTick: number | undefined,
  isBoss: boolean,
  config: GameConfig,
  rng: PRNG,
  emit: EventSink
): void {
  throw new Error('this implementation body must not enter signatures');
}
export async function load(name = "a)b("): Promise<string> { return name; }
'''
    signatures = CodingAgent._export_function_signatures(source)
    assert [row['name'] for row in signatures] == ['applyStatus', 'load']
    assert signatures[0]['signature'] == source[source.index('export function applyStatus'):source.index(' {\n  throw')]
    assert signatures[0]['parameters_complete'] and signatures[0]['return_type_complete']
    assert 'name = "a)b("' in signatures[1]['signature']
    assert 'throw' not in json.dumps(signatures)


async def test_case2_evicted_status_dependency_signature_stays_visible_in_focused_projectile_repair(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / 'game')
    signature = '''export function applyStatus(
  entityId: string, type: 'slow' | 'freeze' | 'burn' | 'poison' | 'stun' | 'knockback',
  duration: number, strength: number, damagePerTick: number | undefined,
  isBoss: boolean, config: GameConfig, rng: PRNG, emit: EventSink
): void'''
    workspace.write_file('src/game/types.ts', 'export interface PRNG { next(): number; }')
    workspace.write_file('src/game/CombatSystem.ts', 'export function updateCombat(): void {}')
    workspace.write_file('src/game/StatusEffectSystem.ts', signature + ' { /* actual body */ }')
    projectile = 'export function updateProjectiles(): void {\n' + '// projectile lifecycle\n' * 160 + '\n}\n'
    workspace.write_file('src/game/ProjectilePool.ts', projectile)
    actions = [
        {'tool': 'read_file', 'path': 'src/game/StatusEffectSystem.ts'},
        {'tool': 'note', 'summary': '依赖签名已读。'},
        {'tool': 'note', 'summary': '准备投射物具体调用修复。'},
        {'tool': 'note', 'summary': '旧源码观察会从history3淘汰。'},
        {'tool': 'read_file', 'path': 'src/game/ProjectilePool.ts'},
        {'tool': 'read_file', 'path': 'src/game/ProjectilePool.ts'},
        {'tool': 'apply_patch', 'path': 'src/game/ProjectilePool.ts', 'old': 'export function updateProjectiles(): void', 'new': 'export function updateProjectiles(): void /* dependency signature verified */'},
        {'tool': 'finish', 'summary': '按真实签名完成精确修复；未宣称逐T验收。'},
    ]
    class InspectingClient(ScriptedClient):
        async def structured(self, rid, stage, role, instruction, packet, schema, **kwargs):
            if len(self.inputs) == 6:
                assert schema is FocusedCodingAction
                assert packet['focused_recovery']['target_file'] == 'src/game/ProjectilePool.ts'
                assert not any(row.get('path') == 'src/game/StatusEffectSystem.ts' for row in packet['recent_observations'])
                dependency = next(row for row in packet['read_function_signatures'] if row['path'] == 'src/game/StatusEffectSystem.ts')
                assert dependency['signature_only'] and not dependency['full_source'] and not dependency['grants_full_read']
                assert dependency['functions'][0]['signature'] == signature
                assert dependency['sha256'] == hashlib.sha256(workspace.read_file(dependency['path']).encode()).hexdigest()
                assert len(json.dumps(packet, ensure_ascii=False)) <= 32_000
            return await super().structured(rid, stage, role, instruction, packet, schema, **kwargs)
    client = InspectingClient(actions)
    result = await CodingAgent(client, store, workspace, context_chars=32_000, history_limit=3).run_feature(rid,
        {'id': 'case2-status-dependency-eviction', 'owned_paths': ['src/game/CombatSystem.ts', 'src/game/ProjectilePool.ts', 'src/game/StatusEffectSystem.ts']}, {})
    assert result['status'] == 'implemented' and result['changed_files'] == ['src/game/ProjectilePool.ts']


async def test_signature_only_inherited_read_does_not_authorize_whole_file_replacement(store, request_data, tmp_path):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / 'game')
    path = 'src/dependency.ts'; text = 'export function dependency(value: number): number { return value; }'
    workspace.write_file(path, text)
    receipt = {'source': 'file:' + path, 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'characters': len(text),
        'ranges': [[0, len(text)]], 'next_unread_offset': len(text), 'next_offset': len(text), 'reads': 1}
    client = ScriptedClient([{'tool': 'write_file', 'path': path, 'content': text.replace('return value;', 'return value + 1;')},
        {'tool': 'blocked', 'summary': '签名视图不是全文件阅读，需精确patch或真实fullread。'}])
    result = await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {'id': 'signature-no-write-permission', 'owned_paths': ['src/**']}, {'continuation': {'read_receipts': [receipt]}})
    assert client.inputs[0]['data']['read_function_signatures'][0]['signature_only']
    error = client.inputs[1]['data']['recent_observations'][-1]['result']
    assert error['ok'] is False and 'read the entire existing file' in error['error']
    assert workspace.read_file(path) == text and result['status'] == 'blocked'


@pytest.mark.parametrize('mode', ['stale', 'partial'])
async def test_signature_catalog_omits_stale_or_partly_read_sources(store, request_data, tmp_path, mode):
    rid = run_id(store, request_data)
    workspace = Workspace(tmp_path / 'game')
    path = 'src/dependency.ts'; text = 'export function dependency(value: number): number { return value; }'
    workspace.write_file(path, text)
    length = len(text)
    receipt = {'source': 'file:' + path, 'sha256': '0' * 64 if mode == 'stale' else hashlib.sha256(text.encode()).hexdigest(),
        'characters': length, 'ranges': [[0, length if mode == 'stale' else length // 2]]}
    client = ScriptedClient([{'tool': 'blocked', 'summary': '隔离检查，不生成游戏通过记录。'}])
    await CodingAgent(client, store, workspace, context_chars=32_000).run_feature(rid,
        {'id': 'signature-omit-' + mode, 'owned_paths': ['src/output.ts']}, {'continuation': {'read_receipts': [receipt]}})
    assert 'read_function_signatures' not in client.inputs[0]['data']


@pytest.mark.parametrize('limit', [8_000, 32_000])
async def test_runtime_invariants_remain_exact_when_large_context_and_latest_page_are_compacted(store, request_data, tmp_path, limit):
    rid = run_id(store, request_data)
    invariant = {'advance': '每次使用生产固定步长处理全部给定毫秒，不丢剩余时间。', 'updateLoops': '活动更新循环数，不是累计tick。',
        'fixture': '只有可信isolatedfixture可按真实关卡规则进入PLAYING，不能保留MENU/level0。', 'binding': 'SHA必须一致。' * 200}
    assert len(json.dumps(invariant, ensure_ascii=False)) < 2500
    escaped = ('中文\n"\\source' * 1600)
    client = ScriptedClient([{'tool': 'read_context', 'section': 'production_contract', 'limit': 6000},
        {'tool': 'blocked', 'summary': '只检查有界packet完整核心契约。'}])
    await CodingAgent(client, store, Workspace(tmp_path / 'game'), context_chars=limit).run_feature(rid,
        {'id': 'pinned-runtime-invariants-' + str(limit), 'objective': escaped},
        {'runtime_invariants': invariant, 'production_contract': escaped, 'spec_excerpts': escaped,
         'architecture': escaped, 'review_feedback': escaped})
    for entry in client.inputs:
        packet = entry['data']
        assert packet['context']['runtime_invariants'] == invariant
        assert len(json.dumps(packet, ensure_ascii=False)) <= limit
