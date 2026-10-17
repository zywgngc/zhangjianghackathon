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


def bind_export_fixture(run, workspace):
    """Rebind synthetic boundary data, never representing real game acceptance."""
    snapshot = workspace.snapshot()
    build_path = workspace._internal("build.json")
    build = json.loads(build_path.read_text())
    build["source_sha256"] = snapshot["sha256"]
    dump(build_path, build)
    dump(run / "project-build-0.json", build)
    dump(run / "source-snapshot.json", snapshot)
    for filename, source_field in (("project-validation.json", "source_sha256"), ("project-evidence-0.json", "project_sha256")):
        value = json.loads((run / filename).read_text())
        value[source_field] = snapshot["sha256"]
        dump(run / filename, value)
    return snapshot


def write_validation_manifest(run, workspace):
    spec = load_case("GAME-TEST-002")
    files = [{"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
        for p in sorted(run.iterdir()) if p.is_file() and export_project.is_evidence_name(p.name) and p.name != "validation-manifest.json"]
    dump(run / "validation-manifest.json", {"source_sha256": workspace.snapshot()["sha256"], "spec_sha256": spec.sha256, "files": files})


@pytest.fixture
def external_export_run(export_run, tmp_path):
    run, old_workspace = export_run
    project = tmp_path / "external-project"
    old_workspace.root.rename(project)
    workspace = ProjectWorkspace(project, "phaser")
    bind_export_fixture(run, workspace)
    write_validation_manifest(run, workspace)
    return run, workspace


def test_export_external_workspace_preserves_cli_reports_and_ledger_root(external_export_run, tmp_path, capsys):
    run, workspace = external_export_run
    before = workspace.snapshot()
    originals = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    output = tmp_path / "external-delivery"
    code = export_project.main(["--run-dir", str(run), "--workspace", str(workspace.root), "--output", str(output)])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["delivery_kind"] == "candidate" and not result["accepted"]
    assert workspace.snapshot() == before and not (run / "project").exists()
    for name, raw in originals.items():
        assert (run / name).read_bytes() == (output / "evidence" / name).read_bytes() == raw
    ledger = json.loads((output / "evidence/project-evidence-0.json").read_text())
    assert ledger["artifact_root"] == str(run)
    status = json.loads((output / "delivery-status.json").read_text())
    assert status["validation_report_current"] and status["counts"]["passed"] == 0
    assert status["original_reports_modified"] is False


@pytest.mark.parametrize("parent,relation", [("run", "equal"), ("run", "inside"), ("run", "ancestor"),
    ("workspace", "equal"), ("workspace", "inside"), ("workspace", "ancestor")])
def test_external_export_output_is_independent_of_both_inputs(external_export_run, parent, relation):
    run, workspace = external_export_run
    source = run if parent == "run" else workspace.root
    output = source if relation == "equal" else source / "delivery" if relation == "inside" else source.parent
    before = workspace.snapshot()
    with pytest.raises(ValueError, match="independent"):
        export_project.export_project(run, output, workspace=workspace.root)
    assert workspace.snapshot() == before


@pytest.mark.parametrize("target", ["run", "workspace", "output"])
def test_external_export_refuses_symlink_roots(external_export_run, tmp_path, target):
    run, workspace = external_export_run
    output = tmp_path / "export"
    link = tmp_path / "link"
    link.symlink_to(run if target == "run" else workspace.root if target == "workspace" else tmp_path / "destination", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        export_project.export_project(link if target == "run" else run, link if target == "output" else output,
            workspace=link if target == "workspace" else workspace.root)
    assert not output.exists()


@pytest.mark.parametrize("tamper", ["source", "dist", "validation_source", "spec", "spec_newlines", "ledger_source", "ledger_root",
    "snapshot", "archived_build", "validation_manifest", "manifest_file", "missing_build"])
def test_external_export_rejects_mixed_source_spec_build_and_evidence(external_export_run, tmp_path, tamper):
    run, workspace = external_export_run
    if tamper == "source":
        path = workspace.root / "src/style.css"
        path.write_text(path.read_text() + "\nbody { color: red; }")
        # Even a fresh build cache cannot make old validation refer to new source.
        build = json.loads(workspace._internal("build.json").read_text())
        build["source_sha256"] = workspace.snapshot()["sha256"]
        dump(workspace._internal("build.json"), build)
    elif tamper == "dist":
        path = workspace.root / "dist/index.html"
        path.write_text(path.read_text() + "changed")
    elif tamper == "spec":
        path = run / "specification.txt"
        path.write_text(path.read_text() + "changed")
    elif tamper == "spec_newlines":
        path = run / "specification.txt"
        # Universal-newline text reads must not conceal a changed raw spec SHA.
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    elif tamper == "missing_build":
        (run / "project-build-0.json").unlink()
        (run / "validation-manifest.json").unlink()
    else:
        filenames = {"validation_source": "project-validation.json", "ledger_source": "project-evidence-0.json",
            "ledger_root": "project-evidence-0.json", "snapshot": "source-snapshot.json", "archived_build": "project-build-0.json",
            "validation_manifest": "validation-manifest.json", "manifest_file": "project-validation.json"}
        path = run / filenames[tamper]
        value = json.loads(path.read_text())
        if tamper == "ledger_source":
            value["project_sha256"] = "a" * 64
        elif tamper == "ledger_root":
            value["artifact_root"] = str(tmp_path / "unrelated-run")
        elif tamper == "snapshot":
            value["files"][0]["sha256"] = "a" * 64
        elif tamper == "archived_build":
            value["artifact_manifest"][0]["sha256"] = "a" * 64
        elif tamper == "manifest_file":
            value["unrelated_mutation"] = "This must invalidate the raw CLI manifest hash"
        else:
            value["source_sha256"] = "a" * 64
        dump(path, value)
        if tamper not in {"validation_manifest", "manifest_file"}:
            # A self-consistent new export index cannot conceal mismatched source
            # or build identities inside the actual original report documents.
            write_validation_manifest(run, workspace)
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="hash|snapshot|build|ledger"):
        export_project.export_project(run, output, workspace=workspace.root)
    assert not output.exists() and not list(tmp_path.glob(".rejected-*"))


def test_export_only_installed_production_license_whitelist_and_covers_manifest(export_run, tmp_path):
    run, workspace = export_run
    modules = workspace.root / "node_modules"
    notice_text = "Actual boundary fixture package notice, not a game acceptance claim.\n"
    installed = {"phaser": {"LICENSE": notice_text, "NOTICE": notice_text + "NOTICE", "credentials.json": "DO_NOT_EXPORT"},
        "@scope/example": {"LICENSE.md": notice_text + "SCOPED"}, "dev-only": {"LICENSE": "DEV_DO_NOT_EXPORT"},
        "phaser/node_modules/nested": {"COPYING": notice_text + "NESTED"}, "unlisted": {"LICENSE": "UNLISTED_DO_NOT_EXPORT"}}
    for package, files in installed.items():
        for filename, raw in files.items():
            path = modules / package / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    # Neither package nor ancestor links may turn a whitelisted name into a host credential.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "LICENSE").write_text("HOST_SECRET_DO_NOT_EXPORT")
    (modules / "linked-package").symlink_to(outside, target_is_directory=True)
    (modules / "@linked").symlink_to(outside, target_is_directory=True)
    (modules / "phaser/LICENSE.txt").symlink_to(outside / "LICENSE")
    lock_path = workspace.root / "package-lock.json"
    lock = json.loads(lock_path.read_text())
    lock["packages"].update({"node_modules/phaser": {}, "node_modules/@scope/example": {}, "node_modules/dev-only": {"dev": True},
        "node_modules/phaser/node_modules/nested": {}, "node_modules/linked-package": {}, "node_modules/@linked/example": {},
        "node_modules/../outside": {}, "node_modules/phaser/../../outside": {}, str(outside): {}, "node_modules/phaser/private": {}})
    dump(lock_path, lock)
    bind_export_fixture(run, workspace)
    output = tmp_path / "licensed-export"
    result = export_project.export_project(run, output)
    assert not result["accepted"]
    manifest = json.loads((output / "delivery-manifest.json").read_text())
    licenses = {row["path"]: row for row in manifest["files"] if row["path"].startswith("licenses/")}
    assert set(licenses) == {"licenses/npm/phaser/LICENSE", "licenses/npm/phaser/NOTICE", "licenses/npm/@scope/example/LICENSE.md",
        "licenses/npm/phaser/dependencies/nested/COPYING"}
    for name, row in licenses.items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == row["sha256"]
        assert "DO_NOT_EXPORT" not in (output / name).read_text()
    assert not any("node_modules" in Path(row["path"]).parts for row in manifest["files"])
    assert not any(p.is_symlink() for p in output.rglob("*"))


def test_export_detects_license_changes_during_copy(export_run, tmp_path, monkeypatch):
    run, workspace = export_run
    license_path = workspace.root / "node_modules/phaser/LICENSE"
    license_path.parent.mkdir()
    license_path.write_text("actual initial package license")
    original = export_project.dependency_licenses
    calls = 0
    def mutate_after_read(project):
        nonlocal calls
        result = original(project)
        calls += 1
        if calls == 1:
            license_path.write_text("changed during export")
        return result
    monkeypatch.setattr(export_project, "dependency_licenses", mutate_after_read)
    with pytest.raises(ValueError, match="licenses changed"):
        export_project.export_project(run, tmp_path / "unsafe")
    assert not (tmp_path / "unsafe").exists() and not list(tmp_path.glob(".unsafe-*"))


@pytest.mark.parametrize("marker", [{"official_acceptance": False}, {"accepted": False}, {"delivery_kind": "candidate"}])
def test_export_never_promotes_an_explicit_candidate_report(export_run, tmp_path, monkeypatch, marker):
    """Guard the labeling decision, independently of the real acceptance engine."""
    run, _ = export_run
    path = run / "project-validation.json"
    report = json.loads(path.read_text())
    report.update(marker)
    dump(path, report)
    original = path.read_bytes()
    # This stub solely exercises the explicit-candidate veto. Other tests use
    # the real evaluator, and this boundary fixture is not official evidence.
    monkeypatch.setattr(export_project, "evaluate_evidence", lambda *a: {"complete": True, "counts": {}, "tests": []})
    output = tmp_path / "candidate"
    result = export_project.export_project(run, output)
    assert not result["accepted"] and result["delivery_kind"] == "candidate"
    assert (output / "evidence/project-validation.json").read_bytes() == original
    assert any("explicitly" in reason for reason in json.loads((output / "delivery-status.json").read_text())["blockers"])


def test_export_rechecks_raw_evidence_during_copy(export_run, tmp_path, monkeypatch):
    run, _ = export_run
    original = export_project.dependency_licenses
    def mutate_original_report(project):
        result = original(project)
        path = run / "project-validation.json"
        value = json.loads(path.read_text())
        value["source_sha256"] = "b" * 64
        dump(path, value)
        return result
    monkeypatch.setattr(export_project, "dependency_licenses", mutate_original_report)
    with pytest.raises(ValueError, match="Evidence changed"):
        export_project.export_project(run, tmp_path / "unsafe")
    assert not (tmp_path / "unsafe").exists() and not list(tmp_path.glob(".unsafe-*"))


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
    # Export the independent CLI output directly, without copying, rewriting or
    # moving the source-bound evidence root to the source run directory.
    originals = {p.name: p.read_bytes() for p in args.output.iterdir() if p.is_file() and export_project.is_evidence_name(p.name)}
    result = export_project.export_project(args.output, tmp_path / "independent-delivery", workspace=workspace.root)
    assert result["delivery_kind"] == "candidate" and result["counts"]["passed"] == 0 and not result["accepted"]
    for name, raw in originals.items():
        assert (tmp_path / "independent-delivery/evidence" / name).read_bytes() == raw
        assert (args.output / name).read_bytes() == raw
    license_manifest = json.loads((tmp_path / "independent-delivery/delivery-manifest.json").read_text())
    assert any(row["path"] == "licenses/npm/phaser/LICENSE.md" for row in license_manifest["files"])
