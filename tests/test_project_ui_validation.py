"""Real browser UI probes with deterministic game fixtures, not real-case claims."""
import copy
import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from game_harness.acceptance import evaluate_assertion
from game_harness.project_ui_validation import capture_project_ui_scene, capture_isolated_project_ui, observe_project_power_labels, validate_project_ui


pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Requires real Chromium")


GAME = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body><main id="app"></main><script>
const loaded=JSON.parse(localStorage.getItem('fixture-progress')||'{"gold":0,"upgrades":{"A":0},"audio":{"music":true,"sfx":true},"unlockedLevel":1}');
let state={phase:'MENU',simTime:0,level:1,player:{power:10},progress:loaded};let screen='menu', purchases=0;
function save(){const data=JSON.parse(JSON.stringify(state.progress));if(BROKEN)data.upgrades.A=0;localStorage.setItem('fixture-progress',JSON.stringify(data));}
function render(){let content='';if(screen==='menu')content='<button data-action="start">开始游戏</button><button data-action="upgrades">永久升级</button><button data-action="settings">设置</button>';
if(screen==='shop')content='<h2>永久升级</h2><p>初始士兵 武器伤害 生命强化 攻击强化 身法强化 悟性强化 聚气强化 聚宝强化</p><button data-action="purchase">购买士兵升级 100 金币</button>';
if(screen==='settings')content='<h2>音乐与音效</h2><button data-action="toggle-music">音乐 '+state.progress.audio.music+'</button><button data-action="toggle-sfx">音效 '+state.progress.audio.sfx+'</button>';
if(screen==='play')content='<p>正常游戏</p><button data-action="pause">暂停</button>';
if(screen==='defeat')content='<h2>挑战失败</h2><button data-action="restart">重新开始</button>';
if(screen==='victory')content='<h2>胜利！金币 +100</h2>';
content+='<button data-action="menu">返回主菜单</button>';document.getElementById('app').innerHTML=content;
for(const b of document.querySelectorAll('button'))b.onclick=()=>dispatch({type:b.dataset.action});}
function dispatch(c){if(c.type==='menu'){screen='menu';state.phase='MENU';}if(c.type==='upgrades')screen='shop';if(c.type==='settings')screen='settings';
if(c.type==='start'||c.type==='restart'){screen='play';state.phase='PLAYING';state.simTime=0;}
if(c.type==='pause')state.phase='PAUSED';
if(c.type==='toggle-music'){state.progress.audio.music=!state.progress.audio.music;save();}
if(c.type==='toggle-sfx'){state.progress.audio.sfx=!state.progress.audio.sfx;save();}
if(c.type==='purchase'&&state.progress.gold>=100){purchases++;state.progress.gold-=100;state.progress.upgrades.A++;save();}render();}
window.__HARNESS__={snapshot:()=>JSON.parse(JSON.stringify(state)),config:()=>({}),dispatch,
advance(ms){if(state.phase!=='PLAYING')return;state.simTime+=ms/1000;if(state.simTime>=1){if(state.progress.upgrades.A>0){state.phase='DEFEAT';screen='defeat';}else{state.phase='VICTORY';screen='victory';state.progress.gold+=100;save();}render();}},
isolatedSimulation(){throw Error('No active fixtures allowed');},get purchases(){return purchases;}};render();
</script></body></html>'''

CARDS = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body><button data-action="start">开始修炼</button><div id="cards"></div><script>
let phase='MENU';const choices=[{id:'W01',name:'御剑术'},{id:'U01',name:'万剑诛仙'}];
const draw=()=>document.getElementById('cards').innerHTML=HIDDEN?'':'<button data-action="choose-W01">御剑术：飞剑伤害提升至十二点</button><button data-action="choose-U01" style="background:gold">万剑诛仙：终极进化，飞剑穿透目标</button>';
document.querySelector('button').onclick=()=>{phase='UPGRADE';draw();};
window.__HARNESS__={snapshot:()=>({phase,skills:{upgradeChoices:phase==='UPGRADE'?choices:[]}}),config:()=>({skills:choices})};
</script></body></html>'''

LABELS = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>.power-label{position:absolute;top:65px;font:20px sans-serif}.hero{left:105px}.weak{left:205px}.cross{left:305px}</style></head><body><button id="attack">攻击弱敌</button><div id="labels"></div><script>
let state={phase:'PLAYING',level:1,player:{power:10},entities:[{id:'weak',kind:'enemy',power:3,active:true},{id:'cross',kind:'enemy',power:12,active:true}]};
function draw(){document.getElementById('labels').innerHTML=HIDDEN?'':`<span class="power-label hero" data-entity-id="player">${state.player.power}</span>`+state.entities.filter(e=>e.active).map(e=>`<span class="power-label ${e.id}" data-entity-id="${e.id}" style="color:${state.player.power>e.power?'green':'red'}">${e.power}</span>`).join('');}
document.getElementById('attack').onclick=()=>{state.player.power+=3;state.entities[0].active=false;draw();};
window.__HARNESS__={snapshot:()=>JSON.parse(JSON.stringify(state)),projectEntity:id=>({x:id==='player'?120:id==='weak'?220:320,y:110}),config:()=>({})};draw();
</script></body></html>'''


class Recorder:
    def __init__(self, store):
        self.store = store

    def _evidence(self, rid, spec, evidence, round_no, label, trace, ids, assertions, **metadata):
        name = f"project-r{round_no}-{label}.json"
        digest = self.store.write(rid, name, trace)
        for identifier in ids:
            evidence["checks"].append({"requirement_id": identifier, "trace": trace, "assertions": assertions,
                "artifacts": [{"path": name, "sha256": digest}], **metadata})


async def page_for(browser, html):
    page = await browser.new_page(viewport={"width": 1000, "height": 700})
    async def route(request):
        await request.fulfill(body=html, content_type="text/html")
    await page.route("https://fixture.invalid/**", route)
    await page.goto("https://fixture.invalid/")
    return page


def setup_probe(store, case):
    run = store.create({"description": "UI验证fixture", "engine": "canvas"})
    spec = SimpleNamespace(case_id=case, sha256="a" * 64)
    evidence = {"project_sha256": "b" * 64, "checks": []}
    return Recorder(store), run["id"], spec, evidence


def passed(check):
    return all(evaluate_assertion(check["trace"], assertion) for assertion in check["assertions"])


@pytest.mark.parametrize("broken", [False, True])
async def test_real_purchase_refresh_settings_and_defeat_ui(store, settings, broken):
    validator, rid, spec, evidence = setup_probe(store, "GAME-TEST-001")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
        try:
            page = await page_for(browser, GAME.replace("BROKEN", "true" if broken else "false"))
            initial = await page.evaluate("window.__HARNESS__.snapshot()")
            await page.locator('[data-action="start"]').click()
            await page.evaluate("window.__HARNESS__.advance(1000)")
            final = await page.evaluate("window.__HARNESS__.snapshot()")
            routes = [{"mode": "normal_play", "fixture": None, "scenario_id": "earned-via-real-start", "inputs": [{"name": "start"}], "initial": initial, "final": final}]
            result = await validate_project_ui(validator, page, rid, spec, evidence, 0, routes)
            persistence = [check for check in evidence["checks"] if check["requirement_id"].endswith("/T30")]
            assert len(persistence) == 1
            assert passed(persistence[0]) is not broken
            assert persistence[0]["trace"]["after_purchase"]["progress"]["gold"] == 0
            assert persistence[0]["trace"]["after_purchase"]["progress"]["upgrades"]["A"] == 1
            settings_checks = [check for check in evidence["checks"] if check["requirement_id"].endswith("/T36")]
            assert len(settings_checks) == 1 and passed(settings_checks[0])
            assert result["screenshots"]
            assert all(check["fixture"] is None for check in evidence["checks"])
            if not broken:
                await page.locator('[data-action="menu"]').click()
                await page.locator('[data-action="start"]').click()
                await page.evaluate("window.__HARNESS__.advance(1000)")
                await capture_project_ui_scene(validator, page, rid, spec, evidence, 0, "defeat")
                assert any(check["requirement_id"].endswith("/T34") and passed(check) for check in evidence["checks"])
        finally:
            await browser.close()


async def test_no_legitimate_earned_currency_never_becomes_persistence_pass(store, settings):
    validator, rid, spec, evidence = setup_probe(store, "GAME-TEST-002")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
        try:
            page = await page_for(browser, GAME.replace("BROKEN", "false"))
            # Even a caller-supplied fixture-style success is not a normal route.
            fake = [{"mode": "normal_play", "fixture": {"gold": 100}, "inputs": [{"name": "start"}], "initial": {"progress": {"gold": 0}}, "final": {"progress": {"gold": 100}}}]
            result = await validate_project_ui(validator, page, rid, spec, evidence, 0, fake)
            assert not any(check["requirement_id"].endswith("/T59") for check in evidence["checks"])
            assert any("GAME-TEST-002/T59" in missing["acceptance_ids"] for missing in result["missing_executors"])
            assert await page.evaluate("window.__HARNESS__.purchases") == 0
        finally:
            await browser.close()


@pytest.mark.parametrize("label,works", [("重新挑战", True), ("继续冒险", False)])
async def test_specified_defeat_retry_label_requires_a_real_restart(store, settings, label, works):
    """The case2 spec's exact retry wording is a real control, not a forced state."""
    validator, rid, spec, evidence = setup_probe(store, "GAME-TEST-002")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
        try:
            page = await page_for(browser, GAME.replace("BROKEN", "false").replace('data-action="restart">重新开始', 'data-action="restart">' + label))
            await page.locator('[data-action="start"]').click()
            await page.evaluate("() => window.__HARNESS__.advance(1000)")
            await page.locator('[data-action="menu"]').click()
            await page.locator('[data-action="upgrades"]').click()
            await page.locator('[data-action="purchase"]').click()
            await page.locator('[data-action="menu"]').click()
            await page.locator('[data-action="start"]').click()
            await page.evaluate("() => window.__HARNESS__.advance(1000)")
            assert (await page.evaluate("() => window.__HARNESS__.snapshot()"))["phase"] == "DEFEAT"
            result = await capture_project_ui_scene(validator, page, rid, spec, evidence, 0, "defeat")
            assert any(check["requirement_id"].endswith("/T66") and passed(check) for check in evidence["checks"]) is works
            assert bool(result["errors"]) is (not works)
            if works:
                assert (await page.evaluate("() => window.__HARNESS__.snapshot()"))["phase"] == "PLAYING"
        finally:
            await browser.close()


@pytest.mark.parametrize("hidden", [False, True])
async def test_choices_require_real_visible_chinese_cards_not_snapshot_claims(store, settings, hidden):
    validator, rid, spec, evidence = setup_probe(store, "GAME-TEST-002")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
        try:
            page = await page_for(browser, CARDS.replace("HIDDEN", "true" if hidden else "false"))
            await page.locator('[data-action="start"]').click()
            result = await capture_project_ui_scene(validator, page, rid, spec, evidence, 0, "ultimate_evolution")
            assert len(evidence["checks"]) == 1
            assert passed(evidence["checks"][0]) is not hidden
            if not hidden:
                shot = result["screenshots"][0]
                raw = validator.store.path(rid, shot["path"]).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == shot["sha256"]
                assert shot["scene"] == "ultimate_evolution"
                assert shot["width"] == 1000 and shot["height"] == 700
        finally:
            await browser.close()


@pytest.mark.parametrize("hidden", [False, True])
async def test_real_projection_and_labels_capture_same_enemy_power_transition(store, settings, hidden):
    validator, rid, spec, evidence = setup_probe(store, "GAME-TEST-003")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
        try:
            page = await page_for(browser, LABELS.replace("HIDDEN", "true" if hidden else "false"))
            state_before = await page.evaluate("() => JSON.stringify(window.__HARNESS__.snapshot())")
            frame = await observe_project_power_labels(validator, page, rid, spec, evidence, 0)
            assert await page.evaluate("() => JSON.stringify(window.__HARNESS__.snapshot())") == state_before
            assert evidence["checks"] == []  # Observation alone declares no pass.
            assert {row["id"] for row in frame["observation"]["entities"]} == {"player", "weak", "cross"}
            assert all(bool(row["matchingLabels"]) is (not hidden) for row in frame["observation"]["entities"])
            before = await capture_project_ui_scene(validator, page, rid, spec, evidence, 0, "power_change_before")
            if hidden:
                assert before["errors"]
                assert not any(passed(check) for check in evidence["checks"])
                return
            await page.locator("#attack").click()
            after = await capture_project_ui_scene(validator, page, rid, spec, evidence, 0, "power_change_after")
            assert not before["errors"] and not after["errors"]
            assert {check["requirement_id"] for check in evidence["checks"]} == {"GAME-TEST-003/T02", "GAME-TEST-003/T03", "GAME-TEST-003/T24", "GAME-TEST-003/T25"}
            assert all(passed(check) for check in evidence["checks"])
            crossing = next(check for check in evidence["checks"] if check["requirement_id"].endswith("/T25"))["trace"]["same_enemy_threshold_crossings"][0]
            assert crossing["id"] == "cross"
            assert crossing["before_labels"][0]["color"] != crossing["after_labels"][0]["color"]
            assert after["screenshots"][0]["scene"] == "power_change_after"
        finally:
            await browser.close()


ISOLATED_VIEW = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body><div id="live"></div><script>
class ProductionSimulation {
 constructor(fixture=null){this.fixture=fixture;this.value={phase:fixture?'PLAYING':'MENU',player:{power:fixture?.player?.power||10,x:fixture?.player?.x||0,z:fixture?.player?.z||0},progress:{gold:0}};this.actions=0;}
 snapshot(){return JSON.parse(JSON.stringify(this.value));}
 config(){return {};}
 dispatch(command){this.actions++;if(command.type==='pause')this.value.phase='PAUSED';if(command.type==='resume'||command.type==='restart')this.value.phase='PLAYING';this.draw?.();}
 advance(ms){if(this.fixture&&ms>=100&&!this.fixture.enemies.length&&this.fixture.boss.power>=this.value.player.power)this.value.phase='DEADLOCK';this.draw?.();}
}
function mountProductionView(sim,root){sim.draw=()=>{root.innerHTML='<canvas width="480" height="200"></canvas><h3>'+(sim.value.phase==='DEADLOCK'?'僵局，无法继续':sim.value.phase)+'</h3><button data-action="restart">重新开始</button>';const ctx=root.querySelector('canvas').getContext('2d');ctx.fillStyle='green';ctx.fillRect(20,20,50,80);root.querySelector('button').onclick=()=>sim.dispatch({type:'restart'});};root.style.cssText='width:480px;min-height:300px;background:#ddd';sim.draw();}
const live=new ProductionSimulation();mountProductionView(live,document.getElementById('live'));
window.__HARNESS__={snapshot:()=>live.snapshot(),config:()=>live.config(),dispatch:c=>live.dispatch(c),
isolatedSimulation(options){const sim=new ProductionSimulation(options.fixture),root=document.createElement('div');root.id='isolated-view';document.body.appendChild(root);mountProductionView(sim,root);return {containerId:root.id,snapshot:()=>sim.snapshot(),config:()=>sim.config(),dispatch:c=>sim.dispatch(c),advance:ms=>sim.advance(ms),engineInfo:()=>({name:'fixture-renderer',version:'test-only',running:root.isConnected}),dispose(){root.remove();if(LEAK_LIVE)live.value.player.power++;}};},get liveActions(){return live.actions;}};
</script></body></html>'''


@pytest.mark.parametrize("leak", [False, True])
async def test_isolated_same_view_is_scoped_marked_and_checks_live_noninterference(store, settings, leak):
    validator, rid, spec, evidence = setup_probe(store, "GAME-TEST-003")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
        try:
            page = await page_for(browser, ISOLATED_VIEW.replace("LEAK_LIVE", "true" if leak else "false"))
            fixture = {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [], "weapons": [], "boss": {"id": "boss", "power": 150, "x": 3, "z": 0}}
            result = await capture_isolated_project_ui(validator, page, rid, spec, evidence, 0, fixture, "deadlock", [{"call": "advance", "milliseconds": 100}])
            assert not result["errors"]
            assert all(check["fixture"] == fixture and check["mode"] == "isolated_ui_boundary" for check in evidence["checks"])
            assert all(check["requirement_id"] == "GAME-TEST-003/T60" for check in evidence["checks"])
            if leak:
                assert result["adapter_failures"]
                assert evidence["checks"] == []
            else:
                assert evidence["checks"] and all(passed(check) for check in evidence["checks"])
            assert await page.locator("#isolated-view").count() == 0
            assert await page.evaluate("window.__HARNESS__.liveActions") == 0
            assert len(result["screenshots"]) == 1 and result["screenshots"][0]["scene"] == "deadlock"
        finally:
            await browser.close()


@pytest.mark.parametrize("phase", ["MOVING", "INTERACTING"])
async def test_isolated_view_freezes_real_active_subphases_and_resumes_normally(store, settings, phase):
    validator, rid, spec, evidence = setup_probe(store, "GAME-TEST-003")
    html = ISOLATED_VIEW.replace("LEAK_LIVE", "false").replace("fixture?'PLAYING':'MENU'", "fixture?'PLAYING':'" + phase + "'")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
        try:
            page = await page_for(browser, html)
            fixture = {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [], "weapons": [], "boss": {"id": "boss", "power": 150, "x": 3, "z": 0}}
            result = await capture_isolated_project_ui(validator, page, rid, spec, evidence, 0, fixture, "deadlock", [{"call": "advance", "milliseconds": 100}])
            assert not result["errors"] and not result["adapter_failures"]
            assert evidence["checks"] and all(passed(check) for check in evidence["checks"])
            assert await page.evaluate("window.__HARNESS__.liveActions") == 2
            assert await page.evaluate("window.__HARNESS__.snapshot().phase") == "PLAYING"
            isolation = next(check["trace"] for check in evidence["checks"] if "live_state_unchanged" in check["trace"])
            assert isolation["live_before"]["phase"] == isolation["live_after"]["phase"] == "PAUSED"
        finally:
            await browser.close()


@pytest.mark.parametrize("broken", ["pause", "resume"])
async def test_isolated_ui_rejects_a_broken_normal_pause_or_resume_adapter(store, settings, broken):
    validator, rid, spec, evidence = setup_probe(store, "GAME-TEST-003")
    html = ISOLATED_VIEW.replace("LEAK_LIVE", "false").replace("fixture?'PLAYING':'MENU'", "fixture?'PLAYING':'MOVING'")
    if broken == "pause":
        html = html.replace("if(command.type==='pause')this.value.phase='PAUSED';", "if(command.type==='pause'){};")
    else:
        html = html.replace("command.type==='resume'||command.type==='restart'", "command.type==='restart'")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
        try:
            page = await page_for(browser, html)
            fixture = {"player": {"power": 10, "x": 0, "z": 0}, "enemies": [], "weapons": [], "boss": {"id": "boss", "power": 150, "x": 3, "z": 0}}
            result = await capture_isolated_project_ui(validator, page, rid, spec, evidence, 0, fixture, "deadlock", [{"call": "advance", "milliseconds": 100}])
            assert result["adapter_failures"]
            assert not evidence["checks"]
            assert await page.locator("#isolated-view").count() == 0
        finally:
            await browser.close()
