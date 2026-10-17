import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import AsyncMock, patch

from game_harness.acceptance import evaluate_assertion
from game_harness.project_validation import INSTRUMENT_RENDERER, ProjectValidator, RUN_NORMAL_ROUTE, RUN_SCENARIO, verified_server
from game_harness.specifications import ingest_text, load_case


class FixtureStore:
    def __init__(self, root):
        self.root = root

    def directory(self, _):
        self.root.mkdir(exist_ok=True)
        return self.root

    def write(self, rid, name, value):
        content = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        (self.directory(rid) / name).write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    def event(self, *_):
        pass


class FixtureWorkspace:
    def __init__(self, root, html="<!doctype html><html lang='zh-CN'><body>真实浏览器隔离测试</body></html>".encode()):
        self.root, self.html = root, html
        self.sha = "a" * 64

    def snapshot(self):
        return {"sha256": self.sha}

    def read_asset(self, path):
        if path != "index.html":
            raise ValueError("Asset is not in the verified build")
        return self.html, "text/html"


class VerifiedServerTests(unittest.TestCase):
    def test_numeric_loopback_listener_does_not_depend_on_reverse_dns(self):
        with patch("socket.getfqdn", side_effect=AssertionError("DNS is unrelated to local artifact validation")):
            with verified_server(FixtureWorkspace(Path("/unused"))) as origin:
                with urlopen(origin + "/", timeout=2) as response:
                    self.assertEqual(response.status, 200)

    def test_serves_only_manifest_assets_on_an_ephemeral_loopback_port(self):
        workspace = FixtureWorkspace(Path("/unused"))
        with verified_server(workspace) as origin:
            self.assertTrue(origin.startswith("http://127.0.0.1:"))
            self.assertNotEqual(origin, "http://127.0.0.1:18080")
            with urlopen(origin + "/", timeout=2) as response:
                self.assertEqual(response.read(), workspace.html)
                self.assertIn("connect-src 'none'", response.headers["Content-Security-Policy"])
            for path in ["/../secret.txt", "/%2e%2e/secret.txt", "/src/main.ts", "/.env"]:
                with self.subTest(path=path), self.assertRaises(HTTPError):
                    urlopen(origin + path, timeout=2)


class ProjectValidationGates(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = FixtureStore(self.root / "evidence")
        self.workspace = FixtureWorkspace(self.root)
        self.validator = ProjectValidator(SimpleNamespace(browser_sandbox=True), self.store)
        self.spec = load_case("GAME-TEST-001")

    async def test_failed_build_never_launches_browser_or_passes_acceptance(self):
        self.validator._execute = AsyncMock()
        report = await self.validator.validate("test", self.spec, self.workspace, {"passed": False}, 0)
        self.assertFalse(report["passed"])
        self.assertEqual(report["acceptance"]["counts"]["passed"], 0)
        self.validator._execute.assert_not_awaited()

    async def test_changed_source_never_launches_browser(self):
        self.validator._execute = AsyncMock()
        report = await self.validator.validate("test", self.spec, self.workspace, {"passed": True, "source_sha256": "b" * 64}, 0)
        self.assertFalse(report["passed"])
        self.assertIn("Source changed", report["errors"][0])
        self.validator._execute.assert_not_awaited()

    async def test_tampered_dist_fails_before_browser_execution(self):
        self.validator._execute = AsyncMock()
        build = {"passed": True, "source_sha256": self.workspace.sha, "artifact_manifest": [{"path": "index.html", "sha256": "c" * 64}]}
        report = await self.validator.validate("test", self.spec, self.workspace, build, 0)
        self.assertFalse(report["passed"])
        self.assertIn("artifact hash changed", report["errors"][0])
        self.validator._execute.assert_not_awaited()

    async def test_empty_successful_execution_cannot_publish_untested_game(self):
        self.validator._execute = AsyncMock(return_value=None)
        build = {"passed": True, "source_sha256": self.workspace.sha, "artifact_manifest": []}
        report = await self.validator.validate("test", self.spec, self.workspace, build, 0)
        self.assertFalse(report["passed"])
        self.assertFalse(report["acceptance"]["complete"])
        self.assertEqual(report["acceptance"]["counts"]["passed"], 0)

    async def test_configured_browser_deadline_still_stops_execution_without_acceptance(self):
        async def wait_forever(*_):
            await asyncio.Event().wait()
        self.validator.settings.project_browser_timeout = .02
        self.validator._execute = wait_forever
        report = await self.validator.validate("test", self.spec, self.workspace,
            {"passed": True, "source_sha256": self.workspace.sha, "artifact_manifest": []}, 0)
        self.assertFalse(report["passed"])
        self.assertFalse(report["acceptance"]["complete"])
        self.assertEqual(report["acceptance"]["counts"]["passed"], 0)
        self.assertTrue(any("TimeoutError" in error for error in report["errors"]))

    async def test_custom_spec_uses_generic_smoke_and_cannot_publish_without_executor(self):
        self.validator._execute = AsyncMock()
        self.validator._generic_execute = AsyncMock()
        spec = ingest_text("请生成中文贪吃蛇，方向键移动，碰墙失败。", case_id="CUSTOM-SNAKE")
        report = await self.validator.validate("test", spec, self.workspace,
            {"passed": True, "source_sha256": self.workspace.sha, "artifact_manifest": []}, 0)
        self.assertFalse(report["passed"])
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["missing_executors"])
        self.validator._generic_execute.assert_awaited_once()
        self.validator._execute.assert_not_awaited()


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "Real Chromium tests are opt-in")
class RealProjectBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_power_strategy_waits_for_actual_absorption_lock_before_next_selection(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                page = await browser.new_page()
                await page.route("http://protocol.test/**", lambda route: route.fulfill(body="<!doctype html><body>normal inputs</body>", content_type="text/html"))
                await page.goto("http://protocol.test/")
                await page.evaluate("""() => {
                  let time=0,phase='MENU',locked=false,releaseAt=0,weak=true,boss=true,power=200,selected=null;
                  window.rejectedSelections=0;
                  window.__HARNESS__={
                    snapshot:()=>({phase,level:1,simTime:time,interactionLock:locked,player:{power,x:0,z:0},entities:[
                      {id:'weak',kind:'enemy',power:3,x:1,z:0,active:weak},
                      {id:'boss',kind:'boss',power:150,x:2,z:0,active:boss,hp:boss?1:0}]}),
                    command:(name,args)=>{if(name==='start')phase='PLAYING';else if(name==='selectTarget'){
                      if(locked){window.rejectedSelections++;return;}selected=args.id;}},
                    advance:ms=>{time+=ms/1000;
                      if(selected==='weak'&&weak){weak=false;locked=true;releaseAt=time+.4;selected=null;}
                      if(locked&&time>=releaseAt-1e-9){locked=false;power+=3;}
                      if(selected==='boss'&&!locked){boss=false;power+=150;phase='VICTORY';}},
                    events:()=>boss?[]:[{seq:1,type:'enemy_defeated',data:{enemyId:'boss'}}]
                  };
                }""")
                trace = await page.evaluate(RUN_NORMAL_ROUTE, {"route": {"id": "locked-power", "level": 1, "maxSimSeconds": 2},
                    "sourceHash": "a" * 64, "specHash": "b" * 64, "caseId": "GAME-TEST-003"})
                self.assertEqual(trace["final"]["phase"], "VICTORY")
                self.assertEqual(await page.evaluate("() => window.rejectedSelections"), 0)
                boss_input = next(item for item in trace["inputs"] if item.get("args", {}).get("id") == "boss")
                self.assertGreaterEqual(boss_input["simTime"], .6 - 1e-9)
                self.assertEqual(boss_input["before"]["playerPower"], 203)
            finally:
                await browser.close()

    async def test_normal_runner_records_boss_between_checkpoints_and_drains_terminal_events(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                page = await browser.new_page()
                await page.route("http://protocol.test/**", lambda route: route.fulfill(body="<!doctype html><body>normal protocol</body>", content_type="text/html"))
                await page.goto("http://protocol.test/")
                await page.evaluate("""() => {
                  class Simulation {
                    constructor(){this.phase='MENU';this.time=0;this.log=[];this.boss=null;this.needsMove=false;this.didUpgrade=false;this.player={hp:100,power:200,x:0,y:0,z:0};}
                    snapshot(){return {phase:this.phase,level:1,simTime:this.time,player:{...this.player,maxHp:100},entities:this.boss?[{...this.boss}]:[],skills:{active:[],passive:[],ultimate:[],upgradeChoices:[]},effectiveStats:{moveSpeed:220},upgradeChoices:this.phase==='LEVEL_UP'?[{id:'W01',type:'active',currentLevel:1,nextLevel:2}]:[]};}
                    config(){return {enemies:[],worldBounds:{minX:-800,maxX:800,minY:-800,maxY:800}};}
                    command(name,args){if(name==='start'){this.phase='PLAYING';this.time=0;this.log=[];this.boss=null;this.needsMove=false;this.didUpgrade=false;}else if(name==='chooseUpgrade'){this.phase='PLAYING';this.needsMove=true;}else if(name==='move')this.needsMove=false;else if(name==='selectTarget')this.target=args.id;else throw Error('Only normal commands are supported');}
                    advance(ms){if(this.needsMove)throw Error('Must observe and choose a new movement after upgrading');this.time+=ms/1000;if(!this.didUpgrade&&this.time>=.2){this.didUpgrade=true;this.phase='LEVEL_UP';}if(!this.boss)this.boss={id:'real-boss',kind:'boss',x:1,y:1,z:1,power:150,hp:3000,maxHp:3000,active:true};
                      if(this.time>=0.4){this.boss.hp=0;this.boss.active=false;this.player.power+=150;this.phase='VICTORY';this.log.push({seq:1,type:'damage',data:{targetId:this.boss.id,amount:3000}},{seq:2,type:'death',data:{entityId:this.boss.id}});}}
                    events(after){return this.log.filter(event=>event.seq>after);}
                  }
                  window.__HARNESS__=new Simulation();
                }""")
                trace = await page.evaluate(RUN_NORMAL_ROUTE, {"route": {"id": "normal", "level": 1, "maxSimSeconds": 1, "strategy": {}},
                    "sourceHash": "a" * 64, "specHash": "b" * 64, "caseId": "GAME-TEST-002"})
                self.assertEqual(trace["final"]["phase"], "VICTORY")
                self.assertEqual(len(trace["checkpoints"]), 1)
                self.assertTrue(any(item["name"] == "chooseUpgrade" for item in trace["inputs"]))
                self.assertEqual(trace["input_policy"]["decision_interval_ms"], 100)
                self.assertEqual(trace["input_policy"]["checkpoint_interval_ms"], 10000)
                self.assertEqual(len(trace["input_policy"]["controller_sha256"]), 64)
                self.assertTrue(all(item["name"] in {"start", "move", "chooseUpgrade"} for item in trace["inputs"]))
                self.assertEqual(trace["events"][-1]["type"], "death")
                self.assertTrue(trace["process_observations"][0]["entities"][0]["active"])
                self.assertTrue(evaluate_assertion(trace, {"pointer": "", "op": "normal_level_process",
                    "expected": {"case_id": "GAME-TEST-002", "level": 1, "bossHp": 3000}}))
                # Reusing the same production-input runner for the power case
                # records the actual pre-command power rather than inventing it.
                await page.evaluate("""() => {const api=window.__HARNESS__,command=api.command;
                  api.command=function(name,args){command.call(this,name,args);if(name==='start')this.boss={id:'real-boss',kind:'boss',x:1,y:1,z:1,power:150,hp:3000,maxHp:3000,active:true};};
                }""")
                trace = await page.evaluate(RUN_NORMAL_ROUTE, {"route": {"id": "normal-power", "level": 1, "maxSimSeconds": 1},
                    "sourceHash": "a" * 64, "specHash": "b" * 64, "caseId": "GAME-TEST-003"})
                selected = next(item for item in trace["inputs"] if item["name"] == "selectTarget")
                self.assertEqual(selected["before"]["playerPower"], 350)
                self.assertTrue(evaluate_assertion(trace, {"pointer": "", "op": "normal_level_process",
                    "expected": {"case_id": "GAME-TEST-003", "level": 1, "bossPower": 150}}))
            finally:
                await browser.close()

    async def test_function_polling_works_under_unrelaxed_production_csp(self):
        from playwright.async_api import async_playwright
        with tempfile.TemporaryDirectory() as folder:
            workspace = FixtureWorkspace(Path(folder))
            with verified_server(workspace) as origin:
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
                    try:
                        page = await browser.new_page()
                        response = await page.goto(origin + "/")
                        policy = response.headers["content-security-policy"]
                        self.assertNotIn("unsafe-eval", policy)
                        self.assertIn("script-src 'self'", policy)
                        await page.evaluate("() => {window.__HARNESS__={snapshot:()=>({phase:'MENU'})};}")
                        await page.wait_for_function("() => Boolean(window.__HARNESS__ && typeof window.__HARNESS__.snapshot==='function')")
                        self.assertEqual(await page.evaluate("() => window.__HARNESS__.snapshot().phase"), "MENU")
                    finally:
                        await browser.close()

    async def test_trusted_dsl_executes_independent_handles_and_retains_observations(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                page = await browser.new_page()
                await page.route("http://protocol.test/**", lambda route: route.fulfill(body="<!doctype html><body>protocol</body>", content_type="text/html"))
                await page.goto("http://protocol.test/")
                await page.evaluate("""() => {
                  window.disposed=0;
                  class Simulation {
                    constructor(f={}){this.time=0;this.player={x:0,z:0,...f.player};this.phase='PLAYING';}
                    snapshot(){return {simTime:this.time,phase:this.phase,player:{...this.player},entities:[{id:'target',x:3,z:0}]};}
                    dispatch(c){if(!['start','move','pause','resume'].includes(c.type))throw Error('Unexpected non-user command');if(c.type==='move')this.player.x=c.x;if(c.type==='pause')this.phase='PAUSED';if(c.type==='resume')this.phase='PLAYING';}
                    advance(ms){this.time+=ms/1000;this.player.z+=ms/1000;if(this.time>=0.5)this.phase='INTERACTING';}
                    events(){return [];}
                    config(){return {};}
                    dispose(){window.disposed++;}
                  }
                  window.__HARNESS__=new Simulation();window.__HARNESS__.caseId='PROTOCOL-TEST';
                  window.__HARNESS__.isolatedSimulation=({fixture})=>new Simulation(fixture);
                }""")
                scenario = {"id": "protocol", "seed": 1, "fixture": {"player": {"x": 0, "z": 0}}, "steps": [
                    {"call": "command", "name": "move", "args": {"x": 2}},
                    {"call": "until", "pointer": "/phase", "expected": "INTERACTING", "milliseconds": 100,
                     "maxMilliseconds": 1000, "save": "after", "history": "path"},
                    {"call": "variant", "save": "other", "milliseconds": 1000, "fixture": {"player": {"x": 5}}}]}
                trace = await page.evaluate(RUN_SCENARIO, {"scenario": scenario, "sourceHash": "a" * 64, "specHash": "b" * 64})
                self.assertEqual(trace["after"]["player"]["x"], 2)
                self.assertAlmostEqual(trace["after"]["simTime"], 0.5)
                self.assertEqual(trace["other"]["player"]["x"], 5)
                self.assertEqual(trace["after"]["_entities"]["target"]["x"], 3)
                self.assertEqual(await page.evaluate("window.disposed"), 2)
                self.assertEqual(await page.evaluate("window.__HARNESS__.snapshot().simTime"), 0)
                self.assertTrue(trace["noninterference"]["liveStateUnchanged"])
                await page.evaluate("() => {window.__HARNESS__.isolatedSimulation=()=>{throw Error('creation failed')}}")
                with self.assertRaises(Exception):
                    await page.evaluate(RUN_SCENARIO, {"scenario": scenario, "sourceHash": "a" * 64, "specHash": "b" * 64})
                self.assertEqual(await page.evaluate("window.__HARNESS__.snapshot().phase"), "PLAYING")
                await page.evaluate("""() => {window.__HARNESS__.isolatedSimulation=({fixture})=>{
                  const sim=new window.__HARNESS__.constructor(fixture);sim.dispose=()=>{throw Error('bad dispose')};return sim;};}""")
                broken_dispose = {**scenario, "steps": [{"call": "snapshot", "save": "after"}]}
                trace = await page.evaluate(RUN_SCENARIO, {"scenario": broken_dispose, "sourceHash": "a" * 64, "specHash": "b" * 64})
                self.assertIn("dispose failed", trace["adapter_error"])
                self.assertEqual(await page.evaluate("window.__HARNESS__.snapshot().phase"), "PLAYING")
            finally:
                await browser.close()

    async def test_rendered_endurance_collects_real_cdp_metrics_and_disposes_isolated_canvas(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = await browser.new_context()
                await context.add_init_script("(" + INSTRUMENT_RENDERER + ")()")
                page = await context.new_page()
                await page.route("http://protocol.test/**", lambda route: route.fulfill(body="<!doctype html><body>protocol</body>", content_type="text/html"))
                await page.goto("http://protocol.test/")
                await page.evaluate("""()=>{
                  class Simulation {
                    constructor(f={}){this.phase='PLAYING';this.time=0;this.damage=0;this.player={hp:300,...f.player};this.entities=f.enemies||[];}
                    snapshot(){return {phase:this.phase,simTime:this.time,player:{...this.player},entities:this.entities,metrics:{totalDamage:this.damage}};}
                    dispatch(c){if(c.type==='pause')this.phase='PAUSED';if(c.type==='resume')this.phase='PLAYING';}
                    advance(ms){if(this.phase==='PLAYING'){this.time+=ms/1000;this.damage+=this.entities.length;}}
                  }
                  const live=new Simulation();window.__HARNESS__=live;
                  live.isolatedSimulation=({fixture})=>{
                    const sim=new Simulation(fixture),canvas=document.createElement('canvas');document.body.appendChild(canvas);
                    const ctx=canvas.getContext('2d');let animation;
                    function draw(){ctx.clearRect(0,0,300,150);for(const e of sim.entities)ctx.fillRect(e.x+150,e.y+75,2,2);animation=requestAnimationFrame(draw);}draw();
                    sim.engineInfo=()=>({name:'test-canvas',version:'1',running:true,resources:{canvases:1,entities:sim.entities.length,contexts:1}});
                    sim.dispose=()=>{cancelAnimationFrame(animation);canvas.remove();};return sim;
                  };
                }""")
                with tempfile.TemporaryDirectory() as folder:
                    validator = ProjectValidator(SimpleNamespace(browser_sandbox=True), FixtureStore(Path(folder)))
                    scenario = {"id": "endurance-protocol", "seed": 1, "fixture": {"player": {"hp": 300},
                        "enemyGenerator": {"count": 200, "radius": 50, "hp": 100000, "damage": 0, "speed": 0}},
                        "steps": [{"durationSeconds": 3, "minimumWallSeconds": .1, "sampleEvery": 1}]}
                    trace = await validator._renderer_memory(page, context, SimpleNamespace(case_id="PROTOCOL", engine="test-canvas"), scenario)
                    self.assertEqual(len(trace["samples"]), 4)
                    self.assertGreater(trace["samples"][-1]["snapshot"]["metrics"]["totalDamage"], 0)
                    self.assertGreater(trace["drawCalls"], 0)
                    self.assertTrue(all(trace["noninterference"].values()))
                    self.assertNotIn("adapter_error", trace)
                    self.assertEqual(await page.locator("canvas").count(), 0)
                    self.assertGreater(trace["samples"][-1]["heap"]["usedSize"], 0)
            finally:
                await browser.close()

    async def test_external_resources_are_blocked_and_touch_events_are_real(self):
        from playwright.async_api import async_playwright
        html = b"""<!doctype html><html><body style='margin:0'><canvas width='390' height='800'></canvas>
        <script>window.touchTrace=[];for(const type of ['pointerdown','pointermove','pointerup'])
        document.querySelector('canvas').addEventListener(type,e=>window.touchTrace.push({type:e.type,trusted:e.isTrusted,pointer:e.pointerType}));
        </script></body></html>"""
        # The server deliberately denies inline script in real projects. This
        # isolated fixture has its script supplied by Playwright, not a generated
        # bundle; it verifies native input independently of project behavior.
        with tempfile.TemporaryDirectory() as folder:
            workspace = FixtureWorkspace(Path(folder), html)
            validator = ProjectValidator(SimpleNamespace(browser_sandbox=True), FixtureStore(Path(folder)))
            with verified_server(workspace) as origin, self.subTest(real_browser=True):
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
                    try:
                        context = await validator._context(browser, origin, width=390, height=844, touch=True)
                        page = await context.new_page()
                        await page.goto(origin + "/")
                        await page.evaluate("""() => {window.touchTrace=[];for(const type of ['pointerdown','pointermove','pointerup'])
                          document.querySelector('canvas').addEventListener(type,e=>window.touchTrace.push({type:e.type,trusted:e.isTrusted,pointer:e.pointerType}));
                          document.querySelector('canvas').style.touchAction='none';}""")
                        await validator._touch_drag(context, page, 80, 500, 140, 500)
                        trace = await page.evaluate("window.touchTrace")
                        self.assertTrue(any(event["type"] == "pointerdown" and event["trusted"] and event["pointer"] == "touch" for event in trace))
                        self.assertTrue(any(event["type"] == "pointermove" and event["trusted"] for event in trace))
                        await page.add_init_script("fetch('https://example.invalid/private').catch(()=>{})")
                        await page.reload()
                        await page.wait_for_timeout(100)
                        # CSP blocks fetch even before route interception. A
                        # navigation is also denied by the explicit origin route.
                        try:
                            await page.goto("https://example.invalid/", timeout=3000)
                        except Exception:
                            pass
                        self.assertTrue(context._harness_blocked)
                        await context.close()
                    finally:
                        await browser.close()


if __name__ == "__main__":
    unittest.main()
