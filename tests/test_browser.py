import os
from pathlib import Path

import pytest

from game_harness.validation import BrowserValidator


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Set RUN_BROWSER_TESTS=1 after playwright install chromium")
@pytest.mark.parametrize("input_style", ["actions", "grid"])
async def test_real_chromium_three_viewports(settings, store, request_data, input_style):
    rid = store.create(request_data)["id"]
    html = (Path(__file__).parent / "fixtures" / "contract-game.html").read_text(encoding="utf-8")
    # Regression: a legitimate modal duplicates a toolbar action and covers it.
    overlay = '''<div id="overlay" style="position:fixed;inset:0;z-index:10;background:#1238;display:grid;place-items:center">
      <button type="button" data-action="start" onclick="window.__GAME__.state='running';this.parentElement.remove()">开始游戏</button></div>'''
    html = html.replace("<script>", overlay + "<script>", 1)
    if input_style == "grid":
        html = html.replace('data-action="left"', 'data-index="0"').replace('data-action="right"', 'data-index="1"')
        html = html.replace("[data-action=left]", "[data-index=\"0\"]").replace("[data-action=right]", "[data-index=\"1\"]")
    report = await BrowserValidator(settings, store).validate(rid, 1, html)
    assert report["passed"], report
    assert len(report["viewports"]) == 3
    assert all(store.path(rid, v["screenshot"]).exists() for v in report["viewports"])
