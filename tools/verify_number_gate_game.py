"""Observe the generated number-gate game through public controls and clock.

This verifier never changes game variables, scoring, randomness, collision
handlers, or HTML. Playwright's clock runs normal animation callbacks; all
actions are keyboard/mouse/touch inputs. This is a game-specific acceptance
check, not a generic harness smoke test.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from playwright.async_api import async_playwright


_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; "
    "worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; sandbox allow-scripts"
)


async def snapshot(page, label):
    value = await page.evaluate("""() => ({
        state: window.__GAME__.state, score: window.__GAME__.score, ticks: window.__GAME__.ticks,
        troops: Number(document.querySelector('#troopVal').textContent),
        remaining: Number(document.querySelector('#timeVal').textContent),
        boss: document.querySelector('#bossHp').textContent,
        feedback: document.querySelector('#feedback').textContent,
        overlay: document.querySelector('#overlayMsg').textContent,
        canvas: (() => {const r=document.querySelector('canvas').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})(),
        root: (() => {const r=document.querySelector('[data-testid="game-root"]').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})()
    })""")
    value["label"] = label
    return value


async def advance_to(page, seconds):
    current = await snapshot(page, "clock_read")
    milliseconds = max(0, round((seconds - (45 - current["remaining"])) * 1000))
    if milliseconds:
        await page.clock.run_for(milliseconds)


async def open_game(browser, html, *, mobile=False):
    context = await browser.new_context(
        viewport={"width": 390 if mobile else 1280, "height": 844 if mobile else 800},
        has_touch=mobile, is_mobile=mobile, service_workers="block", accept_downloads=False,
    )
    errors = []

    async def route(request):
        if request.request.url == "https://game.invalid/index.html":
            await request.fulfill(status=200, body=html, content_type="text/html", headers={"Content-Security-Policy": _CSP})
        else:
            errors.append("blocked request: " + request.request.url)
            await request.abort()

    await context.route("**/*", route)
    page = await context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_default_timeout(5000)
    await page.clock.install()
    await page.goto("https://game.invalid/index.html", wait_until="load")
    await page.locator('#overlay [data-action="start"]').click()
    return context, page, errors


async def input_probe(browser, html, output, mobile):
    context, page, errors = await open_game(browser, html, mobile=mobile)
    try:
        await page.keyboard.press("ArrowLeft")  # Center -> left, once.
        button = page.locator('#touchPad [data-action="right"]')
        await (button.tap() if mobile else button.click())
        await advance_to(page, 6.4)
        value = await snapshot(page, "one_right_button_after_keyboard_left")
        # One right operation should return to the middle lane, bypassing the
        # left/right gate. A troop count of 3 identifies a second move to right.
        value["expected_troops_for_one_lane_move"] = 1
        value["single_move_passed"] = value["troops"] == 1
        await page.screenshot(path=str(output / ("input-touch.png" if mobile else "input-mouse.png")), full_page=True)
        return {"mobile": mobile, "observation": value, "errors": errors}
    finally:
        await context.close()


async def favorable_route(browser, html, output):
    context, page, errors = await open_game(browser, html)
    observations, checks = [], []
    try:
        await page.keyboard.press("ArrowLeft")
        await advance_to(page, 5.5)
        await page.locator('#controls [data-action="pause"]').click()
        before = await snapshot(page, "paused_before_first_gate")
        await page.clock.run_for(2000)
        after = await snapshot(page, "paused_after_two_seconds")
        observations.extend([before, after])
        checks.append({"name": "pause_freezes_clock_ticks_troops", "passed": all(before[key] == after[key] for key in ("state", "ticks", "remaining", "troops")) and before["state"] == "paused"})
        await page.locator('#controls [data-action="resume"]').click()
        for seconds, expected in ((6.3, 6), (7.3, 6), (9.3, 4), (10.3, 4), (12.3, 8), (18.3, 18), (21.3, 15), (24.3, 30), (27.3, 28)):
            await advance_to(page, seconds)
            value = await snapshot(page, "route_at_" + str(seconds))
            value["expected_troops"] = expected
            observations.append(value)
            checks.append({"name": "troops_at_" + str(seconds), "passed": value["troops"] == expected})
        await page.keyboard.press("ArrowRight")  # Aim from left lane to boss's center lane.
        await advance_to(page, 34.5)
        final = await snapshot(page, "favorable_route_final")
        observations.append(final)
        checks.append({"name": "real_boss_defeat_before_deadline", "passed": final["state"] == "won" and final["boss"].startswith("0 ") and final["remaining"] > 0})
        await page.screenshot(path=str(output / "favorable-route.png"), full_page=True)
        await page.locator('#overlay [data-action="restart"]').click()
        restarted = await snapshot(page, "restart_after_win")
        observations.append(restarted)
        checks.append({"name": "restart_resets_score_troops_and_time", "passed": restarted["state"] == "running" and restarted["score"] == 0 and restarted["troops"] == 1 and restarted["remaining"] > 44})
        return {"observations": observations, "checks": checks, "errors": errors}
    finally:
        await context.close()


async def floor_division_loss(browser, html):
    context, page, errors = await open_game(browser, html)
    values = []
    try:
        await page.keyboard.press("ArrowLeft")
        await advance_to(page, 9.3)  # 1 + 5 - 2 = 4.
        values.append(await snapshot(page, "after_first_gate_and_sand"))
        await page.keyboard.press("ArrowRight")
        await page.keyboard.press("ArrowRight")
        await advance_to(page, 12.3)  # 4 - 3 = 1.
        values.append(await snapshot(page, "after_minus_three"))
        await advance_to(page, 18.3)  # floor(1/2) = 0; immediate defeat.
        final = await snapshot(page, "after_division")
        values.append(final)
        return {"observations": values, "passed": final["troops"] == 0 and final["state"] == "lost" and "÷2" in final["feedback"], "errors": errors}
    finally:
        await context.close()


async def deadline_loss(browser, html, output):
    context, page, errors = await open_game(browser, html)
    try:
        # Remain in the middle to bypass all gates, dodge only the two middle
        # obstacles, then remain on the right so the center boss cannot be hit.
        await advance_to(page, 14)
        await page.keyboard.press("ArrowLeft")
        await advance_to(page, 16)
        await page.keyboard.press("ArrowRight")
        await advance_to(page, 26)
        await page.keyboard.press("ArrowRight")
        await advance_to(page, 45.5)
        final = await snapshot(page, "deadline_with_boss_alive")
        await page.screenshot(path=str(output / "deadline-loss.png"), full_page=True)
        return {"observation": final, "passed": final["state"] == "lost" and final["troops"] == 1 and final["remaining"] == 0 and "终点" in final["overlay"], "errors": errors}
    finally:
        await context.close()


async def run(source, output):
    output.mkdir(parents=True, exist_ok=False)
    data = source.read_bytes()
    result = {"source": str(source), "sha256": hashlib.sha256(data).hexdigest(), "scope": "Actual Chromium UI/keyboard/touch interaction with normal callbacks advanced by Playwright clock; no game-state injection"}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            html = data.decode("utf-8")
            result["mouse_button"] = await input_probe(browser, html, output, False)
            result["touch_button"] = await input_probe(browser, html, output, True)
            result["favorable_route"] = await favorable_route(browser, html, output)
            result["floor_division_loss"] = await floor_division_loss(browser, html)
            result["deadline_loss"] = await deadline_loss(browser, html, output)
        finally:
            await browser.close()
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output / "report.json"), "mouse_single_move": result["mouse_button"]["observation"]["single_move_passed"], "touch_single_move": result["touch_button"]["observation"]["single_move_passed"], "favorable_route_checks": result["favorable_route"]["checks"], "floor_division_loss": result["floor_division_loss"]["passed"], "deadline_loss": result["deadline_loss"]["passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.source.resolve(), arguments.output.resolve()))
