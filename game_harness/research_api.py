"""Authenticated main-to-MAS routes, with the MAS remaining the only counter/revision writer."""
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .research import ResearchError, ResearchReference


def install_research_routes(app, research, auth):
    router = APIRouter(prefix="/api/research", dependencies=auth)

    @app.exception_handler(ResearchError)
    async def research_error(_request, exc):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    async def payload(request):
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise ResearchError("INVALID_INPUT", "Content-Type must be application/json", 415)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 65536:
                raise ResearchError("INVALID_INPUT", "Request exceeds 64 KiB", 413)
            chunks.append(chunk)
        try:
            value = json.loads(b"".join(chunks))
        except (ValueError, UnicodeError):
            raise ResearchError("INVALID_INPUT", "Malformed JSON", 400) from None
        if not isinstance(value, dict):
            raise ResearchError("INVALID_INPUT", "Expected a JSON object", 400)
        return value

    @router.get("/config")
    async def config():
        return {"configured": research.configured, "builder_engines": ["canvas", "dom"],
                "handoff_target": "/api/runs", "project_handoff_supported": False,
                "discussion_owner": "webmcp", "main_follow_up_limit": 3, "material_follow_up_limit": 3}

    @router.get("/cases")
    async def cases():
        return await research.request("GET", "/api/cases")

    @router.get("/contract")
    async def contract():
        return await research.request("GET", "/api/contract")

    @router.get("/runs")
    async def runs():
        return await research.request("GET", "/api/runs")

    @router.post("/runs", status_code=202)
    async def start(request: Request):
        return await research.request("POST", "/api/runs", await payload(request))

    @router.get("/runs/{rid}")
    async def state(rid: str):
        return await research.request("GET", "/api/runs/" + quote(rid, safe=""))

    @router.get("/runs/{rid}/blueprint")
    async def blueprint(rid: str):
        return await research.request("GET", "/api/runs/" + quote(rid, safe="") + "/blueprint")

    @router.post("/runs/{rid}/follow-ups", status_code=202)
    async def follow_up(rid: str, request: Request):
        return await research.request("POST", "/api/runs/" + quote(rid, safe="") + "/follow-ups", await payload(request))

    @router.post("/runs/{rid}/material-follow-ups", status_code=202)
    async def material_follow_up(rid: str, request: Request):
        return await research.request("POST", "/api/runs/" + quote(rid, safe="") + "/material-follow-ups", await payload(request))

    @router.post("/runs/{rid}/cancel")
    async def cancel(rid: str, request: Request):
        return await research.request("POST", "/api/runs/" + quote(rid, safe="") + "/cancel", await payload(request))

    @router.post("/runs/{rid}/handoff")
    async def handoff(rid: str, reference: ResearchReference):
        if rid != reference.runId:
            raise ResearchError("INVALID_INPUT", "Route and reference run IDs differ", 400)
        return await research.handoff(reference)

    app.include_router(router)
    assets = Path(__file__).resolve().parents[1] / "webmcp" / "public"

    @app.get("/research-assets/{name}")
    async def research_asset(name: str):
        if name not in {"app.mjs", "webmcp.mjs", "request-schema.mjs", "style.css"} or not (assets / name).is_file():
            raise HTTPException(404, "研究工作台资源不存在，请从完整仓库运行")
        media = "text/css" if name.endswith(".css") else "text/javascript"
        return FileResponse(assets / name, media_type=media, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/research")
    async def research_studio():
        if not (assets / "index.html").is_file():
            raise HTTPException(503, "研究工作台未随当前安装提供，请从完整仓库运行")
        html = (assets / "index.html").read_text(encoding="utf-8")
        html = html.replace('<body>', '<body data-api-base="/api/research" data-auth-store="harness-token" data-handoff="true">')
        html = html.replace('href="/style.css"', 'href="/research-assets/style.css"').replace('src="/app.mjs"', 'src="/research-assets/app.mjs"')
        html = html.replace('<header>', '<header><p><a href="/">← 游造工作台</a> · 如需口令，请先在主工作台的“访问设置”中应用。</p>')
        return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"})
