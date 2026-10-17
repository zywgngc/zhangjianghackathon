"""Exercise the shipped UI, API authentication, and sandbox together."""
import hashlib
import json
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
                await page.add_init_script("""if(location.hostname==='harness.invalid') {
                  localStorage.setItem('parent-secret', 'never-share-this');
                  localStorage.setItem('game-save:other-run', JSON.stringify({secret:'other-game'}));
                }""")
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
                assert await frame.evaluate("localStorage.getItem('parent-secret')") is None
                assert await frame.evaluate("localStorage.getItem('harness-token')") is None
                await frame.evaluate("localStorage.setItem('progress',JSON.stringify({unlockedLevel:2,gold:35})); localStorage.setItem('temporary','remove'); localStorage.removeItem('temporary')")
                await page.wait_for_function("rid => JSON.parse(localStorage.getItem('game-save:'+rid)||'{}').progress", arg=rid)
                assert await page.evaluate("rid => JSON.parse(localStorage.getItem('game-save:'+rid)).progress", rid) == '{"unlockedLevel":2,"gold":35}'
                assert await frame.evaluate("localStorage.length") == 1
                assert await frame.evaluate("localStorage.key(0)") == "progress"
                await frame.evaluate("parent.postMessage({type:'harness-game-save',runId:'other-run',values:{secret:'attempt'}},'*')")
                await frame.evaluate("parent.postMessage({type:'harness-game-save',runId:" + json.dumps(rid) + ",values:{oversize:'x'.repeat(1048577)}},'*')")
                await page.evaluate("rid => window.postMessage({type:'harness-game-save',runId:rid,values:{evil:'parent-source'}},'*')", rid)
                await page.wait_for_timeout(100)
                assert await page.evaluate("localStorage.getItem('game-save:other-run')") == '{"secret":"other-game"}'
                saved = await page.evaluate("rid => JSON.parse(localStorage.getItem('game-save:'+rid))", rid)
                assert saved == {"progress": '{"unlockedLevel":2,"gold":35}'}
                await page.reload()
                await page.frame_locator("#game").locator('[data-testid="game-root"]').wait_for()
                assert await page.frames[-1].evaluate("localStorage.getItem('progress')") == '{"unlockedLevel":2,"gold":35}'
            finally:
                await browser.close()


@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="Requires installed Chromium")
async def test_project_candidate_is_distinct_from_acceptance_and_preserves_full_spec(settings):
    from game_harness.project_runtime import ProjectWorkspace
    from game_harness.project_state import ProjectLedger

    app = create_app(settings)
    store = app.state.store
    specification = "完整规格，不得删减。\n" + "真实游戏功能与验收数值。\n" * 2500
    run = store.create({"mode": "project", "description": "大型项目测试", "engine": "phaser", "case_id": "GAME-TEST-002", "specification": specification})
    rid = run["id"]
    tests = [{"id": f"GAME-TEST-002/T{number:02}", "title": title} for number, title in [(1, "移动"), (2, "伤害公式"), (3, "三关可赢")]]
    store.write(rid, "specification.json", {"text": specification, "sha256": hashlib.sha256(specification.encode()).hexdigest(), "chunks": [{"id": "chunk-0001", "text": specification, "source": {"line_start": 1, "line_end": 2501}}], "requirements": tests, "acceptance_tests": tests})
    store.write(rid, "specification.txt", specification)
    features = [{"id": "foundation", "title": "引擎与配置", "owner": "engine-agent", "depends_on": [], "owned_paths": ["src/config.ts"], "acceptance_ids": [tests[0]["id"]]}, {"id": "combat", "title": "战斗系统", "owner": "combat-agent", "depends_on": ["foundation"], "owned_paths": ["src/combat.ts"], "acceptance_ids": [tests[1]["id"], tests[2]["id"]]}]
    store.write(rid, "implementation-plan.json", {"plan": {"architecture": "模拟与渲染分离", "interfaces": "Simulation -> scene", "features": features}})
    ledger = ProjectLedger(store)
    ledger.register(rid, features)
    ledger.finish(rid, "foundation", {"status": "implemented"})
    store.write(rid, "project-progress.json", ledger.summary(rid))
    store.write(rid, "project-validation.json", {"passed": False, "acceptance": {"counts": {"passed": 1, "failed": 1, "not_run": 1, "manual_required": 0}, "tests": [{**tests[0], "status": "passed", "errors": []}, {**tests[1], "status": "failed", "errors": ["伤害值不符"]}, {**tests[2], "status": "not_run", "errors": []}]}})
    store.update(rid, status="needs_attention", stage="project_verification")
    # A deterministic built fixture exercises the real candidate API. Real
    # Phaser/Babylon compiler and rendering checks live in test_project_runtime.
    workspace = ProjectWorkspace(store.directory(rid) / "project", "phaser")
    workspace.initialize()
    (workspace.root / "dist").mkdir()
    (workspace.root / "dist/index.html").write_text((Path(__file__).parent / "fixtures/contract-game.html").read_text())
    workspace._internal("build.json").write_text(json.dumps({"passed": True, "source_sha256": workspace.snapshot()["sha256"], "artifact_manifest": workspace._artifact_manifest()}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://harness.invalid") as client:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(chromium_sandbox=settings.browser_sandbox)
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                async def route_request(route):
                    parsed = urlsplit(route.request.url)
                    if parsed.hostname != "harness.invalid":
                        return await route.abort()
                    response = await client.request(route.request.method, parsed.path + ("?" + parsed.query if parsed.query else ""), headers=route.request.headers, content=route.request.post_data_buffer)
                    await route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
                await page.route("**/*", route_request)
                await page.goto("https://harness.invalid/#run=" + rid)
                await page.frame_locator("#game").locator('[data-testid="game-root"]').wait_for()
                assert "尚未全部验收" in await page.locator("#preview-disclaimer").inner_text()
                assert await page.locator("#download").inner_text() == "下载候选项目 ZIP ↓"
                assert await page.locator("#game").get_attribute("sandbox") == "allow-scripts"
                await page.locator('[data-tab="spec"]').click()
                spec_text = await page.locator("#spec").inner_text()
                assert f"{len(specification):,} 字符" in spec_text
                assert "combat-agent" in spec_text and "foundation" in spec_text
                if os.getenv("WORKBENCH_ARTIFACTS"):
                    output = Path(os.environ["WORKBENCH_ARTIFACTS"])
                    output.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(output / "workbench-project-spec.png"))
                async with page.expect_download() as download:
                    await page.get_by_role("button", name="下载完整原文 .txt").click()
                path = await (await download.value).path()
                assert Path(path).read_text() == specification
                await page.locator('[data-tab="qa"]').click()
                qa_text = await page.locator("#qa").inner_text()
                assert "失败 1" in qa_text and "未运行 1" in qa_text
                assert "伤害值不符" in qa_text
                assert "GAME-TEST-002/T03" in qa_text
                if os.getenv("WORKBENCH_ARTIFACTS"):
                    await page.screenshot(path=str(output / "workbench-project-acceptance.png"))
                await page.set_viewport_size({"width": 390, "height": 844})
                assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                await page.set_viewport_size({"width": 1440, "height": 1000})
                # File import keeps the complete text and switches to the long
                # project endpoint. ASGI transport has no running worker here.
                uploaded = "导入的完整规格\n" + "章节内容必须保留\n" * 2200
                await page.locator("#spec-file").set_input_files({"name": "完整规格.txt", "mimeType": "text/plain", "buffer": uploaded.encode()})
                assert await page.locator("#description").input_value() == uploaded
                assert await page.locator("#description").get_attribute("maxlength") == "1000000"
                await page.locator("#generate").click()
                await page.wait_for_function("previous => location.hash !== '#run='+previous", arg=rid)
                submitted = store.list()[0]
                assert submitted["request"]["mode"] == "project"
                assert store.request(submitted["id"])["specification"] == uploaded
                await page.locator('[data-case="GAME-TEST-001"]').click()
                await page.wait_for_function("previous => location.hash !== '#run='+previous", arg=submitted["id"])
                benchmark = store.list()[0]
                assert benchmark["request"]["case_id"] == "GAME-TEST-001"
                assert len(store.request(benchmark["id"])["specification"]) > 20000
                assert benchmark["request"]["engine"] == "babylon"
                # A 409 before the first build is a waiting state, not an error.
                assert await page.locator("#game").is_hidden()
                assert "尚无可预览" not in await page.locator("#notice").inner_text()
            finally:
                await browser.close()
