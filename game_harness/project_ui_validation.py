"""Host-observed UI evidence on the production game; no reward/state injection.

Call ``capture_project_ui_scene`` at real normal-play upgrade/power transitions.
Call ``validate_project_ui`` on the same page/context after normal routes. Missing
normal-play prerequisites are never replaced by injected rewards. Explicit UI
boundaries may use initial-condition fixtures with the same production view.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import struct
from typing import Any


class MissingUIPrerequisite(RuntimeError):
    pass


class ProjectUIAdapterError(RuntimeError):
    """Generated observation/render adapter is absent or violates its protocol."""


async def _state(page):
    available = await page.evaluate("() => !!window.__HARNESS__ && typeof window.__HARNESS__.snapshot==='function'")
    if not available:
        raise ProjectUIAdapterError("Missing production window.__HARNESS__.snapshot adapter")
    value = await page.evaluate("() => JSON.parse(JSON.stringify(window.__HARNESS__.snapshot()))")
    if not isinstance(value, dict) or not isinstance(value.get("phase"), str):
        raise ProjectUIAdapterError("Production snapshot has no observable phase")
    return value


async def observe_project_power_labels(validator, page, rid, spec, evidence, round_no):
    """Read one real normal-play DOM/camera frame without changing the game.

    The trusted normal-route trace retains this whole observation and hashes it
    together with the production source. It does not declare a test verdict.
    """
    probe = _Probe(validator, page, rid, spec, evidence, round_no)
    state = await probe.state()
    return {"state": state, "observation": await probe.label_observations(state)}


def _choices(state):
    return state.get("upgradeChoices") or state.get("choices") or state.get("skills", {}).get("upgradeChoices") or []


def _identifier(choice):
    return choice if isinstance(choice, str) else str(choice.get("id", ""))


class _Probe:
    def __init__(self, validator, page, rid, spec, evidence, round_no, *, handle=None, container_id=None, fixture=None):
        self.validator, self.page, self.rid = validator, page, rid
        self.spec, self.evidence, self.round = spec, evidence, round_no
        self.case = spec.case_id
        self.handle, self.fixture = handle, fixture
        self.root = page.locator("[id=" + json.dumps(container_id) + "]") if container_id else page.locator("body")
        self.result = {"screenshots": [], "errors": [], "missing_executors": [], "adapter_failures": []}
        cache = getattr(page, "_harness_ui_probe_cache", None)
        if cache is None:
            cache = {"captures": {}, "power_before": None, "scenes": {}, "evidence_count": 0}
            setattr(page, "_harness_ui_probe_cache", cache)
        self.cache = cache

    async def state(self):
        if self.handle is not None:
            return await self.handle.evaluate("api => JSON.parse(JSON.stringify(api.snapshot()))")
        return await _state(self.page)

    async def text(self):
        return await self.root.inner_text()

    def ids(self, *numbers):
        return [self.case + "/T" + str(number).zfill(2) for number in numbers]

    def missing(self, numbers, reason):
        self.result["missing_executors"].append({"acceptance_ids": self.ids(*numbers), "reason": reason})

    def emit(self, label, trace, numbers, assertions):
        self.cache["evidence_count"] = self.cache.get("evidence_count", 0) + 1
        trace["ui_boundary"] = self.fixture is not None
        self.validator._evidence(self.rid, self.spec, self.evidence, self.round, "ui-" + label + "-" + str(self.cache["evidence_count"]),
            trace, self.ids(*numbers), assertions, kind="browser", mode="isolated_ui_boundary" if self.fixture is not None else "normal_ui", fixture=self.fixture)

    async def check(self, label, numbers, operation):
        try:
            await operation()
        except asyncio.CancelledError:
            raise
        except MissingUIPrerequisite as error:
            self.missing(numbers, str(error))
        except ProjectUIAdapterError as error:
            self.result["adapter_failures"].append({"acceptance_ids": self.ids(*numbers), "reason": str(error)})
        except Exception as error:
            message = type(error).__name__ + ": " + str(error)[:1200]
            self.result["errors"].append({"acceptance_ids": self.ids(*numbers), "message": message})
            self.emit(label + "-error", {"ui_check_completed": False, "error": message}, numbers,
                [{"pointer": "/ui_check_completed", "op": "eq", "expected": True}])

    async def click(self, action, names=None, *, required=True):
        candidates = self.root.locator('[data-action="' + action + '"]:visible')
        if names and not await candidates.count():
            candidates = self.root.get_by_role("button", name=re.compile(names))
        for index in reversed(range(await candidates.count())):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible() or not await candidate.is_enabled():
                    continue
                await candidate.click(trial=True, timeout=500)
                await candidate.click(timeout=1500)
                await self.page.wait_for_timeout(40)
                return True
            except Exception:
                continue
        if required:
            raise AssertionError("Missing actionable production UI control: " + action)
        return False

    async def menu(self):
        if not await self.click("menu", "返回主菜单|回到主菜单|主菜单", required=False):
            await self.click("back", "返回|关闭", required=False)

    async def screenshot(self, scene):
        count = self.cache["captures"].get(scene, 0)
        if count >= 3:
            return None
        self.cache["captures"][scene] = count + 1
        raw = await self.root.screenshot() if self.handle is not None else await self.page.screenshot(full_page=False)
        width, height = struct.unpack(">II", raw[16:24])
        name = f"project-r{self.round}-ui-{scene}-{count + 1}.png"
        digest = self.validator.store.write(self.rid, name, raw)
        descriptor = {"path": name, "sha256": digest, "screenshot": name, "screenshot_sha256": digest,
            "width": width, "height": height, "name": "portrait" if height > width else "landscape" if width <= 900 else "desktop",
            "scene": scene, "project_sha256": self.evidence["project_sha256"], "spec_sha256": self.spec.sha256}
        self.result["screenshots"].append(descriptor)
        return descriptor

    async def cards(self, scene):
        before = await self.state()
        choices = _choices(before)
        if not choices or before.get("phase") not in {"UPGRADE", "LEVEL_UP", "PAUSED"}:
            raise MissingUIPrerequisite("No actual paused production upgrade choices are currently observable")
        await self.page.wait_for_timeout(60)
        controls = await self.root.locator("button:visible,[role=button]:visible").evaluate_all("""nodes => nodes.map(n=>({text:n.innerText||n.getAttribute('aria-label')||'',id:n.dataset.upgradeId||n.dataset.choiceId||n.dataset.id||'',action:n.dataset.action||'',disabled:n.disabled===true})).filter(n=>!n.disabled)""")
        text = await self.text()
        ids = [_identifier(choice) for choice in choices]
        ultimate = [identifier for identifier in ids if re.match(r"^(U|E)\d{2}", identifier)]
        if scene == "ultimate_evolution" and not ultimate:
            raise MissingUIPrerequisite("No actual eligible ultimate/evolution card is present in production choices")
        matching = []
        config = await self.handle.evaluate("api => JSON.parse(JSON.stringify(api.config()))") if self.handle is not None else await self.page.evaluate("() => JSON.parse(JSON.stringify(window.__HARNESS__.config()))")
        names = {}
        def collect(value, fallback=""):
            if isinstance(value, dict):
                if value.get("name") and (value.get("id") or fallback):
                    names[str(value.get("id", fallback))] = str(value["name"])
                for key, item in value.items():
                    collect(item, str(key))
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(config)
        for choice in choices:
            identifier = _identifier(choice)
            name = choice.get("name", names.get(identifier, "")) if isinstance(choice, dict) else names.get(identifier, "")
            matches = [control for control in controls if control["id"] == identifier or identifier in control["action"] or (name and name in control["text"])]
            matching.extend(matches)
        # A control must visibly contain a description, not just a W01 token or
        # a name. Correct semantics are independently checked against the spec.
        descriptions = [control["text"] for control in matching if len(re.findall(r"[\u4e00-\u9fff]", control["text"])) >= 4 and len(control["text"].strip()) >= 8]
        if not descriptions:
            raise AssertionError("Upgrade choices have no matching visible actionable Chinese description cards")
        shot = await self.screenshot(scene)
        trace = {"state": before, "choice_ids": ids, "ultimate_ids": ultimate, "controls": matching,
            "visible_descriptions": descriptions, "body_text": text, "screenshot": shot,
            "scope": "Mechanical UI precondition only; exact Chinese description semantics and distinct evolution appearance require independent screenshot review"}
        self.emit(scene, trace, [64 if scene == "ultimate_evolution" else 63],
            [{"pointer": "/visible_descriptions", "op": "nonempty"}, {"pointer": "/choice_ids", "op": "nonempty"}])
        self.cache["scenes"][scene] = trace

    async def label_observations(self, state):
        requests = [{"id": state.get("player", {}).get("id", "player"), "kind": "player", "power": state.get("player", {}).get("power")}]
        requests.extend({"id": entity["id"], "kind": entity["kind"], "power": entity.get("power")} for entity in state.get("entities", []) if entity.get("kind") in {"enemy", "boss"} and entity.get("active", True))
        return await self.page.evaluate("""async requests => {
          const visible=n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)>0;};
          const nodes=[...document.querySelectorAll('[data-entity-id],[data-power-id],[data-testid=player-power],[data-testid=enemy-power],.power-label,[data-power],body *')];
          const labels=nodes.filter((n,i)=>nodes.indexOf(n)===i&&visible(n)&&(!n.children.length||n.hasAttribute('data-entity-id')||n.hasAttribute('data-power-id'))).map(n=>{
            const text=(n.innerText||n.textContent||'').trim(),r=n.getBoundingClientRect(),s=getComputedStyle(n);
            const matches=text.match(/(?:战力[：:\\s]*)?(\\d+)\\s*$/);
            return {text,power:matches?Number(matches[1]):null,id:n.dataset.entityId||n.dataset.powerId||'',testid:n.dataset.testid||'',color:s.color,background:s.backgroundColor,box:{x:r.x,y:r.y,width:r.width,height:r.height}};
          }).filter(n=>n.power!==null&&n.text.length<80);
          const rows=[];
          for(const request of requests){
            let point=null;try{point=await window.__HARNESS__.projectEntity(request.id);}catch{}
            const inView=!!point&&Number.isFinite(point.x)&&Number.isFinite(point.y)&&point.x>=0&&point.x<=innerWidth&&point.y>=0&&point.y<=innerHeight;
            const nearby=point?labels.filter(n=>Math.abs(n.box.x+n.box.width/2-point.x)<130&&Math.abs(n.box.y+n.box.height/2-point.y)<Math.max(130,innerHeight*.22)):[];
            const linked=nearby.filter(n=>n.power===request.power&&(n.id===request.id||!n.id));
            rows.push({...request,point,inView,matchingLabels:linked});
          }
          return {entities:rows,labels,telemetry:window.__HARNESS_RENDER_OBSERVER__||null};
        }""", requests)

    async def power_scene(self, scene):
        state = await self.state()
        observed = await self.label_observations(state)
        shot = await self.screenshot(scene)
        trace = {"state": state, "observation": observed, "screenshot": shot}
        self.cache["scenes"][scene] = trace
        if scene == "power_change_before":
            self.cache["power_before"] = trace
            # Projection and draw calls are independent preconditions for Canvas
            # labels. They cannot prove that text was actually drawn correctly.
            player = observed["entities"][0]
            enemies = observed["entities"][1:]
            rendered = (observed.get("telemetry") or {}).get("draws", 0) > 0
            for number, entities in [(2, [player]), (3, enemies)]:
                if not entities or not all(entity["inView"] for entity in entities):
                    self.missing([number], "Not all required entities have observable real camera projections in this scene")
                    continue
                trace_part = {"entities": entities, "all_dom_labels_match": all(entity["matchingLabels"] for entity in entities),
                    "real_render_observed": rendered, "screenshot": shot,
                    "scope": "Real projection/rendering precondition; visual evidence must establish actual overhead text, especially for Canvas labels"}
                if not trace_part["all_dom_labels_match"] and not rendered:
                    raise AssertionError("Neither visible matching DOM power labels nor real renderer draws were observed")
                self.emit("power-label-" + str(number), trace_part, [number],
                    [{"pointer": "/entities", "op": "nonempty"}, {"pointer": "/real_render_observed", "op": "eq", "expected": True}] if not trace_part["all_dom_labels_match"] else [{"pointer": "/all_dom_labels_match", "op": "eq", "expected": True}])
            return
        before = self.cache.get("power_before")
        if not before:
            raise MissingUIPrerequisite("No host-captured pre-interaction state for power label update")
        old_power = before["state"].get("player", {}).get("power")
        new_power = state.get("player", {}).get("power")
        if not isinstance(new_power, (int, float)) or not isinstance(old_power, (int, float)) or new_power <= old_power:
            raise MissingUIPrerequisite("No completed legitimate power-growth interaction observed")
        player = observed["entities"][0]
        if not player["matchingLabels"]:
            self.missing([24], "Current player power is not observable in DOM; Canvas requires time-bound text observation beyond a historical text set")
        else:
            self.emit("power-hud-update", {"before": before["state"], "after": state, "current_player_labels": player["matchingLabels"], "screenshot": shot}, [24],
                [{"pointer": "/current_player_labels", "op": "nonempty"}])
        old_entities = {entity["id"]: entity for entity in before["observation"]["entities"][1:]}
        crossings = []
        for entity in observed["entities"][1:]:
            old = old_entities.get(entity["id"])
            if old and old_power <= entity["power"] < new_power and old["inView"] and entity["inView"]:
                crossings.append({"id": entity["id"], "power": entity["power"], "before_projection": old["point"], "after_projection": entity["point"], "before_labels": old["matchingLabels"], "after_labels": entity["matchingLabels"]})
        if not crossings:
            self.missing([25], "No same active enemy crossed the strict defeatability threshold in the captured before/after scenes")
        else:
            self.emit("power-color-threshold", {"before_power": old_power, "after_power": new_power, "same_enemy_threshold_crossings": crossings,
                "before_screenshot": before["screenshot"], "after_screenshot": shot, "scope": "Actual same-enemy threshold transition; independent visual review must check the observed label colors"}, [25],
                [{"pointer": "/same_enemy_threshold_crossings", "op": "nonempty"}])

    async def settings(self):
        await self.menu()
        await self.click("settings", "设置")
        before = await self.state()
        audio_before = copy.deepcopy(before.get("progress", {}).get("audio", {}))
        transitions = []
        for key, chinese in [("music", "音乐"), ("sfx", "音效")]:
            control = self.root.get_by_role("checkbox", name=re.compile(chinese))
            clicked = False
            if await control.count():
                await control.first.click()
                clicked = True
            if not clicked:
                clicked = await self.click("toggle-" + key, chinese, required=False)
            if not clicked:
                clicked = await self.click(key, chinese, required=False)
            if not clicked:
                raise AssertionError("Settings has no visible actionable " + chinese + " control")
            await self.page.wait_for_timeout(40)
            after = await self.state()
            value_before = audio_before.get(key)
            value_after = after.get("progress", {}).get("audio", {}).get(key)
            if not isinstance(value_before, bool) or value_after is not (not value_before):
                raise AssertionError("Actual settings click did not toggle production audio." + key)
            transitions.append({"key": key, "before": value_before, "after": value_after})
        text = await self.text()
        if not all(word in text for word in ("音乐", "音效")):
            raise AssertionError("Settings does not visibly label both music and sound effects")
        shot = await self.screenshot("settings")
        number = 36 if self.case.endswith("001") else 68 if self.case.endswith("002") else 61
        self.emit("settings", {"before": before, "after": await self.state(), "ui_text": text, "audio_transitions": transitions, "screenshot": shot}, [number],
            [{"pointer": "/audio_transitions", "op": "count", "expected": 2}])

    async def shop(self, normal_traces):
        number = 35 if self.case.endswith("001") else 67
        persistence = 30 if self.case.endswith("001") else 59
        await self.menu()
        await self.click("upgrades", "永久升级|升级商店|强化商店|升级")
        before = await self.state()
        buttons = self.root.get_by_role("button", name=re.compile("购买|升级|强化"))
        visible = []
        for index in range(await buttons.count()):
            item = buttons.nth(index)
            if await item.is_visible():
                visible.append({"text": await item.inner_text(), "enabled": await item.is_enabled()})
        text = await self.text()
        required = ["士兵", "伤害"] if self.case.endswith("001") else ["生命", "攻击", "身法", "悟性", "聚气", "聚宝"]
        if not all(word in text for word in required) or not visible:
            raise AssertionError("Permanent upgrade UI omits required upgrade categories or controls")
        self.emit("upgrade-screen", {"ui_text": text, "visible_purchase_controls": visible, "required_categories": required}, [number],
            [{"pointer": "/visible_purchase_controls", "op": "nonempty"}])
        earned = any(trace.get("mode") == "normal_play" and trace.get("fixture") is None and any(item.get("name") == "start" for item in trace.get("inputs", [])) and trace.get("final", {}).get("progress", {}).get("gold", 0) > trace.get("initial", {}).get("progress", {}).get("gold", 0) for trace in normal_traces or [])
        if not earned:
            raise MissingUIPrerequisite("Purchase persistence requires gold earned by a recorded normal production route; no reward injection is allowed")
        purchased = False
        attempted = False
        for index in range(await buttons.count()):
            button = buttons.nth(index)
            if not await button.is_visible() or not await button.is_enabled():
                continue
            action = await button.get_attribute("data-action") or ""
            name = await button.inner_text()
            if not re.search("购买|强化", name) and action in {"upgrades", "menu"}:
                continue
            attempted = True
            await button.click()
            await self.page.wait_for_timeout(60)
            after = await self.state()
            if after.get("progress", {}).get("upgrades") != before.get("progress", {}).get("upgrades"):
                purchased = True
                break
        if not purchased:
            if attempted:
                raise AssertionError("Enabled purchase controls did not change actual production upgrade state")
            raise MissingUIPrerequisite("No affordable non-maximal upgrade purchase was observed using actual UI controls")
        gold_before, gold_after = before["progress"].get("gold"), after["progress"].get("gold")
        if not isinstance(gold_before, (int, float)) or not isinstance(gold_after, (int, float)) or gold_after >= gold_before:
            raise AssertionError("Upgrade did not deduct actual production gold")
        await self.page.reload(wait_until="networkidle", timeout=30000)
        await self.page.wait_for_function("() => Boolean(window.__HARNESS__ && typeof window.__HARNESS__.snapshot==='function')")
        refreshed = await self.state()
        trace = {"before_purchase": before, "after_purchase": after, "after_refresh": refreshed,
            "earned_route_ids": [route.get("scenario_id") for route in normal_traces or [] if route.get("mode") == "normal_play"],
            "upgrade_persisted": refreshed.get("progress", {}).get("upgrades") == after["progress"].get("upgrades"),
            "gold_persisted": refreshed.get("progress", {}).get("gold") == gold_after}
        self.emit("purchase-refresh", trace, [persistence], [{"pointer": "/upgrade_persisted", "op": "eq", "expected": True}, {"pointer": "/gold_persisted", "op": "eq", "expected": True}])

    async def defeat(self):
        await self.menu()
        started = await self.click("start", "开始游戏|开始", required=False)
        if not started:
            await self.click("pause", "暂停", required=False)
            await self.click("restart", "重新开始|重开|重试")
        inputs = [{"type": "start" if started else "restart", "source": "actual_ui"}]
        for _ in range(480):
            state = await self.state()
            if state.get("phase") == "DEFEAT":
                break
            if state.get("phase") in {"VICTORY", "DEADLOCK"}:
                raise MissingUIPrerequisite("Passive normal play did not reach DEFEAT; no forced death was injected")
            if _choices(state):
                raise MissingUIPrerequisite("Normal loss probe reached an actual upgrade choice; no automatic test-only selection was injected")
            await self.page.evaluate("() => window.__HARNESS__.advance(500)")
            if _ % 20 == 0:
                await self.page.wait_for_timeout(1)
        else:
            raise MissingUIPrerequisite("No legitimate DEFEAT reached within bounded normal production time")
        await self.defeat_screen(inputs)

    async def defeat_screen(self, inputs=None):
        state = await self.state()
        if state.get("phase") != "DEFEAT":
            raise AssertionError("The production simulation has not reached DEFEAT")
        await self.page.wait_for_timeout(50)
        text = await self.text()
        retry = self.root.get_by_role("button", name=re.compile("重试|重新开始|再来|重开"))
        if not re.search("失败|战败|挑战结束|落败", text) or not await retry.count():
            raise AssertionError("Production DEFEAT state has no visible defeat explanation and retry control")
        shot = await self.screenshot("defeat")
        await retry.first.click()
        await self.page.wait_for_timeout(40)
        after = await self.state()
        self.emit("defeat-ui", {"defeat": state, "ui_text": text, "inputs": inputs or [], "after_retry": after, "screenshot": shot}, [34 if self.case.endswith("001") else 66],
            [{"pointer": "/defeat/phase", "op": "eq", "expected": "DEFEAT"}, {"pointer": "/after_retry/phase", "op": "eq", "expected": "PLAYING"}])

    async def levels(self):
        await self.menu()
        await self.click("levels", "选择关卡|关卡选择", required=False)
        before = await self.state()
        unlocked = before.get("progress", {}).get("unlockedLevel", 1)
        controls = []
        selected = None
        for level in (1, 2, 3):
            control = self.page.locator(f'[data-action="level-{level}"]:visible')
            if not await control.count():
                raise AssertionError("Level selector omits level " + str(level))
            enabled = await control.first.is_enabled()
            controls.append({"level": level, "enabled": enabled, "text": await control.first.inner_text()})
            if level > unlocked and enabled:
                raise AssertionError("Locked level is actionable in the production level selector")
            if level <= unlocked:
                selected = level
        if selected is None:
            raise AssertionError("No unlocked level is selectable")
        await self.page.locator(f'[data-action="level-{selected}"]:visible').first.click()
        await self.click("start", "开始游戏|开始", required=False)
        await self.page.wait_for_timeout(40)
        after = await self.state()
        self.emit("level-select", {"before": before, "controls": controls, "selected_level": selected, "after": after}, [57],
            [{"pointer": "/controls", "op": "count", "expected": 3}, {"pointer": "/after/level", "op": "eq", "expected": selected}])

    async def deadlock(self):
        state = await self.state()
        if state.get("phase") != "DEADLOCK":
            raise MissingUIPrerequisite("No legitimately reached production DEADLOCK is currently visible; isolated numeric fixtures cannot establish this UI")
        text = await self.text()
        if not re.search("僵局|无法继续|无法获胜|无可用目标|重新开始", text):
            raise AssertionError("DEADLOCK has no visible explanatory UI")
        restart = self.root.locator('[data-action="restart"]:visible')
        if not await restart.count():
            raise AssertionError("DEADLOCK screen omits an actionable restart")
        shot = await self.screenshot("deadlock")
        await restart.first.click()
        await self.page.wait_for_timeout(40)
        self.emit("deadlock-ui", {"before": state, "text": text, "after": await self.state(), "screenshot": shot}, [60],
            [{"pointer": "/before/phase", "op": "eq", "expected": "DEADLOCK"}, {"pointer": "/after/phase", "op": "eq", "expected": "PLAYING"}])


async def capture_project_ui_scene(validator, page, rid, spec, evidence, round_no, scene):
    """Capture a real normal-play scene before a trusted runner advances past it."""
    probe = _Probe(validator, page, rid, spec, evidence, round_no)
    if spec.case_id.endswith("002") and scene in {"upgrade_cards", "ultimate_evolution"}:
        await probe.check(scene, [64 if scene == "ultimate_evolution" else 63], lambda: probe.cards(scene))
    elif spec.case_id.endswith("003") and scene in {"power_change_before", "power_change_after"}:
        await probe.check(scene, [2, 3] if scene.endswith("before") else [24, 25], lambda: probe.power_scene(scene))
    elif scene == "defeat" and not spec.case_id.endswith("003"):
        await probe.check(scene, [34 if spec.case_id.endswith("001") else 66], probe.defeat_screen)
    else:
        probe.missing([], "Unsupported host scene capture: " + str(scene))
    return probe.result


async def validate_project_ui(validator, page, rid, spec, evidence, round_no, normal_play_traces=None):
    probe = _Probe(validator, page, rid, spec, evidence, round_no)
    if spec.case_id.endswith("003"):
        if (await probe.state()).get("phase") == "DEADLOCK":
            await probe.check("deadlock", [60], probe.deadlock)
        await probe.check("level-select", [57], probe.levels)
        await probe.check("settings", [61], probe.settings)
    else:
        shop, persistence, defeat, settings = (35, 30, 34, 36) if spec.case_id.endswith("001") else (67, 59, 66, 68)
        await probe.check("shop", [shop, persistence], lambda: probe.shop(normal_play_traces))
        await probe.check("settings", [settings], probe.settings)
        # Defeat/upgrade/deadlock UI boundaries are captured at actual normal
        # transitions or via the explicit same-production-view isolated API.
    return probe.result


async def capture_isolated_project_ui(validator, page, rid, spec, evidence, round_no, fixture, scene, steps=None):
    """Initial-condition UI boundary using the same production sim/view/renderer.

    Never awards normal-play evidence. The live game is paused via its normal
    command, the isolated handle is disposed, and live state/storage/canvas
    count must be unchanged. A fixture must describe initial conditions only.
    """
    numbers = [60] if scene == "deadlock" else [34 if spec.case_id.endswith("001") else 66] if scene == "defeat" else [64 if scene == "ultimate_evolution" else 63]
    probe = _Probe(validator, page, rid, spec, evidence, round_no, fixture=copy.deepcopy(fixture))
    handle = None
    resumed = False
    before = None
    storage_before = None
    canvas_before = None
    first_check = len(evidence["checks"])
    try:
        if scene not in {"deadlock", "defeat", "upgrade_cards", "ultimate_evolution"}:
            raise ValueError("Unsupported isolated UI boundary scene")
        if not isinstance(fixture, dict) or any(key in fixture for key in ("phase", "result", "victory", "defeat", "outcome", "gold", "progress")):
            raise ValueError("Isolated UI fixtures must contain initial conditions, never outcomes or rewards")
        live = await _state(page)
        if live.get("phase") in {"PLAYING", "MOVING", "INTERACTING"}:
            await page.evaluate("() => window.__HARNESS__.dispatch({type:'pause'})")
            resumed = True
            await page.wait_for_timeout(40)
        before = await _state(page)
        if resumed and before.get("phase") != "PAUSED":
            raise ProjectUIAdapterError("Could not freeze the live production game before the isolated UI boundary")
        storage_before = await page.evaluate("() => JSON.stringify(Object.keys(localStorage).sort().map(key=>[key,localStorage.getItem(key)]))")
        canvas_before = await page.locator("canvas").count()
        if not await page.evaluate("() => typeof window.__HARNESS__.isolatedSimulation==='function'"):
            raise ProjectUIAdapterError("Generated isolatedSimulation render/ui adapter is missing")
        try:
            handle = await page.evaluate_handle("async options => await window.__HARNESS__.isolatedSimulation(options)",
                {"caseId": spec.case_id, "seed": 20261016, "fixture": copy.deepcopy(fixture), "render": True, "ui": True})
        except Exception as error:
            raise ProjectUIAdapterError("Generated isolatedSimulation could not create a production UI handle: " + str(error)[:600]) from error
        if not await handle.evaluate("api => !!api && typeof api==='object'"):
            raise ProjectUIAdapterError("Generated isolatedSimulation returned no production handle")
        container_id = await handle.evaluate("api => api.containerId")
        if not isinstance(container_id, str) or not container_id or len(container_id) > 200 or "\x00" in container_id:
            raise ProjectUIAdapterError("Production isolated UI did not return a valid containerId")
        probe = _Probe(validator, page, rid, spec, evidence, round_no, handle=handle, container_id=container_id, fixture=copy.deepcopy(fixture))
        if await probe.root.count() != 1 or not await probe.root.is_visible():
            raise ProjectUIAdapterError("Production isolated UI container is absent, duplicated, or hidden")
        if not await probe.root.locator("canvas").count():
            raise ProjectUIAdapterError("Isolated UI has no scoped production renderer canvas")
        if not await handle.evaluate("api => ['snapshot','config','dispatch','advance','dispose','engineInfo'].every(name=>typeof api[name]==='function')"):
            raise ProjectUIAdapterError("Isolated UI handle is missing required production methods")
        info = await handle.evaluate("api => typeof api.engineInfo==='function' ? api.engineInfo() : null")
        expected = getattr(spec, "engine", None)
        actual = str((info or {}).get("name", "")).lower().replace(".js", "")
        if not info or not info.get("version") or not info.get("running") or expected and actual != expected:
            raise ProjectUIAdapterError("Isolated UI did not expose its real running locked-engine identity")
        initialized = await probe.state()
        if any(initialized.get("player", {}).get(key) != value for key, value in fixture.get("player", {}).items()):
            raise ProjectUIAdapterError("Isolated UI adapter did not apply the requested initial player fixture")
        for step in steps or []:
            if step.get("call") == "advance":
                milliseconds = step.get("milliseconds")
                if not isinstance(milliseconds, (int, float)) or not 0 <= milliseconds <= 30000:
                    raise ValueError("Invalid bounded isolated UI advance")
                await handle.evaluate("async (api, ms) => { await api.advance(ms); }", milliseconds)
            elif step.get("call") == "command":
                name = step.get("name")
                if name not in {"start", "selectLevel", "pause", "resume", "restart", "move", "selectTarget", "chooseUpgrade", "purchase", "setAudio"}:
                    raise ValueError("Unapproved isolated production command")
                await handle.evaluate("async (api, command) => { await api.dispatch(command); }", {"type": name, **step.get("args", {})})
            else:
                raise ValueError("Unknown trusted isolated UI step")
        await page.wait_for_timeout(80)
        if scene == "deadlock":
            await probe.deadlock()
        elif scene == "defeat":
            await probe.defeat_screen(steps)
        else:
            await probe.cards(scene)
    except asyncio.CancelledError:
        raise
    except ProjectUIAdapterError as error:
        probe.result["adapter_failures"].append({"acceptance_ids": probe.ids(*numbers), "reason": str(error)})
    except Exception as error:
        message = type(error).__name__ + ": " + str(error)[:1200]
        probe.result["errors"].append({"acceptance_ids": probe.ids(*numbers), "message": message})
        probe.emit("isolated-" + scene + "-error", {"ui_check_completed": False, "error": message}, numbers,
            [{"pointer": "/ui_check_completed", "op": "eq", "expected": True}])
    finally:
        cleanup_errors = []
        if handle is not None:
            try:
                await handle.evaluate("async api => { if(typeof api.dispose!=='function')throw Error('Missing isolated dispose');await api.dispose(); }")
            except Exception as error:
                cleanup_errors.append(str(error)[:1000])
            finally:
                await handle.dispose()
        if before is not None:
            try:
                await page.wait_for_timeout(40)
                after = await _state(page)
                storage_after = await page.evaluate("() => JSON.stringify(Object.keys(localStorage).sort().map(key=>[key,localStorage.getItem(key)]))")
                unchanged = {"live_state_unchanged": before == after, "live_storage_unchanged": storage_before == storage_after,
                    "live_canvas_count_unchanged": canvas_before == await page.locator("canvas").count(),
                    "dispose_errors": cleanup_errors, "live_before": before, "live_after": after,
                    "storage_before_sha256": hashlib.sha256((storage_before or "").encode()).hexdigest(),
                    "storage_after_sha256": hashlib.sha256(storage_after.encode()).hexdigest()}
                if cleanup_errors or not all(unchanged[name] for name in ("live_state_unchanged", "live_storage_unchanged", "live_canvas_count_unchanged")):
                    probe.result["adapter_failures"].append({"acceptance_ids": probe.ids(*numbers), "reason": "Isolated production UI adapter mutated live state/storage/canvases or failed disposal", "observation": unchanged})
                elif not probe.result["adapter_failures"]:
                    probe.emit("isolated-" + scene + "-noninterference", unchanged, numbers,
                        [{"pointer": "/" + name, "op": "eq", "expected": True} for name in ("live_state_unchanged", "live_storage_unchanged", "live_canvas_count_unchanged")] + [{"pointer": "/dispose_errors", "op": "count", "expected": 0}])
            except Exception as error:
                probe.result["adapter_failures"].append({"acceptance_ids": probe.ids(*numbers), "reason": "Cannot verify isolated UI cleanup: " + str(error)[:1000]})
        if resumed:
            try:
                resumed_phase = await page.evaluate("""async () => {await window.__HARNESS__.dispatch({type:'resume'});
                    return (await window.__HARNESS__.snapshot()).phase;}""")
                if resumed_phase not in {"PLAYING", "MOVING", "INTERACTING"}:
                    raise ProjectUIAdapterError("Ordinary resume did not restore a live gameplay phase: " + str(resumed_phase))
            except Exception as error:
                probe.result["adapter_failures"].append({"acceptance_ids": probe.ids(*numbers), "reason": "Could not restore live production pause preference: " + str(error)[:1000]})
        if probe.result["adapter_failures"]:
            del evidence["checks"][first_check:]
    return probe.result
