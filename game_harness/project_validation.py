"""Execute compiled games in Chromium and retain source-bound acceptance evidence.

Only the verified dist is served, on an ephemeral loopback port. Generated source
is never imported into Python/Node. Model-written results are not acceptance input.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlsplit

from playwright.async_api import async_playwright

from .acceptance import evaluate_assertion, evaluate_evidence, scenario_plan


PROJECT_CSP = ("default-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'none'; "
               "font-src 'self' data:; worker-src 'none'; frame-src 'none'; object-src 'none'; "
               "base-uri 'none'; form-action 'none'")


@contextmanager
def verified_server(workspace):
    """No directory listings, source files, traversal, caches or arbitrary routes."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                relative = unquote(urlsplit(self.path).path).lstrip("/") or "index.html"
                if relative.startswith(".") or any(part in {".", ".."} for part in relative.split("/")):
                    raise ValueError("Invalid asset path")
                raw, mime = workspace.read_asset(relative)
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Security-Policy", PROJECT_CSP)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            except (OSError, ValueError, KeyError):
                self.send_error(404)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


INSTRUMENT_RENDERER = """() => {
  const telemetry = {contexts: [], contextCount: 0, draws: 0, renderers: [], texts: []};
  Object.defineProperty(window, '__HARNESS_RENDER_OBSERVER__', {value: telemetry, configurable: false});
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function(type, ...args) {
    const context = original.call(this, type, ...args);
    if(context && !telemetry.contexts.includes(type)) telemetry.contexts.push(type);
    if(context && ['webgl','webgl2','experimental-webgl'].includes(type)) {
      const extension=context.getExtension('WEBGL_debug_renderer_info');
      const renderer=String(context.getParameter(extension?extension.UNMASKED_RENDERER_WEBGL:context.RENDERER));
      if(!telemetry.renderers.includes(renderer))telemetry.renderers.push(renderer);
    }
    if(context && !context.__harnessObserved) {
      telemetry.contextCount++;
      Object.defineProperty(context, '__harnessObserved', {value: true});
      for(const name of ['drawArrays','drawElements','drawArraysInstanced','drawElementsInstanced','drawImage','fillRect','fill']) {
        if(typeof context[name] !== 'function') continue;
        const call = context[name].bind(context);
        context[name] = (...values) => {telemetry.draws++; return call(...values);};
      }
      for(const name of ['fillText','strokeText']) {
        if(typeof context[name]!=='function')continue;
        const draw=context[name].bind(context);
        context[name]=(...values)=>{const value=String(values[0]);if(!telemetry.texts.includes(value))telemetry.texts.push(value);return draw(...values);};
      }
    }
    return context;
  };
}"""


RUN_SCENARIO = """async ({scenario, sourceHash, specHash, caseId}) => {
  const bridge = window.__HARNESS__;
  if(!bridge) throw Error('Missing window.__HARNESS__');
  const expand=fixture=>{
    if(!fixture || !fixture.enemyGenerator)return fixture;
    const f=JSON.parse(JSON.stringify(fixture)),g=f.enemyGenerator;delete f.enemyGenerator;
    const points=[];
    if(g.layout==='grid') {
      const side=Math.ceil(Math.sqrt(g.count+1)),half=Math.floor(side/2);
      for(let x=-half;x<=half;x++)for(let y=-half;y<=half;y++){if((x||y)&&points.length<g.count)points.push({x:x*g.spacing,y:y*g.spacing});}
    } else for(let i=0;i<g.count;i++)points.push({x:Math.cos(2*Math.PI*i/g.count)*g.radius,y:Math.sin(2*Math.PI*i/g.count)*g.radius});
    f.enemies=[...(f.enemies||[]),...points.map((point,i)=>({id:'generated-'+i,kind:'enemy',hp:g.hp,damage:g.damage,speed:g.speed,...point}))];return f;
  };
  const renderBefore={contexts:window.__HARNESS_RENDER_OBSERVER__?.contextCount||0,draws:window.__HARNESS_RENDER_OBSERVER__?.draws||0};
  const api = scenario.fixture === null || scenario.fixture === undefined ? bridge :
    await bridge.isolatedSimulation({caseId: caseId || bridge.caseId, seed: scenario.seed, fixture: expand(scenario.fixture),render:!!scenario.render});
  if(!api || typeof api.snapshot !== 'function') throw Error('isolatedSimulation must return the production simulation handle');
  const copy = value => JSON.parse(JSON.stringify(value));
  const observe = async handle => {const s=copy(await handle.snapshot());s._entities=Object.fromEntries((s.entities||[]).map(e=>[e.id,e]));return s;};
  const send = async (handle,name,args={}) => typeof handle.command==='function' ? handle.command(name,args) : handle.dispatch({type:name,...args});
  const trace = {project_sha256:sourceHash, spec_sha256:specHash, scenario_id:scenario.id,
                 fixture:scenario.fixture, seed:scenario.seed, steps:[]};
  try {
    for(const step of scenario.steps) {
      if(step.call==='command') {
        try {await send(api,step.name,step.args||{});} catch(error) {if(!step.allow_rejection)throw error;trace.rejections=trace.rejections||[];trace.rejections.push(String(error));}
      } else if(step.call==='advance') await api.advance(step.milliseconds);
      else if(step.call==='snapshot') trace[step.save] = await observe(api);
      else if(step.call==='chooseFirstUpgrade') {
        const s=await api.snapshot(),choices=s.upgradeChoices||s.choices||[];
        if(!choices.length) throw Error('No actual upgrade choices available');
        const selected=choices[0];await send(api,'chooseUpgrade',{id:typeof selected==='string'?selected:selected.id});
      } else if(step.call==='selectWeakEnemy') {
        const s=await api.snapshot(), target=(s.entities||[]).find(e=>e.kind==='enemy' && e.active!==false && e.power<s.player.power);
        if(!target)throw Error('No valid real enemy found');await send(api,'selectTarget',{id:target.id});
      } else if(step.call==='until') {
        const get=(s,p)=>p.split('/').slice(1).reduce((v,k)=>v[k],s);
        const history=[];let found=false;
        for(let ms=0;ms<=step.maxMilliseconds;ms+=step.milliseconds) {
          const s=await observe(api);history.push(s);
          const current=get(s,step.pointer);
          const interaction=step.pointer==='/phase' && step.expected==='INTERACTING' && ['attack','attacking','ATTACK'].includes(s.player?.animation);
          if(current===step.expected || interaction){trace[step.save]=s;found=true;break;}
          await api.advance(step.milliseconds);
        }
        if(step.history)trace[step.history]=history;
        if(!found)throw Error('Expected simulation condition not reached: '+step.pointer+'='+step.expected);
      } else if(step.call==='variant') {
        const instance=await bridge.isolatedSimulation({caseId:caseId || bridge.caseId,seed:scenario.seed,fixture:expand(step.fixture)});
        try {await instance.advance(step.milliseconds);trace[step.save]=await observe(instance);} finally {if(instance.dispose)await instance.dispose();}
      }
      else throw Error('Unknown trusted scenario step '+step.call);
      trace.steps.push(copy(step));
    }
    trace.events = typeof api.events==='function' ? copy(await api.events(0)) : [];
    if(scenario.render) {
      if(typeof api.engineInfo!=='function') trace.renderUnavailable=true;
      else {
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        trace.renderEvidence={engineInfo:copy(await api.engineInfo()),
          additionalContexts:(window.__HARNESS_RENDER_OBSERVER__?.contextCount||0)-renderBefore.contexts,
          drawCalls:(window.__HARNESS_RENDER_OBSERVER__?.draws||0)-renderBefore.draws};
      }
    }
    return trace;
  } finally {
    if(api !== bridge && typeof api.dispose==='function') await api.dispose();
  }
}"""


RUN_NORMAL_ROUTE = """async ({route, sourceHash, specHash, caseId}) => {
  const api=window.__HARNESS__, copy=value=>JSON.parse(JSON.stringify(value));
  const observe=async()=>{const s=copy(await api.snapshot());s._entities=Object.fromEntries((s.entities||[]).map(e=>[e.id,e]));return s;};
  const command=async(name,args={})=> typeof api.command==='function' ? api.command(name,args) : api.dispatch({type:name,...args});
  const trace={project_sha256:sourceHash,spec_sha256:specHash,scenario_id:route.id,fixture:null,mode:'normal_play',inputs:[],checkpoints:[],events:[]};
  trace.configuration=typeof api.config==='function'?copy(await api.config()):null;
  const send=async(name,args={})=>{await command(name,args);trace.inputs.push({simTime:(await api.snapshot()).simTime,name,args:copy(args)});};
  await send('start',{level:route.level,seed:20261016});
  trace.initial=await observe();
  if(trace.initial.level!==route.level) throw Error('Level is locked or start(level) did not select the requested level');
  let target=null,targetSince=0,lastSeq=0,lastCheckpoint=-1;
  const maxMs=(route.maxSimSeconds || 150)*1000;
  for(let elapsed=0;elapsed<=maxMs;elapsed+=200) {
    const s=await api.snapshot();
    if(s.phase==='VICTORY' || s.phase==='DEFEAT' || s.phase==='DEADLOCK') break;
    const entities=(s.entities||[]).filter(e=>e.active!==false && !e.defeated && !e.collected);
    const p=s.player||{}, time=s.simTime || elapsed/1000;
    if(caseId==='GAME-TEST-001') {
      const ahead=entities.filter(e=>e.kind==='gate' && e.z>p.z && !e.consumed).sort((a,b)=>a.z-b.z);
      if(ahead.length) {
        const gates=ahead.filter(e=>e.group===ahead[0].group || Math.abs(e.z-ahead[0].z)<0.1);
        const value=e=>{const n=p.soldiers||0,op=e.operator,v=e.operand;return op==='+'?n+v:op==='-'?n-v:op==='*'||op==='×'?n*v:op==='/'||op==='÷'?Math.floor(n/v):-Infinity;};
        gates.sort((a,b)=>value(b)-value(a));
        const gate=gates[0];
        let x=gate.x;
        const hazard=entities.find(e=>['obstacle','mine','sandbag','barbedWire'].includes(e.kind) && e.z>p.z && e.z-p.z<14 && Math.abs(e.x-p.x)<1.8);
        if(hazard && ahead[0].z-p.z>15) x=hazard.x>=0?-4.5:4.5;
        if(typeof x==='number') await send('move',{x});
      } else {
        const hazard=entities.find(e=>['obstacle','mine','sandbag','barbedWire'].includes(e.kind) && e.z>p.z && e.z-p.z<14 && Math.abs(e.x-p.x)<1.8);
        if(hazard) await send('move',{x:hazard.x>=0?-4.5:4.5});
      }
    } else if(caseId==='GAME-TEST-003') {
      if(target && !entities.some(e=>e.id===target)) target=null;
      if(target && time-targetSince>45) throw Error('Navigation/interaction did not resolve target '+target);
      if(!target && !['MOVING','INTERACTING'].includes(s.phase)) {
        const distance=e=>Math.hypot((e.x||0)-(p.x||0),(e.z||0)-(p.z||0));
        const weapons=entities.filter(e=>e.kind==='weapon').sort((a,b)=>distance(a)-distance(b));
        const enemies=entities.filter(e=>(e.kind==='enemy'||e.kind==='boss') && p.power>e.power).sort((a,b)=>(a.kind==='boss')-(b.kind==='boss') || a.power-b.power || distance(a)-distance(b));
        const next=weapons[0] || enemies[0];
        if(!next) throw Error('No legitimate reachable power-growth action remains');
        target=next.id;targetSince=time;await send('selectTarget',{id:target});
      }
    } else {
      if(s.phase==='LEVEL_UP' || (s.upgradeChoices||s.choices||[]).length) {
        const choices=s.upgradeChoices||s.choices||[];
        if(!choices.length) throw Error('LEVEL_UP has no observable choices');
        const priority=route.strategy.upgradePriority||[];
        const id=c=>typeof c==='string'?c:c.id;
        const rank=c=>{const value=id(c),i=priority.findIndex(p=>value===p||value.startsWith(p+'-'));return i<0?100:i;};
        const owned=new Set((s.skills?.active||[]).map(value=>value.id));
        const needNew=owned.size<2&&time>=15 || owned.size<3&&time>=30;
        const newActive=needNew?choices.find(c=>/^W\\d{2}$/.test(id(c))&&!owned.has(id(c))):null;
        const choice=newActive || [...choices].sort((a,b)=>rank(a)-rank(b))[0];
        await send('chooseUpgrade',{id:id(choice)});
      } else {
        const enemies=entities.filter(e=>['enemy','elite','boss'].includes(e.kind));
        const nearest=[...enemies].sort((a,b)=>Math.hypot(a.x-p.x,a.y-p.y)-Math.hypot(b.x-p.x,b.y-p.y))[0];
        const gems=entities.filter(e=>e.kind==='gem'||e.kind==='xp').sort((a,b)=>Math.hypot(a.x-p.x,a.y-p.y)-Math.hypot(b.x-p.x,b.y-p.y));
        let dx=Math.cos(time*0.7),dy=Math.sin(time*0.7);
        if(nearest && Math.hypot(nearest.x-p.x,nearest.y-p.y)<150) {dx=p.x-nearest.x;dy=p.y-nearest.y;const d=Math.hypot(dx,dy)||1;dx/=d;dy/=d;}
        else if(gems.length) {dx=gems[0].x-p.x;dy=gems[0].y-p.y;const d=Math.hypot(dx,dy)||1;dx/=d;dy/=d;}
        await send('move',{dx,dy});
      }
    }
    await api.advance(200);
    if(Math.floor(elapsed/1000)!==lastCheckpoint) {
      lastCheckpoint=Math.floor(elapsed/1000);trace.checkpoints.push(await observe());
      if(typeof api.events==='function') {const events=await api.events(lastSeq);for(const e of events||[]) {if(e.seq>lastSeq){trace.events.push(copy(e));lastSeq=e.seq;}}}
      await new Promise(resolve=>setTimeout(resolve,0));
    }
  }
  trace.final=await observe();
  return trace;
}"""


class ProjectValidator:
    version = "project-acceptance-1"

    def __init__(self, settings, store):
        self.settings, self.store = settings, store

    async def validate(self, rid, spec, workspace, build, round_no):
        report = {"version": self.version, "passed": False, "errors": [], "browser": [], "scenarios": [],
                  "actionable_failures": [], "missing_executors": [], "executor_gaps": [], "manual_required": [], "visual_required": [],
                  "source_sha256": build.get("source_sha256"), "spec_sha256": spec.sha256}
        if build.get("passed") is not True:
            report["errors"].append("Production typecheck/build did not pass")
            report["actionable_failures"].append({"id": "production-build", "errors": build.get("errors", report["errors"])})
            report["acceptance"] = evaluate_evidence(spec, {"checks": []})
            return report
        if build.get("source_sha256") != workspace.snapshot().get("sha256"):
            report["errors"].append("Source changed after the production build")
            report["acceptance"] = evaluate_evidence(spec, {"checks": []})
            return report
        evidence = {"spec_sha256": spec.sha256, "project_sha256": build["source_sha256"],
                    "artifact_root": str(self.store.directory(rid)), "checks": []}
        try:
            for artifact in build.get("artifact_manifest", []):
                raw, _ = workspace.read_asset(artifact["path"])
                if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
                    raise ValueError("Production artifact hash changed: " + artifact["path"])
            plan = scenario_plan(spec.case_id)
            with verified_server(workspace) as origin:
                await asyncio.wait_for(self._execute(rid, spec, workspace, report, evidence, plan, origin, round_no),
                                       timeout=float(getattr(self.settings, "project_browser_timeout", 600)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            report["errors"].append(type(exc).__name__ + ": " + str(exc)[:2000])
        if workspace.snapshot().get("sha256") != build["source_sha256"]:
            report["errors"].append("Source changed during browser acceptance")
        report["acceptance"] = evaluate_evidence(spec, evidence)
        report["passed"] = report["acceptance"]["complete"] and not report["errors"]
        report["evidence_manifest"] = [{"requirement_id": c["requirement_id"], "scenario_id": c.get("scenario_id"), "artifacts": c["artifacts"]} for c in evidence["checks"]]
        browser_executors = {
            "GAME-TEST-001": {3, 4, 31, 32, 33, 37, 38, 39, 40, 41, 42, 45, 46},
            "GAME-TEST-002": {2, 3, 61, 62, 65, 69, 70, 71, 72, 73, 74, 77, 78},
            "GAME-TEST-003": {52, 53, 54, 56, 58, 59, 62, 63, 64, 65, 66, 67, 68, 72, 73},
        }.get(spec.case_id, set())
        mapping = plan["acceptance_mapping"] if "plan" in locals() else {}
        report["actionable_failures"] = [{"id": test["id"], "text": test["text"], "errors": test["errors"]}
            for test in report["acceptance"]["tests"] if test["status"] == "failed"]
        report["missing_executors"] = [{"id": test["id"], "text": test["text"]}
            for test in report["acceptance"]["tests"] if test["status"] == "not_run"
            and not mapping.get(test["id"]) and int(test["test_id"][1:]) not in browser_executors]
        report["missing_executors"].extend({"id": test_id, "reason": gap["reason"]} for gap in report["executor_gaps"] for test_id in gap["acceptance_ids"])
        report["manual_required"] = [test["id"] for test in report["acceptance"]["tests"] if test["status"] == "manual_required"]
        report["visual_required"] = [test["id"] for test in report["acceptance"]["tests"] if test["status"] == "visual_required"]
        report["repair_policy"] = "Repair actual failed assertions; missing executors are harness work, and manual/visual pending checks are separate verification work."
        self.store.write(rid, f"project-evidence-{round_no}.json", evidence)
        return report

    def _evidence(self, rid, spec, evidence, round_no, label, trace, ids, assertions, *, kind="runtime", scenario_id=None, mode=None, fixture=None):
        safe = re.sub(r"[^a-zA-Z0-9_-]", "-", label)[:120]
        name = f"project-r{round_no}-{safe}.json"
        trace.update(project_sha256=evidence["project_sha256"], spec_sha256=spec.sha256)
        digest = self.store.write(rid, name, trace)
        for test_id in ids:
            evidence["checks"].append({"requirement_id": test_id, "scenario_id": scenario_id,
                "project_sha256": evidence["project_sha256"], "spec_sha256": spec.sha256,
                "kind": kind, "producer": "playwright", "executed": True, "mode": mode, "fixture": fixture,
                "artifacts": [{"path": name, "sha256": digest}],
                "assertions": [{**assertion, "artifact": name} for assertion in assertions]})

    async def _execute(self, rid, spec, workspace, report, evidence, plan, origin, round_no):
        async with async_playwright() as playwright:
            software = bool(getattr(self.settings, "project_software_rendering", True))
            args = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if software else []
            report["rendering"] = {"requested_software_backend": software, "launch_arguments": args,
                "performance_scope": "Desktop headless functional evidence only; software rendering is not physical-mobile performance evidence"}
            browser = await playwright.chromium.launch(headless=True, chromium_sandbox=self.settings.browser_sandbox, args=args)
            try:
                context = await self._context(browser, origin)
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)[:1000]))
                await page.goto(origin + "/", wait_until="networkidle", timeout=30000)
                await page.wait_for_function("window.__HARNESS__ && typeof window.__HARNESS__.snapshot==='function'", timeout=15000)
                for index, scenario in enumerate(plan["scenarios"]):
                    if index % 10 == 0:
                        self.store.event(rid, "project_verification", "acceptance_progress", {"scenario": index, "total": len(plan["scenarios"]), "case_id": spec.case_id})
                    try:
                        trace = await page.evaluate(RUN_SCENARIO, {"scenario": scenario, "sourceHash": evidence["project_sha256"], "specHash": spec.sha256, "caseId": spec.case_id})
                        assertions = scenario["assertions"]
                    except Exception as exc:
                        trace = {"execution_error": str(exc)[:3000]}
                        assertions = [{"pointer": "/execution_error", "op": "eq", "expected": None}]
                    if scenario.get("render") and trace.get("renderUnavailable"):
                        report["scenarios"].append({"id": scenario["id"], "status": "not_run", "executor_gap": "Optional isolated real-renderer handle is unavailable"})
                        report["executor_gaps"].append({"scenario_id": scenario["id"], "acceptance_ids": scenario["acceptance_ids"], "reason": "isolatedSimulation(render:true) must supply the actual renderer handle"})
                        continue
                    self._evidence(rid, spec, evidence, round_no, f"scenario-{index}", trace,
                        scenario["acceptance_ids"], assertions, scenario_id=scenario["id"], fixture=scenario["fixture"])
                    assertion_errors = []
                    for assertion in scenario["assertions"]:
                        try:
                            if not evaluate_assertion(trace, assertion):
                                assertion_errors.append("Failed assertion: " + json.dumps(assertion, ensure_ascii=False))
                        except Exception as exc:
                            assertion_errors.append(type(exc).__name__ + ": " + str(exc)[:500])
                    report["scenarios"].append({"id": scenario["id"], "error": trace.get("execution_error"),
                        "status": "failed" if assertion_errors else "passed", "assertion_errors": assertion_errors})
                # A fresh page removes isolated test instance state from normal play.
                await page.reload(wait_until="networkidle")
                await page.wait_for_function("window.__HARNESS__ && typeof window.__HARNESS__.snapshot==='function'")
                for route in plan["normal_play_routes"]:
                    try:
                        trace = await page.evaluate(RUN_NORMAL_ROUTE, {"route": route, "sourceHash": evidence["project_sha256"], "specHash": spec.sha256, "caseId": spec.case_id})
                        assertions = route["assertions"]
                    except Exception as exc:
                        trace = {"execution_error": str(exc)[:3000]}
                        assertions = [{"pointer": "/execution_error", "op": "eq", "expected": None}]
                    self._evidence(rid, spec, evidence, round_no, "normal-level-" + str(route["level"]), trace,
                        route["acceptance_ids"], assertions, kind="gameplay", scenario_id=route["id"], mode="normal_play")
                    report["scenarios"].append({"id": route["id"], "error": trace.get("execution_error"), "phase": trace.get("final", {}).get("phase")})
                    if trace.get("final", {}).get("phase") != "VICTORY":
                        break  # Subsequent levels are locked; do not bypass progression.
                    await page.wait_for_timeout(100)
                    screen_text = await page.locator("body").inner_text()
                    victory_id = 33 if spec.case_id.endswith("001") else 65 if spec.case_id.endswith("002") else 59
                    self._evidence(rid, spec, evidence, round_no, "victory-ui-level-" + str(route["level"]),
                        {"phase": trace["final"]["phase"], "text": screen_text}, [spec.case_id + f"/T{victory_id:02}"],
                        [{"pointer": "/phase", "op": "eq", "expected": "VICTORY"},
                         {"pointer": "/text", "op": "contains", "expected": "闯关成功"}], kind="browser")
                    # The won state is real; reloading must preserve unlocked and
                    # completed levels rather than exercising default save values.
                    before_reload = await page.evaluate("() => JSON.parse(JSON.stringify(window.__HARNESS__.snapshot()))")
                    await page.reload(wait_until="networkidle")
                    await page.wait_for_function("window.__HARNESS__ && typeof window.__HARNESS__.snapshot==='function'")
                    after_reload = await page.evaluate("() => JSON.parse(JSON.stringify(window.__HARNESS__.snapshot()))")
                    persistence_ids = [52, 53] if spec.case_id.endswith("003") else []
                    assertions = [{"pointer": "/after/progress/unlockedLevel", "op": "same", "other_pointer": "/before/progress/unlockedLevel"},
                                  {"pointer": "/after/progress/completedLevels", "op": "same", "other_pointer": "/before/progress/completedLevels"}]
                    self._evidence(rid, spec, evidence, round_no, "persistence-level-" + str(route["level"]),
                        {"before": before_reload, "after": after_reload}, [spec.case_id + "/T" + str(n).zfill(2) for n in persistence_ids], assertions, kind="browser")
                report["errors"].extend(errors)
                await context.close()
                for viewport in [("desktop", 1280, 800, False), ("portrait", 390, 844, True), ("landscape", 844, 390, True)]:
                    result = await self._viewport(browser, origin, rid, spec, workspace, evidence, round_no, *viewport)
                    report["browser"].append(result)
                    report["errors"].extend(result["errors"])
            finally:
                await browser.close()

    async def _context(self, browser, origin, *, width=1280, height=800, touch=False):
        context = await browser.new_context(viewport={"width": width, "height": height}, has_touch=touch,
                                            is_mobile=touch, service_workers="block", accept_downloads=False)
        await context.clear_permissions()
        await context.add_init_script("(" + INSTRUMENT_RENDERER + ")()")
        blocked = []

        async def route_request(route):
            parsed = urlsplit(route.request.url)
            if route.request.url.startswith(origin + "/") and parsed.scheme == "http":
                await route.continue_()
            else:
                blocked.append(route.request.url[:300])
                await route.abort()

        await context.route("**/*", route_request)
        context._harness_blocked = blocked
        return context

    async def _viewport(self, browser, origin, rid, spec, workspace, evidence, round_no, name, width, height, touch):
        result = {"name": name, "width": width, "height": height, "errors": [], "observations": {}}
        context = await self._context(browser, origin, width=width, height=height, touch=touch)
        page = await context.new_page()
        page.set_default_timeout(3000)
        page.on("pageerror", lambda error: result["errors"].append(str(error)[:1000]))
        state = lambda: page.evaluate("() => JSON.parse(JSON.stringify(window.__HARNESS__.snapshot()))")
        prefix = spec.case_id

        async def action(name):
            locators = page.locator('[data-action="' + name + '"]:visible')
            for index in reversed(range(await locators.count())):
                candidate = locators.nth(index)
                try:
                    await candidate.click(trial=True, timeout=600)
                    await candidate.click()
                    return
                except Exception:
                    continue
            raise AssertionError("No actionable data-action=" + name)

        def save(label, trace, tests, assertions):
            self._evidence(rid, spec, evidence, round_no, name + "-" + label, trace,
                [prefix + "/T" + str(number).zfill(2) for number in tests], assertions, kind="browser")

        try:
            await page.goto(origin + "/", wait_until="networkidle", timeout=30000)
            await page.wait_for_function("window.__HARNESS__ && typeof window.__HARNESS__.snapshot==='function'", timeout=15000)
            result["initial"] = await state()
            initial_text = await page.locator("body").inner_text()
            menu_id = 31 if prefix.endswith("001") else 61 if prefix.endswith("002") else 56
            save("main-menu", {"state": result["initial"], "text": initial_text}, [menu_id],
                 [{"pointer": "/state/phase", "op": "eq", "expected": "MENU"},
                  {"pointer": "/text", "op": "nonempty"}])
            if prefix.endswith("003"):
                bad_words = re.findall(r"\b[A-Za-z]{3,}\b", initial_text)
                save("chinese-ui", {"text": initial_text, "englishWords": bad_words}, [62],
                    [{"pointer": "/englishWords", "op": "count", "expected": 0}])
            await action("start")
            await page.wait_for_timeout(300)
            result["running"] = await state()
            await action("pause")
            before = await state()
            await page.wait_for_timeout(150)
            after = await state()
            pause_ids = [32] if prefix.endswith("001") else [62] if prefix.endswith("002") else [58]
            save("pause", {"before": before, "after": after}, pause_ids,
                 [{"pointer": "/after/phase", "op": "eq", "expected": "PAUSED"}, {"pointer": "/after/simTime", "op": "same", "other_pointer": "/before/simTime"}])
            await action("resume")
            await page.wait_for_timeout(200)
            result["resumed"] = await state()
            save("resume", {"state": result["resumed"]}, pause_ids,
                 [{"pointer": "/state/phase", "op": "eq", "expected": "PLAYING"}])
            await action("pause")
            before = await state()
            await page.set_viewport_size({"width": height, "height": width})
            await page.wait_for_timeout(100)
            after = await state()
            orientation_ids = [41] if prefix.endswith("001") else [73] if prefix.endswith("002") else [67]
            assertions = [{"pointer": "/after/level", "op": "same", "other_pointer": "/before/level"}]
            for stat in ["soldiers"] if prefix.endswith("001") else ["hp"] if prefix.endswith("002") else ["power"]:
                assertions.append({"pointer": "/after/player/" + stat, "op": "same", "other_pointer": "/before/player/" + stat})
            save("orientation", {"before": before, "after": after}, orientation_ids, assertions)
            await page.set_viewport_size({"width": width, "height": height})
            await action("restart")
            await page.wait_for_timeout(150)
            result["restarted"] = await state()
            for _ in range(4):
                await action("restart")
            repeated = await state()
            loop_ids = [45] if prefix.endswith("001") else [77] if prefix.endswith("002") else [72]
            save("restart-loop", {"after": repeated}, loop_ids,
                 [{"pointer": "/after/metrics/updateLoops", "op": "eq", "expected": 1}])
            if name == "desktop":
                # Heap/DOM counters are observed by Chromium itself. A game-owned
                # counter returning a constant cannot establish resource cleanup.
                session = await context.new_cdp_session(page)
                try:
                    await session.send("Performance.enable")
                    await session.send("HeapProfiler.collectGarbage")
                    heap_before = await session.send("Runtime.getHeapUsage")
                    dom_before = await session.send("Memory.getDOMCounters")
                    resources_before = await page.evaluate("() => window.__HARNESS__.engineInfo ? window.__HARNESS__.engineInfo().resources : null")
                    for _ in range(40):
                        await action("restart")
                    await page.wait_for_timeout(200)
                    await session.send("HeapProfiler.collectGarbage")
                    heap_after = await session.send("Runtime.getHeapUsage")
                    dom_after = await session.send("Memory.getDOMCounters")
                    resources_after = await page.evaluate("() => window.__HARNESS__.engineInfo ? window.__HARNESS__.engineInfo().resources : null")
                    resources_bounded = isinstance(resources_before, dict) and isinstance(resources_after, dict) and len(resources_before) >= 3 and all(
                        isinstance(value, (int, float)) and resources_after.get(key, float("inf")) <= value + 4 for key, value in resources_before.items())
                    memory = {"before": heap_before, "after": heap_after, "domBefore": dom_before, "domAfter": dom_after,
                              "resourcesBefore": resources_before, "resourcesAfter": resources_after,
                              "heapBounded": heap_after["usedSize"] <= max(heap_before["usedSize"] * 1.35, heap_before["usedSize"] + 3_000_000),
                              "listenersBounded": dom_after["jsEventListeners"] <= dom_before["jsEventListeners"] + 20,
                              "nodesBounded": dom_after["nodes"] <= dom_before["nodes"] + 250,
                              "resourcesBounded": resources_bounded}
                    cleanup_id = 46 if prefix.endswith("001") else 78 if prefix.endswith("002") else 73
                    save("restart-resource-cleanup", memory, [cleanup_id],
                        [{"pointer": "/" + field, "op": "eq", "expected": True} for field in ("heapBounded", "listenersBounded", "nodesBounded", "resourcesBounded")])
                finally:
                    await session.detach()
            await action("pause")
            canvas = page.locator("canvas").first
            box = await canvas.bounding_box()
            result["observations"] = await page.evaluate("""() => ({hasCanvas:!!document.querySelector('canvas'),
              noHorizontalOverflow:document.documentElement.scrollWidth<=innerWidth+1,
              chinese:/[\\u4e00-\\u9fff]/.test(document.body.innerText),
              telemetry:window.__HARNESS_RENDER_OBSERVER__,engineInfo:window.__HARNESS__.engineInfo?window.__HARNESS__.engineInfo():null})""")
            engine_info = result["observations"].get("engineInfo") or {}
            observed_engine = str(engine_info.get("name", "")).lower().replace(".js", "")
            if observed_engine != spec.engine or not engine_info.get("version"):
                raise AssertionError("engineInfo() must identify the actual running locked engine and its version")
            contexts = result["observations"].get("telemetry", {}).get("contexts", [])
            if spec.engine == "babylon" and not any(value in contexts for value in ("webgl", "webgl2", "experimental-webgl")):
                raise AssertionError("Babylon project has not created a real WebGL context")
            dependency = "@babylonjs/core" if spec.engine == "babylon" else "phaser"
            if not any(dependency in workspace.read_file(path) for path in workspace.list_files() if path.startswith("src/") and Path(path).suffix in {".ts", ".tsx", ".js", ".jsx"}):
                raise AssertionError("Source does not import the pinned actual engine")
            if result["observations"].get("telemetry", {}).get("draws", 0) <= 0:
                raise AssertionError("No real rendering draw calls were observed")
            screenshot_name = f"project-r{round_no}-{name}.png"
            result["screenshot_sha256"] = self.store.write(rid, screenshot_name, await page.screenshot(full_page=True))
            result["screenshot"] = screenshot_name
            if not box or box["width"] < 100 or box["height"] < 100:
                raise AssertionError("The real engine canvas is not visible")
            expected_ids = [37 if name == "portrait" else 38] if prefix.endswith("001") else [69 if name == "portrait" else 70] if prefix.endswith("002") else [63 if name == "portrait" else 64]
            if name != "desktop":
                # A screenshot and geometry are necessary, but readability/fidelity
                # criteria still remain manual instead of being inferred here.
                save("layout", result, expected_ids,
                    [{"pointer": "/observations/hasCanvas", "op": "eq", "expected": True},
                     {"pointer": "/observations/noHorizontalOverflow", "op": "eq", "expected": True},
                     {"pointer": "/observations/chinese", "op": "eq", "expected": True},
                     {"pointer": "/observations/telemetry/draws", "op": "gt", "expected": 0}])
            # Exercise real browser input and inspect production state, never call
            # selectTarget and label it a click/tap test.
            await action("resume")
            before = await state()
            if prefix.endswith("003"):
                entities = [e for e in before.get("entities", []) if e.get("active", True) and e.get("kind") == "enemy" and e.get("power", 10**9) < before.get("player", {}).get("power", 0)]
                if not entities:
                    raise AssertionError("No visible valid target available for real picking verification")
                target = entities[0]
                point = await page.evaluate("id => window.__HARNESS__.projectEntity ? window.__HARNESS__.projectEntity(id) : null", target["id"])
                if not point:
                    raise AssertionError("projectEntity(id) must expose the actual scene camera projection for picking tests")
                if touch:
                    await page.touchscreen.tap(point["x"], point["y"])
                else:
                    await page.mouse.click(point["x"], point["y"])
                await page.wait_for_timeout(100)
                after = await state()
                trace = {"before": before, "after": after, "clickedEntity": target["id"], "point": point}
                # Movement/interaction must be observed, not just an event log.
                changed = before.get("player") != after.get("player") or before.get("phase") != after.get("phase") or after.get("target") == target["id"]
                trace["selectionChangedProductionState"] = changed
                save("picking", trace, [65 if touch else 66], [{"pointer": "/selectionChangedProductionState", "op": "eq", "expected": True}])
            elif prefix.endswith("001"):
                x, y = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.65
                if touch:
                    await self._touch_drag(context, page, x, y, x + box["width"] * 0.25, y)
                else:
                    await page.mouse.move(x, y)
                    await page.mouse.down()
                    await page.mouse.move(x + box["width"] * 0.25, y, steps=10)
                await page.wait_for_timeout(150)
                after = await state()
                if not touch:
                    await page.mouse.up()
                save("drag", {"before": before, "after": after}, [3, 39] if touch else [40],
                    [{"pointer": "/after/player/x", "op": "ne", "expected": before["player"]["x"]}])
                await page.wait_for_timeout(250)
                released = await state()
                await page.wait_for_timeout(200)
                stable = await state()
                save("release", {"released": released, "stable": stable}, [4],
                    [{"pointer": "/stable/player/x", "op": "approx", "expected": released["player"]["x"], "tolerance": 0.02}])
            else:
                if touch:
                    joystick = page.locator('[data-testid="joystick"], [data-action="joystick"]').first
                    joystick_box = await joystick.bounding_box() if await joystick.count() else None
                    x = joystick_box["x"] + joystick_box["width"] / 2 if joystick_box else box["x"] + box["width"] * 0.15
                    y = joystick_box["y"] + joystick_box["height"] / 2 if joystick_box else box["y"] + box["height"] * 0.82
                    await self._touch_drag(context, page, x, y, x + 50, y)
                    after = await state()
                    save("touch-joystick", {"before": before, "after": after}, [2, 71],
                        [{"pointer": "/after/player/x", "op": "ne", "expected": before["player"]["x"]}])
                else:
                    await page.keyboard.down("ArrowRight")
                    await page.wait_for_timeout(250)
                    await page.keyboard.up("ArrowRight")
                    after = await state()
                    save("keyboard", {"before": before, "after": after}, [3, 72],
                        [{"pointer": "/after/player/x", "op": "ne", "expected": before["player"]["x"]}])
            result["blocked_requests"] = context._harness_blocked
            offline_ids = [42] if prefix.endswith("001") else [74] if prefix.endswith("002") else [68]
            save("local-only", {"blocked": context._harness_blocked}, offline_ids,
                [{"pointer": "/blocked", "op": "count", "expected": 0}])
            await page.evaluate("""() => {
              const a=window.__HARNESS__, args={music:false,sfx:false};
              return a.command ? a.command('setAudio',args) : a.dispatch({type:'setAudio',...args});
            }""")
            before_reload = await state()
            await page.reload(wait_until="networkidle")
            await page.wait_for_function("window.__HARNESS__ && typeof window.__HARNESS__.snapshot==='function'")
            after_reload = await state()
            save("audio-persistence", {"before": before_reload, "after": after_reload}, [54] if prefix.endswith("003") else [],
                [{"pointer": "/before/progress/audio/music", "op": "eq", "expected": False},
                 {"pointer": "/before/progress/audio/sfx", "op": "eq", "expected": False},
                 {"pointer": "/after/progress/audio", "op": "same", "other_pointer": "/before/progress/audio"}])
        except Exception as exc:
            result["errors"].append(type(exc).__name__ + ": " + str(exc)[:2000])
        finally:
            if "screenshot" not in result:
                try:
                    screenshot_name = f"project-r{round_no}-{name}.png"
                    result["screenshot_sha256"] = self.store.write(rid, screenshot_name, await page.screenshot(full_page=True, timeout=4000))
                    result["screenshot"] = screenshot_name
                except Exception as exc:
                    result["errors"].append("Could not retain failure screenshot: " + str(exc)[:500])
            await context.close()
        return result

    async def _touch_drag(self, context, page, x, y, end_x, end_y):
        """Native Chromium touch events; mouse emulation is not touch evidence."""
        session = await context.new_cdp_session(page)
        try:
            await session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 0}]})
            for step in range(1, 7):
                await session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + (end_x-x)*step/6, "y": y + (end_y-y)*step/6, "id": 0}]})
                await page.wait_for_timeout(30)
            await page.wait_for_timeout(180)
            await session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        finally:
            await session.detach()
