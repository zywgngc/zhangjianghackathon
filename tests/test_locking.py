"""Real kernel contention and cleanup; no model or build service is needed."""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from game_harness.coding_agent import CodingAgent
from game_harness.locking import exclusive_lock, LockUnavailable, owner_lock
from game_harness.project_runtime import ProjectWorkspace


def test_separate_handle_contention_preserves_metadata_and_releases_after_exception(tmp_path):
    path = tmp_path / "owner.lock"
    with pytest.raises(ValueError, match="interrupted owner"):
        with exclusive_lock(path) as handle:
            handle.seek(0)
            handle.write(b"owner metadata")
            handle.truncate()
            handle.flush()
            with pytest.raises(LockUnavailable):
                with exclusive_lock(path):
                    pytest.fail("a second handle acquired an owned lock")
            handle.seek(0)
            assert handle.read() == b"owner metadata"
            raise ValueError("interrupted owner")
    with exclusive_lock(path):
        pass


def test_subprocess_contention_and_kernel_release_after_process_exit(tmp_path):
    path = tmp_path / "owner.lock"
    script = """
import sys
from pathlib import Path
from game_harness.locking import exclusive_lock
with exclusive_lock(Path(sys.argv[1])):
    print('owned', flush=True)
    sys.stdin.read()
"""
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-c", script, str(path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd=Path(__file__).resolve().parents[1])
    try:
        assert process.stdout.readline().strip() == "owned"
        with pytest.raises(LockUnavailable):
            with exclusive_lock(path):
                pytest.fail("parent acquired the child process's lock")
        process.kill()
        process.communicate(timeout=10)
        with exclusive_lock(path):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_coding_metadata_can_shrink_without_releasing_workspace_lock(tmp_path):
    workspace = SimpleNamespace(root=tmp_path)
    agent = CodingAgent(None, None, workspace)
    path = tmp_path / ".harness-coding.lock"
    with agent._workspace_lock("r" * 100, "f" * 100, "o" * 100):
        pass
    with agent._workspace_lock("r", "f", "中文 owner"):
        with pytest.raises(RuntimeError, match="already has a coding owner"):
            with CodingAgent(None, None, workspace)._workspace_lock("other", "other", "other"):
                pytest.fail("competing coding owner entered")
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "r"
    assert json.loads(path.read_text(encoding="utf-8"))["owner"] == "中文 owner"
    with agent._workspace_lock("next", "next", "next"):
        pass


async def test_contended_build_does_not_overwrite_owners_report(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    report_path = workspace._internal("build.json")
    report_path.write_text('{"owner":"active"}', encoding="utf-8")
    with exclusive_lock(workspace._internal("build.lock")):
        report = await workspace.build(install=False)
    assert report["errors"] == ["Another build owns this workspace"]
    assert report_path.read_text(encoding="utf-8") == '{"owner":"active"}'
    # The owner released its lock; a subsequent call reaches dependency checks.
    report = await workspace.build(install=False)
    assert "Pinned dependencies have not been installed" in report["errors"]


def test_app_lifespan_starts_and_releases_service_owner(settings):
    from game_harness.app import create_app

    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/api/config").status_code == 200
        with pytest.raises(RuntimeError, match="单个 uvicorn worker"):
            with owner_lock(settings.data_dir / "server.lock"):
                pytest.fail("a second service owner entered")
    with owner_lock(settings.data_dir / "server.lock"):
        pass


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
async def test_build_command_timeout_kills_owned_node_process_tree(tmp_path):
    workspace = ProjectWorkspace(tmp_path / "project", "phaser")
    workspace.initialize()
    report = await workspace._command("timeout", ["node", "-e",
        "require('child_process').spawn(process.execPath, ['-e', 'setTimeout(()=>{}, 30000)'], {stdio:'inherit'}); setTimeout(()=>{}, 30000)"],
        time.monotonic() + 1, workspace._environment())
    assert report["timed_out"] and report["exit_code"] == -9
    assert report["duration_seconds"] < 10
