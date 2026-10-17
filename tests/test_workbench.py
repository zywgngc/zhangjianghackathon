"""Exercise the shipped UI, API authentication, and sandbox together."""
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.async_api import async_playwright

from game_harness.app import create_app


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Requires installed Chromium")
@pytest.mark.parametrize("protected", [False, True])
async def test_approved_game_loads_and_runs_inside_workbench(settings, request_data, protected):
    settings.api_token = "workbench-test-token" if protected else ""
    app = create_app(settings)
    store = app.state.store
    run = store.create(request_data)
    rid = run["id"]
    html = (Path(__file__).parent / "fixtures/contract-game.html").read_text(encoding="utf-8")
    store.write(rid, "v1.html", html)
    store.write(rid, "manifest.json", {"sha256": hashlib.sha256(html.encode()).hexdigest()})
    store.update(rid, status="passed", stage="complete", approved_version=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://harness.invalid") as client:
        async with async_playwright() as p:
            browser = await p.chromium.launch(chromium_sandbox=settings.browser_sandbox)
            try:
                page = await browser.new_page()
                messages = []
                page.on("console", lambda msg: messages.append(msg.text))
                page.on("pageerror", lambda error: messages.append(str(error)))
                async def route_request(route):
                    parsed = urlsplit(route.request.url)
                    if parsed.hostname != "harness.invalid":
                        await route.abort()
                        return
                    response = await client.request(route.request.method, parsed.path + ("?" + parsed.query if parsed.query else ""),
                        headers=route.request.headers, content=route.request.post_data_buffer)
                    await route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
                await page.route("**/*", route_request)
                if protected:
                    await page.add_init_script("if(location.hostname==='harness.invalid')sessionStorage.setItem('harness-token','workbench-test-token')")
                await page.goto("https://harness.invalid/#run=" + rid)
                game = page.frame_locator("#game")
                try:
                    await game.locator('[data-testid="game-root"]').wait_for(timeout=5000)
                except Exception:
                    pytest.fail("Embedded preview did not render: " + repr(messages))
                await game.locator('[data-action="start"]').click()
                await page.wait_for_timeout(150)
                frame = page.frames[-1]
                assert await frame.evaluate("window.__GAME__.state") == "running"
                assert await frame.evaluate("window.__GAME__.ticks") > 0
                assert await page.locator("#game").get_attribute("sandbox") == "allow-scripts"
                assert await frame.evaluate("location.origin") == "null"
                assert await frame.evaluate("(() => {try { return !!parent.document } catch(e) { return e.name }})()") == "SecurityError"
            finally:
                await browser.close()
