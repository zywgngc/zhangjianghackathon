import fcntl
import hashlib
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .agents import AgentClient
from .config import load_settings
from .rag import Retriever
from .runner import Harness
from .schemas import RunRequest, ProjectRequest
from .store import Store
from .validation import BrowserValidator, CSP


def create_app(settings=None, agents_factory=AgentClient, validator_factory=BrowserValidator):
    settings = settings or load_settings()
    store = Store(settings.data_dir)
    retriever = Retriever(settings)

    @asynccontextmanager
    async def lifespan(app):
        lock = (settings.data_dir / "server.lock").open("w")
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("此 DATA_DIR 已有服务运行；请使用单个 uvicorn worker") from None
            agents = agents_factory(settings, store)
            from .project_runner import ProjectOrchestrator
            projects = ProjectOrchestrator(settings, store)
            harness = Harness(settings, store, agents, retriever, validator_factory(settings, store), projects=projects)
            app.state.harness = harness
            app.state.projects = projects
            await harness.start()
            try:
                yield
            finally:
                await harness.stop()
                await agents.close()
                await projects.close()
        finally:
            lock.close()

    app = FastAPI(title="游造 · 中文游戏生成 Harness", version="0.1.0", lifespan=lifespan)
    app.state.store = store

    async def authorize(request: Request):
        if settings.api_token and not hmac.compare_digest(request.headers.get("Authorization", ""), "Bearer " + settings.api_token):
            raise HTTPException(401, "访问口令无效")

    auth = [Depends(authorize)]

    def require_run(rid):
        try:
            run = store.get(rid)
        except ValueError:
            run = None
        if run is None:
            raise HTTPException(404, "任务不存在")
        return run

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "model": settings.model, "api_configured": bool(settings.api_key),
                "knowledge_chunks": len(retriever.documents)}

    @app.get("/api/config", dependencies=auth)
    async def config():
        return {"model": settings.model, "role_models": {r: settings.role_model(r) for r in ["rewrite", "design", "build", "qa", "repair"]},
                "engines": ["auto", "canvas", "dom", "phaser", "babylon"], "max_repairs": settings.max_repairs,
                "max_calls": settings.max_calls, "max_tokens": settings.max_tokens,
                "reasoning_effort": settings.reasoning_effort, "stream_build": settings.stream_build,
                "knowledge_chunks": len(retriever.documents), "external_rag": bool(settings.rag_url), "vision_qa": settings.vision_qa}

    @app.post("/api/projects", status_code=202, dependencies=auth)
    async def create_project(body: ProjectRequest):
        from .specifications import load_case, ingest_text
        if not settings.api_key:
            raise HTTPException(503, "服务端尚未配置模型密钥")
        if body.case_id:
            if body.specification is not None:
                raise HTTPException(422, "官方 case 使用完整原始规格；自定义规格请省略 case_id")
            spec = load_case(body.case_id)
            if body.engine != "auto" and body.engine != spec.engine:
                raise HTTPException(422, "引擎参数违反原始规格锁定")
            engine = spec.engine
        elif body.specification:
            spec = ingest_text(body.specification)
            if spec.engine in {'phaser', 'babylon'} and body.engine not in {'auto', spec.engine}:
                raise HTTPException(422, '引擎参数违反完整规格中的技术栈锁定')
            engine = body.engine if body.engine != "auto" else spec.engine if spec.engine in {"phaser", "babylon"} else "phaser"
        else:
            raise HTTPException(422, "请提供完整 specification 或官方 case_id")
        return store.create({"mode": "project", "description": (spec.names or [spec.case_id])[-1] + " · 完整项目",
            "engine": engine, "case_id": spec.case_id, "specification": spec.text, "source_path": spec.source_path,
            "reference_images": spec.reference_images})

    @app.post("/api/benchmarks/{case_id}/runs", status_code=202, dependencies=auth)
    async def start_benchmark(case_id: str):
        if case_id not in {"GAME-TEST-001", "GAME-TEST-002", "GAME-TEST-003"}:
            raise HTTPException(404, "未知测试 case")
        for run in store.list(200):
            if run['request'].get('case_id') == case_id and run['status'] in {'preparing', 'queued', 'running'}:
                return run
        return await create_project(ProjectRequest(case_id=case_id))

    @app.get("/api/projects/{rid}", dependencies=auth)
    async def project_status(rid: str):
        run = require_run(rid)
        if run['request'].get('mode') != 'project':
            raise HTTPException(404, "非项目任务")
        from .project_state import ProjectLedger
        return {"run": run, **ProjectLedger(store).summary(rid),
                "validation": store.read_json(rid, 'project-validation.json')}

    @app.get("/api/projects/{rid}/candidate", dependencies=auth)
    async def project_candidate(rid: str):
        run = require_run(rid)
        if run['request'].get('mode') != 'project':
            raise HTTPException(404, "非项目任务")
        from .project_runtime import ProjectWorkspace, WorkspaceError
        workspace = ProjectWorkspace(store.directory(rid) / 'project', run['request']['engine'])
        try:
            source_sha = workspace.snapshot()['sha256']
            manifest = store.read_json(rid, 'manifest.json') or {}
            if run['status'] == 'passed' and manifest.get('source_sha256') != source_sha:
                raise RuntimeError('Approved source changed')
            html = workspace.inline_preview()
            if workspace.snapshot()['sha256'] != source_sha:
                raise RuntimeError('Source changed during preview')
        except (WorkspaceError, FileNotFoundError, RuntimeError):
            raise HTTPException(409, "尚无可预览的完整构建") from None
        return HTMLResponse(html, headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store",
            "X-Harness-Acceptance": run['status'], "X-Harness-Source-SHA256": source_sha,
            "X-Content-Type-Options": "nosniff"})

    @app.get("/api/projects/{rid}/archive", dependencies=auth)
    async def project_archive(rid: str):
        run = require_run(rid)
        if run['request'].get('mode') != 'project':
            raise HTTPException(404, "非项目任务")
        from .project_runtime import ProjectWorkspace
        workspace = ProjectWorkspace(store.directory(rid) / 'project', run['request']['engine'])
        try:
            manifest = store.read_json(rid, 'manifest.json') or {}
            if run['status'] == 'passed' and manifest.get('source_sha256') != workspace.snapshot()['sha256']:
                raise RuntimeError('Approved source changed')
            app.state.projects.package(rid, workspace)
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(409, '项目正在更新或尚未初始化，稍后重试下载') from None
        return FileResponse(store.path(rid, 'project-delivery.zip'), filename=rid + '-project.zip')

    @app.post("/api/runs", status_code=202, dependencies=auth)
    async def create_run(body: RunRequest):
        if not settings.api_key:
            raise HTTPException(503, "服务端尚未配置模型密钥")
        queued = sum(r["status"] in {"queued", "running"} for r in store.list(200))
        if queued >= 20:
            raise HTTPException(429, "生成队列已满，请稍后再试")
        return store.create(body.model_dump())

    @app.get("/api/runs", dependencies=auth)
    async def list_runs():
        return store.list()

    @app.get("/api/runs/{rid}", dependencies=auth)
    async def get_run(rid: str):
        return require_run(rid)

    @app.get("/api/runs/{rid}/events", dependencies=auth)
    async def events(rid: str, after: int = 0):
        require_run(rid)
        return store.events(rid, after)

    @app.post("/api/runs/{rid}/cancel", dependencies=auth)
    async def cancel(rid: str):
        require_run(rid)
        app.state.harness.cancel(rid)
        return store.get(rid)

    @app.post("/api/runs/{rid}/resume", dependencies=auth)
    async def resume(rid: str):
        run = require_run(rid)
        project = run['request'].get('mode') == 'project'
        if run["status"] != "failed" and not (project and run["status"] in {'needs_attention', 'cancelled'}):
            raise HTTPException(409, "当前任务无需恢复；项目可从失败、待处理或已取消状态继续")
        if project:
            for feature in app.state.projects.ledger.features(rid):
                if feature['status'] in {'needs_work', 'blocked'}:
                    app.state.projects.ledger.retry(rid, feature['feature_id'])
                    store.event(rid, 'project_recovery', 'feature_resume_requested', {
                        'feature': feature['feature_id'], 'previous_attempts': feature['attempts'], 'budget_reset': False})
        store.update(rid, status="queued", error=None)
        store.event(rid, "queued", "resumed", {"message": "手动恢复，继续使用原有预算"})
        return store.get(rid)

    @app.get("/api/runs/{rid}/artifacts/{name}", dependencies=auth)
    async def artifact(rid: str, name: str):
        require_run(rid)
        try:
            path = store.path(rid, name)
        except ValueError:
            raise HTTPException(404, "产物不存在") from None
        if not path.is_file():
            raise HTTPException(404, "产物不存在")
        # HTML is always an attachment here; only approved previews may execute.
        return FileResponse(path, filename=name, content_disposition_type="attachment",
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @app.get("/api/runs/{rid}/preview/{version}", dependencies=auth)
    async def preview(rid: str, version: int):
        run = require_run(rid)
        if run["status"] != "passed" or run["approved_version"] != version:
            raise HTTPException(409, "此版本尚未通过验证")
        manifest = store.read_json(rid, "manifest.json")
        html = store.path(rid, f"v{version}.html").read_bytes()
        if not manifest or hashlib.sha256(html).hexdigest() != manifest["sha256"]:
            raise HTTPException(409, "文件与已验证版本不一致")
        return HTMLResponse(html, headers={"Content-Security-Policy": CSP, "Cache-Control": "no-store",
                                          "X-Content-Type-Options": "nosniff"})

    @app.get("/api/knowledge/search", dependencies=auth)
    async def search(q: str):
        if not 1 <= len(q) <= 4000:
            raise HTTPException(422, "查询长度须为 1–4000")
        return await retriever.search(q)

    @app.get("/api/benchmarks", dependencies=auth)
    async def benchmarks():
        from .benchmarks import inventory
        try:
            cases = inventory()
            runs = store.list(200)
            for case in cases:
                latest = next((run for run in runs if run['request'].get('case_id') == case['case_id']), None)
                if latest:
                    case.update(latest_run_id=latest['id'], execution=latest['status'], stage=latest['stage'])
            return {"scope": "完整项目运行能力及真实任务状态；ready不代表游戏验收通过", "cases": cases}
        except FileNotFoundError:
            raise HTTPException(503, "验收规格未随当前安装提供，请在完整仓库中运行") from None

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def home():
        return FileResponse(static / "index.html")

    return app


app = create_app()
