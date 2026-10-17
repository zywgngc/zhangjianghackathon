"""Offline UI-driven gameplay checks for the generated Wuxia Canvas prototype.

Generated JavaScript runs only in sandboxed Chromium under the harness CSP.
A memory-only read probe observes closure state; no game state, RNG, timers,
health, experience or outcome is overwritten. Keyboard, touch and buttons drive
the game; Playwright's clock runs its actual animation frames faster than wall
time. The original HTML file is never modified.
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
DEFAULT_HARNESS = ROOT
URL = "https://game.invalid/index.html"
PROBE = r"""
  // Independent acceptance probe: fresh data snapshots only; no game writes.
  var __verifyIds = new WeakMap(), __verifyNextId = 1;
  function __verifyId(object){
    if (!__verifyIds.has(object)) __verifyIds.set(object, __verifyNextId++);
    return __verifyIds.get(object);
  }
  Object.defineProperty(window, '__WUXIA_VERIFY__', {value: function(){
    return {
      state:state, score:score, ticks:ticks, elapsed:elapsed, lastFrame:lastFrame,
      player:{x:player.x,y:player.y,hp:player.hp,invuln:player.invuln},
      stats:{level:stats.level,exp:stats.exp,dmgMul:stats.dmgMul,intervalMul:stats.intervalMul,speedMul:stats.speedMul},
      enemies:enemies.map(function(e){return {id:__verifyId(e),x:e.x,y:e.y,hp:e.hp,maxHp:e.maxHp};}),
      swords:swords.map(function(s){return {id:__verifyId(s),x:s.x,y:s.y,life:s.life};}),
      orbs:orbs.map(function(o){return {id:__verifyId(o),x:o.x,y:o.y};}),
      upgradeOpen:upgradeOpen,pendingUpgrades:pendingUpgrades,
      spawnTimer:spawnTimer,shootTimer:shootTimer,
      joy:{x:joyVec.x,y:joyVec.y,pointerId:joyPointerId}
    };
  }});
"""


def load_csp(harness):
    path = harness / "game_harness/validation.py"
    spec = importlib.util.spec_from_file_location("wuxia_acceptance_harness_validator", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CSP


def instrument(html):
    marker = "\n  boot();"
    position = html.rfind(marker)
    if position < 0:
        raise ValueError("Cannot safely locate the final Wuxia closure boot for the read-only probe")
    return html[:position] + "\n" + PROBE + html[position:]


def check(report, name, passed, **evidence):
    report["checks"].append({"name": name, "passed": bool(passed), "evidence": evidence})


async def snapshot(page):
    return await page.evaluate("() => window.__WUXIA_VERIFY__()")


async def keys(page, current, desired):
    desired = set(desired)
    for key in current - desired:
        await page.keyboard.up(key)
    for key in desired - current:
        await page.keyboard.down(key)
    return desired


async def click_action(page, action):
    # Force skips only actionability animation waits while the clock is paused;
    # a normal DOM pointer click still invokes the game's public UI.
    await page.locator('[data-action="' + action + '"]:visible').last.click(force=True)


async def new_page(browser, served_html, csp, report, *, mobile=False):
    context = await browser.new_context(
        viewport={"width": 390 if mobile else 1280, "height": 844 if mobile else 800},
        has_touch=True, is_mobile=mobile, service_workers="block", accept_downloads=False,
    )
    await context.clear_permissions()

    async def route_request(route):
        if route.request.url == URL and route.request.is_navigation_request():
            await route.fulfill(status=200, body=served_html, content_type="text/html", headers={"Content-Security-Policy": csp})
        else:
            report["blocked_resource_requests"].append(route.request.url[:180])
            await route.abort()

    await context.route("**/*", route_request)
    page = await context.new_page()
    page.on("pageerror", lambda error: report["page_errors"].append(str(error)[:700]))
    page.set_default_timeout(3500)
    epoch = datetime(2026, 10, 17, tzinfo=timezone.utc)
    await page.clock.install(time=epoch)
    await page.goto(URL, wait_until="load")
    await page.clock.pause_at(epoch + timedelta(seconds=10))
    return context, page


def freeze_fields(value):
    return {key: value[key] for key in ("state", "score", "ticks", "elapsed", "player", "stats", "enemies", "swords", "orbs", "spawnTimer", "shootTimer", "upgradeOpen", "pendingUpgrades")}


async def upgrade_checks(page, report, output):
    before = await snapshot(page)
    await page.clock.run_for(2000)
    after = await snapshot(page)
    check(report, "upgrade freezes state/ticks/time/entities", freeze_fields(before) == freeze_fields(after), before=before, after=after)
    disabled = await page.locator('#btnResume').is_disabled()
    await page.keyboard.press("Space")
    protected = await snapshot(page)
    check(report, "ordinary resume and Space cannot skip upgrade", disabled and protected["state"] == "paused" and protected["upgradeOpen"], resume_disabled=disabled, observed=protected)
    layouts = []
    for name, width, height in (("desktop", 1280, 800), ("portrait", 390, 844), ("landscape", 844, 390)):
        await page.set_viewport_size({"width": width, "height": height})
        await page.clock.run_for(150)
        boxes = await page.locator('[data-upgrade]').evaluate_all("""els => els.map(el => {
          const r=el.getBoundingClientRect(), root=document.querySelector('[data-testid="game-root"]').getBoundingClientRect();
          const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          return {choice:el.dataset.upgrade,rect:{x:r.x,y:r.y,width:r.width,height:r.height},
            fully_inside_stage:r.top>=root.top&&r.bottom<=root.bottom&&r.left>=root.left&&r.right<=root.right,
            fully_inside_viewport:r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth,
            center_hit:!!hit&&(hit===el||el.contains(hit))};
        })""")
        screenshot = output / ("upgrade-" + name + ".png")
        await page.screenshot(path=str(screenshot), full_page=True, timeout=15000)
        layouts.append({"viewport": name, "width": width, "height": height, "choices": boxes, "screenshot": str(screenshot)})
        check(report, "upgrade choices accessible in " + name, len(boxes) == 3 and all(item["fully_inside_stage"] and item["fully_inside_viewport"] and item["center_hit"] for item in boxes), choices=boxes, screenshot=str(screenshot))
    report["upgrade_layouts"] = layouts
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.clock.run_for(150)
    damage_before = (await snapshot(page))["stats"]["dmgMul"]
    await page.locator('[data-upgrade="dmg"]').click(force=True)
    await page.clock.run_for(100)
    chosen = await snapshot(page)
    check(report, "real upgrade button applies 20 percent damage and resumes", chosen["state"] == "running" and not chosen["upgradeOpen"] and math.isclose(chosen["stats"]["dmgMul"], damage_before * 1.2), observed=chosen)


def steering(value):
    player, enemies, orbs = value["player"], value["enemies"], value["orbs"]
    directions = [((), 0, 0), (("d",), 1, 0), (("a",), -1, 0), (("s",), 0, 1), (("w",), 0, -1),
                  (("d", "s"), 0.7071, 0.7071), (("d", "w"), 0.7071, -0.7071),
                  (("a", "s"), -0.7071, 0.7071), (("a", "w"), -0.7071, -0.7071)]
    target = min(orbs, key=lambda orb: math.hypot(orb["x"] - player["x"], orb["y"] - player["y"])) if orbs else None
    ranked = []
    for controls, dx, dy in directions:
        x = max(13, min(887, player["x"] + dx * 220 * value["stats"]["speedMul"] * 0.45))
        y = max(13, min(607, player["y"] + dy * 220 * value["stats"]["speedMul"] * 0.45))
        distances = [math.hypot(x - enemy["x"], y - enemy["y"]) for enemy in enemies]
        danger = sum(max(0, 105 - distance) ** 2 * 0.035 for distance in distances)
        clearance = min(distances) if distances else 250
        reward = min(clearance, 250) * 0.025 - danger - math.hypot(x - 450, y - 310) * 0.003
        if target:
            reward -= math.hypot(x - target["x"], y - target["y"]) * 0.035
        reward -= max(0, 55 - min(x, 900-x, y, 620-y)) * 0.05
        ranked.append((reward, controls))
    return max(ranked, key=lambda item: item[0])[1]


async def play_session(page, report, output, *, adaptive, inspect_upgrade=False):
    await click_action(page, "restart")
    await page.clock.run_for(50)
    held, history, births = set(), [], set()
    captured_upgrade = False
    partial_hp_seen = False
    maximum_enemies = 0
    last = None
    for _ in range(360):
        value = await snapshot(page)
        maximum_enemies = max(maximum_enemies, len(value["enemies"]))
        for enemy in value["enemies"]:
            births.add(enemy["id"])
            partial_hp_seen |= enemy["maxHp"] == 20 and math.isclose(enemy["hp"], 8)
        if last is None or any(value[key] != last[key] for key in ("state", "score")) or value["stats"]["level"] != last["stats"]["level"] or value["player"]["hp"] != last["player"]["hp"]:
            history.append({"elapsed": value["elapsed"], "state": value["state"], "score": value["score"], "hp": value["player"]["hp"], "level": value["stats"]["level"], "exp": value["stats"]["exp"], "enemy_count": len(value["enemies"]), "upgrade": value["upgradeOpen"]})
        if value["state"] in {"won", "lost"}:
            break
        if value["upgradeOpen"]:
            held = await keys(page, held, ())
            if inspect_upgrade and not captured_upgrade:
                await upgrade_checks(page, report, output)
                captured_upgrade = True
            else:
                await page.locator('[data-upgrade="rate"]').click(force=True)
            last = value
            continue
        if value["state"] == "paused":
            await click_action(page, "resume")
        held = await keys(page, held, steering(value) if adaptive else ())
        last = value
        await page.clock.run_for(250)
    held = await keys(page, held, ())
    final = await snapshot(page)
    screenshot = output / ("adaptive-outcome.png" if adaptive else "idle-outcome.png")
    await page.screenshot(path=str(screenshot), full_page=True, timeout=15000)
    return {"strategy": "keyboard movement chosen from read-only observations" if adaptive else "idle, choosing legitimate upgrades if reached", "final": final,
            "events": history, "maximum_observed_enemies": maximum_enemies, "unique_enemies_observed": len(births),
            "base_enemy_20_to_8_hp_observed": partial_hp_seen, "upgrade_inspected": captured_upgrade, "screenshot": str(screenshot)}


async def movement_checks(page, report):
    await click_action(page, "start")
    await page.clock.run_for(50)
    start = await snapshot(page)
    await page.keyboard.down("d")
    await page.clock.run_for(1000)
    await page.keyboard.up("d")
    straight = await snapshot(page)
    straight_distance = math.hypot(straight["player"]["x"] - start["player"]["x"], straight["player"]["y"] - start["player"]["y"])
    await click_action(page, "restart")
    await page.clock.run_for(50)
    start = await snapshot(page)
    await page.keyboard.down("d")
    await page.keyboard.down("s")
    await page.clock.run_for(1000)
    await page.keyboard.up("d")
    await page.keyboard.up("s")
    diagonal = await snapshot(page)
    diagonal_distance = math.hypot(diagonal["player"]["x"] - start["player"]["x"], diagonal["player"]["y"] - start["player"]["y"])
    check(report, "220 units per second and diagonal normalization", abs(straight_distance - 220) < 8 and abs(straight_distance - diagonal_distance) < 5, straight_distance=straight_distance, diagonal_distance=diagonal_distance)


async def touch_checks(browser, html, csp, report):
    context, page = await new_page(browser, html, csp, report, mobile=True)
    try:
        await click_action(page, "start")
        await page.clock.run_for(50)
        before = await snapshot(page)
        box = await page.locator('#joy').bounding_box()
        center = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}
        session = await context.new_cdp_session(page)
        await session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [dict(center, id=1)]})
        await session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": center["x"] + 45, "y": center["y"], "id": 1}]})
        await page.clock.run_for(700)
        moved = await snapshot(page)
        await session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        await page.clock.run_for(500)
        stopped = await snapshot(page)
        check(report, "mobile touch joystick moves and release stops", moved["player"]["x"] - before["player"]["x"] > 30 and math.hypot(stopped["player"]["x"] - moved["player"]["x"], stopped["player"]["y"] - moved["player"]["y"]) < 2, before=before["player"], moved=moved["player"], stopped=stopped["player"], joy=stopped["joy"])
    finally:
        await context.close()


async def verify(args, report, output):
    original = args.html.read_bytes()
    html = instrument(original.decode("utf-8-sig"))
    report["html_sha256"] = hashlib.sha256(original).hexdigest()
    csp = load_csp(args.harness_root)
    if args.browser_root is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(args.browser_root.resolve(strict=True))
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True, chromium_sandbox=True)
        try:
            context, page = await new_page(browser, html, csp, report)
            try:
                await movement_checks(page, report)
                adaptive = await play_session(page, report, output, adaptive=True, inspect_upgrade=True)
                report["adaptive_session"] = adaptive
                check(report, "natural gameplay reaches upgrade selection", adaptive["upgrade_inspected"], final=adaptive["final"], screenshot=adaptive["screenshot"])
                check(report, "natural survival reaches 60-second victory", adaptive["final"]["state"] == "won" and math.isclose(adaptive["final"]["elapsed"], 60), state=adaptive["final"]["state"], elapsed=adaptive["final"]["elapsed"], hp=adaptive["final"]["player"]["hp"])
                idle = await play_session(page, report, output, adaptive=False)
                report["idle_session"] = idle
                check(report, "natural contact damage reaches HP-zero defeat", idle["final"]["state"] == "lost" and idle["final"]["player"]["hp"] == 0, final=idle["final"], screenshot=idle["screenshot"])
                hp_events = [item["hp"] for item in idle["events"]]
                deltas = [previous - current for previous, current in zip(hp_events, hp_events[1:]) if current < previous]
                check(report, "observed contact losses are 8 except final clamp", bool(deltas) and all(delta == 8 or delta == 4 for delta in deltas), hp_losses=deltas)
                check(report, "observed base sword hit changes 20 HP to 8", adaptive["base_enemy_20_to_8_hp_observed"] or idle["base_enemy_20_to_8_hp_observed"])
                check(report, "observed enemy count never exceeds 24", max(adaptive["maximum_observed_enemies"], idle["maximum_observed_enemies"]) <= 24, adaptive_max=adaptive["maximum_observed_enemies"], idle_max=idle["maximum_observed_enemies"])
                await click_action(page, "restart")
                reset = await snapshot(page)
                check(report, "restart clears combat and upgrades", reset["state"] == "running" and reset["score"] == 0 and reset["ticks"] == 0 and reset["player"]["hp"] == 100 and reset["stats"] == {"level": 1, "exp": 0, "dmgMul": 1, "intervalMul": 1, "speedMul": 1} and not reset["enemies"] and not reset["swords"] and not reset["orbs"], observed=reset)
            finally:
                await context.close()
            await touch_checks(browser, html, csp, report)
        finally:
            await browser.close()
    report["original_html_unchanged"] = args.html.read_bytes() == original
    check(report, "offline browser has no page errors or extra resource attempts", not report["page_errors"] and not report["blocked_resource_requests"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, default=DEFAULT_HARNESS)
    parser.add_argument("--browser-root", type=Path, help="Optional custom Playwright browser installation")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    args.html = args.html.resolve(strict=True)
    args.harness_root = args.harness_root.resolve(strict=True)
    output = (args.output_dir or ROOT / ("output/verification-wuxia-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"html": str(args.html), "execution": "real_game_simulation_in_sandboxed_chromium", "controls": "Public keyboard, touch and buttons only; private-state probe is read-only", "clock": "Playwright run_for executes actual game frames; no state/result fast-forward assignments", "checks": [], "page_errors": [], "blocked_resource_requests": [], "limitations": ["Independent derived-core acceptance, not the original long-spec acceptance or real-device/Safari/WeChat validation.", "Upgrade viewport checks resize the same emulated page; separate portrait touch input is checked independently."]}
    try:
        asyncio.run(verify(args, report, output))
        report["status"] = "passed" if all(item["passed"] for item in report["checks"]) else "needs_attention"
    except Exception as exc:
        report["status"] = "infrastructure_or_probe_error"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
    path = output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "failed": [item["name"] for item in report["checks"] if not item["passed"]], "report": str(path)}, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
