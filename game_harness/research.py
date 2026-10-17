"""Harness adapter to the durable WebMCP research service; not the material portal."""
import hashlib
import json
from urllib.parse import quote, urlsplit

import httpx
from .schemas import ResearchReference


class ResearchError(Exception):
    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class ResearchClient:
    def __init__(self, settings, transport=None):
        self.url = settings.research_url.rstrip("/")
        self.token = settings.research_token
        self.transport = transport
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
                raise ValueError("WEBMCP_RESEARCH_URL must be an HTTP(S) origin without credentials or a path")
            if len(self.token) < 16:
                raise ValueError("WEBMCP_RESEARCH_TOKEN must match the research service token (at least 16 characters)")

    @property
    def configured(self):
        return bool(self.url)

    async def request(self, method, path, payload=None):
        if not self.configured:
            raise ResearchError("RESEARCH_NOT_CONFIGURED", "尚未配置 WebMCP 研究服务。", 503)
        # Only our route handlers construct path; browser callers cannot supply upstream URLs or headers.
        if not path.startswith("/api/") or method not in {"GET", "POST"}:
            raise ValueError("Unsupported research operation")
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                async with client.stream(method, self.url + path,
                        headers={"Authorization": "Bearer " + self.token}, json=payload if method == "POST" else None) as response:
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 8 * 1024 * 1024:
                            raise ResearchError("INVALID_RESEARCH_RESPONSE", "研究响应超过大小限制。")
                        chunks.append(chunk)
                    result = json.loads(b"".join(chunks))
                    if response.status_code >= 400:
                        if response.status_code in {401, 403}:
                            raise ResearchError("RESEARCH_AUTH_FAILED", "研究服务的后端口令配置不匹配。")
                        error = result.get("error", {}) if isinstance(result, dict) else {}
                        raise ResearchError(str(error.get("code", "RESEARCH_ERROR")),
                            str(error.get("message", "研究请求失败。"))[:1500], response.status_code)
                    if not 200 <= response.status_code < 300 or not isinstance(result, (dict, list)):
                        raise ResearchError("INVALID_RESEARCH_RESPONSE", "研究服务返回了不支持的响应。")
                    return result
        except httpx.TimeoutException:
            raise ResearchError("RESEARCH_TIMEOUT", "研究服务请求超时；请用相同 requestId 重试写入。", 504) from None
        except httpx.HTTPError:
            raise ResearchError("RESEARCH_UNAVAILABLE", "无法连接研究服务。", 503) from None
        except (ValueError, UnicodeError):
            raise ResearchError("INVALID_RESEARCH_RESPONSE", "研究服务未返回有效 JSON。") from None

    async def handoff(self, reference, requested_engine="auto"):
        """Pin a blueprint from one atomic run snapshot, never mix reads of different revisions."""
        state = await self.request("GET", "/api/runs/" + quote(reference.runId, safe=""))
        if not isinstance(state, dict) or state.get("id") != reference.runId:
            raise ResearchError("INVALID_RESEARCH_RESPONSE", "研究任务关联不匹配。")
        if state.get("revision") != reference.projectRevision:
            raise ResearchError("STALE_REVISION", "研究修订已改变，请刷新后重新检查。", 409)
        blueprints = state.get("blueprints")
        if not isinstance(blueprints, list) or not blueprints:
            raise ResearchError("NOT_READY", "当前修订尚无研究蓝图。", 409)
        blueprint = blueprints[-1]
        if not isinstance(blueprint, dict) or blueprint.get("projectRevision") != reference.projectRevision or blueprint.get("version") != reference.blueprintVersion:
            raise ResearchError("STALE_BLUEPRINT", "蓝图版本已改变或尚未完成，请刷新。", 409)
        blockers = []
        plan = blueprint.get("plan", {})
        if not isinstance(plan, dict) or plan.get("engine") not in {"html", "phaser", "babylon"}:
            raise ResearchError("INVALID_RESEARCH_RESPONSE", "蓝图缺少有效的引擎约束。")
        if state.get("status") != "completed" or blueprint.get("readiness") != "ready":
            blockers.append("research_not_ready: 当前蓝图为 partial；研究完成不代表可生成。")
        if plan["engine"] != "html":
            blockers.append("engine_adapter_not_installed:" + plan["engine"])
        if requested_engine not in {"auto", "canvas", "dom"}:
            blockers.append("unsupported_builder_engine:" + requested_engine)
        candidates = blueprint.get("candidates", [])
        mechanics = plan.get("mechanics", [])
        if not isinstance(candidates, list) or not isinstance(mechanics, list) or not mechanics:
            raise ResearchError("INVALID_RESEARCH_RESPONSE", "蓝图缺少玩法契约或候选列表。")
        if blueprint.get("gaps"):
            blockers.append("unresolved_research_gaps")
        if any(not isinstance(c, dict) or c.get("isMock") is not False for c in candidates):
            blockers.append("synthetic_or_unknown_source_evidence")
        for mechanic in mechanics:
            if not isinstance(mechanic, dict) or not isinstance(mechanic.get("id"), str):
                raise ResearchError("INVALID_RESEARCH_RESPONSE", "玩法契约格式无效。")
            if not any(isinstance(c, dict) and c.get("mechanicId") == mechanic["id"] and
                       c.get("isMock") is False and c.get("evidence") == "behavior_verified" and
                       c.get("projectRevision") == reference.projectRevision for c in candidates):
                blockers.append("unverified_mechanic:" + mechanic["id"])
        digest = hashlib.sha256(json.dumps(blueprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {"reference": reference.model_dump(), "projectId": state.get("projectId"),
                "eligible": not blockers, "blockers": blockers, "sha256": digest, "blueprint": blueprint,
                "scope": "研究到 Builder 的兼容性检查；不是游戏验收。"}
