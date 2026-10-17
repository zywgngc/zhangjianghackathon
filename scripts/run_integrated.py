"""Start the loopback harness and its Node research sidecar with one private backend token."""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

import httpx
import uvicorn
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description="Run the harness with its WebMCP research workspace")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--research-port", type=int, default=4174)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.research_port <= 65535 or args.port == args.research_port:
        parser.error("Use two distinct ports in 1..65535")
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    load_dotenv(root / ".env")
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js 24 is required; install webmcp dependencies with pnpm first.")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    parts = version.lstrip("v").split(".")
    if int(parts[0]) != 24 or int(parts[1]) < 10:
        raise SystemExit("Use Node.js >=24.10 within the Node 24 release line.")
    token = secrets.token_urlsafe(32)
    origin = f"http://127.0.0.1:{args.research_port}"
    database = Path(os.getenv("WEBMCP_DB", str(root / "webmcp/.data/research.sqlite"))).resolve()
    env = {**os.environ, "PORT": str(args.research_port), "WEBMCP_TOKEN": token, "WEBMCP_DB": str(database)}
    child = subprocess.Popen([node, "src/server.ts"], cwd=root / "webmcp", env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic() + 20
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("Research service exited. Check dependencies and port availability.")
                try:
                    if client.get(origin + "/api/cases", headers={"Authorization": "Bearer " + token}).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Research service did not become ready.")
        from game_harness.config import load_settings
        from game_harness.app import create_app
        settings = load_settings()
        settings.research_url, settings.research_token = origin, token
        print(f"Integrated workbench: http://127.0.0.1:{args.port}/research", flush=True)
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, workers=1)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    main()
