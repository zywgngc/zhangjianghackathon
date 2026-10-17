#!/usr/bin/env python3
"""Atomically export a current built candidate with source-bound evidence.

This command does not build, generate, repair, alter acceptance reports, or call
models. A playable candidate is not an accepted game.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from game_harness.acceptance import evaluate_evidence
from game_harness.project_runtime import ProjectWorkspace
from game_harness.specifications import CASES, ingest_text, load_case

EVIDENCE_NAMES = {
    "specification.txt", "specification.json", "implementation-plan.json", "project-progress.json",
    "project-validation.json", "manifest.json", "summary.json", "validation-manifest.json", "source-snapshot.json",
}
EVIDENCE_PATTERNS = ("project-build-[0-9]*.json", "project-evidence-[0-9]*.json",
    "project-r[0-9]*.json", "project-r[0-9]*.png", "project-visual-*.json")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path):
    if path.is_symlink():
        raise ValueError("Symlink report is forbidden: " + path.name)
    return json.loads(path.read_text(encoding="utf-8"))


def is_evidence_name(name: str) -> bool:
    return (bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)) and
        (name in EVIDENCE_NAMES or any(fnmatch.fnmatchcase(name, p) for p in EVIDENCE_PATTERNS)))


def detect_engine(workspace: Path) -> str:
    package = read_json(workspace / "package.json")
    dependencies = package.get("dependencies", {})
    found = [engine for engine, package_name in (("babylon", "@babylonjs/core"), ("phaser", "phaser")) if package_name in dependencies]
    if len(found) != 1:
        raise ValueError("Expected exactly one pinned Phaser or Babylon engine")
    return found[0]


def load_specification(run: Path):
    document = read_json(run / "specification.json") if (run / "specification.json").exists() else None
    if not document or not (run / "specification.txt").is_file():
        return None
    text = (run / "specification.txt").read_text(encoding="utf-8")
    if digest(text.encode()) != document.get("sha256"):
        raise ValueError("Raw specification differs from its recorded hash")
    case_id = document["case_id"]
    spec = load_case(case_id) if case_id in CASES else ingest_text(text, case_id=case_id, source_path=document.get("source_path", "specification.txt"))
    if spec.sha256 != document["sha256"]:
        raise ValueError("Recorded official specification differs from this checkout")
    return spec


def export_project(run_dir: Path, output: Path) -> dict:
    run = run_dir.expanduser().absolute()
    output = output.expanduser().absolute()
    if not run.is_dir() or run.is_symlink() or output.is_symlink():
        raise ValueError("Run and output paths must not be symlinks")
    run, output = run.resolve(), output.resolve()
    if output == run or output.is_relative_to(run) or run.is_relative_to(output):
        raise ValueError("Output must be independent of the source run")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Refusing to overwrite a nonempty output directory")
    workspace = ProjectWorkspace(run / "project", detect_engine(run / "project"))
    before = workspace.snapshot()
    build_path = workspace.root / ".harness/build.json"
    if not build_path.is_file() or build_path.is_symlink():
        raise ValueError("No verified current build; validate/build the project before export")
    build_bytes = build_path.read_bytes()
    build = json.loads(build_bytes)
    if build.get("passed") is not True or build.get("source_sha256") != before["sha256"]:
        raise ValueError("Build is failed or stale for the current source")
    if build.get("engine") != workspace.engine:
        raise ValueError("Build engine differs from its workspace")
    # read_asset verifies every byte against the current host build manifest.
    built_assets = {row["path"]: workspace.read_asset(row["path"])[0] for row in build.get("artifact_manifest", [])}
    if "index.html" not in built_assets:
        raise ValueError("Build manifest has no entrypoint")
    playable = workspace.inline_preview().encode("utf-8")
    validation = read_json(run / "project-validation.json") if (run / "project-validation.json").exists() else {}
    spec = load_specification(run)
    current = bool(spec and validation.get("source_sha256") == before["sha256"] and validation.get("spec_sha256") == spec.sha256)
    evidence_files = {}
    for path in sorted(run.iterdir()):
        if is_evidence_name(path.name):
            if path.is_symlink() or not path.is_file():
                raise ValueError("Evidence must be a regular run-local file: " + path.name)
            evidence_files[path.name] = path.read_bytes()
    references = [item for row in validation.get("acceptance", {}).get("tests", []) for item in row.get("evidence", [])]
    references += [item for row in validation.get("evidence_manifest", []) for item in row.get("artifacts", [])]
    # All exported evidence ledgers must retain the exact documents they cite.
    ledgers = []
    for name, raw in evidence_files.items():
        match = re.fullmatch(r"project-evidence-(\d+)\.json", name)
        if match:
            ledger = json.loads(raw)
            ledgers.append((int(match.group(1)), name, ledger))
            references += [item for check in ledger.get("checks", []) for item in check.get("artifacts", [])]
    for reference in references:
        name = reference.get("path")
        if not isinstance(name, str) or not is_evidence_name(name):
            raise ValueError("Evidence reference is outside the curated artifact set")
        raw = evidence_files.get(name)
        if raw is None or digest(raw) != reference.get("sha256"):
            raise ValueError("Evidence is missing or its hash changed: " + name)
    acceptance = None
    selected_ledger = None
    if current:
        for _, name, ledger in sorted(ledgers, reverse=True):
            if ledger.get("project_sha256") == before["sha256"] and ledger.get("spec_sha256") == spec.sha256:
                if Path(ledger.get("artifact_root", "")).resolve() != run:
                    continue
                acceptance = evaluate_evidence(spec, ledger)
                selected_ledger = name
                break
    formal = bool(current and acceptance and acceptance.get("complete") is True
        and validation.get("passed") is True and not validation.get("errors") and not validation.get("adapter_failures"))
    blockers = []
    if not current:
        blockers.append("Missing or stale validation/specification for current source")
    if acceptance is None:
        blockers.append("No current source-bound independent evidence ledger could be evaluated")
    elif not acceptance.get("complete"):
        blockers.append("Official acceptance contains failed, not-run, manual or visual pending criteria")
    if validation.get("errors") or validation.get("adapter_failures") or validation.get("passed") is not True:
        blockers.append("Recorded independent validation did not pass")
    status = {"version": 1, "delivery_kind": "accepted" if formal else "candidate", "accepted": formal,
        "source_sha256": before["sha256"], "engine": workspace.engine, "case_id": spec.case_id if spec else None,
        "build_current": True, "validation_report_current": current, "reported_validation_passed": validation.get("passed"),
        "recomputed_acceptance": acceptance, "evidence_ledger": selected_ledger,
        "counts": acceptance.get("counts", {}) if acceptance else {},
        "adapter_failures": validation.get("adapter_failures", []) if current else [],
        "blockers": blockers, "original_reports_modified": False,
        "scope": "A verified playable candidate is not full gameplay acceptance; original evidence reports are copied byte-for-byte."}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="." + output.name + "-", dir=output.parent))
    original_bytes = {}
    try:
        def put(relative: str, raw: bytes):
            parts = PurePosixPath(relative).parts
            if not parts or PurePosixPath(relative).is_absolute() or any(p in {".", ".."} or p.startswith(".") for p in parts):
                raise ValueError("Invalid export path")
            path = temporary.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        snapshots = {row["path"]: row for row in before["files"]}
        for relative in workspace.list_files():
            path = workspace._inside(relative)
            raw = path.read_bytes()
            if digest(raw) != snapshots[relative]["sha256"]:
                raise ValueError("Source changed while copying: " + relative)
            put("project/" + relative, raw)
        for relative, raw in built_assets.items():
            put("project/dist/" + relative, raw)
        put("playable.html", playable)
        put("evidence/current-build.json", build_bytes)
        for name, raw in evidence_files.items():
            put("evidence/" + name, raw)
            original_bytes[run / name] = digest(raw)
        put("delivery-status.json", (json.dumps(status, ensure_ascii=False, indent=2) + "\n").encode())
        put("DELIVERY.md", ("# 游戏工程交付\n\n状态：" + ("已完成全部正式验收" if formal else "候选，尚未完成全部正式验收") +
            "。具体依据见 delivery-status.json 与 evidence/ 中的原始报告。\n\n"
            "直接打开 playable.html 可离线运行。源码与多文件构建位于 project/；不包含 node_modules、凭据或 .harness。\n"
            "使用与仓库一致的 Python/Node 依赖后，可重新运行 scripts/validate_project.py 对源码独立验收。\n").encode())
        manifest = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": before["sha256"], "spec_sha256": spec.sha256 if spec else None,
            "delivery_kind": status["delivery_kind"], "status_report": "delivery-status.json",
            "playable": {"path": "playable.html", "sha256": digest(playable)},
            "evidence_root": "evidence", "original_reports_modified": False,
            "files": [{"path": p.relative_to(temporary).as_posix(), "sha256": digest(p.read_bytes()), "bytes": p.stat().st_size}
                for p in sorted(temporary.rglob("*")) if p.is_file()],
            "manifest_excludes_itself": True}
        put("delivery-manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
        if workspace.snapshot() != before or build_path.read_bytes() != build_bytes:
            raise ValueError("Source or current build changed during export")
        for name, raw in built_assets.items():
            if workspace.read_asset(name)[0] != raw:
                raise ValueError("Build artifact changed during export: " + name)
        for path, expected in original_bytes.items():
            if path.is_symlink() or digest(path.read_bytes()) != expected:
                raise ValueError("Evidence changed during export: " + path.name)
        if output.exists():
            # rmdir succeeds only if the original output is still empty.
            output.rmdir()
        temporary.rename(output)
        return {"output": str(output), "delivery_kind": status["delivery_kind"], "accepted": formal,
            "source_sha256": before["sha256"], "counts": status["counts"],
            "files": len(manifest["files"]), "manifest_sha256": digest((output / "delivery-manifest.json").read_bytes())}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="原子导出现有有效构建、源码与真实证据；候选会明确标记，拒绝覆盖非空目录")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(export_project(args.run_dir, args.output), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"exported": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
