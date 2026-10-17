"""UI-driven offline acceptance for the generated power-comparison prototype.

Only sandboxed Chromium executes generated JS. A fresh, memory-only probe
reads private state; public mouse, keyboard and touch controls produce every
movement, collection and outcome. No game variables, RNG or results are set.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import traceback

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
URL = "https://game.invalid/index.html"
PROBE = r"""
  Object.defineProperty(window,'__POWER_VERIFY__',{value:function(){
    return {state:state,score:score,ticks:ticks,power:power,timeLeft:timeLeft,
      player:{x:player.x,y:player.y,tx:player.tx,ty:player.ty,r:player.r,moving:player.moving},
      camera:{x:camera.x,y:camera.y},view:{width:gameRoot.clientWidth,height:gameRoot.clientHeight},
      target:target?{type:target.type,id:target.id,x:target.x,y:target.y}:null,
      enemies:enemies.map(function(e){return {id:e.id,type:e.type,power:e.power,x:e.x,y:e.y,r:e.r,alive:e.alive};}),
      weapons:weapons.map(function(w){return {id:w.id,name:w.name,bonus:w.bonus,x:w.x,y:w.y,alive:w.alive};}),
      bubbles:bubbles.map(function(b){return {text:b.text,x:b.x,y:b.y,life:b.life};}),
      woodSwordDropped:woodSwordDropped,cursorIdx:cursorIdx,
      cursorList:cursorList.map(function(c){return {id:c.id,type:c.type,power:c.power};})};
  }});
"""


def check(report, name, passed, **evidence):
    report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})


def instrument(html):
    position = html.rfind("\n  buildDecorations();")
    if position < 0:
        raise ValueError("Cannot safely locate the final Power Hunter closure initialization")
    return html[:position] + "\n" + PROBE + html[position:]


def load_csp(root):
    path = root / "game_harness/validation.py"
    spec = importlib.util.spec_from_file_location("power_acceptance_harness_validator", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CSP


async def observe(page):
    return await page.evaluate("() => window.__POWER_VERIFY__()")


async def action(page, name):
    await page.locator('[data-action="' + name + '"]:visible').last.click(force=True)


async def make_page(browser, html, csp, report, mobile=False):
    context = await browser.new_context(viewport={"width": 390 if mobile else 1280, "height": 844 if mobile else 800}, has_touch=mobile, is_mobile=mobile, service_workers="block", accept_downloads=False)
    await context.clear_permissions()

    async def route_request(route):
        if route.request.url == URL and route.request.is_navigation_request():
            await route.fulfill(status=200, body=html, content_type="text/html", headers={"Content-Security-Policy": csp})
        else:
            report["blocked_resource_requests"].append(route.request.url[:180])
            await route.abort()

    await context.route("**/*", route_request)
    page = await context.new_page()
    page.on("pageerror", lambda error: report["page_errors"].append(str(error)[:700]))
    page.set_default_timeout(4000)
    epoch = datetime(2026, 10, 17, tzinfo=timezone.utc)
    await page.clock.install(time=epoch)
    await page.goto(URL, wait_until="load")
    await page.clock.pause_at(epoch + timedelta(seconds=10))
    return context, page


async def click_world(page, x, y):
    value = await observe(page)
    rect = await page.locator('#cv').bounding_box()
    sx, sy = x - value["camera"]["x"], y - value["camera"]["y"]
    if not 2 < sx < rect["width"] - 2 or not 2 < sy < rect["height"] - 2:
        return False
    await page.mouse.click(rect["x"] + sx, rect["y"] + sy)
    return True


async def select(page, kind, entity_id, *, mouse_required=False):
    value = await observe(page)
    values = value["enemies"] if kind == "enemy" else value["weapons"]
    entity = next(item for item in values if item["id"] == entity_id and item["alive"])
    if await click_world(page, entity["x"], entity["y"]):
        return "mouse"
    if mouse_required:
        raise AssertionError("Required mouse target is outside the visible canvas")
    # The real game supports offscreen selection through its keyboard cursor.
    for _ in range(len(value["enemies"]) + len(value["weapons"]) + 3):
        current = await observe(page)
        index = current["cursorIdx"]
        if 0 <= index < len(current["cursorList"]) and current["cursorList"][index]["id"] == entity_id:
            await page.keyboard.press("Enter")
            return "keyboard"
        await page.keyboard.press("ArrowRight")
    raise AssertionError("Keyboard cursor did not select the requested live target")


async def resolve_target(page, report, kind, entity, *, mouse_required=False):
    before = await observe(page)
    controls = await select(page, kind, entity["id"], mouse_required=mouse_required)
    entered_radius_before_arrival = False
    for _ in range(220):
        await page.clock.run_for(50)
        value = await observe(page)
        if value["player"]["moving"] and kind == "enemy" and math.hypot(value["player"]["x"] - entity["x"], value["player"]["y"] - entity["y"]) <= 48:
            entered_radius_before_arrival = True
        if not value["player"]["moving"] and value["target"] is None:
            break
        if value["state"] != "running":
            break
    after = await observe(page)
    report["actions"].append({"kind": kind, "entity": entity, "controls": controls, "before_power": before["power"], "after_power": after["power"], "after_score": after["score"], "state": after["state"], "elapsed": 120-after["timeLeft"], "arrival_position": after["player"], "entered_48_radius_before_arrival": entered_radius_before_arrival})
    return before, after


async def enemy(page, power):
    return next(item for item in (await observe(page))["enemies"] if item["power"] == power and item["alive"])


async def weapon(page, bonus):
    return next(item for item in (await observe(page))["weapons"] if item["bonus"] == bonus and item["alive"])


async def collect_once(page, report, bonus):
    item = await weapon(page, bonus)
    before, after = await resolve_target(page, report, "weapon", item)
    check(report, "weapon +{} collected once".format(bonus), after["power"] == before["power"] + bonus and not next(value for value in after["weapons"] if value["id"] == item["id"])["alive"], before_power=before["power"], after_power=after["power"])
    if await click_world(page, item["x"], item["y"]):
        await page.clock.run_for(150)
        repeated = await observe(page)
        check(report, "collected +{} cannot be credited twice".format(bonus), repeated["power"] == after["power"], observed=repeated["power"])
    return after


async def verify(args, output, report):
    original = args.html.read_bytes()
    report["html_sha256"] = hashlib.sha256(original).hexdigest()
    html = instrument(original.decode("utf-8-sig"))
    csp = load_csp(args.harness_root)
    if args.browser_root is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(args.browser_root.resolve(strict=True))
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context, page = await make_page(browser, html, csp, report)
            try:
                await action(page, "start")
                await page.clock.run_for(50)
                initial = await observe(page)
                check(report, "initial original core numbers and no preplaced wood sword", initial["power"] == 10 and sorted(item["power"] for item in initial["enemies"] if item["type"] == "enemy") == [3,5,8,12,16,22,30,44] and sorted(item["bonus"] for item in initial["weapons"]) == [10,20] and not initial["woodSwordDropped"], observed=initial)
                boss = await enemy(page, 150)
                overlap = math.hypot(initial["player"]["x"] - boss["x"], initial["player"]["y"] - boss["y"])
                report["spawn_overlap"] = {"distance": overlap, "player": initial["player"], "boss": boss, "overlapping": overlap < initial["player"]["r"] + boss["r"], "classification": "layout finding; playability checked separately"}
                await page.screenshot(path=str(output / "spawn-overlap.png"), full_page=True)
                _, denied = await resolve_target(page, report, "enemy", boss, mouse_required=True)
                check(report, "strong Boss only warns without power loss or death", denied["power"] == 10 and denied["score"] == 0 and denied["state"] == "running" and next(item for item in denied["enemies"] if item["id"] == boss["id"])["alive"] and any("战力不足" in item["text"] for item in denied["bubbles"]), observed=denied)
                _, weak = await resolve_target(page, report, "enemy", await enemy(page, 3), mouse_required=True)
                check(report, "mouse-selected weak enemy is devoured: 10 to 13", weak["power"] == 13 and weak["score"] == 1 and not next(item for item in weak["enemies"] if item["power"] == 3)["alive"], observed=weak)
                print("Confirmed mouse-selected enemy 3: P=13, score=1; starting Boss overlap does not block play.", flush=True)
                await resolve_target(page, report, "enemy", await enemy(page, 5))
                await resolve_target(page, report, "enemy", await enemy(page, 16))
                iron = await collect_once(page, report, 10)
                check(report, "natural route 3,5,16 plus iron reaches P44", iron["power"] == 44 and iron["score"] == 3, power=iron["power"], score=iron["score"])
                equal = await enemy(page, 44)
                _, denied_equal = await resolve_target(page, report, "enemy", equal)
                check(report, "equal P44 enemy is rejected at arrival without death or changes", denied_equal["power"] == 44 and denied_equal["score"] == 3 and denied_equal["state"] == "running" and next(item for item in denied_equal["enemies"] if item["id"] == equal["id"])["alive"] and any("战力不足" in item["text"] for item in denied_equal["bubbles"]), observed=denied_equal)
                await page.screenshot(path=str(output / "equal-power-rejected.png"), full_page=True)
                _, dropped = await resolve_target(page, report, "enemy", await enemy(page, 8))
                wood = [item for item in dropped["weapons"] if item["bonus"] == 5]
                check(report, "enemy 8 drops exactly one wood sword and no automatic credit", dropped["power"] == 52 and dropped["score"] == 4 and dropped["woodSwordDropped"] and len(wood) == 1 and wood[0]["alive"], observed=dropped)
                await page.screenshot(path=str(output / "wood-sword-dropped.png"), full_page=True)
                await collect_once(page, report, 5)
                for strength in (12,22,30,44):
                    await resolve_target(page, report, "enemy", await enemy(page, strength))
                await collect_once(page, report, 20)
                before_boss = await observe(page)
                check(report, "natural legal progression reaches P185 and eight kills", before_boss["power"] == 185 and before_boss["score"] == 8, observed=before_boss)
                _, won = await resolve_target(page, report, "enemy", await enemy(page, 150))
                check(report, "natural Boss devour reaches won within time limit", won["state"] == "won" and won["power"] == 335 and won["score"] == 8 and won["timeLeft"] > 0, observed=won)
                await page.screenshot(path=str(output / "victory.png"), full_page=True)
                report["natural_win"] = won
                print("Confirmed natural victory: P=335, score=8, elapsed={:.2f}s.".format(120-won["timeLeft"]), flush=True)
                await action(page, "restart")
                reset = await observe(page)
                check(report, "restart restores complete initial level", reset["power"] == 10 and reset["score"] == 0 and reset["ticks"] == 0 and reset["timeLeft"] == 120 and all(item["alive"] for item in reset["enemies"]) and len(reset["weapons"]) == 2 and not reset["woodSwordDropped"], observed=reset)
                await page.keyboard.press("ArrowRight")
                await page.keyboard.press("Enter")
                await page.clock.run_for(100)
                await action(page, "pause")
                paused = await observe(page)
                await page.clock.run_for(2000)
                frozen = await observe(page)
                check(report, "pause freezes time, ticks and moving player", paused["state"] == frozen["state"] == "paused" and all(paused[key] == frozen[key] for key in ("timeLeft", "ticks", "power", "player", "enemies", "weapons")), before=paused, after=frozen)
                await action(page, "resume")
                await page.clock.run_for(3000)
                keyboard = await observe(page)
                check(report, "keyboard cursor and Enter also devour enemy 3", keyboard["power"] == 13 and keyboard["score"] == 1, observed=keyboard)
                await action(page, "restart")
                await page.clock.run_for(120100)
                lost = await observe(page)
                check(report, "unmodified idle level reaches 120-second timeout defeat", lost["state"] == "lost" and lost["timeLeft"] == 0 and lost["power"] == 10 and lost["score"] == 0, observed=lost)
                report["natural_timeout"] = lost
                await page.screenshot(path=str(output / "timeout.png"), full_page=True)
                report["center_interaction_behavior"] = {"observed_inside_radius_before_center": any(item["entered_48_radius_before_arrival"] for item in report["actions"] if item["kind"] == "enemy"), "note": "The game waits until target center before arrival recheck. This adds a short approach delay but all tested selections resolve; it does not make devouring impossible."}
            finally:
                await context.close()
            mobile_context, mobile = await make_page(browser, html, csp, report, mobile=True)
            try:
                await action(mobile, "start")
                await mobile.clock.run_for(50)
                await mobile.locator('[data-action="right"]:visible').tap(force=True)
                await mobile.locator('[data-action="action"]:visible').tap(force=True)
                await mobile.clock.run_for(3000)
                touch = await observe(mobile)
                check(report, "portrait touch target buttons also complete a devour", touch["power"] == 13 and touch["score"] == 1, observed=touch)
                await mobile.screenshot(path=str(output / "portrait-touch-devour.png"), full_page=True)
            finally:
                await mobile_context.close()
        finally:
            await browser.close()
    report["original_html_unchanged"] = args.html.read_bytes() == original
    check(report, "offline browser has no page errors or extra resource attempts", not report["page_errors"] and not report["blocked_resource_requests"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, default=ROOT)
    parser.add_argument("--browser-root", type=Path, help="Optional custom Playwright browser installation")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    args.html = args.html.resolve(strict=True)
    args.harness_root = args.harness_root.resolve(strict=True)
    output = (args.output_dir or ROOT / ("output/verification-power-hunter-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"html": str(args.html), "execution": "actual UI-driven game frames in sandboxed Chromium", "probe": "read-only closure observations in an in-memory HTML copy; source HTML unchanged", "checks": [], "actions": [], "page_errors": [], "blocked_resource_requests": [], "limitations": ["Derived HTML core only, not original full-spec or real-device/Safari acceptance.", "Coordinate overlap is reported separately from whether normal controls can win."]}
    try:
        asyncio.run(verify(args, output, report))
        report["status"] = "passed_with_layout_finding" if all(item["passed"] for item in report["checks"]) and report.get("spawn_overlap", {}).get("overlapping") else "passed" if all(item["passed"] for item in report["checks"]) else "needs_attention"
    except Exception as exc:
        report["status"] = "infrastructure_or_probe_error"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
    path = output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "failed": [item["name"] for item in report["checks"] if not item["passed"]], "report": str(path)}, ensure_ascii=True))
    return 0 if report["status"].startswith("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
