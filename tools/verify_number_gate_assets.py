"""Observe the asset-enabled number-gate build without changing game state.

This acceptance check targets the generated October 17 build's public DOM.
Keyboard, mouse, and touch drive the game; Playwright's clock advances the
normal animation callbacks. A transparent drawImage wrapper records which
decoded pictures are actually painted and preserves the native call.
"""

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path

from playwright.async_api import async_playwright


CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; "
    "worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; sandbox allow-scripts"
)

DRAW_OBSERVER = """(() => {
  const native = CanvasRenderingContext2D.prototype.drawImage;
  const records = [];
  const seen = new WeakMap();
  Object.defineProperty(window, '__DRAW_OBSERVATIONS__', {value: records});
  CanvasRenderingContext2D.prototype.drawImage = function(image, ...args) {
    let item = seen.get(image);
    if (!item) {
      item = {source: image.currentSrc || image.src || '',
              naturalWidth: image.naturalWidth || image.width || 0,
              naturalHeight: image.naturalHeight || image.height || 0,
              count: 0, lastArgs: []};
      seen.set(image, item);
      records.push(item);
    }
    const result = native.call(this, image, ...args);
    item.count += 1;
    item.lastArgs = args;
    return result;
  };
  const nativeText = CanvasRenderingContext2D.prototype.fillText;
  const textStats = {totalCalls: 0, gateLabelCalls: 0, gateLabels: {}};
  const gateLabels = new Set(['+5', '+2', '×2', '-3', '+10', '÷2', '-5']);
  Object.defineProperty(window, '__TEXT_OBSERVATIONS__', {value: textStats});
  CanvasRenderingContext2D.prototype.fillText = function(text, ...args) {
    const result = nativeText.call(this, text, ...args);
    textStats.totalCalls += 1;
    // Gate panels use exactly the generated game's 34px label font.
    if (this.font.includes('34px') && gateLabels.has(String(text))) {
      textStats.gateLabelCalls += 1;
      textStats.gateLabels[String(text)] = (textStats.gateLabels[String(text)] || 0) + 1;
    }
    return result;
  };
})();"""


def image_observations(records):
    output = []
    for record in records:
        record = dict(record)
        source = record.pop("source")
        if source.startswith("data:image/png;base64,"):
            data = base64.b64decode(source.split(",", 1)[1], validate=True)
            record["sha256"] = hashlib.sha256(data).hexdigest()
            record["embedded_bytes"] = len(data)
        else:
            record["source"] = source
        output.append(record)
    return output


async def snapshot(page, label):
    value = await page.evaluate("""() => ({
      state: window.__GAME__.state, score: window.__GAME__.score,
      ticks: window.__GAME__.ticks,
      troops: Number(document.querySelector('#troopVal').textContent),
      remaining: Number(document.querySelector('#timeVal').textContent),
      boss: document.querySelector('#bossHp').textContent,
      feedback: document.querySelector('#feedback').textContent,
      overlay: document.querySelector('#ovDesc').textContent,
      canvas: (() => {const r=document.querySelector('canvas').getBoundingClientRect();
        return {x:r.x,y:r.y,width:r.width,height:r.height};})(),
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
      draws: window.__DRAW_OBSERVATIONS__,
      text_paints: window.__TEXT_OBSERVATIONS__
    })""")
    value["draws"] = image_observations(value["draws"])
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
            await request.fulfill(
                status=200, body=html, content_type="text/html",
                headers={"Content-Security-Policy": CSP},
            )
        else:
            errors.append("blocked request: " + request.request.url)
            await request.abort()

    await context.route("**/*", route)
    await context.add_init_script(DRAW_OBSERVER)
    page = await context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_default_timeout(5000)
    await page.clock.install()
    await page.goto("https://game.invalid/index.html", wait_until="load")
    await page.locator('#overlay [data-action="start"]').click()
    return context, page, errors


def soldier_x(value):
    # The generated single soldier is a 28 x 41 PNG painted at 22 x 32.
    for item in value["draws"]:
        if item["naturalWidth"] == 28 and item["naturalHeight"] == 41:
            args = item["lastArgs"]
            if len(args) == 4:
                return args[0] + args[2] / 2
    return None


async def input_probe(browser, html, output, mobile):
    context, page, errors = await open_game(browser, html, mobile=mobile)
    observations = []
    try:
        await advance_to(page, 0.5)
        initial = await snapshot(page, "initial_middle")
        observations.append(initial)
        await page.keyboard.press("ArrowLeft")
        await advance_to(page, 1.1)
        left = await snapshot(page, "keyboard_left")
        observations.append(left)
        button = page.locator('#controls [data-action="right"]')
        target = await button.evaluate("""el => {const r=el.getBoundingClientRect();
          const t=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          return {tag:t.tagName, action:t.getAttribute('data-action'), text:t.textContent};}""")
        await (button.tap() if mobile else button.click())
        await advance_to(page, 1.8)
        after = await snapshot(page, "one_right_button_after_keyboard_left")
        observations.append(after)
        original_x, left_x, after_x = map(soldier_x, (initial, left, after))
        passed = all(x is not None for x in (original_x, left_x, after_x)) and abs(after_x-original_x) < 5 and after_x-left_x > 80
        name = "input-touch.png" if mobile else "input-mouse.png"
        await page.screenshot(path=str(output / name), full_page=True)
        return {"mobile": mobile, "observations": observations, "button_center_target": target,
                "single_move_passed": passed, "soldier_x": [original_x, left_x, after_x], "errors": errors}
    finally:
        await context.close()


async def favorable_route(browser, html, output, mobile=False):
    context, page, errors = await open_game(browser, html, mobile=mobile)
    observations, checks = [], []
    prefix = "portrait" if mobile else "desktop"
    try:
        await page.keyboard.press("ArrowLeft")
        for seconds, expected in ((4.7, 1), (4.9, 6), (5, 6)):
            await advance_to(page, seconds)
            value = await snapshot(page, "route_at_" + str(seconds))
            observations.append(value)
            checks.append({"name": "observed_troops_at_" + str(seconds), "passed": value["troops"] == expected})
        await page.screenshot(path=str(output / (prefix + "-5.png")), full_page=True)
        # Pause just after the first gate, then verify the same gate is not applied again.
        await page.locator('#controls [data-action="pause"]').click()
        before = await snapshot(page, "paused_after_first_gate")
        await page.clock.run_for(2000)
        after = await snapshot(page, "paused_two_seconds")
        observations.extend((before, after))
        checks.append({"name": "pause_freezes_simulation_and_troops", "passed": before["state"] == "paused" and all(before[k] == after[k] for k in ("state", "ticks", "remaining", "troops"))})
        await page.locator('#controls [data-action="resume"]').click()
        for seconds, expected in ((6.3, 6), (7.5, 4), (10.9, 8), (12.3, 8), (13.5, 5), (16.9, 15), (18.3, 15), (21.5, 13), (22.9, 26), (25, 26)):
            await advance_to(page, seconds)
            value = await snapshot(page, "route_at_" + str(seconds))
            observations.append(value)
            checks.append({"name": "troops_at_" + str(seconds), "passed": value["troops"] == expected})
            if seconds in (18.3, 25):
                await page.screenshot(path=str(output / (prefix + "-" + str(seconds).replace(".", "_") + ".png")), full_page=True)
        await advance_to(page, 30.2)
        boss_start = await snapshot(page, "boss_appeared")
        observations.append(boss_start)
        await advance_to(page, 32)
        boss_mid = await snapshot(page, "boss_damage")
        observations.append(boss_mid)
        checks.append({"name": "boss_loses_health_from_real_bullets", "passed": int(boss_mid["boss"].split("/")[0].strip()) < 150})
        await advance_to(page, 40)
        final = await snapshot(page, "favorable_route_final")
        observations.append(final)
        checks.append({"name": "real_boss_defeat_before_deadline", "passed": final["state"] == "won" and int(final["boss"].split("/")[0].strip()) == 0 and final["remaining"] > 0})
        checks.append({"name": "three_images_actually_painted", "passed": len(final["draws"]) == 3 and all(item["count"] > 10 and item["naturalWidth"] > 0 for item in final["draws"])})
        checks.append({"name": "arithmetic_gate_panels_visible_before_choice", "passed": final["text_paints"]["gateLabelCalls"] > 0})
        await page.screenshot(path=str(output / (prefix + "-favorable-route.png")), full_page=True)
        await page.locator('#controls [data-action="restart"]').click()
        restarted = await snapshot(page, "restart_after_win")
        observations.append(restarted)
        checks.append({"name": "restart_resets_score_troops_time", "passed": restarted["state"] == "running" and restarted["score"] == 0 and restarted["troops"] == 1 and restarted["remaining"] > 44})
        # Timing is tested separately from the observed arithmetic progression.
        early = next(v for v in observations if v["label"] == "route_at_4.9")
        checks.append({"name": "first_gate_waits_until_requested_six_seconds", "passed": early["troops"] == 1})
        return {"mobile": mobile, "observations": observations, "checks": checks, "errors": errors}
    finally:
        await context.close()


async def floor_division_loss(browser, html, output):
    context, page, errors = await open_game(browser, html)
    values = []
    try:
        await page.keyboard.press("ArrowLeft")
        await advance_to(page, 7.8)  # +5 then sandbag -2 => 4.
        values.append(await snapshot(page, "four_after_first_sandbag"))
        await advance_to(page, 9.6)  # Wait until the right-lane wire has passed.
        await page.keyboard.press("ArrowRight")
        await page.keyboard.press("ArrowRight")  # Explicitly take the right gate.
        await advance_to(page, 12.2)  # 4 - 3 => 1.
        values.append(await snapshot(page, "one_after_minus_three"))
        await page.keyboard.press("ArrowLeft")  # Avoid the next right-lane obstacle.
        await advance_to(page, 18.5)  # floor(1/2) => 0 and immediate loss.
        final = await snapshot(page, "after_division")
        values.append(final)
        passed = values[0]["troops"] == 4 and values[1]["troops"] == 1 and final["troops"] == 0 and final["state"] == "lost" and "÷2" in final["feedback"]
        await page.screenshot(path=str(output / "floor-division-loss.png"), full_page=True)
        return {"observations": values, "passed": passed, "errors": errors}
    finally:
        await context.close()


async def deadline_loss(browser, html, output):
    context, page, errors = await open_game(browser, html)
    values = []
    try:
        await page.keyboard.press("ArrowLeft")
        await advance_to(page, 6.5)  # +5 => 6.
        await page.keyboard.press("ArrowRight")  # Avoid left sandbag.
        await advance_to(page, 9.6)  # Right wire has passed.
        await page.keyboard.press("ArrowRight")  # Explicitly take right -3 gate.
        await advance_to(page, 12.2)
        await page.keyboard.press("ArrowLeft")  # Avoid right sandbag; approach center from right.
        await advance_to(page, 18)  # right -3 then floor(/2) => 1.
        values.append(await snapshot(page, "one_after_right_gates"))
        await page.keyboard.press("ArrowRight")  # Avoid center sandbag.
        await advance_to(page, 22)
        await page.keyboard.press("ArrowLeft")
        await page.keyboard.press("ArrowLeft")  # left *2 => 2.
        await advance_to(page, 25)
        values.append(await snapshot(page, "two_before_boss"))
        await advance_to(page, 45.5)
        final = await snapshot(page, "deadline_with_boss_alive")
        values.append(final)
        passed = final["state"] == "lost" and final["troops"] == 2 and final["remaining"] == 0 and int(final["boss"].split("/")[0].strip()) > 0
        await page.screenshot(path=str(output / "deadline-loss.png"), full_page=True)
        return {"observations": values, "passed": passed, "errors": errors}
    finally:
        await context.close()


async def run(source, output, sections=None):
    output.mkdir(parents=True, exist_ok=False)
    data = source.read_bytes()
    result = {"source": str(source), "sha256": hashlib.sha256(data).hexdigest(),
              "scope": "Chromium real public keyboard/mouse/touch controls; normal animation callbacks via Playwright clock; native drawImage observation; no game-state or source edits."}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            html = data.decode("utf-8")
            for name, operation in (
                ("mouse_button", lambda: input_probe(browser, html, output, False)),
                ("touch_button", lambda: input_probe(browser, html, output, True)),
                ("desktop_route", lambda: favorable_route(browser, html, output, False)),
                ("portrait_route", lambda: favorable_route(browser, html, output, True)),
                ("floor_division_loss", lambda: floor_division_loss(browser, html, output)),
                ("deadline_loss", lambda: deadline_loss(browser, html, output)),
            ):
                if sections and name not in sections:
                    continue
                try:
                    result[name] = await operation()
                except Exception as error:
                    result[name] = {"error": type(error).__name__ + ": " + str(error)}
                (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            await browser.close()
    result["source_unchanged"] = hashlib.sha256(source.read_bytes()).hexdigest() == result["sha256"]
    checks = []
    for key, value in result.items():
        if not isinstance(value, dict):
            continue
        checks.extend({"section": key, **check} for check in value.get("checks", []))
        if "single_move_passed" in value:
            checks.append({"section": key, "name": "button_moves_exactly_one_lane", "passed": value["single_move_passed"]})
        if "passed" in value:
            checks.append({"section": key, "name": key, "passed": value["passed"]})
    result["summary"] = {"passed": sum(item["passed"] for item in checks), "failed": sum(not item["passed"] for item in checks), "checks": checks,
                         "operation_errors": [key for key, value in result.items() if isinstance(value, dict) and "error" in value]}
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output / "report.json"), "summary": result["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sections", help="Optional comma-separated acceptance sections to execute.")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.source.resolve(), arguments.output.resolve(),
                    set(arguments.sections.split(",")) if arguments.sections else None))
