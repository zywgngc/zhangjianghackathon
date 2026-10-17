"""AST audit fixtures prove only module wiring, never official game behavior."""
import asyncio
import hashlib
import os
from pathlib import Path
import shutil

import pytest

from game_harness.project_integration import audit_project_integration
from game_harness.project_runtime import ProjectWorkspace

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def compiler_source():
    candidates = [Path(os.environ["TYPESCRIPT_COMPILER_PATH"])] if os.getenv("TYPESCRIPT_COMPILER_PATH") else []
    candidates += [ROOT / ".harness-dev/cli-verification/run/project/node_modules/typescript/lib/typescript.js",
        ROOT / "webmcp/node_modules/typescript/lib/typescript.js"]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        pytest.skip("A real pinned TypeScript 5.9.3 installation is required; no compiler stub is accepted")
    return source


@pytest.fixture
def workspace(tmp_path, compiler_source):
    project = ProjectWorkspace(tmp_path / "project", "phaser")
    project.initialize()
    target = project.root / "node_modules/typescript/lib/typescript.js"
    target.parent.mkdir(parents=True)
    # Copy the real compiler, never a fake parser or a link outside node_modules.
    shutil.copyfile(compiler_source, target)
    project.write_file("src/main.tsx", "export const entry = true;\n")
    return project


def plan(*paths):
    return {"features": [{"id": "mechanic-owner", "owned_paths": list(paths)}]}


def module(report, path):
    return next(row for row in report["modules"] if row["path"] == path)


def directory_bytes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


@pytest.mark.asyncio
async def test_disconnected_real_runtime_module_is_source_bound_and_never_executed(workspace, tmp_path):
    sentinel = tmp_path / "executed-game-code.txt"
    workspace.write_file("src/systems/shooting.ts",
        "import {writeFileSync} from 'node:fs';\nwriteFileSync(" + repr(str(sentinel)) + ", 'never');\nexport function updateShooting(){return 7;}\n")
    before = directory_bytes(workspace.root)
    snapshot = workspace.snapshot()
    report = await audit_project_integration(workspace, plan("src/systems/shooting.ts", "src/main.tsx"))
    assert report["status"] == "gaps_observed" and report["source_sha256"] == snapshot["sha256"]
    assert report["compiler"]["version"] == "5.9.3"
    assert [row["path"] for row in report["gaps"]] == ["src/systems/shooting.ts"]
    gap = report["gaps"][0]
    assert gap["owners"] == ["mechanic-owner"] and gap["sha256"]
    assert any(row["name"] == "updateShooting" and row["line"] == 3 for row in gap["runtime_declarations"])
    assert not sentinel.exists() and directory_bytes(workspace.root) == before
    assert report["source_unchanged"] and "passed" not in report and "acceptance" not in report


@pytest.mark.asyncio
async def test_actual_entry_runtime_import_reexport_and_literal_dynamic_graph(workspace):
    workspace.write_file("src/main.tsx", "import {Simulation} from './game/barrel'; new Simulation(); void import('./lazy');\n")
    workspace.write_file("src/game/barrel.ts", "export {Simulation} from './Simulation'; export type {State} from './types';\n")
    workspace.write_file("src/game/Simulation.ts", "import {step} from './system.js'; export class Simulation { constructor(){step();} }\n")
    workspace.write_file("src/game/system.ts", "export function step(){return 1;}\n")
    workspace.write_file("src/game/types.ts", "export interface State {power:number};\n")
    workspace.write_file("src/lazy.ts", "import './sideEffect'; export const lazy = 3;\n")
    workspace.write_file("src/sideEffect.ts", "globalThis.name = 'side effect';\n")
    paths = ["src/game/Simulation.ts", "src/game/system.ts", "src/game/barrel.ts", "src/lazy.ts", "src/sideEffect.ts", "src/game/types.ts"]
    report = await audit_project_integration(workspace, {"plan": plan(*paths)})
    assert report["status"] == "no_static_gap_observed" and not report["gaps"]
    assert report["counts"]["reachable"] == 5 and report["counts"]["type_only"] == 1
    witness = module(report, "src/game/system.ts")["witness"]
    assert [step["to"] for step in witness] == ["src/main.tsx", "src/game/barrel.ts", "src/game/Simulation.ts", "src/game/system.ts"]
    assert witness[2]["kind"] == "reexport"
    assert module(report, "src/lazy.ts")["witness"][-1]["kind"] == "dynamic_import"


@pytest.mark.asyncio
async def test_type_only_and_compiler_erased_imports_do_not_fake_runtime_wiring(workspace):
    workspace.write_file("src/main.tsx", "import {Config, unused} from './system'; let value:Config|undefined; export const main=1;\n")
    workspace.write_file("src/system.ts", "export interface Config {power:number}; export function unused(){return 7;}\n")
    workspace.write_file("src/types.ts", "export type Power = number; export interface State {power:Power};\n")
    report = await audit_project_integration(workspace, plan("src/system.ts", "src/types.ts"))
    assert module(report, "src/system.ts")["status"] == "unreachable"
    assert module(report, "src/types.ts")["status"] == "type_only"
    assert any(row["from"] == "src/main.tsx" and row["specifier"] == "./system" for row in report["erased_imports"])


@pytest.mark.asyncio
async def test_unused_value_import_and_type_reexport_cannot_hide_a_dropped_system(workspace):
    workspace.write_file("src/main.tsx", "import {step} from './system'; import './barrel'; export const main=1;\n")
    workspace.write_file("src/system.ts", "export function step(){return 7;} export interface Config{power:number};\n")
    workspace.write_file("src/barrel.ts", "export type {Config} from './system';\n")
    report = await audit_project_integration(workspace, plan("src/system.ts", "src/barrel.ts"))
    assert module(report, "src/system.ts")["status"] == "unreachable"
    assert module(report, "src/barrel.ts")["status"] == "type_only"


@pytest.mark.asyncio
async def test_mixed_type_and_used_value_import_reaches_actual_module(workspace):
    workspace.write_file("src/main.tsx", "import {type Config,step} from './system'; const config:Config={power:10}; step(config);\n")
    workspace.write_file("src/system.ts", "export interface Config {power:number}; export function step(c:Config){return c.power;}\n")
    report = await audit_project_integration(workspace, plan("src/system.ts"))
    assert module(report, "src/system.ts")["status"] == "reachable"
    assert report["status"] == "no_static_gap_observed"


@pytest.mark.asyncio
@pytest.mark.parametrize("loading", ["const path='./system'; void import(path);", "const loaders=import.meta.glob('./*.ts');", "const value=require('./system');", "import {x} from '@/system'; console.log(x);"])
async def test_ambiguous_reachable_loading_reports_unknown_not_disconnection(workspace, loading):
    workspace.write_file("src/main.tsx", loading)
    workspace.write_file("src/system.ts", "export const power=10;\n")
    report = await audit_project_integration(workspace, plan("src/system.ts"))
    assert report["status"] == "unknown" and not report["gaps"]
    assert module(report, "src/system.ts")["status"] == "unknown" and report["unknown"]


@pytest.mark.asyncio
async def test_unreachable_dynamic_module_does_not_poison_known_entry_graph(workspace):
    workspace.write_file("src/dormant.ts", "const file='./system';export function load(){return import(file);}\n")
    workspace.write_file("src/system.ts", "export const power=10;\n")
    report = await audit_project_integration(workspace, plan("src/system.ts"))
    assert module(report, "src/system.ts")["status"] == "unreachable" and not report["unknown"]


@pytest.mark.asyncio
async def test_css_assets_raw_source_and_import_text_are_not_runtime_edges(workspace):
    workspace.write_file("src/main.tsx", "import './style.css'; import text from './system.ts?raw'; document.title=text; const sample=\"import './system'\"; /* import './system'; */\n")
    workspace.write_file("src/system.ts", "export function step(){return 7;}\n")
    report = await audit_project_integration(workspace, plan("src/system.ts"))
    assert module(report, "src/system.ts")["status"] == "unreachable" and not report["unknown"]
    assert all(row["resolution"] == "asset" for row in report["edges"] if row["from"] == "src/main.tsx")


@pytest.mark.asyncio
async def test_inline_html_module_has_a_real_relative_entry_witness(workspace):
    workspace.write_file("index.html", '<!doctype html><script type="module">import {step} from "./src/system"; step();</script>')
    workspace.write_file("src/system.ts", "export function step(){return 7;}\n")
    report = await audit_project_integration(workspace, plan("src/system.ts"))
    row = module(report, "src/system.ts")
    assert row["status"] == "reachable" and row["witness"][0]["kind"] == "inline_module"
    assert row["witness"][0]["from"] == "index.html"


@pytest.mark.asyncio
async def test_unsupported_base_does_not_claim_a_local_entry(workspace):
    workspace.write_file("index.html", '<base href="https://external.invalid/"><script type="module" src="/src/main.tsx"></script>')
    report = await audit_project_integration(workspace, plan("src/main.tsx"))
    assert report["status"] == "unknown" and not report["gaps"]
    assert module(report, "src/main.tsx")["status"] == "unknown"


@pytest.mark.asyncio
async def test_concrete_missing_files_and_ownership_patterns_remain_observations(workspace):
    workspace.write_file("src/types.ts", "export interface State {power:number};")
    report = await audit_project_integration(workspace, plan("src/types.ts", "src/absent.ts", "src/**", "public/*.png"))
    assert module(report, "src/types.ts")["status"] == "type_only"
    assert module(report, "src/absent.ts")["status"] == "missing"
    assert len(report["skipped_owned_paths"]) == 2 and not report["gaps"]
    assert report["status"] == "unknown" and "passed" not in report


@pytest.mark.asyncio
@pytest.mark.parametrize("paths", [[], ["src/**"]])
async def test_empty_or_only_glob_scope_cannot_claim_static_integration(workspace, paths):
    report = await audit_project_integration(workspace, plan(*paths))
    assert report["status"] == "unknown" and not report["gaps"]
    assert any(row["reason"] == "no_concrete_plan_owned_typescript_modules" for row in report["unknown"])


@pytest.mark.asyncio
async def test_missing_compiler_is_unknown_without_installing_or_writing(tmp_path, monkeypatch):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    before = directory_bytes(workspace.root)
    async def forbidden(*a, **kw):
        pytest.fail("Unavailable compiler triggered a subprocess/install")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    report = await audit_project_integration(workspace, plan("src/App.tsx"))
    assert report["status"] == "unknown" and not report["gaps"]
    assert "unavailable" in report["unknown"][0]["error"]
    assert before == directory_bytes(workspace.root)


@pytest.mark.asyncio
async def test_source_change_invalidates_the_entire_static_gap_report(workspace, monkeypatch):
    workspace.write_file("src/system.ts", "export const power=10;")
    original = asyncio.create_subprocess_exec
    async def changing(*a, **kw):
        process = await original(*a, **kw)
        workspace.write_file("src/system.ts", "export const power=11;")
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", changing)
    report = await audit_project_integration(workspace, plan("src/system.ts"))
    assert report["status"] == "unknown" and not report["source_unchanged"] and not report["gaps"]
    assert module(report, "src/system.ts")["status"] == "unknown"


@pytest.mark.asyncio
async def test_unreadable_source_after_audit_remains_unknown_without_stale_gaps(workspace, monkeypatch):
    workspace.write_file("src/system.ts", "export const power=10;")
    snapshot = workspace.snapshot()
    calls = 0
    def observed():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ValueError("Source became unreadable during audit")
        return snapshot
    monkeypatch.setattr(workspace, "snapshot", observed)
    report = await audit_project_integration(workspace, plan("src/system.ts"))
    assert report["status"] == "unknown" and not report["source_unchanged"] and not report["gaps"]
    assert module(report, "src/system.ts")["status"] == "unknown"
    assert any(row["reason"] == "source_snapshot_unavailable_after_audit" for row in report["unknown"])


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_leaves_source_untouched(workspace):
    before = directory_bytes(workspace.root)
    report = await audit_project_integration(workspace, plan("src/main.tsx"), timeout=.000001)
    assert report["status"] == "unknown" and report["source_unchanged"]
    assert before == directory_bytes(workspace.root)
