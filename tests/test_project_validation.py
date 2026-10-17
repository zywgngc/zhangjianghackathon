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
from unittest.mock import AsyncMock

from game_harness.project_validation import ProjectValidator, RUN_SCENARIO, verified_server
from game_harness.specifications import load_case


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


@unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "Real Chromium tests are opt-in")
class RealProjectBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_trusted_dsl_executes_independent_handles_and_retains_observations(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                page = await browser.new_page()
                await page.goto("about:blank")
                await page.evaluate("""() => {
                  window.disposed=0;
                  class Simulation {
                    constructor(f={}){this.time=0;this.player={x:0,z:0,...f.player};this.phase='PLAYING';}
                    snapshot(){return {simTime:this.time,phase:this.phase,player:{...this.player},entities:[{id:'target',x:3,z:0}]};}
                    dispatch(c){if(!['start','move'].includes(c.type))throw Error('Unexpected non-user command');if(c.type==='move')this.player.x=c.x;}
                    advance(ms){this.time+=ms/1000;this.player.z+=ms/1000;if(this.time>=0.5)this.phase='INTERACTING';}
                    events(){return [];}
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
