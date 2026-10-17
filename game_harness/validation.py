"""Run generated code only inside Chromium; never execute it as host code."""
import asyncio
import hashlib
import re
from html.parser import HTMLParser

from playwright.async_api import async_playwright


CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; "
       "worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "sandbox allow-scripts")


class Inspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.errors = []
        self.tags = set()
        self.actions = set()
        self.testids = set()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        self.tags.add(tag)
        self.actions.add(attrs.get("data-action"))
        self.testids.add(attrs.get("data-testid"))
        if tag in {"iframe", "object", "embed", "base", "form"}:
            self.errors.append("不允许的元素：" + tag)
        if tag == "script" and (attrs.get("src") or attrs.get("type") == "module"):
            self.errors.append("禁止外部或模块脚本，产物必须单文件离线运行")
        for key in ("src", "href", "action", "srcset"):
            value = attrs.get(key) or ""
            if value and not value.startswith(("#", "data:")):
                self.errors.append("禁止外部/相对资源引用：" + key)
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh":
            self.errors.append("禁止页面重定向")


def inspect_html(html: str):
    parser = Inspector()
    parser.feed(html)
    errors = list(parser.errors)
    if len(html.encode()) > 300000:
        errors.append("HTML 超过 300KB 限制")
    for tag in ("html", "script", "meta"):
        if tag not in parser.tags:
            errors.append("缺少元素：" + tag)
    for action in ("start", "pause", "resume", "restart"):
        if action not in parser.actions:
            errors.append("缺少 data-action=" + action)
    for testid in ("game-root", "score"):
        if testid not in parser.testids:
            errors.append("缺少 data-testid=" + testid)
    if "__GAME__" not in html:
        errors.append("缺少 window.__GAME__ 运行状态接口")
    if not re.search(r"[\u4e00-\u9fff]", html):
        errors.append("缺少中文界面")
    if re.search(r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|eval|open)\s*\(|\bimport\s*(?:\(|[{'\"])", html):
        errors.append("禁止联网、动态代码执行或新窗口调用")
    if re.search(r"url\(\s*['\"]?(?!data:)[^)'\"]+", html, re.I):
        errors.append("CSS 不得引用外部资源")
    return list(dict.fromkeys(errors))


class BrowserValidator:
    version = "4-gameplay-input-targets"
    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    async def validate(self, rid: str, version: int, html: str):
        report = {"sha256": hashlib.sha256(html.encode()).hexdigest(), "passed": False,
                  "static_errors": inspect_html(html), "browser_errors": [], "viewports": [],
                  "scope": "Chromium 冒烟测试：离线加载、开始、运行计时、输入、暂停、继续、重开与三种尺寸；不等同完整玩法或 Safari/微信验收。"}
        if report["static_errors"]:
            return report
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, chromium_sandbox=self.settings.browser_sandbox)
                try:
                    for name, width, height, touch in [("desktop", 1280, 800, False), ("portrait", 390, 844, True), ("landscape", 844, 390, True)]:
                        result = await self._viewport(browser, rid, version, html, name, width, height, touch)
                        report["viewports"].append(result)
                finally:
                    await browser.close()
        except Exception as exc:
            # Environment failures are not defects to send back to the builder.
            report["infrastructure_error"] = str(exc)[:1500]
            return report
        report["passed"] = all(v["passed"] for v in report["viewports"]) and len(report["viewports"]) == 3
        return report

    async def _viewport(self, browser, rid, version, html, name, width, height, touch):
        result = {"name": name, "width": width, "height": height, "passed": False, "checks": [], "errors": []}
        context = await browser.new_context(viewport={"width": width, "height": height}, has_touch=touch,
                                            is_mobile=touch, service_workers="block", accept_downloads=False)
        await context.clear_permissions()
        errors = result["errors"]

        async def route_request(route):
            if route.request.url == "https://game.invalid/index.html" and route.request.is_navigation_request():
                await route.fulfill(status=200, body=html, content_type="text/html", headers={"Content-Security-Policy": CSP})
            else:
                errors.append("尝试访问非内联资源：" + route.request.url[:180])
                await route.abort()

        await context.route("**/*", route_request)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)[:700]))
        page.on("console", lambda message: errors.append(message.text[:700]) if message.type == "error" else None)
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.set_default_timeout(4000)

        async def check(condition, message):
            if not condition:
                raise AssertionError("未通过检查：" + message)
            result["checks"].append(message)

        async def state():
            return await page.evaluate("() => {const g=window.__GAME__; return g ? {state:g.state,score:g.score,ticks:g.ticks} : null}")

        async def click_action(action):
            controls = page.locator(f'[data-action="{action}"]:visible')
            # A toolbar and a modal may expose the same action; select an unobstructed one.
            for index in reversed(range(await controls.count())):
                candidate = controls.nth(index)
                try:
                    await candidate.click(trial=True, timeout=800)
                except Exception:
                    continue
                await candidate.click(timeout=2000)
                return
            raise AssertionError("没有可点击的操作按钮：" + action)

        try:
            await page.goto("https://game.invalid/index.html", wait_until="load", timeout=10000)
            await page.get_by_test_id("game-root").wait_for(state="visible")
            initial = await state()
            await check(initial and initial.get("state") == "ready", "初始状态 ready")
            await click_action("start")
            await page.wait_for_timeout(400)
            running = await state()
            await check(running and running.get("state") == "running" and isinstance(running.get("ticks"), (int, float)) and running["ticks"] > 0, "开始后真实运行并更新 ticks")
            await click_action("pause")
            paused = await state()
            await page.wait_for_timeout(160)
            still_paused = await state()
            await check(paused["state"] == still_paused["state"] == "paused" and paused["ticks"] == still_paused["ticks"], "暂停停止更新")
            await click_action("resume")
            await page.wait_for_timeout(100)
            resumed = await state()
            await check(resumed["state"] == "running" and resumed["ticks"] > paused["ticks"], "继续恢复更新")
            if touch:
                controls = page.locator('[data-action]')
                candidates = await controls.evaluate_all("els => els.filter(e => !['start','pause','resume','restart'].includes(e.dataset.action) && e.getBoundingClientRect().width > 0).map(e=>e.dataset.action)")
                if candidates:
                    target = page.locator('[data-action="' + candidates[0] + '"]').first
                    result["input_target"] = "data-action=" + candidates[0]
                else:
                    # Grid and canvas games can receive touches directly in the playfield.
                    target = page.locator('[data-testid="game-root"] [data-index]:visible, [data-testid="game-root"] canvas:visible').first
                    result["input_target"] = "playfield grid/canvas"
                await check(await target.count() > 0, "存在可见的移动端操作目标（按钮、棋盘格或画布）")
                await target.tap()
            else:
                for key in ("ArrowLeft", "ArrowRight"):
                    await page.keyboard.press(key)
            await check(True, "已发送触屏/键盘操作")
            await click_action("restart")
            restarted = await state()
            await check(restarted["state"] == "running" and restarted["score"] == 0, "重开回到 running 且分数归零")
            await check(await page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2"), "布局没有横向溢出")
            await page.wait_for_timeout(250)
            screenshot = await page.screenshot(full_page=True, timeout=7000)
            result["screenshot"] = f"v{version}-{name}.png"
            self.store.write(rid, result["screenshot"], screenshot)
            result["observed_state"] = await state()
        except Exception as exc:
            errors.append(str(exc)[:1200])
            try:
                self.store.write(rid, f"v{version}-{name}.png", await page.screenshot(timeout=3000))
                result["screenshot"] = f"v{version}-{name}.png"
            except Exception:
                pass
        finally:
            await context.close()
        result["passed"] = not errors and len(result["checks"]) >= (8 if touch else 7)
        return result
