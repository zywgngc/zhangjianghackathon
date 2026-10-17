"""The data-directory ownership lock rejects competing servers and releases."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from game_harness.app import _server_lock, create_app


ROOT = Path(__file__).resolve().parents[1]
CHILD = """
from pathlib import Path
import sys
from game_harness.app import _server_lock
try:
    with _server_lock(Path(sys.argv[1])):
        pass
except RuntimeError:
    raise SystemExit(13)
"""


def competing_process(directory):
    environment = os.environ.copy()
    environment["DATA_DIR"] = str(directory / "child-state")
    return subprocess.run([sys.executable, "-X", "utf8", "-c", CHILD, str(directory)],
                          cwd=ROOT, env=environment, capture_output=True, text=True, timeout=20)


def test_server_lock_rejects_other_process_then_can_be_reused(tmp_path):
    with _server_lock(tmp_path):
        contender = competing_process(tmp_path)
        assert contender.returncode == 13, contender.stderr
    successor = competing_process(tmp_path)
    assert successor.returncode == 0, successor.stderr


def test_server_lock_is_released_when_owner_raises(tmp_path):
    with pytest.raises(ValueError, match="startup failed"):
        with _server_lock(tmp_path):
            raise ValueError("startup failed")
    with _server_lock(tmp_path):
        pass


async def test_app_startup_keeps_lock_until_shutdown_and_releases_it(settings):
    class Agents:
        async def close(self):
            pass

    app = create_app(settings, agents_factory=lambda *_: Agents(), validator_factory=lambda *_: object())
    async with app.router.lifespan_context(app):
        with pytest.raises(RuntimeError, match="DATA_DIR"):
            with _server_lock(settings.data_dir):
                pass
    async with app.router.lifespan_context(app):
        assert app.state.harness is not None
