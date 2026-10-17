#!/usr/bin/env python3
"""Rebuild and independently validate an existing official-case project.

No game source or model output is generated. Model calls require --vision.
Build dependencies, dist and .harness metadata may be written in the workspace.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from game_harness.acceptance import evaluate_evidence
from game_harness.project_runtime import ProjectWorkspace, TEMPLATES
from game_harness.project_validation import ProjectValidator
from game_harness.specifications import CASES, load_case
from game_harness.store import Store


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_output(path: Path, workspace: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Output must not be a symlink")
    path = path.resolve()
    if path == workspace or path.is_relative_to(workspace) or workspace.is_relative_to(path):
        raise ValueError("Output must be independent of the project workspace")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("Refusing to overwrite a nonempty output directory")
    path.mkdir(parents=True, exist_ok=True)
    return path


class ValidationStore(Store):
    """Normal model accounting, with artifacts flat inside the explicit output."""
    def __init__(self, output: Path):
        self.output = output
        super().__init__(output / ".ledger")

    def directory(self, rid: str):
        # Store.path already constrains artifact names; never accept another run.
        if hasattr(self, "run_id") and rid != self.run_id:
            raise ValueError("Artifact belongs to another validation run")
        return self.output


def source_preflight(workspace: ProjectWorkspace) -> list[str]:
    """Avoid initialize() adding missing template source files during validation."""
    errors = []
    for directory in (TEMPLATES / "common", workspace.template_dir):
        for source in directory.rglob("*"):
            if source.is_file():
                relative = source.relative_to(directory).as_posix()
                try:
                    target = workspace._inside(relative)
                    if not target.is_file():
                        errors.append("Missing existing template file; validation will not create source: " + relative)
                except ValueError as exc:
                    errors.append(str(exc))
    return errors


def refresh_summary(report: dict, source_sha256: str, spec, evidence: dict | None) -> None:
    if evidence is not None:
        report["acceptance"] = evaluate_evidence(spec, evidence)
    acceptance = report["acceptance"]
    report["manual_required"] = [row["id"] for row in acceptance["tests"] if row["status"] == "manual_required"]
    report["visual_required"] = [row["id"] for row in acceptance["tests"] if row["status"] == "visual_required"]
    report["not_run"] = [row["id"] for row in acceptance["tests"] if row["status"] == "not_run"]
    report["passed"] = (acceptance.get("complete") is True and not report.get("errors")
        and not report.get("adapter_failures") and report.get("source_sha256") == source_sha256)


async def validate(args) -> tuple[int, dict]:
    original = args.workspace.expanduser().absolute()
    if not original.is_dir() or original.is_symlink():
        raise ValueError("--workspace must be an existing non-symlink project directory")
    original = original.resolve()
    output = prepare_output(args.output.expanduser().absolute(), original)
    spec = load_case(args.case_id)
    workspace = ProjectWorkspace(original, spec.engine, cache_dir=output / ".npm-cache")
    before = workspace.snapshot()
    store = ValidationStore(output)
    row = store.create({"description": "Independent official-case project validation", "case_id": spec.case_id})
    rid = store.run_id = row["id"]
    store.write(rid, "specification.txt", spec.text)
    store.write(rid, "specification.json", spec.to_dict())
    store.write(rid, "source-snapshot.json", before)
    settings = SimpleNamespace(browser_sandbox=not args.no_browser_sandbox, project_browser_timeout=args.browser_timeout)
    preflight = source_preflight(workspace)
    if preflight:
        build = {"passed": False, "engine": spec.engine, "source_sha256": before["sha256"], "errors": preflight, "stages": []}
    else:
        # initialize inside build verifies the immutable package/config templates.
        build = await workspace.build(offline=args.offline, timeout=args.build_timeout)
    store.write(rid, "project-build-0.json", build)
    report = await ProjectValidator(settings, store).validate(rid, spec, workspace, build, 0)
    evidence = store.read_json(rid, "project-evidence-0.json")
    # Early build/admission failures do not normally create an evidence ledger.
    if evidence is None:
        evidence = {"spec_sha256": spec.sha256, "project_sha256": before["sha256"], "artifact_root": str(output), "checks": []}
        store.write(rid, "project-evidence-0.json", evidence)
    vision = {"requested": args.vision, "executed": False}
    if args.vision and build.get("passed") and workspace.snapshot()["sha256"] == build.get("source_sha256"):
        from game_harness.agents import AgentClient
        from game_harness.config import load_settings
        from game_harness.project_review import review_visual
        visual_settings = load_settings()
        if not visual_settings.api_key:
            report["errors"].append("--vision explicitly requested, but OPENAI_API_KEY is not configured")
        else:
            client = AgentClient(visual_settings, store)
            try:
                screenshots = [row for row in report.get("browser", []) + report.get("captured_screenshots", [])
                    if row.get("path") or row.get("screenshot")]
                calls_before = store.get(rid)["calls"]
                checks = await review_visual(rid, spec, store, client, before["sha256"], screenshots)
                evidence["checks"].extend(checks)
                calls = store.get(rid)["calls"] - calls_before
                vision.update(executed=calls > 0, calls=calls, checks=len(checks))
                store.write(rid, "project-evidence-0.json", evidence)
            except Exception as exc:
                # API responses are not printed; provider messages may contain credentials.
                report["errors"].append("Explicit visual review failed: " + type(exc).__name__)
            finally:
                await client.close()
    after = workspace.snapshot()
    if before != after:
        report["errors"].append("Source changed during validation; no source was restored or overwritten")
    refresh_summary(report, after["sha256"], spec, evidence)
    report["evidence_manifest"] = [{"requirement_id": check["requirement_id"],
        "scenario_id": check.get("scenario_id"), "artifacts": check["artifacts"]}
        for check in evidence["checks"]]
    report.update(cli_version=1, case_id=spec.case_id, validation_run_id=rid,
        workspace=str(original), source_unchanged=before == after, vision=vision)
    store.write(rid, "project-validation.json", report)
    summary = {"case_id": spec.case_id, "passed": report["passed"], "build_passed": build.get("passed") is True,
        "source_sha256": after["sha256"], "source_unchanged": before == after,
        "counts": report["acceptance"]["counts"], "adapter_failures": report.get("adapter_failures", []),
        "manual_required": report["manual_required"], "visual_required": report["visual_required"],
        "not_run": report["not_run"], "missing_executors": report.get("missing_executors", []),
        "errors": report.get("errors", []), "report": str(output / "project-validation.json"), "vision": vision}
    write_json(output / "summary.json", summary)
    manifest = [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        for path in sorted(output.iterdir()) if path.is_file() and not path.is_symlink() and not path.name.startswith(".")]
    write_json(output / "validation-manifest.json", {"source_sha256": after["sha256"], "spec_sha256": spec.sha256, "files": manifest})
    store.update(rid, status="passed" if report["passed"] else "needs_attention", stage="independent_validation")
    return (0 if report["passed"] else 1), summary


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="真实构建并验收现有官方游戏工程；默认零模型请求，未全部通过退出1")
    p.add_argument("--case-id", required=True, choices=sorted(CASES))
    p.add_argument("--workspace", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--vision", action="store_true", help="显式允许已有 QA 视觉模型审查截图；使用现有环境/.env 配置")
    p.add_argument("--offline", action="store_true", help="npm仅使用现有本地缓存")
    p.add_argument("--build-timeout", type=float, default=240)
    p.add_argument("--browser-timeout", type=float, default=600)
    p.add_argument("--no-browser-sandbox", action="store_true", help="仅用于不支持Chromium沙箱的受控机器")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.build_timeout <= 0 or args.browser_timeout <= 0:
        parser().error("timeouts must be positive")
    try:
        code, summary = asyncio.run(validate(args))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return code
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
