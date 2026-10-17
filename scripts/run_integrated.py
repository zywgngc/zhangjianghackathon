"""Start the loopback harness and its Node research sidecar with one private backend token."""
import argparse
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time

import httpx
import uvicorn
from dotenv import load_dotenv


RUNTIME_ENV_KEYS = frozenset({
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
})


def runtime_environment():
    """The trusted sidecar needs platform settings, not host service credentials."""
    return {name: value for name, value in os.environ.items() if name in RUNTIME_ENV_KEYS}


def research_environment(port, token, database):
    """Forward only named research settings; networking and credentials are opt-in."""
    env = {**runtime_environment(), "PORT": str(port), "WEBMCP_TOKEN": token,
           "WEBMCP_DB": str(Path(database).resolve()), "WEBMCP_GITHUB_NETWORK": "off"}
    mode = os.getenv("WEBMCP_RESEARCH_MODE")
    if mode:
        env["WEBMCP_RESEARCH_MODE"] = mode
    state = os.getenv("WEBMCP_GITHUB_STATE")
    if state:
        env["WEBMCP_GITHUB_STATE"] = str(Path(state).expanduser().resolve())
    if os.getenv("WEBMCP_GITHUB_NETWORK") == "bounded" and mode != "fixtures":
        env["WEBMCP_GITHUB_NETWORK"] = "bounded"
        github_token = os.getenv("WEBMCP_GITHUB_TOKEN")
        if github_token:
            env["WEBMCP_GITHUB_TOKEN"] = github_token
    return env


def select_research_node(explicit=None):
    """Select only the sidecar executable; never change the Builder's PATH."""
    node = str(Path(explicit).expanduser().resolve()) if explicit else shutil.which("node")
    if not node:
        raise SystemExit("Node.js 24 is required; pass --research-node /path/to/node24.")
    try:
        version = subprocess.check_output([node, "--version"], text=True,
            env=runtime_environment()).strip()
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit("Cannot execute the research Node runtime; check --research-node.") from None
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    if not match or int(match[1]) != 24 or int(match[2]) < 10:
        raise SystemExit("Use Node.js >=24.10 within Node 24; pass --research-node without changing the Builder PATH.")
    return node


def main():
    parser = argparse.ArgumentParser(description="Run the harness with its WebMCP research workspace")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--research-port", type=int, default=4174)
    parser.add_argument("--research-node", type=Path,
        help="Sidecar-only Node >=24.10 <25 executable; the game Builder keeps its current PATH/runtime")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.research_port <= 65535 or args.port == args.research_port:
        parser.error("Use two distinct ports in 1..65535")
    if args.research_node:
        args.research_node = args.research_node.expanduser().resolve()
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    load_dotenv(root / ".env")
    node = select_research_node(args.research_node)
    token = secrets.token_urlsafe(32)
    origin = f"http://127.0.0.1:{args.research_port}"
    database = Path(os.getenv("WEBMCP_DB", str(root / "webmcp/.data/research.sqlite"))).resolve()
    env = research_environment(args.research_port, token, database)
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
