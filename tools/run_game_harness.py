"""Run a validated small HTML request through the real harness, without its web app.

Use the project environment and Python -X utf8. Credentials come from
environment/.env only; no secrets are accepted as command-line arguments.
This runner does not simplify, truncate or silently adapt the original cases.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def _parser():
    parser = argparse.ArgumentParser(description="Run the real game harness on an explicitly supplied HTML request")
    parser.add_argument("--harness-root", type=Path, default=ROOT)
    parser.add_argument("--env-file", type=Path, default=ROOT / ("agent.env" if (ROOT / "agent.env").is_file() else ".env"))
    parser.add_argument("--request", type=Path, required=True, help='JSON object with description and engine: auto/canvas/dom')
    parser.add_argument("--output", type=Path, help="Fresh output directory; separate from the web server's data directory")
    parser.add_argument("--check-only", action="store_true", help="Validate request/runtime readiness; no model calls")
    parser.add_argument("--resume-report", type=Path, help="Resume a failed run with its existing checkpoints and budgets")
    parser.add_argument("--build-output-tokens", type=int, default=16000,
                        help="Test-run override for build/repair output; the existing total run budget still applies")
    return parser


def _load(args):
    from dotenv import load_dotenv

    harness_root = args.harness_root.resolve()
    if not (harness_root / "game_harness/runner.py").is_file():
        raise ValueError("Complete harness source was not found at --harness-root")
    sys.path.insert(0, str(harness_root))
    # Call the same Harness pipeline directly, without starting its web server.
    from game_harness.config import Settings
    from game_harness.schemas import RunRequest

    if args.env_file.is_file():
        load_dotenv(args.env_file, override=False)
    browser_root = ROOT / ".asset-agent/playwright-browsers"
    if browser_root.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_root))
    if args.resume_report:
        if args.output:
            raise ValueError("--output cannot be combined with --resume-report")
        saved = json.loads(args.resume_report.read_text(encoding="utf-8"))
        if (saved["request_sha256"] != hashlib.sha256(args.request.read_bytes()).hexdigest()
                or Path(saved["harness_root"]).resolve() != harness_root):
            raise ValueError("Resume request or harness does not match the previous run")
        output = args.resume_report.resolve().parent
    elif args.output:
        output = args.output.resolve()
    else:
        output = ROOT / "output" / ("harness-real-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
    if not args.resume_report and output.exists() and any(output.iterdir()):
        raise ValueError("--output must be a fresh or empty directory; existing runs are not overwritten")
    # RunRequest rejects unsupported engines, long input and unknown fields.
    request = RunRequest.model_validate_json(args.request.read_text(encoding="utf-8-sig")).model_dump()
    settings = Settings()
    settings = replace(settings, data_dir=output / "data", concurrency=1,
                       portal_url=settings.portal_url or "local://references")
    if not 0 <= settings.max_repairs <= 5 or min(settings.max_calls, settings.max_tokens, settings.run_timeout) <= 0:
        raise ValueError("Invalid model/repair/time budget")
    if not 256 <= args.build_output_tokens <= 32768:
        raise ValueError("--build-output-tokens must be 256..32768")
    # Only compare digests: this never writes to the inspected harness source.
    installed_portal = harness_root / "game_harness/material_portal.py"
    our_portal = ROOT / "game_harness/material_portal.py"
    current_portal = hashlib.sha256(installed_portal.read_bytes()).digest() == hashlib.sha256(our_portal.read_bytes()).digest()
    if settings.portal_url in {"local://references", "web://assets"} and not current_portal:
        raise ValueError("Selected harness does not have the updated single-file portal")
    return harness_root, output, settings, request


async def _browser_ready(settings):
    from playwright.async_api import async_playwright

    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True, chromium_sandbox=settings.browser_sandbox)
        try:
            page = await asyncio.wait_for(browser.new_page(), timeout=15)
            await page.set_content("<!doctype html><html><body>运行检查</body></html>")
            if await page.locator("body").inner_text() != "运行检查":
                raise RuntimeError("Browser page initialization failed")
            return browser.version
        finally:
            await browser.close()


async def _main(args):
    harness_root, output, settings, request = _load(args)
    from game_harness.agents import AgentClient
    from game_harness.rag import Retriever
    from game_harness.runner import Harness
    from game_harness.store import Store
    from game_harness.validation import BrowserValidator

    key_present = bool(settings.api_key.strip()) and settings.api_key.strip().lower() not in {
        "replace-with-your-key", "your-api-key", "your-key", "unconfigured"}
    readiness = {"request_characters": len(request["description"]), "engine": request["engine"],
                 "api_key_configured": key_present,
                 "model": settings.model, "qa_model": settings.role_model("qa"),
                 "vision_qa": settings.vision_qa, "stream_build": settings.stream_build,
                 "portal_mode": ("web" if settings.portal_url == "web://assets" else
                                 "local" if settings.portal_url == "local://references" else "http"),
                 "limits": {"calls":settings.max_calls,"tokens":settings.max_tokens,
                            "repairs":settings.max_repairs,"seconds":settings.run_timeout}}
    try:
        readiness["chromium_version"] = await _browser_ready(settings)
    except Exception as exc:
        readiness["browser_ready"] = False
        readiness["browser_error_type"] = type(exc).__name__
    else:
        readiness["browser_ready"] = True
    if args.check_only:
        print(json.dumps({"readiness":readiness,"model_calls":0},ensure_ascii=False,indent=2))
        return 0 if readiness["browser_ready"] and key_present else 2
    if not key_present or not readiness["browser_ready"]:
        print(json.dumps({"status":"blocked","readiness":readiness,"model_calls":0},ensure_ascii=False,indent=2))
        return 2

    output.mkdir(parents=True, exist_ok=True)
    store = Store(settings.data_dir)
    if args.resume_report:
        previous = json.loads(args.resume_report.read_text(encoding="utf-8"))
        run_id = previous["run_id"]
        run = store.get(run_id)
        if (not run or run["status"] not in {"failed", "needs_attention"}
                or run["request"] != request or previous["readiness"]["model"] != settings.model):
            raise ValueError("Only the same failed request/model can resume")
        for name, current in (("calls",settings.max_calls),("tokens",settings.max_tokens),
                              ("repairs",settings.max_repairs),("seconds",settings.run_timeout)):
            if previous["readiness"]["limits"][name] != current:
                raise ValueError("Resume must preserve the original run budgets")
        attempt = 1
        while (output/f"report.attempt-{attempt}.json").exists():
            attempt += 1
        (output/f"report.attempt-{attempt}.json").write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding="utf-8")
        store.update(run_id,status="running",error=None)
        store.event(run_id,"execution","manual_resume",{"build_output_tokens":args.build_output_tokens,
                                                       "original_budgets_preserved":True})
    else:
        run_id = store.create(request)["id"]
        assert store.claim() == run_id

    class OutputLimitedClient(AgentClient):
        async def complete(self, rid, stage, role, system, data, max_tokens=4000, images=None):
            if role in {"build","repair"}:
                max_tokens = args.build_output_tokens
            return await super().complete(rid,stage,role,system,data,max_tokens=max_tokens,images=images)

    agents = OutputLimitedClient(settings, store)
    harness = Harness(settings, store, agents, Retriever(settings), BrowserValidator(settings, store))
    print(json.dumps({"run_id":run_id,"output":str(output),"readiness":readiness},ensure_ascii=False),flush=True)
    task = asyncio.create_task(harness.execute(run_id))
    last_event = 0
    try:
        while not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task),timeout=5)
            except asyncio.TimeoutError:
                pass
            for event in store.events(run_id, after=last_event):
                last_event = event["id"]
                # Model text and error bodies can contain provider details;
                # progress output includes only controlled field names/counts.
                if event["kind"] in {"stage_start","model_start","model_done","repair_needed","passed","failed"}:
                    print(json.dumps({"stage":event["stage"],"event":event["kind"],
                                      "calls":store.get(run_id)["calls"],"tokens":store.get(run_id)["tokens"]},
                                     ensure_ascii=False),flush=True)
        await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        await agents.close()
    run = store.get(run_id)
    reports = sorted(store.directory(run_id).glob("v*.report.json"))
    report = {"run_id":run_id,"status":run["status"],"stage":run["stage"],
              "model_calls":run["calls"],"tokens":run["tokens"],"approved_version":run["approved_version"],
              "readiness":readiness,"request_file":str(args.request.resolve()),
              "test_overrides":{"build_output_tokens":args.build_output_tokens,
                                "resumed_with_same_budget":bool(args.resume_report)},
              "request_sha256":hashlib.sha256(args.request.read_bytes()).hexdigest(),
              "harness_source_sha256":{name:hashlib.sha256((harness_root/"game_harness"/name).read_bytes()).hexdigest()
                                       for name in ("material_portal.py","agents.py","runner.py")},
              "harness_root":str(harness_root),"artifacts":str(store.directory(run_id)),
              "browser_reports":[str(path) for path in reports],
              "html_files":[str(path) for path in sorted(store.directory(run_id).glob("v*.html"))],
              "scope":"Real model + portal + build + Chromium + QA pipeline; browser smoke checks do not establish full original-case acceptance."}
    (output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if run["status"] == "passed" else 1


if __name__ == "__main__":
    if os.name == "nt" and not sys.flags.utf8_mode:
        os.execv(sys.executable,[sys.executable,"-X","utf8",*sys.argv])
    try:
        raise SystemExit(asyncio.run(_main(_parser().parse_args())))
    except KeyboardInterrupt:
        raise SystemExit(130)
