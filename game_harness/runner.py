import asyncio
import hashlib
import json
import re
import time

from .agents import GAME_CONTRACT
from .material_portal import embed_asset_documents, prepare_asset_documents
from .schemas import Brief, Design, Review


class Harness:
    def __init__(self, settings, store, agents, retriever, validator):
        self.settings, self.store, self.agents = settings, store, agents
        self.retriever, self.validator = retriever, validator
        self.active = {}
        self.workers = []
        self.stopping = False

    async def start(self):
        self.store.recover()
        self.workers = [asyncio.create_task(self.worker()) for _ in range(self.settings.concurrency)]

    async def stop(self):
        self.stopping = True
        for task in list(self.active.values()) + self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, *list(self.active.values()), return_exceptions=True)

    def cancel(self, rid):
        run = self.store.get(rid)
        if run["status"] in {"queued", "running"}:
            self.store.update(rid, status="cancelled", stage="cancelled")
            self.store.event(rid, "cancelled", "cancelled", {"message": "用户取消任务"})
            if rid in self.active:
                self.active[rid].cancel()

    async def worker(self):
        while not self.stopping:
            rid = self.store.claim()
            if not rid:
                await asyncio.sleep(.5)
                continue
            self.active[rid] = asyncio.create_task(self.execute(rid))
            try:
                await self.active[rid]
            except asyncio.CancelledError:
                if self.stopping:
                    return
            finally:
                self.active.pop(rid, None)

    def stage(self, rid, name):
        self.store.update(rid, stage=name)
        self.store.event(rid, name, "stage_start", {"message": name})

    async def execute(self, rid):
        try:
            # A persisted deadline includes downtime after crashes/restarts.
            starts = [e["time"] for e in self.store.events(rid) if e["kind"] == "execution_started"]
            started = starts[0] if starts else time.time()
            if not starts:
                self.store.event(rid, "execution", "execution_started", {})
            remaining = self.settings.run_timeout - (time.time() - started)
            if remaining <= 0:
                raise asyncio.TimeoutError()
            start = time.monotonic()
            try:
                await asyncio.wait_for(self.pipeline(rid), timeout=remaining)
            finally:
                self.store.event(rid, "execution", "execution_elapsed", {"seconds": round(time.monotonic() - start, 3)})
        except asyncio.CancelledError:
            # On shutdown leave running state for durable recovery; explicit cancel already persisted.
            raise
        except Exception as exc:
            message = "本任务达到总运行时限" if isinstance(exc, asyncio.TimeoutError) else str(exc)[:1500]
            self.store.update(rid, status="failed", error=message)
            self.store.event(rid, "failed", "failed", {"message": message})

    async def checkpoint(self, rid, name, role, instruction, data, schema, images=None):
        saved = self.store.read_json(rid, name + ".json")
        metadata = {"data": data, "model": self.settings.role_model(role), "instruction": instruction,
                    "reasoning_effort": self.settings.reasoning_effort,
                    "images": [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in images or []]}
        fingerprint = hashlib.sha256(json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        completed = self.store.read_json(rid, name + ".checkpoint.json")
        saved_digest = hashlib.sha256(json.dumps(saved, ensure_ascii=False, sort_keys=True).encode()).hexdigest() if saved is not None else None
        if saved is not None and completed and completed.get("fingerprint") == fingerprint and completed.get("output_sha256") == saved_digest:
            self.store.event(rid, name, "checkpoint_reused", {"message": "复用已完成检查点"})
            return schema.model_validate(saved)
        self.stage(rid, name)
        self.store.write(rid, name + ".input.json", {"fingerprint": fingerprint, **metadata})
        result = await self.agents.structured(rid, name, role, instruction, data, schema, images=images)
        self.store.write(rid, name + ".json", result.model_dump())
        output_digest = hashlib.sha256(json.dumps(result.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.store.write(rid, name + ".checkpoint.json", {"fingerprint": fingerprint, "output_sha256": output_digest})
        self.store.event(rid, name, "stage_done", {"artifact": name + ".json"})
        return result

    async def pipeline(self, rid):
        request = self.store.get(rid)["request"]
        brief = await self.checkpoint(rid, "brief", "rewrite",
            "你是需求改写代理。将用户的一句话变成可实现且可验收的游戏需求；保留原意和具体数值，补充核心循环、操作、胜负与横竖屏要求。"
            "user_requirements 仅列用户描述或 engine 参数明确要求，不得把系统默认要求归为用户原话。"
            "system_requirements 列本系统的简体中文、离线、横竖屏和单文件默认约束。"
            "没有原型时不要虚构已验证来源。所有新增数值与规则放 assumptions。不要向用户反复索取非关键细节。",
            request, Brief)
        self.stage(rid, "retrieval")
        evidence = self.store.read_json(rid, "retrieval.json")
        if evidence is None:
            evidence = await self.retriever.search(brief.rewritten_prompt, run_id=rid)
            self.store.write(rid, "retrieval.json", evidence)
        self.store.event(rid, "retrieval", "stage_done", {"ids": [d["id"] for d in evidence["documents"]], "warnings": evidence["warnings"]})
        # Keep downloaded bundles only in the persisted evidence. Models receive
        # verified metadata and compact asset:// references, never image bytes.
        model_documents = prepare_asset_documents(evidence["documents"])
        model_evidence = {**evidence, "documents": model_documents}
        design = await self.checkpoint(rid, "design", "design",
            "你是游戏设计与引擎路由代理。动作游戏选 canvas，棋盘/文字/卡牌选 dom；只支持这两个已安装的离线适配器。"
            "若用户锁定 engine 必须遵守。制定具体数值规则、胜负、中文视觉风格和移动布局。"
            "knowledge_ids 只可引用提供的文档 ID。不要扩展用户未要求的大型系统。设计要适合单文件游戏。",
            {"request": request, "brief": brief.model_dump(), "retrieval": model_evidence}, Design)
        if request["engine"] != "auto" and design.engine != request["engine"]:
            raise RuntimeError("设计违反用户的引擎锁定")
        if not set(design.knowledge_ids) <= {d["id"] for d in evidence["documents"]}:
            raise RuntimeError("设计引用了未检索到的知识 ID")
        context = {"brief": brief.model_dump(), "design": design.model_dump(), "retrieved_knowledge": model_documents}
        for version in range(1, self.settings.max_repairs + 2):
            path = self.store.path(rid, f"v{version}.html")
            if not path.exists():
                self.stage(rid, "build" if version == 1 else "repair")
                data = context
                if version > 1:
                    previous_source = self.store.path(rid, f"v{version - 1}.source.html")
                    previous_path = previous_source if previous_source.exists() else self.store.path(rid, f"v{version - 1}.html")
                    data = {**context, "previous_html": previous_path.read_text(encoding="utf-8"),
                            "test_report": self.store.read_json(rid, f"v{version - 1}.report.json"),
                            "qa_review": self.store.read_json(rid, f"v{version - 1}.review.json")}
                role = "build" if version == 1 else "repair"
                self.store.write(rid, f"v{version}.build-input.json", {"role": role, "model": self.settings.role_model(role),
                    "reasoning_effort": self.settings.reasoning_effort, "data": data})
                instruction = ("你是资深游戏开发代理，按设计实现完整可玩的中文游戏。" if version == 1 else
                               "你是游戏修复代理，按实测报告和 QA 缺陷修复全部严重问题，保留已正确实现的玩法。返回完整修复文件。")
                html = await self.agents.complete(rid, role, role, instruction + GAME_CONTRACT, data, max_tokens=16000)
                html = re.sub(r"^```(?:html)?\s*|\s*```$", "", html.strip())
                self.store.write(rid, f"v{version}.source.html", html)
                asset_ids = list(dict.fromkeys(re.findall(r"asset://([A-Za-z0-9_-]+)", html)))
                html = embed_asset_documents(html, evidence["documents"])
                self.store.write(rid, f"v{version}.asset-usage.json", {"asset_ids": asset_ids,
                    "count": len(asset_ids), "embedded_html_bytes": len(html.encode("utf-8"))})
                self.store.write(rid, f"v{version}.html", html)
                self.store.event(rid, role, "stage_done", {"version": version, "artifact": f"v{version}.html",
                    "asset_count": len(asset_ids)})
            html = path.read_text(encoding="utf-8")
            self.stage(rid, "browser_test")
            report = self.store.read_json(rid, f"v{version}.report.json")
            digest = hashlib.sha256(html.encode()).hexdigest()
            validator_version = getattr(self.validator, "version", "custom-v1")
            if report is None or report.get("sha256") != digest or report.get("infrastructure_error") or report.get("validator_version") != validator_version:
                try:
                    report = await asyncio.wait_for(self.validator.validate(rid, version, html), timeout=self.settings.browser_timeout)
                except asyncio.TimeoutError:
                    report = {"sha256": digest, "passed": False, "static_errors": [], "viewports": [],
                              "browser_errors": ["浏览器测试超时，请检查阻塞主线程或无法终止的游戏逻辑"],
                              "scope": "浏览器测试超时，未取得完整实测证据"}
                report["validator_version"] = validator_version
                self.store.write(rid, f"v{version}.report.json", report)
            self.store.event(rid, "browser_test", "stage_done", {"version": version, "passed": report["passed"]})
            if report.get("infrastructure_error"):
                raise RuntimeError("浏览器运行环境异常：" + report["infrastructure_error"])
            screenshots = [self.store.path(rid, v["screenshot"]) for v in report["viewports"] if v.get("screenshot")] if self.settings.vision_qa else []
            review = await self.checkpoint(rid, f"v{version}.review", "qa",
                "你是严格的游戏 QA 代理。对照原始需求、设计、HTML 实现与真实测试报告判断核心玩法、胜负、重开、移动输入和数值是否实现。"
                "证据必须是代码或测试记录；没有观测到的内容不得声称测试通过。"
                "如果附有截图，结合截图检查裁切、文字重叠、游戏区域可读性与按钮可访问性，明确视觉证据。"
                "浏览器测试未通过则 verdict 必须 repair；存在 critical/major 缺陷必须 repair。minor 可通过并说明。"
                "避免提出偏好性修改或新增需求；别因未提供高级玩法测试而凭空判定代码有错，但在 limitations 中准确说明覆盖范围。",
                {"original_request": request, "design": design.model_dump(), "html": html, "test_report": report,
                 "screenshots": [p.name for p in screenshots]}, Review, images=screenshots)
            accepted = report["passed"] and review.verdict == "pass" and not any(d.severity in {"critical", "major"} for d in review.defects)
            if accepted:
                manifest = {"run_id": rid, "version": version, "sha256": digest, "engine": design.engine,
                            "model": self.settings.model, "title": brief.title, "verified_at": time.time(),
                            "test_report": f"v{version}.report.json", "qa_review": f"v{version}.review.json"}
                self.store.write(rid, "manifest.json", manifest)
                self.store.update(rid, status="passed", stage="complete", approved_version=version)
                self.store.event(rid, "complete", "passed", manifest)
                return
            self.store.event(rid, "repair", "repair_needed", {"version": version, "summary": review.summary})
        self.store.update(rid, status="needs_attention", stage="complete", error="已达到自动修复上限，保留候选版本与缺陷证据供检查")
        self.store.event(rid, "complete", "needs_attention", {"message": "达到修复上限，没有发布未通过验证的版本"})
