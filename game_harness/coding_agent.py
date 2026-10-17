"""Durable, feature-scoped coding sessions over the existing model gateway.

Model actions are proposals. The host owns file permissions, execution, recovery,
build evidence, and acceptance status. A successful build means ``implemented``;
it does not establish that the game's specification or acceptance tests passed.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import inspect
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Workspace(Protocol):
    root: Path

    def resolve(self, relative: str, writable: bool = False) -> Path: ...
    def read_file(self, relative: str) -> str: ...
    def write_file(self, relative: str, content: str) -> Any: ...
    def apply_patch(self, relative: str, old: str, new: str,
                    expected_sha256: str | None = None) -> Any: ...
    def delete_file(self, relative: str, expected_sha256: str | None = None) -> Any: ...
    def list_files(self) -> Any: ...
    def snapshot(self) -> dict: ...
    async def build(self, install: bool = True, timeout: float = 180) -> dict: ...


class CodingAction(BaseModel):
    """One bounded action per model call; no arbitrary shell or test editing."""
    model_config = ConfigDict(extra="forbid")
    tool: Literal["list_files", "read_file", "read_lines", "read_context", "read_observation",
                  "write_file", "apply_patch", "delete_file", "build", "note", "finish", "blocked"]
    path: str = Field(default="", max_length=300)
    content: str = Field(default="", max_length=180_000)
    old: str = Field(default="", max_length=90_000)
    new: str = Field(default="", max_length=90_000)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    section: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12_000, ge=1, le=16_000)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    step: int = Field(default=0, ge=0)
    memory: str = Field(default="", max_length=5_000)
    summary: str = Field(default="", max_length=3_000)

    @model_validator(mode="after")
    def validate_action(self):
        if self.tool in {"read_file", "read_lines", "write_file", "apply_patch", "delete_file"} and not self.path:
            raise ValueError("file actions require path")
        if self.tool == "read_lines" and self.start_line is None:
            raise ValueError("read_lines requires a 1-based start_line")
        if self.end_line is not None and (self.start_line is None or self.end_line < self.start_line):
            raise ValueError("end_line requires start_line and must not precede it")
        if self.start_line is not None and self.tool not in {"read_file", "read_lines"}:
            raise ValueError("line coordinates are only valid for source-file reads")
        if self.tool == "apply_patch" and (not self.old or self.old == self.new):
            raise ValueError("patch requires a nonempty, changing exact match")
        if self.tool == "read_context" and not self.section:
            raise ValueError("read_context requires a section")
        if self.tool in {"finish", "blocked", "note"} and not self.summary.strip():
            raise ValueError("finish, blocked and note require a factual summary")
        return self


class FocusedCodingAction(CodingAction):
    """A single recovery proposal cannot spend its lease rereading inputs."""
    tool: Literal["write_file", "apply_patch", "build", "blocked"]


class DiagnosticCodingAction(CodingAction):
    """Fresh diagnostics permit bounded reads of source and its dependencies."""
    tool: Literal["read_file", "read_lines", "write_file", "apply_patch", "build", "blocked"]


CODING_INSTRUCTION = """你是长程游戏工程团队中负责一个 feature 的编码代理。只执行当前 feature，保留现有已实现功能。
当前会话按文件增量交付。每读取1至2个必要资料页后，优先创建或精确修改一个可独立编译的文件；从尚缺的类型/配置等低依赖文件开始。
next_unwritten_owned_files是主机根据真实目录计算的待创建文件，优先完成其中一个文件，再读取下一文件需要的资料；每次按一个文件推进。
优先使用已批准approved_configuration和公共接口，原文按需核对精确差异。已经读完整的源不得再次从头通读。
memory应保存当前/下一文件、关键导出签名、已确定参数值及其来源、确切剩余偏移；避免只写“还需更多资料/已经读过”。
continuation是上一分段会话的主机交接；优先沿下一文件继续，核对已读区间后复用已确定配置，避免新会话又从全文offset0开始。
resident_files是主机本轮从真实工作区读出的完整文件，含当前SHA与完整export名称；实际字段以这些文件为准，不用再次read_file确认。
read_function_signatures来自已完整读过且SHA仍一致的真实源文件，只保留export函数的参数/返回类型，不是完整源码，不授予整文件替换权限；按这些确切接口精确patch，勿因旧观察淘汰再次通读。
runtime_invariants是每轮固定的真实生产运行协议，必须逐条保持；不能用预览、测试专用状态或丢弃advance剩余时间替代。
接口提案中存在而真实types未导出的名字，不能通过反复读interfaces找到。可在当前owned文件定义兼容的局部结构类型；确实需要上游新增字段则blocked列出最小缺失，禁止捏造已存在字段。
若packet有focused_recovery，本次JSON只能write_file/apply_patch/build/blocked。主机已提供可容纳的当前文件与接口；仍缺必要信息则blocked列出确切缺失，不能再读资料或只写note。
这是真实多文件 React + TypeScript + Vite 项目；必须使用项目指定 Phaser/Babylon 引擎，npm 依赖已由主机固定并本地打包。
每次只提交一个 JSON 工具动作，不输出计划之外的 shell、不修改受保护的构建配置、规格、验收脚本或证据。
先读接口、依赖实现和相关规格片段再编码；一个文件一个动作，保持模块化，不把项目塞进一个文件。
工具：list_files；read_file(path,offset,limit)；read_lines(path,start_line,end_line)；read_context(section,offset,limit)；read_observation(step,offset,limit)；
write_file(path,content)；apply_patch(path,old,new,expected_sha256)；delete_file(path,expected_sha256)；build；note；finish；blocked。
现有文件完整替换前必须先完整读取；大文件用精确 patch（old 只能匹配一次）。offset和limit均为字符，不是行号。
tsc的file.ts(255,30)表示第255行第30列，绝不能用offset=255代替；用read_lines(start_line=245,end_line=265)或read_file的start_line/end_line字段读取。
build_diagnostics由主机解析真实构建输出并读取当前源，含行号、SHA和错误行上下文。new_build_diagnostic允许最多3次有新源/新区间的源码读取，可读故障文件与依赖接口，不限制为报错路径；重复同页仍会暂停。取得必要片段后提交修复。
read_context 可按节读取完整存档，__feature__ 是完整 feature，__specification__ 可读取主机保存的完整原始规格（若提供）。
__catalog__可读取上下文章节目录。read_receipts是主机记录的已读源区间与真实总长度，优先继续next_offset，禁止反复读同一页。
若旧观察content_view_truncated，shown_next_offset是当前真正展示到的位置；可从该源位置继续取未展示文字，不把预览长度当全文长度。
规划代理已看过完整规格；编码只需当前feature的相关章节与接口，不需要每个feature从头通读全书。取得必要信息后应开始增量编码。
观察被压缩时用 read_observation 取回完整历史。
上下文预览有明确 truncated 标记，不能把未读到的部分当成不存在。每轮 memory 可提供简洁的已知接口、剩余问题与下一步；
memory 不是测试证据，不得把自述当成通过验收。所有原始动作/观察都保留在磁盘。
角色及 owned_paths 是文件所有权边界。协作成员已实现的模块需要修正时给出集成反馈，禁止越权覆写。
实现真实数值、碰撞、技能、状态流转与可见反馈；禁止为了测试写假 __HARNESS__ 状态、假事件或作弊入口。
必须保留失败路径、固定随机种子和正常更新函数，按项目接口实现只读 snapshot/config、真实输入 dispatch 与同一生产 advance。
build 运行真实 typecheck 和打包；失败后读取错误并精确修复，禁止删测试、删类型检查、降级需求或切换引擎。
finish 仅表示当前 feature 已实现并可构建。主机还要求具体owned文件存在且非空；仅glob归属时至少有一个匹配的安全非空源文件。模板或构建成功不能证明业务需求完成，最终产品需独立逐项验收和视觉审查。
不能完成时用 blocked 说明具体缺失依赖、接口或构建限制。不要编造测试已执行、已通过或上游组件已存在。
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _serialized_size(value: Any) -> int:
    # Match AgentClient.complete's actual user-message serialization, including
    # separator spaces, instead of budgeting a smaller canonical hash format.
    return len(json.dumps(value, ensure_ascii=False))


def _preview(value: Any, limit: int) -> Any:
    text = value if isinstance(value, str) else _json(value)
    if _serialized_size(value) <= limit:
        return value
    # A JSON preview is a string embedded in another JSON document. Quotes,
    # backslashes and control characters expand again at that boundary. Budget
    # the final serialized representation, not the unescaped source substring.
    marker = {"preview": "", "truncated": True, "characters": len(text),
              "sha256": _text_hash(text), "retrieve": "read_context / read_observation with offset"}
    if _serialized_size(marker) > limit:
        return {"truncated": True, "retrieve": "read_context / read_observation"}
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        marker["preview"] = text[:middle]
        if _serialized_size(marker) <= limit:
            low = middle
        else:
            high = middle - 1
    marker["preview"] = text[:low]
    return marker


class CodingAgent:
    """A serial integration lane with independent role/context per feature.

    ``client`` implements AgentClient.structured; ``store`` implements the durable
    Store artifact/event API. ``verifier`` is host-supplied, never model-generated.
    All feature sessions sharing a workspace acquire the same process lock.
    Independent workspaces can execute concurrently without sharing source files.
    """

    def __init__(self, client, store, workspace: Workspace, *, max_steps: int = 36,
                 context_chars: int = 28_000, history_limit: int = 5,
                 build_timeout: float = 180,
                 verifier: Callable[..., Awaitable[dict]] | None = None,
                 images: list[Path] | None = None):
        if max_steps < 1 or context_chars < 8_000 or history_limit < 1:
            raise ValueError("invalid coding session limits")
        self.client, self.store, self.workspace = client, store, workspace
        self.max_steps, self.context_chars = max_steps, context_chars
        self.history_limit, self.build_timeout = history_limit, build_timeout
        self.verifier = verifier
        self.images = [Path(path) for path in images or []]

    @staticmethod
    def _prefix(feature_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", feature_id).strip("-")[:48] or "feature"
        return "coding-" + safe + "-" + _text_hash(feature_id)[:10]

    @contextmanager
    def _workspace_lock(self, rid, feature_id, owner):
        # fcntl is available on the macOS development host and Linux 210 server.
        # A file lock survives Python coroutine cancellation and is released by
        # the kernel if a process dies; no stale in-memory lease is trusted.
        import fcntl
        lock_path = Path(self.workspace.root) / ".harness-coding.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("workspace already has a coding owner; use a separate workspace or serial integration") from None
            handle.seek(0)
            handle.truncate()
            handle.write(_json({"run_id": rid, "feature": feature_id, "owner": owner, "pid": os.getpid()}))
            handle.flush()
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _save(self, rid, prefix, state):
        state["updated"] = time.time()
        self.store.write(rid, prefix + ".checkpoint.json", state)

    def _check_cancelled(self, rid):
        run = self.store.get(rid)
        if run is None or run.get("status") == "cancelled":
            raise asyncio.CancelledError("run cancelled")

    def _owned(self, path, feature):
        self.workspace.resolve(path, writable=True)
        patterns = feature.get("owned_paths") or []
        if patterns and not any(fnmatch.fnmatchcase(path, pattern)
                                or (pattern.endswith("/") and path.startswith(pattern))
                                for pattern in patterns):
            raise ValueError("file is owned by another feature: " + path)

    def _read(self, path):
        self.workspace.resolve(path)
        try:
            return self.workspace.read_file(path)
        except FileNotFoundError:
            return None

    def _owned_output_gaps(self, feature):
        """Ownership is not acceptance; absent declared output is still a gap."""
        patterns = feature.get("owned_paths") or []
        concrete = [path for path in patterns if not any(mark in path for mark in ("*", "?", "["))
                    and not path.endswith("/")]
        missing, empty = [], []
        for relative in concrete:
            path = self.workspace.resolve(relative, writable=True)
            if not path.is_file():
                missing.append(relative)
            elif not path.read_bytes().strip():
                empty.append(relative)
        if patterns and not concrete:
            matches = []
            for relative in self.workspace.list_files():
                if not any(fnmatch.fnmatchcase(relative, pattern)
                           or pattern.endswith("/") and relative.startswith(pattern) for pattern in patterns):
                    continue
                try:
                    path = self.workspace.resolve(relative, writable=True)
                    if path.is_file() and path.read_bytes().strip():
                        matches.append(relative)
                except (ValueError, OSError):
                    continue
            if not matches:
                missing.extend(patterns)
        return {"missing_owned_paths": missing, "empty_owned_paths": empty} if missing or empty else {}

    def _prepare(self, action, feature, state):
        if state.get("focused_recovery") and action.tool not in {"write_file", "apply_patch", "build", "blocked"}:
            raise ValueError("focused recovery permits one write_file/apply_patch/build/blocked action, not another read")
        allowance = state.get("diagnostic_read_allowance")
        if allowance:
            if action.tool not in {"read_file", "read_lines", "write_file", "apply_patch", "build", "blocked"}:
                raise ValueError("new compiler diagnostic permits one targeted file read, patch, build or blocked")
        if action.tool not in {"write_file", "apply_patch", "delete_file"}:
            return {}
        self._owned(action.path, feature)
        before = self._read(action.path)
        before_hash = None if before is None else _text_hash(before)
        if action.expected_sha256 and action.expected_sha256 != before_hash:
            raise ValueError("stale expected_sha256; read the current file before editing")
        if action.tool == "delete_file":
            if before is None:
                raise ValueError("file to delete does not exist")
            if state["full_reads"].get(action.path) != before_hash and action.expected_sha256 != before_hash:
                raise ValueError("delete requires a current full read or expected_sha256")
            return {"before_sha256": before_hash, "after_sha256": None}
        if action.tool == "write_file":
            if before is not None and state["full_reads"].get(action.path) != before_hash:
                raise ValueError("read the entire existing file before replacing it, or use an exact apply_patch")
            after = action.content
        else:
            if before is None or before.count(action.old) != 1:
                raise ValueError("apply_patch old must match exactly once in the current file")
            after = before.replace(action.old, action.new, 1)
        return {"before_sha256": before_hash, "after_sha256": _text_hash(after)}

    @staticmethod
    def _configuration_feature(feature):
        paths = feature.get("owned_paths") or []
        return not paths or any(re.search(r"(?:^|/)(?:types|config|balance|levels|skills|passives|enemies|evolutions)(?:[./]|$)",
                                         path, re.I) for path in paths)

    def _focus_target(self, feature, context, state=None):
        state = state or {}

        def owned_source(path, expected=None):
            try:
                self._owned(path, feature)
                text = self._read(path)
                return text is not None and (expected is None or _text_hash(text) == expected)
            except (ValueError, OSError):
                return False

        # The current compiler error is more useful than the first item in a
        # planner's ownership list. Ignore diagnostics for an obsolete version.
        for row in state.get("build_diagnostics", []):
            if owned_source(row.get("path", ""), row.get("source_sha256")):
                return row["path"]
        for row in reversed(state.get("history", [])):
            if row.get("tool") in {"read_file", "read_lines"} and row.get("result", {}).get("ok"):
                if owned_source(row.get("path", ""), row["result"].get("sha256")):
                    return row["path"]
        focused = state.get("focused_recovery", {})
        if focused.get("source", "").startswith("file:"):
            path = focused["source"][5:]
            if owned_source(path, focused.get("sha256")):
                return path
        for row in reversed(list(state.get("read_receipts", {}).values())):
            if row.get("source", "").startswith("file:"):
                path = row["source"][5:]
                if owned_source(path, row.get("sha256")):
                    return path
        paths = [path for path in feature.get("owned_paths", [])
                 if not any(mark in path for mark in ("*", "?", "["))]
        if paths:
            return next((path for path in paths if self._read(path) is None), paths[0])
        # A dependency coordinator owns globs, but its concrete working file is
        # the existing shared contract, not every unrelated project file.
        for path in ("src/game/types.ts", "src/types.ts"):
            try:
                self._owned(path, feature)
                if feature.get("owned_paths") and self._read(path) is not None:
                    return path
            except (ValueError, OSError):
                pass
        return None

    @staticmethod
    def _export_function_signatures(text):
        """Copy bounded declaration headers, never infer types or copy bodies.

        Mask comments and literals while preserving offsets, so documentation
        examples cannot invent exports and parentheses in defaults do not close
        a real parameter list. Unsupported/oversized headers are simply omitted.
        """
        masked = list(text)
        index = 0
        while index < len(text):
            if text.startswith("//", index):
                end = text.find("\n", index + 2)
                end = len(text) if end < 0 else end
            elif text.startswith("/*", index):
                end = text.find("*/", index + 2)
                end = len(text) if end < 0 else end + 2
            elif text[index] in {"'", '"', "`"}:
                quote, end = text[index], index + 1
                while end < len(text):
                    if text[end] == "\\":
                        end += 2
                    elif text[end] == quote:
                        end += 1
                        break
                    else:
                        end += 1
            else:
                index += 1
                continue
            for position in range(index, min(end, len(text))):
                if masked[position] != "\n":
                    masked[position] = " "
            index = end
        code = "".join(masked)
        pattern = re.compile(r"\bexport\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")
        result = []
        for match in pattern.finditer(code):
            opening = code.find("(", match.end(), match.end() + 500)
            if opening < 0 or any(mark in code[match.end():opening] for mark in (";", "=")):
                continue
            depth, end = 1, opening + 1
            while end < len(code) and end - match.start() < 2_000 and depth:
                if code[end] == "(":
                    depth += 1
                elif code[end] == ")":
                    depth -= 1
                end += 1
            if depth:
                continue
            tail = code[end:end + 500]
            boundary = re.search(r"[;{]", tail)
            return_type_complete = False
            if boundary and (not tail[:boundary.start()].strip()
                             or tail[:boundary.start()].lstrip().startswith(":")):
                # Simple return annotations (void, Promise<T>, arrays, unions)
                # are copied exactly. An object return type starts with '{';
                # keep only the complete parameters rather than pretend its
                # first brace is the function body.
                annotation = tail[:boundary.start()].strip()
                if annotation != ":":
                    end += boundary.start()
                    return_type_complete = True
            signature = text[match.start():end].rstrip()
            if _serialized_size(signature) > 2_400:
                continue
            result.append({"name": match.group(1), "signature": signature,
                "parameters_complete": True, "return_type_complete": return_type_complete,
                "start_line": text.count("\n", 0, match.start()) + 1,
                "end_line": text.count("\n", 0, max(match.start(), end - 1)) + 1})
            if len(result) >= 16:
                break
        return result

    def _read_function_signatures(self, state):
        result, paths = [], set()
        budget = min(4_000, self.context_chars // 8)
        for receipt in reversed(list(state.get("read_receipts", {}).values())):
            source = receipt.get("source", "")
            if not source.startswith("file:") or receipt.get("next_unread_offset", 0) < receipt.get("characters", 0):
                continue
            path = source[5:]
            if path in paths:
                continue
            paths.add(path)
            try:
                text = self._read(path)
            except (ValueError, OSError):
                continue
            if text is None or _text_hash(text) != receipt.get("sha256") or len(text) != receipt.get("characters"):
                continue
            functions = self._export_function_signatures(text)
            if not functions:
                continue
            entry = {"path": path, "sha256": _text_hash(text), "signature_only": True,
                     "full_source": False, "grants_full_read": False, "functions": []}
            for function in functions:
                candidate = {**entry, "functions": entry["functions"] + [function]}
                if _serialized_size(result + [candidate]) <= budget:
                    entry = candidate
            if entry["functions"]:
                result.append(entry)
            if len(result) >= 6:
                break
        return result

    def _resident_files(self, feature, context, state):
        """Fresh source beats obsolete plan names; never call a preview complete."""
        if self._configuration_feature(feature) and not state.get("focused_recovery"):
            return []
        paths = ["src/game/types.ts", "src/types.ts"]
        if state.get("focused_recovery"):
            target = self._focus_target(feature, context, state)
            if target and target not in paths:
                paths.append(target)
        result = []
        budget = min(10_000, self.context_chars // 3)
        for path in paths:
            try:
                text = self._read(path)
            except (ValueError, OSError):
                continue
            if text is None or len(text) > 8_000:
                continue
            entry = {"path": path, "sha256": _text_hash(text), "characters": len(text),
                     "content": text, "complete": True,
                     "exports": re.findall(r"\bexport\s+(?:declare\s+)?(?:interface|type|class|function|const|enum)\s+([A-Za-z_$][\w$]*)", text)}
            if _serialized_size(result + [entry]) <= budget:
                result.append(entry)
        return result

    def _prompt(self, feature, context, state, prefix):
        """Bound active context; full context and observations remain retrievable."""
        manifest = self.workspace.snapshot()
        resident = self._resident_files(feature, context, state)
        payload = {
            "feature": _preview(feature, min(6_000, self.context_chars // 6)), "feature_section": "__feature__",
            "context_sections": _preview({k: {"characters": len(_json(v)), "sha256": _hash(v)}
                                          for k, v in context.items()}, min(3_000, self.context_chars // 12)),
            "context_archive": prefix + ".context.json",
            "integration": {"owner": state["owner"], "workspace": str(self.workspace.root),
                            "mode": "serial_workspace_lock", "role": state["role"]},
            "steps": {"used": state["steps"], "limit": self.max_steps},
            "memory": _preview(state["memory"], min(5_000, self.context_chars // 10)),
            "read_receipts": [{**receipt, "ranges": receipt["ranges"][-8:],
                               "ranges_truncated": len(receipt["ranges"]) > 8}
                              for receipt in list(state.get("read_receipts", {}).values())[-max(1, min(3, self.context_chars // 16_000)):]],
            "reference_images": state.get("reference_images", []),
            "next_unwritten_owned_files": [path for path in feature.get("owned_paths", [])
                if not any(mark in path for mark in ("*", "?", "[")) and not self.workspace.resolve(path).is_file()],
            "workspace": _preview(manifest, min(3_000, self.context_chars // 12)),
            "latest_build": _preview(state.get("last_build"), min(2_000, self.context_chars // 16)),
            "context": {}, "recent_observations": [],
        }
        diagnostics = state.get("build_diagnostics", [])
        if diagnostics:
            payload["build_diagnostics"] = diagnostics
        if state.get("diagnostic_read_allowance"):
            payload["new_build_diagnostic"] = state["diagnostic_read_allowance"]
        if resident:
            payload["resident_files"] = resident
        signatures = self._read_function_signatures(state)
        if signatures:
            payload["read_function_signatures"] = signatures
        if state.get("focused_recovery"):
            payload["focused_recovery"] = {**state["focused_recovery"],
                "allowed_tools": ["write_file", "apply_patch", "build", "blocked"],
                "target_file": self._focus_target(feature, context, state),
                "instruction": "工作集为当前真实源码。现在提交一项具体改动；接口确实缺失则blocked列出名字/字段，禁止再读同一资料。"}
        if context.get("source_archive") == "specification.txt":
            payload["context_sections"]["__specification__"] = {
                "archive": "specification.txt", "retrieve": "read_context", "immutable_source": True}
        # Large specs never accumulate in the conversation. This is a small
        # project/feature packet plus a filesystem-style context catalog.
        protected = {"project_summary", "runtime_invariants"}
        configuration = self._configuration_feature(feature)
        for key in ("project_summary", "runtime_invariants", "approved_configuration", "continuation", "independent_code_review", "review_feedback", "validation_report", "interfaces", "blocked_feature",
                    "engine_contract", "production_contract", "architecture", "acceptance", "spec_excerpts", "source_index"):
            if key in context:
                if key == "runtime_invariants":
                    # Host supplies a compact contract, not a specification
                    # preview. Pin it in full through all packet compaction.
                    payload["context"][key] = context[key]
                    continue
                if key == "interfaces" and self.context_chars >= 28_000 and _serialized_size(context[key]) <= 8_000:
                    limit = 8_000
                    protected.add(key)
                elif key == "approved_configuration" and configuration and _serialized_size(context[key]) <= 8_000:
                    limit = 8_000
                    protected.add(key)
                elif key == "approved_configuration" and not configuration:
                    limit = 1_200
                else:
                    limit = 4_000 if key in {"approved_configuration", "spec_excerpts", "validation_report", "production_contract"} else 2_500
                payload["context"][key] = _preview(context[key], limit)
        latest = state["history"][-1:] or []
        reserve = min(14_000, _serialized_size(latest) + 600, self.context_chars - 3_000)
        while _serialized_size(payload) > self.context_chars - reserve and payload["context"]:
            removable = [key for key in payload["context"] if key not in protected]
            if not removable:
                break
            payload["context"].pop(removable[-1])
        # Make room before adding the latest page. Reducing metadata afterwards
        # cannot recover source text that was already cut from an observation.
        for key in ("context_sections", "workspace", "memory", "feature", "read_receipts", "latest_build", "reference_images"):
            excess = _serialized_size(payload) - (self.context_chars - reserve)
            if excess <= 0:
                break
            payload[key] = _preview(payload[key], max(128, _serialized_size(payload[key]) - excess - 100))
        history = state["history"][-self.history_limit:]
        for item in reversed(history):
            compact = {"step": item["step"], "tool": item["tool"], "path": item.get("path"),
                       "result_artifact": item["result_artifact"], "result": None}
            remaining = self.context_chars - _serialized_size(payload) - _serialized_size(compact) - 16
            if remaining < 256:
                break
            result = item["result"]
            budget = min(14_000, remaining)
            if isinstance(result, dict) and "content" in result and _serialized_size(result) > budget:
                # Keep source length and offsets at their original top level.
                # A preview wrapper's own character count must never masquerade
                # as the length of the source that the model is reading.
                view = {key: value for key, value in result.items() if key != "content"}
                view.update(content="", content_view_truncated=True,
                            observation_step=item["step"], retrieve_full_observation="read_observation")
                text = result["content"]
                low, high = 0, len(text)
                while low < high:
                    middle = (low + high + 1) // 2
                    view["content"] = text[:middle]
                    if _serialized_size(view) <= budget - 80:
                        low = middle
                    else:
                        high = middle - 1
                view["content"] = text[:low]
                view["shown_next_offset"] = result.get("offset", 0) + low
                compact["result"] = view
            else:
                compact["result"] = _preview(result, budget)
            payload["recent_observations"].insert(0, compact)
            if _serialized_size(payload) > self.context_chars:
                # Metadata itself can be unusually long. Keep the artifact
                # pointer and discard only this view, never the stored result.
                compact["result"] = {"truncated": True, "retrieve": "read_observation", "step": item["step"]}
                if _serialized_size(payload) > self.context_chars:
                    payload["recent_observations"].pop(0)
        # A context catalog can itself be huge. Keep a retrievable section list
        # and fail before issuing an oversized request rather than silently lose
        # the current feature and last observation.
        for key in ("context_sections", "workspace", "feature", "memory", "latest_build", "reference_images", "read_receipts"):
            excess = _serialized_size(payload) - self.context_chars
            if excess <= 0:
                break
            payload[key] = _preview(payload[key], max(128, _serialized_size(payload[key]) - excess - 100))
        if _serialized_size(payload) > self.context_chars:
            # Only fixed metadata is left; retain authoritative retrieval routes
            # rather than terminate a legitimate long-spec coding session.
            payload = {"feature_id": feature["id"], "feature_section": "__feature__",
                       "context_section": "__catalog__", "context_archive": prefix + ".context.json",
                       "owner": state["owner"], "role": state["role"], "steps": state["steps"],
                       "recent_observations": [{"step": item["step"], "tool": item["tool"],
                           "result_artifact": item["result_artifact"]} for item in history[-2:]],
                       "notice": "Context view compacted; retrieve exact feature, context or observation before editing."}
            if "runtime_invariants" in context:
                payload["context"] = {"runtime_invariants": context["runtime_invariants"]}
        # Full replacement permission is issued only for complete source that
        # actually survived packet budgeting, never for a hash-only receipt.
        for entry in payload.get("resident_files", []):
            state["full_reads"][entry["path"]] = entry["sha256"]
        return payload

    def _record_read(self, state, source, digest, length, start, end):
        receipt_key = source + ":" + digest
        receipts = state.setdefault("read_receipts", {})
        receipt = receipts.setdefault(receipt_key, {"source": source, "sha256": digest, "characters": length,
            "ranges": [], "last_offset": start, "next_offset": end, "next_unread_offset": 0, "reads": 0})
        previous_coverage = sum(right - left for left, right in receipt["ranges"])
        spans = sorted(receipt["ranges"] + [[start, end]])
        merged = []
        for left, right in spans:
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        cursor = 0
        for left, right in merged:
            if left > cursor:
                break
            cursor = max(cursor, right)
        receipt.update(ranges=merged, last_offset=start, next_offset=end, next_unread_offset=cursor, reads=receipt["reads"] + 1)
        counts = state.setdefault("read_counts", {})
        if sum(right - left for left, right in merged) > previous_coverage:
            for key in [key for key in counts if key.startswith(receipt_key + ":")]:
                counts.pop(key)
        repeat_key = receipt_key + ":" + str(start)
        counts[repeat_key] = counts.get(repeat_key, 0) + 1
        return receipt, counts[repeat_key]

    def _inherit_handoff(self, rid, state, context):
        handoff = context.get("continuation")
        if not isinstance(handoff, dict):
            return
        memory = handoff.get("memory")
        if isinstance(memory, str):
            state["memory"] = memory[:5_000]
        for prior in handoff.get("read_receipts") or []:
            if not isinstance(prior, dict):
                continue
            source = prior.get("source", "")
            try:
                if source.startswith("file:"):
                    full = self._read(source[5:])
                    if full is None:
                        continue
                elif source == "context:__specification__" and context.get("source_archive") == "specification.txt":
                    with self.store.path(rid, "specification.txt").open(encoding="utf-8", newline="") as handle:
                        full = handle.read()
                elif source.startswith("context:") and source[8:] in context:
                    value = context[source[8:]]
                    full = value if isinstance(value, str) else _json(value)
                else:
                    continue
                if prior.get("sha256") != _text_hash(full) or prior.get("characters") != len(full):
                    continue
                spans = prior.get("ranges", [])
                if not isinstance(spans, list) or any(not isinstance(span, list) or len(span) != 2 or
                        not all(isinstance(n, int) for n in span) or not 0 <= span[0] <= span[1] <= len(full) for span in spans):
                    continue
                for start, end in spans:
                    self._record_read(state, source, prior["sha256"], len(full), start, end)
                # Do not inherit write authorization: a fresh coding context must
                # still read an existing file before replacing its whole content.
            except (ValueError, TypeError, OSError):
                continue
        state["read_counts"] = {}

    def _build_diagnostics(self, report):
        """Only host build output can name locations; workspace resolves every path."""
        output = "\n".join(str(report.get(key, "")) for key in ("stdout", "stderr"))
        if not output.strip():
            output = "\n".join(str(stage.get(key, "")) for stage in report.get("stages", [])
                               if isinstance(stage, dict) for key in ("stdout", "stderr"))
        output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output)
        pattern = re.compile(r"(?P<path>[^\s():]+\.[cm]?[jt]sx?)(?:\((?P<line>\d+),(?P<column>\d+)\)|:(?P<line2>\d+):(?P<column2>\d+))(?P<message>[^\n]*)")
        candidates, seen = [], set()
        budget = min(6_000, self.context_chars // 3)
        for match in pattern.finditer(output):
            relative = match.group("path")
            try:
                path = Path(relative)
                if path.is_absolute():
                    relative = path.resolve().relative_to(Path(self.workspace.root).resolve()).as_posix()
                text = self._read(relative)
                if text is None:
                    continue
            except (ValueError, OSError):
                continue
            line = int(match.group("line") or match.group("line2"))
            column = int(match.group("column") or match.group("column2"))
            lines = text.splitlines()
            if not 1 <= line <= len(lines) or (relative, line, column) in seen:
                continue
            seen.add((relative, line, column))
            start, end = max(1, line - 10), min(len(lines), line + 10)
            excerpt = [{"line": number, "text": lines[number - 1]} for number in range(start, end + 1)]
            # Keep the error line when long neighbors would exhaust the view.
            while len(excerpt) > 1 and _serialized_size(excerpt) > 2_400:
                if abs(excerpt[0]["line"] - line) >= abs(excerpt[-1]["line"] - line):
                    excerpt.pop(0)
                else:
                    excerpt.pop()
            if _serialized_size(excerpt) > 2_400:
                offset = max(0, column - 601)
                excerpt = [{"line": line, "text": lines[line - 1][offset:offset + 1_200],
                            "column_offset": offset, "text_truncated": True}]
            entry = {"path": relative, "line": line, "column": column,
                     "message": match.group("message")[:1_200], "source_sha256": _text_hash(text),
                     "start_line": excerpt[0]["line"], "end_line": excerpt[-1]["line"], "excerpt": excerpt,
                     "coordinate_unit": "1-based lines and columns; offset is a separate character coordinate"}
            candidates.append(entry)
        # Deduplicate the complete output before any limit. One file's many
        # errors must not hide another file (e.g. StatusEffectSystem after four
        # ProjectilePool errors). Choose a first location per file, then extras.
        first, extra, paths = [], [], set()
        for entry in candidates:
            if entry["path"] in paths:
                extra.append(entry)
            else:
                first.append(entry)
                paths.add(entry["path"])
        diagnostics = []
        for entry in first + extra:
            if _serialized_size(diagnostics + [entry]) > budget:
                if entry in first:
                    entry = {**entry, "excerpt": [row for row in entry["excerpt"] if row["line"] == entry["line"]]}
                    entry["start_line"] = entry["end_line"] = entry["line"]
                if _serialized_size(diagnostics + [entry]) > budget:
                    continue
            diagnostics.append(entry)
            if len(diagnostics) >= 6:
                break
        return diagnostics

    def _ingest_diagnostics(self, result, state, source_sha256):
        diagnostics = self._build_diagnostics(result)
        fingerprint = _hash({"source_sha256": source_sha256, "diagnostics": diagnostics})
        fresh = bool(diagnostics and (fingerprint != state.get("diagnostic_fingerprint")
                     or state.get("diagnostic_policy_version") != 2))
        state["build_diagnostics"] = diagnostics
        if fresh:
            state["diagnostic_fingerprint"] = fingerprint
            state["diagnostic_policy_version"] = 2
            state["read_counts"] = {}
            state.pop("focused_recovery", None)
            state["diagnostic_read_allowance"] = {"remaining": 3, "observed_ranges": {},
                "reported_paths": sorted({row["path"] for row in diagnostics}), "fingerprint": fingerprint,
                "instruction": "可直接patch或最多3次有效读取任意安全源码及依赖接口；每次须有新来源/新区间，重复同页仍受限。"}
        elif not diagnostics:
            state.pop("diagnostic_read_allowance", None)
        return diagnostics, fresh

    def _hydrate_diagnostics(self, rid, prefix, state, prior):
        """A restart must not hide a trusted compiler result behind a focus gate."""
        candidates = []
        for build in (state.get("last_build"), prior.get("build") if isinstance(prior, dict) else None):
            if isinstance(build, dict) and build.get("source_sha256") == state["snapshot"]["sha256"] and build.get("report"):
                try:
                    saved = self.store.read_json(rid, build["report"])
                except (OSError, ValueError):
                    continue
                if isinstance(saved, dict):
                    candidates.append((saved, "run-artifact:" + build["report"]))
        cache = Path(self.workspace.root) / ".harness/build.json"
        if cache.is_file() and cache.stat().st_size <= 2_000_000:
            try:
                candidates.append((json.loads(cache.read_text()), "host-workspace:.harness/build.json"))
            except (OSError, ValueError):
                pass
        # Prefer a current successful external integration build over older
        # same-source compiler failures saved by an interrupted coding session.
        candidates.sort(key=lambda row: bool(isinstance(row[0], dict) and row[0].get("passed")), reverse=True)
        for report, origin in candidates:
            if not isinstance(report, dict) or report.get("source_sha256") != state["snapshot"]["sha256"]:
                continue
            if report.get("passed"):
                if not self._cached_build_valid(report):
                    continue
                name = prefix + ".verified-build-handoff.json"
                self.store.write(rid, name, {**report, "origin": origin})
                state["last_build"] = {"source_sha256": state["snapshot"]["sha256"], "passed": True, "report": name}
                state.pop("focused_recovery", None)
                state.pop("diagnostic_read_allowance", None)
                state["build_diagnostics"], state["read_counts"] = [], {}
                break
            diagnostics, fresh = self._ingest_diagnostics(report, state, state["snapshot"]["sha256"])
            if diagnostics:
                self.store.write(rid, prefix + ".diagnostic-handoff.json", {
                    "origin": origin, "report_sha256": _hash(report), "source_sha256": state["snapshot"]["sha256"],
                    "diagnostics": diagnostics, "fresh": fresh})
                break

    def _cached_build_valid(self, report):
        if not report.get("entrypoint") or not report.get("artifact_manifest"):
            return False
        root = Path(self.workspace.root) / "dist"
        if root.is_symlink():
            return False
        try:
            for item in report["artifact_manifest"]:
                path = root / item["path"]
                if not path.resolve().is_relative_to(root.resolve()) or any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
                    return False
                if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    return False
            return report["entrypoint"] in {item["path"] for item in report["artifact_manifest"]}
        except (OSError, ValueError, TypeError, KeyError):
            return False

    async def _build(self, rid, prefix, state):
        previous = state.get("last_build") or {}
        before = self.workspace.snapshot()
        result = await self.workspace.build(timeout=self.build_timeout)
        after = self.workspace.snapshot()
        if before["sha256"] != after["sha256"]:
            result = {**result, "passed": False, "host_error": "source changed during build"}
        if result.get("passed") and not (result.get("entrypoint") and result.get("artifact_manifest")):
            result = {**result, "passed": False, "host_error": "build returned no verifiable artifact manifest"}
        if before["sha256"] == after["sha256"]:
            diagnostics, fresh = self._ingest_diagnostics(result, state, after["sha256"])
        else:
            diagnostics, fresh = [], False
        verified = result.get("passed") is True and not (previous.get("passed") is True and
            previous.get("source_sha256") == after["sha256"])
        if verified:
            state.pop("focused_recovery", None)
            state.pop("diagnostic_read_allowance", None)
            state["read_counts"] = {}
        result = {**result, "diagnostics": diagnostics, "diagnostic_progress": fresh, "build_progress": verified}
        state["last_build"] = {"source_sha256": after["sha256"], "passed": result.get("passed") is True,
                               "report": prefix + f".build-{state['steps']:04d}.json"}
        self.store.write(rid, state["last_build"]["report"], result)
        return result

    async def _execute(self, rid, prefix, action, prepared, feature, context, state):
        self._check_cancelled(rid)
        if action.tool == "list_files":
            return {"files": self.workspace.list_files(), "snapshot": self.workspace.snapshot()["sha256"]}
        if action.tool in {"read_file", "read_lines", "read_context", "read_observation"}:
            file_read = action.tool in {"read_file", "read_lines"}
            if file_read:
                full = self._read(action.path)
                if full is None:
                    raise FileNotFoundError(action.path)
            elif action.tool == "read_context":
                if action.section == "__feature__":
                    value = feature
                elif action.section == "__catalog__":
                    value = {key: {"characters": len(_json(value)), "sha256": _hash(value)}
                             for key, value in context.items()}
                elif action.section == "__specification__" and context.get("source_archive") == "specification.txt":
                    with self.store.path(rid, "specification.txt").open(encoding="utf-8", newline="") as handle:
                        value = handle.read()
                elif action.section in context:
                    value = context[action.section]
                else:
                    raise ValueError("unknown context section; see context_sections")
                full = value if isinstance(value, str) else _json(value)
            else:
                if action.step < 1 or action.step >= state["steps"]:
                    raise ValueError("observation step must refer to an earlier completed action")
                value = self.store.read_json(rid, prefix + f".step-{action.step:04d}.result.json")
                if value is None:
                    raise ValueError("observation is not available")
                full = _json(value)
            # Bound the actual page so the newest observation fits intact.
            offset, end = action.offset, len(full)
            if file_read and action.start_line is not None:
                lines = full.splitlines(keepends=True)
                requested_end = action.end_line or action.start_line + 39
                offset = sum(map(len, lines[:action.start_line - 1]))
                end = sum(map(len, lines[:requested_end]))
            cap = min(action.limit, 12_000 if file_read else 6_000)
            low, high = 0, min(cap, max(0, end - offset))
            page_budget = min(12_000, self.context_chars // 2) - 1_000
            while low < high:
                middle = (low + high + 1) // 2
                candidate = full[offset:offset + middle]
                if _serialized_size(candidate) <= page_budget:
                    low = middle
                else:
                    high = middle - 1
            text = full[offset:offset + low]
            source = ("file:" + action.path if file_read else
                      "context:" + action.section if action.tool == "read_context" else "observation:" + str(action.step))
            digest = _text_hash(full)
            receipt, repeats = self._record_read(state, source, digest, len(full), offset, offset + len(text))
            result = {"content": text, "offset": offset, "next_offset": offset + len(text),
                      "characters": len(full), "truncated": offset + len(text) < len(full),
                      "sha256": digest, "source": source, "page_characters": len(text), "repeat_count": repeats}
            if file_read:
                start_line = full.count("\n", 0, offset) + 1
                end_line = start_line + text.count("\n") - int(text.endswith("\n")) if text else start_line - 1
                result.update(start_line=start_line, end_line=end_line, total_lines=len(full.splitlines()),
                              coordinate_unit="offset/next_offset are characters; start_line/end_line are 1-based lines")
                if action.start_line is not None:
                    result.update(requested_start_line=action.start_line, requested_end_line=requested_end,
                                  selected_lines_complete=offset + len(text) >= end)
            if repeats >= 2:
                result["read_guidance"] = "此页已读取；按真实 next_offset 继续下一页，或根据当前feature已有章节开始实现，勿重复通读整本规格。"
                if receipt["next_unread_offset"] >= len(full) and self._focus_target(feature, context, state):
                    state["focused_recovery"] = {"reason": "complete source repeatedly reread without code progress",
                        "source": source, "sha256": digest, "armed_at_step": state["steps"]}
                    result["next_action"] = "host will supply the complete shared types and interfaces; next proposal must write/patch/build/blocked"
            if repeats >= 5:
                state["status"] = "needs_work"
                state["stop_reason"] = "non_progress"
                state["summary"] = "连续重复读取相同源位置5次且无阅读进展；已暂停当前feature以避免消耗预算，检查read_receipts和next_offset后继续。"
                result["non_progress_stop"] = True
            if file_read and receipt["next_unread_offset"] >= len(full):
                state["full_reads"][action.path] = result["sha256"]
            if file_read and state.get("diagnostic_read_allowance"):
                allowance = state["diagnostic_read_allowance"]
                ranges = allowance.setdefault("observed_ranges", {}).setdefault(source + ":" + digest, [])
                start, stop = offset, offset + len(text)
                # Count only ranges with new visible source information; a
                # different spelling or overlapping read cannot reset budget.
                covered = 0
                spans = sorted((max(start, left), min(stop, right)) for left, right in ranges
                               if right > start and left < stop)
                cursor = start
                for left, right in spans:
                    covered += max(0, right - max(cursor, left))
                    cursor = max(cursor, right)
                progress = len(text) > covered
                if progress:
                    ranges.append([start, stop])
                    allowance["remaining"] -= 1
                else:
                    state["focused_recovery"] = {"reason": "diagnostic source page repeated without new information",
                        "source": source, "sha256": digest, "armed_at_step": state["steps"]}
                result["diagnostic_read_progress"] = progress
                result["diagnostic_reads_remaining"] = allowance["remaining"]
                if allowance["remaining"] <= 0 or state.get("focused_recovery"):
                    state.pop("diagnostic_read_allowance", None)
                    state.setdefault("focused_recovery", {"reason": "diagnostic source/dependency read budget supplied; submit the repair next",
                        "source": source, "sha256": digest, "armed_at_step": state["steps"]})
            return result
        if action.tool in {"write_file", "apply_patch", "delete_file"}:
            self._owned(action.path, feature)
            current = self._read(action.path)
            current_hash = None if current is None else _text_hash(current)
            # Journaling intent before mutation allows replay after a crash
            # between the write and checkpoint commit, without applying twice.
            if current_hash == prepared["after_sha256"]:
                state["changed_files"] = sorted(set(state["changed_files"]) | {action.path})
                state["full_reads"].pop(action.path, None)
                state["last_build"] = None
                state["build_diagnostics"] = []
                state.pop("diagnostic_read_allowance", None)
                state.pop("acceptance", None)
                state["read_counts"] = {}
                return {"path": action.path, "sha256": current_hash, "recovered_already_applied": True}
            if current_hash != prepared["before_sha256"]:
                raise ValueError("workspace drift since prepared action; refusing to overwrite another owner")
            if action.tool == "delete_file":
                self.workspace.delete_file(action.path, expected_sha256=prepared["before_sha256"])
            elif action.tool == "write_file":
                self.workspace.write_file(action.path, action.content)
            else:
                self.workspace.apply_patch(action.path, action.old, action.new,
                                           expected_sha256=prepared["before_sha256"])
            written = self._read(action.path)
            actual_hash = None if written is None else _text_hash(written)
            if actual_hash != prepared["after_sha256"]:
                raise RuntimeError("written content digest differs from the proposed action")
            state["changed_files"] = sorted(set(state["changed_files"]) | {action.path})
            state["full_reads"].pop(action.path, None)
            state["last_build"] = None
            state["build_diagnostics"] = []
            state.pop("diagnostic_read_allowance", None)
            state.pop("acceptance", None)
            state["read_counts"] = {}
            return {"path": action.path, "bytes": len((written or "").encode()), "sha256": actual_hash,
                    "deleted": action.tool == "delete_file"}
        if action.tool == "build":
            return await self._build(rid, prefix, state)
        if action.tool == "note":
            return {"recorded": True, "summary": action.summary, "evidence": False}
        if action.tool == "blocked":
            state["status"] = "blocked"
            state["stop_reason"] = "blocked"
            state["summary"] = action.summary
            return {"blocked": True, "reason": action.summary}
        if action.tool == "finish":
            gaps = self._owned_output_gaps(feature)
            if gaps:
                return {"accepted": False, "reason": "declared owned outputs are missing or empty", **gaps,
                        "acceptance": {"status": "not_run", "passed": False}}
            snapshot = self.workspace.snapshot()
            last = state.get("last_build") or {}
            if not last.get("passed") or last.get("source_sha256") != snapshot["sha256"]:
                report = await self._build(rid, prefix, state)
                if report.get("passed") is not True:
                    return {"accepted": False, "reason": "latest source did not build", "build": report}
            acceptance = {"status": "not_run", "passed": False,
                          "reason": "feature build is not independent game acceptance"}
            if self.verifier:
                acceptance = self.verifier(feature, self.workspace, state["last_build"])
                if inspect.isawaitable(acceptance):
                    acceptance = await acceptance
                if not isinstance(acceptance, dict):
                    acceptance = {"passed": False, "status": "invalid_verifier_result", "checks": []}
                state["acceptance"] = acceptance
                self.store.write(rid, prefix + f".acceptance-{state['steps']:04d}.json", acceptance)
                if acceptance.get("passed") is not True:
                    return {"accepted": False, "reason": "host acceptance verifier failed", "acceptance": acceptance}
                if not acceptance.get("checks"):
                    return {"accepted": False, "reason": "verifier produced no checks/evidence", "acceptance": acceptance}
            state["status"] = "implemented"
            state["stop_reason"] = "implemented"
            state["summary"] = action.summary
            state["acceptance"] = acceptance
            return {"accepted": True, "status": "implemented", "acceptance": acceptance,
                    "build": state["last_build"]}
        raise ValueError("unsupported action")

    def _result(self, prefix, state):
        return {"feature_id": state["feature_id"], "status": state["status"], "owner": state["owner"],
                "stop_reason": state.get("stop_reason", "unknown"), "memory": state["memory"],
                "read_receipts": list(state.get("read_receipts", {}).values()),
                "role": state["role"], "workspace": str(self.workspace.root), "integration": "serial_workspace_lock",
                "summary": state.get("summary", ""), "steps": state["steps"],
                "changed_files": state["changed_files"], "build": state.get("last_build"),
                "acceptance": state.get("acceptance", {"status": "not_run", "passed": False}),
                "checkpoint": prefix + ".checkpoint.json", "source_sha256": state["snapshot"]["sha256"]}

    async def run_feature(self, rid: str, feature: dict, context: dict, *, role: str = "build",
                          stage: str | None = None, owner: str | None = None) -> dict:
        if not isinstance(feature.get("id"), str) or not feature["id"].strip():
            raise ValueError("feature must have a nonempty string id")
        if not isinstance(context, dict):
            raise TypeError("context must be a retrievable section mapping")
        fid = feature["id"]
        prefix, stage = self._prefix(fid), stage or "project.feature." + fid
        owner = owner or role + ":" + fid
        reference_images = [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                            for path in self.images]
        images_digest = _hash(reference_images)
        # Planner state is mutable; the actual requirement and ownership are not.
        immutable = {k: v for k, v in feature.items() if k not in {"status", "progress", "updated", "result"}}
        with self._workspace_lock(rid, fid, owner):
            self._check_cancelled(rid)
            state = self.store.read_json(rid, prefix + ".checkpoint.json")
            snapshot = self.workspace.snapshot()
            if state and (state["feature_fingerprint"] != _hash(immutable)
                          or state["workspace"] != str(self.workspace.root)):
                raise ValueError("feature requirement/ownership changed; create a new feature revision")
            if state is None:
                state = {"version": 1, "feature_id": fid, "feature_fingerprint": _hash(immutable),
                         "context_sha256": _hash(context), "workspace": str(self.workspace.root),
                         "owner": owner, "role": role, "steps": 0, "history": [], "memory": "",
                         "changed_files": [], "full_reads": {}, "pending": None, "last_build": None,
                         "read_receipts": {}, "read_counts": {},
                         "reference_images": reference_images, "images_seen": None,
                         "snapshot": snapshot, "status": "working", "created": time.time()}
                self._inherit_handoff(rid, state, context)
            else:
                self.store.event(rid, stage, "coding_resume", {"feature": fid, "steps": state["steps"],
                                                            "owner": owner, "pending": bool(state["pending"])})
                if state["owner"] != owner or state["role"] != role:
                    raise ValueError("feature owner/role changed; explicit new revision is required")
                drift = state["snapshot"]["sha256"] != snapshot["sha256"]
                updated_context = state["context_sha256"] != _hash(context)
                updated_images = state.get("reference_images", []) != reference_images
                if drift and not state["pending"]:
                    state["last_build"], state["full_reads"] = None, {}
                    self.store.event(rid, stage, "coding_workspace_drift", {"feature": fid,
                        "previous": state["snapshot"]["sha256"], "current": snapshot["sha256"]})
                outputs_incomplete = state["status"] == "implemented" and bool(self._owned_output_gaps(feature))
                if state["status"] == "implemented" and not drift and not updated_context and not updated_images and not outputs_incomplete:
                    return self._result(prefix, state)
                if drift or updated_context or updated_images or outputs_incomplete:
                    state.pop("acceptance", None)
                state["status"] = "working"
                state.pop("stop_reason", None)
                state["snapshot"], state["context_sha256"] = snapshot, _hash(context)
                state["reference_images"] = reference_images
            self.store.write(rid, prefix + ".context.json", context)
            self.store.write(rid, prefix + ".feature.json", immutable)
            feedback = context.get("review_feedback")
            prior = context.get("continuation") or (feedback.get("failed_session") if isinstance(feedback, dict) else None) or {}
            if (state["steps"] == 0 and isinstance(prior, dict) and prior.get("stop_reason") == "non_progress"
                    and self._focus_target(feature, context, state)):
                state["focused_recovery"] = {"reason": "previous session stopped for repeated reads",
                    "armed_at_step": 0, "prior_stop_reason": "non_progress"}
            if not state.get("pending"):
                self._hydrate_diagnostics(rid, prefix, state, prior)
            self._save(rid, prefix, state)
            self.store.event(rid, stage, "coding_start", {"feature": fid, "owner": owner, "role": role,
                "workspace": str(self.workspace.root), "integration": "serial_workspace_lock"})
            try:
                while state["steps"] < self.max_steps or state["pending"]:
                    self._check_cancelled(rid)
                    if not state["pending"]:
                        packet = self._prompt(feature, context, state, prefix)
                        number = state["steps"] + 1
                        self.store.write(rid, prefix + f".step-{number:04d}.input.json", packet)
                        image_args = {"images": self.images} if self.images and state.get("images_seen") != images_digest else {}
                        action_schema = (FocusedCodingAction if state.get("focused_recovery") else
                                         DiagnosticCodingAction if state.get("diagnostic_read_allowance") else CodingAction)
                        action = await self.client.structured(rid, stage + f".step-{number:04d}", role,
                            CODING_INSTRUCTION, packet, action_schema, max_tokens=16_000, **image_args)
                        state["images_seen"] = images_digest
                        # Preserve the exact validated proposal even if host
                        # preparation rejects it. A failed tool is an observation.
                        self.store.write(rid, prefix + f".step-{number:04d}.action.json", action.model_dump())
                        try:
                            prepared = self._prepare(action, feature, state)
                            preparation_error = None
                        except (ValueError, OSError) as exc:
                            prepared, preparation_error = {}, str(exc)
                        state["steps"] = number
                        state["pending"] = {"action": action.model_dump(), "prepared": prepared,
                                            "preparation_error": preparation_error,
                                            "focused_recovery": bool(state.get("focused_recovery")),
                                            "diagnostic_mode": bool(state.get("diagnostic_read_allowance")),
                                            "before_snapshot_sha256": state["snapshot"]["sha256"]}
                        self._save(rid, prefix, state)
                    pending = state["pending"]
                    action = CodingAction.model_validate(pending["action"])
                    self.store.event(rid, stage, "coding_action", {"feature": fid, "step": state["steps"],
                                                                  "tool": action.tool, "path": action.path})
                    before_action_sha = pending.get("before_snapshot_sha256", state["snapshot"]["sha256"])
                    try:
                        if pending["preparation_error"]:
                            raise ValueError(pending["preparation_error"])
                        result = await self._execute(rid, prefix, action, pending["prepared"],
                                                     feature, context, state)
                        result = {"ok": True, **result}
                    except (ValueError, OSError, RuntimeError) as exc:
                        result = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
                    artifact = prefix + f".step-{state['steps']:04d}.result.json"
                    self.store.write(rid, artifact, result)
                    if action.memory:
                        state["memory"] = action.memory
                    state["history"].append({"step": state["steps"], "tool": action.tool, "path": action.path,
                                             "result_artifact": artifact, "result": result})
                    # Only the recent window lives inside the checkpoint. Raw
                    # historical observations remain independently addressable.
                    state["history"] = state["history"][-self.history_limit:]
                    state["snapshot"] = self.workspace.snapshot()
                    if pending.get("focused_recovery") or pending.get("diagnostic_mode"):
                        changed = state["snapshot"]["sha256"] != before_action_sha
                        self.store.event(rid, stage, "focused_recovery_action", {"feature": fid,
                            "step": state["steps"], "tool": action.tool, "ok": result["ok"],
                            "source_changed": changed, "diagnostic_progress": result.get("diagnostic_progress", False)})
                        if result["ok"] and (changed or result.get("diagnostic_progress") or result.get("build_progress")):
                            state.pop("focused_recovery", None)
                        elif pending.get("diagnostic_mode") and result["ok"] and action.tool in {"read_file", "read_lines"}:
                            pass  # One precise read was allowed; the next proposal is the concrete repair.
                        elif state["status"] != "blocked":
                            state["status"], state["stop_reason"] = "needs_work", "non_progress"
                            state["summary"] = ("focused recovery completed one action without verified source progress; "
                                "preserve the concrete tool result for dependency repair instead of reopening a read loop")
                    state["pending"] = None
                    self._save(rid, prefix, state)
                    self.store.event(rid, stage, "coding_observation", {"feature": fid, "step": state["steps"],
                        "tool": action.tool, "ok": result["ok"], "artifact": artifact})
                    if state["status"] in {"implemented", "blocked", "needs_work"}:
                        break
                if state["status"] == "working":
                    state["status"] = "needs_work"
                    state["stop_reason"] = "action_limit"
                    state["summary"] = "feature action limit reached; durable state is available for a larger explicit session budget"
                self._save(rid, prefix, state)
                result = self._result(prefix, state)
                self.store.write(rid, prefix + ".result.json", result)
                self.store.event(rid, stage, "coding_complete", {"feature": fid, "status": result["status"],
                                                              "steps": state["steps"]})
                return result
            except BaseException:
                # Keep pending intent for idempotent crash/cancel recovery. Model
                # output truncation and provider failures never become success.
                self._save(rid, prefix, state)
                raise
