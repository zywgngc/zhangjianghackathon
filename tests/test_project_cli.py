"""Delivery CLI boundaries; synthetic manifests never count as game acceptance."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from game_harness.project_runtime import ProjectWorkspace
from game_harness.specifications import load_case
from scripts import export_project, validate_project


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def validation_args(workspace, output):
    return SimpleNamespace(case_id="GAME-TEST-002", workspace=workspace, output=output,
        vision=False, offline=False, build_timeout=180, browser_timeout=25, no_browser_sandbox=False)


@pytest.mark.asyncio
async def test_failed_build_uses_actual_acceptance_gate_without_models_or_source_edits(tmp_path, monkeypatch):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    before = workspace.initialize()
    build = AsyncMock(return_value={"passed": False, "engine": "phaser", "source_sha256": before["sha256"], "errors": ["fixture typecheck failure"]})
    monkeypatch.setattr(ProjectWorkspace, "build", build)
    def forbidden(*a, **kw):
        raise AssertionError("Default validation attempted a model client")
    monkeypatch.setattr("game_harness.agents.AgentClient", forbidden)
    output = tmp_path / "report"
    code, summary = await validate_project.validate(validation_args(workspace.root, output))
    assert code == 1 and not summary["passed"] and not summary["build_passed"]
    assert summary["counts"]["passed"] == 0
    assert summary["counts"]["not_run"] > 0
    assert isinstance(summary["manual_required"], list)
    assert sum(summary["counts"].values()) == len(load_case("GAME-TEST-002").acceptance_tests)
    assert summary["source_unchanged"] and workspace.snapshot() == before
    assert summary["vision"] == {"requested": False, "executed": False}
    assert (output / "project-evidence-0.json").is_file()
    manifest = json.loads((output / "validation-manifest.json").read_text())
    for row in manifest["files"]:
        assert hashlib.sha256((output / row["path"]).read_bytes()).hexdigest() == row["sha256"]
    build.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_template_source_is_not_silently_regenerated(tmp_path, monkeypatch):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    (workspace.root / "src/main.tsx").unlink()
    before = workspace.snapshot()
    build = AsyncMock()
    monkeypatch.setattr(ProjectWorkspace, "build", build)
    code, summary = await validate_project.validate(validation_args(workspace.root, tmp_path / "report"))
    assert code == 1 and not summary["build_passed"]
    build.assert_not_awaited()
    assert workspace.snapshot() == before and not (workspace.root / "src/main.tsx").exists()
    assert "will not create source" in (tmp_path / "report/project-build-0.json").read_text()


def test_validation_output_cannot_overwrite_or_enter_workspace(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    occupied = tmp_path / "report"
    occupied.mkdir()
    (occupied / "precious.txt").write_text("keep")
    with pytest.raises(ValueError, match="nonempty"):
        validate_project.prepare_output(occupied, project)
    with pytest.raises(ValueError, match="independent"):
        validate_project.prepare_output(project / "report", project)
    assert (occupied / "precious.txt").read_text() == "keep"


def test_cli_entrypoints_return_failure_for_invalid_input(tmp_path, capsys):
    assert validate_project.main(["--case-id", "GAME-TEST-002", "--workspace", str(tmp_path / "absent"),
        "--output", str(tmp_path / "report")]) == 1
    assert export_project.main(["--run-dir", str(tmp_path / "absent"), "--output", str(tmp_path / "export")]) == 1
    assert "error" in capsys.readouterr().err


@pytest.fixture
def export_run(tmp_path):
    run = tmp_path / "source-run"
    run.mkdir()
    workspace = ProjectWorkspace(run / "project", "phaser")
    workspace.initialize()
    dist = workspace.root / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<!doctype html><html lang="zh-CN"><head></head><body>合成导出边界fixture；不是游戏验收证据</body></html>')
    build = {"passed": True, "engine": "phaser", "source_sha256": workspace.snapshot()["sha256"],
        "artifact_manifest": workspace._artifact_manifest(), "stages": [], "scope": "synthetic export boundary fixture"}
    dump(workspace._internal("build.json"), build)
    spec = load_case("GAME-TEST-002")
    (run / "specification.txt").write_text(spec.text)
    dump(run / "specification.json", spec.to_dict())
    dump(run / "project-validation.json", {"source_sha256": build["source_sha256"], "spec_sha256": spec.sha256,
        "passed": True, "errors": [], "adapter_failures": [], "acceptance": {"complete": True, "status": "passed", "tests": []}})
    dump(run / "project-evidence-0.json", {"artifact_root": str(run), "project_sha256": build["source_sha256"], "spec_sha256": spec.sha256, "checks": []})
    (run / "project/.env").write_text("NEVER_EXPORT=secret")
    (run / "project/node_modules").mkdir()
    (run / "project/node_modules/private-token.txt").write_text("NEVER_EXPORT")
    return run, workspace


def test_export_is_curated_atomic_and_recomputes_unproven_pass_as_candidate(export_run, tmp_path):
    run, workspace = export_run
    output = tmp_path / "export"
    before = workspace.snapshot()
    original = (run / "project-validation.json").read_bytes()
    summary = export_project.export_project(run, output)
    assert summary["delivery_kind"] == "candidate" and not summary["accepted"]
    assert summary["counts"]["passed"] == 0
    assert (output / "playable.html").is_file()
    assert (output / "evidence/project-validation.json").read_bytes() == original
    assert workspace.snapshot() == before
    assert not (output / "project/.env").exists()
    assert not (output / "project/node_modules").exists()
    assert not (output / "project/.harness").exists()
    manifest = json.loads((output / "delivery-manifest.json").read_text())
    for row in manifest["files"]:
        assert hashlib.sha256((output / row["path"]).read_bytes()).hexdigest() == row["sha256"]
    status = json.loads((output / "delivery-status.json").read_text())
    assert status["original_reports_modified"] is False and status["validation_report_current"] is True
    assert not list(tmp_path.glob(".export-*"))


@pytest.mark.parametrize("tamper", ["source", "artifact"])
def test_export_rejects_stale_source_or_dist_without_partial_delivery(export_run, tmp_path, tamper):
    run, workspace = export_run
    target = workspace.root / ("src/style.css" if tamper == "source" else "dist/index.html")
    target.write_text(target.read_text() + "\nchanged")
    output = tmp_path / "export"
    with pytest.raises(ValueError, match="stale|hash changed"):
        export_project.export_project(run, output)
    assert not output.exists()


def test_export_refuses_missing_tampered_or_escape_evidence(export_run, tmp_path):
    run, workspace = export_run
    report = json.loads((run / "project-validation.json").read_text())
    report["evidence_manifest"] = [{"artifacts": [{"path": "../.env", "sha256": "a" * 64}]}]
    dump(run / "project-validation.json", report)
    with pytest.raises(ValueError, match="curated"):
        export_project.export_project(run, tmp_path / "export")
    report["evidence_manifest"] = [{"artifacts": [{"path": "project-r0-real.json", "sha256": "a" * 64}]}]
    dump(run / "project-validation.json", report)
    dump(run / "project-r0-real.json", {"observed": "not a matching hash"})
    with pytest.raises(ValueError, match="hash changed"):
        export_project.export_project(run, tmp_path / "export")


def test_export_refuses_nonempty_output_and_symlink_artifacts(export_run, tmp_path):
    run, _ = export_run
    output = tmp_path / "export"
    output.mkdir()
    (output / "precious.txt").write_text("keep")
    with pytest.raises(ValueError, match="nonempty"):
        export_project.export_project(run, output)
    assert (output / "precious.txt").read_text() == "keep"
    (run / "project-r0-secret.json").symlink_to(run / "project/.env")
    with pytest.raises(ValueError, match="regular"):
        export_project.export_project(run, tmp_path / "safe")


def test_export_retains_hashed_static_integration_audit_without_promoting_game_acceptance(export_run, tmp_path):
    run, workspace = export_run
    audit = run / "project-integration-fixture.json"
    dump(audit, {"kind": "static_integration_audit", "source_sha256": workspace.snapshot()["sha256"],
        "status": "gaps_observed", "counts": {"unreachable": 1}, "official_acceptance": False})
    report = json.loads((run / "project-validation.json").read_text())
    report["integration_audit"] = {"artifact": audit.name, "sha256": hashlib.sha256(audit.read_bytes()).hexdigest(),
        "passed": False, "scope": "runtime_module_reachability", "official_acceptance": False,
        "source_sha256": workspace.snapshot()["sha256"]}
    dump(run / "project-validation.json", report)
    original = (run / "project-validation.json").read_bytes()
    output = tmp_path / "with-audit"
    result = export_project.export_project(run, output)
    assert not result["accepted"] and result["counts"]["passed"] == 0
    assert (output / "evidence" / audit.name).read_bytes() == audit.read_bytes()
    assert (output / "evidence/project-validation.json").read_bytes() == original
    manifest = json.loads((output / "delivery-manifest.json").read_text())
    assert any(row["path"] == "evidence/" + audit.name and row["sha256"] == report["integration_audit"]["sha256"] for row in manifest["files"])
    audit.write_text(audit.read_text() + "\nchanged")
    with pytest.raises(ValueError, match="hash changed"):
        export_project.export_project(run, tmp_path / "tampered-audit")
    assert not (tmp_path / "tampered-audit").exists()


@pytest.mark.skipif(os.getenv("RUN_PROJECT_BUILDS") != "1", reason="Real npm/Chromium CLI flow is opt-in")
@pytest.mark.asyncio
async def test_real_template_validation_fails_honestly_and_exports_current_candidate(tmp_path, monkeypatch):
    """A real engine smoke template lacks a game bridge; it must remain incomplete."""
    run = tmp_path / "run"
    run.mkdir()
    workspace = ProjectWorkspace(run / "project", "phaser")
    before = workspace.initialize()
    args = validation_args(workspace.root, tmp_path / "independent-report")
    args.browser_timeout = 20
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    code, summary = await validate_project.validate(args)
    assert code == 1 and summary["build_passed"] and not summary["passed"]
    assert summary["counts"]["passed"] == 0 and summary["source_unchanged"]
    assert workspace.snapshot() == before
    # Preserve original report bytes, without rewriting the ledger source root.
    for path in args.output.iterdir():
        if path.is_file() and export_project.is_evidence_name(path.name):
            (run / path.name).write_bytes(path.read_bytes())
    result = export_project.export_project(run, tmp_path / "delivery")
    assert result["delivery_kind"] == "candidate" and not result["accepted"]
    assert (tmp_path / "delivery/playable.html").read_text().startswith("<!doctype html>")
