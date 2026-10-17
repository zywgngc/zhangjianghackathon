import os
from pathlib import Path

import pytest

from game_harness.validation import BrowserValidator


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Set RUN_BROWSER_TESTS=1 after playwright install chromium")
async def test_real_chromium_three_viewports(settings, store, request_data):
    rid = store.create(request_data)["id"]
    html = (Path(__file__).parent / "fixtures" / "contract-game.html").read_text()
    report = await BrowserValidator(settings, store).validate(rid, 1, html)
    assert report["passed"], report
    assert len(report["viewports"]) == 3
    assert all(store.path(rid, v["screenshot"]).exists() for v in report["viewports"])
