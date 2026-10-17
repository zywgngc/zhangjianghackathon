import errno
import hashlib
import hmac
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .agents import AgentClient
from .config import load_settings
from .rag import Retriever
from .runner import Harness
from .schemas import RunRequest
from .store import Store
from .validation import BrowserValidator, CSP


@contextmanager
def _server_lock(data_dir):
    # Keep the same file for every owner: removing or truncating it can let
    # another process lock a different file while the first server is running.
    with (data_dir / "server.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise RuntimeError("此 DATA_DIR 已有服务运行；请使用单个 uvicorn worker") from None
            raise
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def create_app(settings=None, agents_factory=AgentClient, validator_factory=BrowserValidator):
    settings = settings or load_settings()
    store = Store(settings.data_dir)
    retriever = Retriever(settings)

    @asynccontextmanager
    async def lifespan(app):
        with _server_lock(settings.data_dir):
            agents = agents_factory(settings, store)
            harness = Harness(settings, store, agents, retriever, validator_factory(settings, store))
            app.state.harness = harness
            await harness.start()
            try:
                yield
            finally:
                await harness.stop()
                await agents.close()

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
                "engines": ["auto", "canvas", "dom"], "max_repairs": settings.max_repairs,
                "max_calls": settings.max_calls, "max_tokens": settings.max_tokens,
                "reasoning_effort": settings.reasoning_effort, "stream_build": settings.stream_build,
                "knowledge_chunks": len(retriever.documents), "external_rag": bool(settings.rag_url), "vision_qa": settings.vision_qa}

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
        if run["status"] != "failed":
            raise HTTPException(409, "仅失败任务可从已有检查点恢复")
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
            return {"scope": "资料与能力预检，不代表游戏验收通过", "cases": inventory()}
        except FileNotFoundError:
            raise HTTPException(503, "验收规格未随当前安装提供，请在完整仓库中运行") from None

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    async def home():
        return FileResponse(static / "index.html")

    return app


app = create_app()
