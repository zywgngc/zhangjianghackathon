import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

from game_harness.project_runtime import ProjectWorkspace, WorkspaceError


def test_workspace_is_idempotent_and_preserves_generated_modules(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    initial = workspace.initialize()
    workspace.write_file("src/systems/combat.ts", "export const damage = 7;\n")
    after = workspace.initialize()
    assert initial["sha256"] != after["sha256"]
    assert workspace.read_file("src/systems/combat.ts") == "export const damage = 7;\n"
    assert workspace.snapshot() == after
    with pytest.raises(WorkspaceError, match="cannot change engines"):
        ProjectWorkspace(workspace.root, "babylon").initialize()


@pytest.mark.parametrize("path", ["../outside.ts", "/tmp/outside.ts", "src/../../outside.ts", "src/.private.ts", "package.json", "vite.config.mjs", "src/script.sh", "node_modules/evil.ts", "src\\evil.ts"])
def test_generated_writes_cannot_escape_or_change_toolchain(tmp_path, path):
    workspace = ProjectWorkspace(tmp_path / "project", "babylon")
    workspace.initialize()
    with pytest.raises(WorkspaceError):
        workspace.write_file(path, "malicious content")


def test_symlink_escape_is_rejected_for_read_write_snapshot_and_preview(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.ts").write_text("secret")
    (workspace.root / "src/link").symlink_to(outside, target_is_directory=True)
    for operation in [lambda: workspace.read_file("src/link/secret.ts"), lambda: workspace.write_file("src/link/secret.ts", "changed"), workspace.snapshot]:
        with pytest.raises(WorkspaceError, match="Symlink"):
            operation()
    assert (outside / "secret.ts").read_text() == "secret"


def test_patch_requires_exact_span_and_current_hash(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    workspace.write_file("src/state.ts", "export const hp = 10;\n")
    before = hashlib.sha256(workspace.read_file("src/state.ts").encode()).hexdigest()
    workspace.apply_patch("src/state.ts", "hp = 10", "hp = 15", expected_sha256=before)
    with pytest.raises(WorkspaceError, match="hash changed"):
        workspace.apply_patch("src/state.ts", "hp = 15", "hp = 20", expected_sha256=before)
    with pytest.raises(WorkspaceError, match="exactly one"):
        workspace.apply_patch("src/state.ts", "missing", "replacement")
    assert "hp = 15" in workspace.read_file("src/state.ts")


async def test_tampered_toolchain_is_never_executed(tmp_path, monkeypatch):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    (workspace.root / "package.json").write_text(json.dumps({"scripts": {"build": "touch /tmp/unsafe"}}))
    async def forbidden(*args, **kwargs):
        pytest.fail("Untrusted configuration reached subprocess execution")
    monkeypatch.setattr(workspace, "_command", forbidden)
    report = await workspace.build()
    assert not report["passed"]
    assert any("Trusted configuration was modified" in error for error in report["errors"])


def test_build_environment_does_not_forward_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/tmp/unsafe.js")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")
    monkeypatch.setenv("npm_config_token", "secret")
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    env = workspace._environment()
    assert not any(key in env for key in ["OPENAI_API_KEY", "NODE_OPTIONS", "SSH_AUTH_SOCK", "npm_config_token"])
    assert env["npm_config_ignore_scripts"] == "true"
    assert Path(env["HOME"]).is_relative_to(workspace.root)


def test_preview_requires_current_source_and_verified_artifact_hash(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    dist = workspace.root / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>verified build</h1>")
    report = {"passed": True, "source_sha256": workspace.snapshot()["sha256"], "artifact_manifest": workspace._artifact_manifest()}
    workspace._internal("build.json").write_text(json.dumps(report))
    assert workspace.read_asset("index.html") == (b"<h1>verified build</h1>", "text/html")
    (dist / "index.html").write_text("tampered")
    with pytest.raises(WorkspaceError, match="hash changed"):
        workspace.read_asset("index.html")
    (dist / "index.html").write_text("<h1>verified build</h1>")
    workspace.write_file("src/state.ts", "export const changed = true;")
    with pytest.raises(WorkspaceError, match="stale"):
        workspace.read_asset("index.html")


def test_inline_preview_embeds_manifest_assets_and_escapes_script_end_tags(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    dist = workspace.root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<html><head><link rel="stylesheet" href="./assets/app.css"><script type="module" src="./assets/app.js"></script></head><body><img src="/image.png"></body></html>')
    (dist / "assets/app.js").write_text('document.title = "</script>";')
    (dist / "assets/app.css").write_text('body { background: url(/image.png); }')
    (dist / "image.png").write_bytes(b"fixture asset bytes")
    workspace._internal("build.json").write_text(json.dumps({"passed": True, "source_sha256": workspace.snapshot()["sha256"], "artifact_manifest": workspace._artifact_manifest()}))
    preview = workspace.inline_preview()
    assert "<\\/script>" in preview
    assert "url(\"data:image/png;base64," in preview
    assert '<img src="data:image/png;base64,' in preview
    assert "connect-src &#x27;none&#x27;" in preview
    assert "./assets/app.js" not in preview


@pytest.mark.parametrize("specifier", ["../../../outside.ts", "node:fs", "https://example.org/code.js", "/etc/passwd", "/../../etc/passwd", "/@fs/etc/passwd", "data:text/javascript,import('node:fs')"])
def test_build_source_cannot_import_host_files_or_unpinned_dependencies(tmp_path, specifier):
    workspace = ProjectWorkspace(tmp_path / "project", "babylon")
    workspace.initialize()
    workspace.write_file("src/escape.ts", f"import '{specifier}';\n")
    with pytest.raises(WorkspaceError):
        workspace._validate_source()


@pytest.mark.parametrize("source", ['<script type="module" src=../../../../etc/passwd></script>', '<script type="module" src="/%40fs/etc/passwd"></script>', '<style>@import "../../../../etc/passwd";</style>'])
def test_html_entry_cannot_import_files_outside_the_run(tmp_path, source):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    workspace.write_file("index.html", source)
    with pytest.raises(WorkspaceError):
        workspace._validate_source()


@pytest.mark.skipif(os.environ.get("RUN_PROJECT_BUILDS") != "1", reason="Explicit real npm/build/browser verification")
@pytest.mark.parametrize("engine", ["phaser", "babylon"])
async def test_pinned_engine_build_renders_without_network(tmp_path, engine):
    from playwright.async_api import async_playwright
    workspace = ProjectWorkspace(tmp_path / engine, engine)
    workspace.initialize()
    report = await workspace.build(timeout=240)
    assert report["passed"], report
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = await browser.new_page(viewport={"width": 1100, "height": 800})
        errors = []
        external = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        async def route(request):
            parsed = urlparse(request.request.url)
            if parsed.hostname != "game.invalid":
                external.append(request.request.url)
                return await request.abort()
            raw, mime = workspace.read_asset(parsed.path.lstrip("/") or "index.html")
            await request.fulfill(body=raw, content_type=mime)
        await page.route("**/*", route)
        await page.goto("https://game.invalid/index.html")
        await page.wait_for_function("engine => { const root=document.querySelector('[data-testid=game-root]'); return root?.dataset.engineReady===engine && Number(root.dataset.renderFrames)>10; }", arg=engine, timeout=30_000)
        assert await page.locator("canvas").count() == 1
        assert not errors
        assert not external
        # The production UI keeps allow-same-origin disabled. A second load
        # proves that the actual bundled module works from an opaque srcdoc.
        preview = workspace.inline_preview()
        await page.set_content('<iframe sandbox="allow-scripts" style="width:1100px;height:800px;border:0"></iframe>')
        iframe = await page.locator("iframe").element_handle()
        await iframe.evaluate("(element, source) => { element.srcdoc = source; }", preview)
        frame = await iframe.content_frame()
        await frame.wait_for_function("engine => { const root=document.querySelector('[data-testid=game-root]'); return root?.dataset.engineReady===engine && Number(root.dataset.renderFrames)>10; }", arg=engine, timeout=30_000)
        assert not errors
        assert not external
        output = Path(os.environ.get("PROJECT_BUILD_ARTIFACTS", str(tmp_path / "verification")))
        output.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output / f"{engine}-render.png"))
        (output / f"{engine}-build.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        await browser.close()
    # A fresh run can use the primed cache without registry access. Dependency
    # versions and integrity hashes are identical across the two workspaces.
    offline = ProjectWorkspace(tmp_path / f"{engine}-offline", engine, cache_dir=workspace.cache_dir)
    offline.initialize()
    offline_report = await offline.build(timeout=240, offline=True)
    assert offline_report["passed"], offline_report
    assert "--offline" in offline_report["stages"][0]["command"]
    (output / f"{engine}-offline-build.json").write_text(json.dumps(offline_report, ensure_ascii=False, indent=2))
    # Diagnostics are from the real TypeScript compiler; a bad feature cannot
    # retain the previously publishable build marker or run Vite afterward.
    workspace.write_file("src/broken-feature.ts", "const count: number = 'invalid'; export { count };\n")
    failed = await workspace.build(install=False)
    assert not failed["passed"]
    assert failed["stages"][0]["name"] == "typecheck"
    assert "TS2322" in failed["stdout"]
    assert not any(stage["name"] == "bundle" for stage in failed["stages"])
    with pytest.raises(WorkspaceError, match="failed or stale"):
        workspace.read_asset("index.html")
