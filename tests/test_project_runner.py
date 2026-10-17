"""Project orchestration control tests; fake games never count as case evidence."""
import asyncio
from dataclasses import replace
import hashlib
import json
import subprocess
import sys
from types import ModuleType
from types import SimpleNamespace
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient
import httpx
from openai import AsyncOpenAI
import pytest

from game_harness.app import create_app
from game_harness.acceptance import evaluate_evidence
from game_harness.project_runner import ImplementationPlan, ProjectOrchestrator
from game_harness.project_state import ProjectLedger
from game_harness.runner import Harness
from game_harness.specifications import ingest_text, load_case


def project_request(case="GAME-TEST-001"):
    spec = load_case(case)
    return {"mode": "project", "description": spec.names[-1], "engine": spec.engine,
            "case_id": spec.case_id, "specification": spec.text, "source_path": spec.source_path,
            "reference_images": spec.reference_images}


def implementation_plan(spec):
    sections = [s["number"] for s in spec.sections if s["level"] == 1]
    return ImplementationPlan.model_validate({"architecture": "真实引擎、Simulation与React分层。",
        "interfaces": "Simulation.update(dt): void; Scene.render(snapshot): void",
        "features": [{"id": f"feature-{i}", "title": f"模块{i}", "owner": f"agent-{i}",
            "objective": f"实现模块{i}及完整相关规格。", "depends_on": [f"feature-{i - 1}"] if i else [],
            "owned_paths": ([f"src/module{i}.ts"] if i != 4 else
                            ["src/module4.ts", "src/main.tsx", "src/App.tsx", "src/engine.ts", "src/game/Simulation.ts"]),
            "section_numbers": sections[i::5],
            "acceptance_ids": [t["id"] for t in spec.acceptance_tests[i::5]], "interfaces": "显式模块接口"}
            for i in range(5)]})


class Planner:
    def __init__(self, plan):
        self.plan, self.calls = plan, []

    async def structured(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.plan

    async def close(self):
        pass


class Workspace:
    def __init__(self, root, passed=True):
        self.root, self.passed = root, passed

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        return self.snapshot()

    def snapshot(self):
        files = [{"path": p.relative_to(self.root).as_posix(),
                  "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                 for p in sorted(self.root.rglob("*.ts"))]
        return {"files": files, "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}

    def list_files(self):
        return [item["path"] for item in self.snapshot()["files"]]

    def inline_preview(self):
        """ZIP packaging fixture only; this is not a generated or accepted game."""
        return "<!doctype html><html lang='zh'><body>仅用于ZIP封装测试</body></html>"

    async def build(self, **kwargs):
        return {"passed": self.passed, "source_sha256": self.snapshot()["sha256"], "entrypoint": "index.html",
                "artifact_manifest": [{"path": "index.html", "sha256": "a" * 64}],
                "stages": [{"name": "typecheck", "passed": self.passed}, {"name": "build", "passed": self.passed}],
                "errors": [] if self.passed else ["TypeScript error"]}


def install_fake_workers(monkeypatch, *, fail_first=None, report=None):
    calls = []
    failed = False

    class Coder:
        def __init__(self, agents, store, workspace, **kwargs):
            self.workspace = workspace

        async def run_feature(self, rid, feature, context, **kwargs):
            nonlocal failed
            calls.append((rid, feature, context, kwargs))
            if fail_first and not failed:
                failed = True
                if fail_first == "exception":
                    raise RuntimeError("transient provider failure")
                if fail_first == "cancelled":
                    raise asyncio.CancelledError("worker shutdown during a feature")
                return {"status": fail_first, "steps": 1, "summary": "durable feature state remains"}
            self.workspace.root.mkdir(parents=True, exist_ok=True)
            (self.workspace.root / (feature["id"] + ".ts")).write_text("export const feature = true;")
            return {"status": "implemented", "steps": 1, "changed_files": feature["owned_paths"]}

    class Validator:
        def __init__(self, settings, store):
            pass

        async def validate(self, rid, spec, workspace, build, round_no):
            if callable(report):
                return report(spec, workspace, build)
            return report or {"passed": False, "scope": "test double, no real acceptance was executed"}

    module = ModuleType("game_harness.project_validation")
    module.ProjectValidator = Validator
    monkeypatch.setitem(sys.modules, "game_harness.project_validation", module)
    monkeypatch.setattr("game_harness.coding_agent.CodingAgent", Coder)
    return calls


@pytest.mark.parametrize("case", ["GAME-TEST-001", "GAME-TEST-002", "GAME-TEST-003"])
async def test_prepare_archives_complete_official_spec_and_reuses_validated_plan(settings, store, case):
    request = project_request(case)
    rid = store.create(request)["id"]
    spec = load_case(case)
    planner = Planner(implementation_plan(spec))
    workflow = ProjectOrchestrator(settings, store, agents=planner)
    loaded, plan = await workflow.prepare(rid)
    assert loaded.text == spec.text
    assert store.path(rid, "specification.txt").read_text() == spec.text
    assert store.read_json(rid, "specification.json")["sha256"] == spec.sha256
    assert planner.calls[0][0][4]["approved_specification"] == spec.text
    assert {t["id"] for t in spec.acceptance_tests} == {t for f in plan.features for t in f.acceptance_ids}
    assert len(workflow.ledger.features(rid)) == 5
    await workflow.prepare(rid)
    assert len(planner.calls) == 1


@pytest.mark.parametrize("defect", ["unknown_section", "missing_section", "unordered_ownership"])
async def test_planning_rejects_lost_scope_and_conflicting_owners(settings, store, defect):
    rid = store.create(project_request())["id"]
    spec = load_case("GAME-TEST-001")
    data = implementation_plan(spec).model_dump()
    if defect == "unknown_section":
        data["features"][0]["section_numbers"].append("99.9")
    elif defect == "missing_section":
        for feature in data["features"]:
            feature["section_numbers"] = [n for n in feature["section_numbers"] if n != "08"]
    else:
        data["features"][1]["depends_on"] = []
        data["features"][1]["owned_paths"] = data["features"][0]["owned_paths"]
    workflow = ProjectOrchestrator(settings, store, agents=Planner(ImplementationPlan.model_validate(data)))
    with pytest.raises((ValueError, RuntimeError)):
        await workflow.prepare(rid)


@pytest.mark.parametrize("failure", ["exception", "cancelled", "needs_work", "blocked"])
async def test_feature_failure_resumes_its_durable_dag_without_server_restart(settings, store, tmp_path, monkeypatch, failure):
    settings = replace(settings, project_repair_rounds=0)
    rid = store.create(project_request())["id"]
    store.claim()
    spec = load_case("GAME-TEST-001")
    workflow = ProjectOrchestrator(settings, store, agents=Planner(implementation_plan(spec)))
    workspace = Workspace(tmp_path / "workspace")
    monkeypatch.setattr(workflow, "workspace", lambda _: workspace)
    calls = install_fake_workers(monkeypatch, fail_first=failure)
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await workflow.run(rid)
        assert workflow.ledger.features(rid)[0]["status"] in {"running", "needs_work", "blocked"}
        # Same process, same run, same source and budgets; no global recover().
        await workflow.run(rid)
    else:
        # A recoverable feature result is retried autonomously within this run.
        await workflow.run(rid)
    assert [f["status"] for f in workflow.ledger.features(rid)] == ["implemented"] * 5
    expected = ["feature-0"]
    if failure != "cancelled":
        expected.append("dependency-repair-feature-0-1")
    expected += ["feature-0" if failure == "cancelled" else "feature-0--attempt-2",
                 "feature-1", "feature-2", "feature-3", "feature-4"]
    assert [entry[1]["id"] for entry in calls] == expected
    assert store.get(rid)["status"] == "needs_attention"
    assert not store.path(rid, "manifest.json").exists()


@pytest.mark.parametrize("defect", ["failed_build", "empty_report", "missing_test", "not_run_test", "wrong_source"])
async def test_publishing_requires_independent_complete_current_evidence(settings, store, tmp_path, monkeypatch, defect):
    settings = replace(settings, project_repair_rounds=0)
    rid = store.create(project_request())["id"]
    store.claim()
    spec = load_case("GAME-TEST-001")
    workflow = ProjectOrchestrator(settings, store, agents=Planner(implementation_plan(spec)))
    workspace = Workspace(tmp_path / "workspace", passed=defect != "failed_build")
    monkeypatch.setattr(workflow, "workspace", lambda _: workspace)
    packaged = []
    monkeypatch.setattr(workflow, "package", lambda *_: packaged.append(True))

    def forged_report(spec, workspace, build):
        evidence_name = "control-test-evidence.json"
        evidence_sha = store.write(rid, evidence_name, {"scope": "unit test double", "source_sha256": build["source_sha256"]})
        tests = [{"id": t["id"], "test_id": t["test_id"], "status": "passed", "passed": True,
                  "evidence": [{"path": evidence_name, "sha256": evidence_sha}]} for t in spec.acceptance_tests]
        result = {"passed": True, "source_sha256": workspace.snapshot()["sha256"], "spec_sha256": spec.sha256,
                  "errors": [], "browser": [{"name": "desktop", "errors": [], "screenshot": "test-double.png"}],
                  "acceptance": {"complete": True, "status": "passed", "tests": tests,
                    "project_sha256": build["source_sha256"], "spec_sha256": spec.sha256,
                    "counts": {"passed": len(tests), "failed": 0, "not_run": 0, "manual_required": 0}},
                  "evidence_manifest": [{"requirement_id": t["id"], "artifacts": t["evidence"]} for t in tests],
                  "visual_review": {"verdict": "pass", "defects": []}}
        if defect == "empty_report":
            return {"passed": True}
        if defect == "missing_test":
            tests.pop()
        elif defect == "not_run_test":
            tests[-1].update(status="not_run", passed=False)
        elif defect == "wrong_source":
            result["source_sha256"] = "b" * 64
        return result

    install_fake_workers(monkeypatch, report=forged_report)
    await workflow.run(rid)
    assert store.get(rid)["status"] != "passed"
    assert not store.path(rid, "manifest.json").exists()
    assert not packaged


@pytest.mark.parametrize("defect", [None, "tampered_evidence", "stale_source"])
async def test_publish_recomputes_a_small_source_bound_process_criterion(settings, store, tmp_path, monkeypatch, defect):
    """Positive control: a real process checks a tiny custom source criterion.

    This is not one of the official games and never becomes case-pass evidence.
    """
    text = ("Test Case ID: CUSTOM-UNIT\nGame Name:\n控制面单元测试\n\nRequired Technology:\n- Phaser\n\n"
            "01. SOURCE CONTRACT\nBuild five owned source modules.\n\n"
            "14. ACCEPTANCE TESTS\nT01:\nThe source contains exactly five TypeScript modules.\n")
    spec = ingest_text(text)
    rid = store.create({"mode": "project", "description": "unit control", "engine": "phaser",
                        "case_id": spec.case_id, "specification": text})["id"]
    store.claim()
    workflow = ProjectOrchestrator(replace(settings, project_repair_rounds=0), store,
                                   agents=Planner(implementation_plan(spec)))
    workspace = Workspace(tmp_path / "workspace")
    monkeypatch.setattr(workflow, "workspace", lambda _: workspace)
    packaged = []
    monkeypatch.setattr(workflow, "package", lambda *_: packaged.append(True))

    def real_process_report(spec, workspace, build):
        observation = subprocess.run([sys.executable, "-c",
            "import json,pathlib,sys; print(json.dumps({'module_count':len(list(pathlib.Path(sys.argv[1]).glob('*.ts')))}))",
            str(workspace.root)], capture_output=True, text=True, check=True)
        trace = {**json.loads(observation.stdout), "project_sha256": build["source_sha256"], "spec_sha256": spec.sha256}
        trace_sha = store.write(rid, "process-count.json", trace)
        evidence = {"spec_sha256": spec.sha256, "project_sha256": build["source_sha256"],
            "artifact_root": str(store.directory(rid)), "checks": [{"requirement_id": spec.acceptance_tests[0]["id"],
                "project_sha256": build["source_sha256"], "spec_sha256": spec.sha256, "executed": True,
                "producer": "subprocess", "kind": "build", "artifacts": [{"path": "process-count.json", "sha256": trace_sha}],
                "assertions": [{"artifact": "process-count.json", "pointer": "/module_count", "op": "eq", "expected": 5}]}]}
        store.write(rid, "project-evidence-0.json", evidence)
        acceptance = evaluate_evidence(spec, evidence)
        if defect == "tampered_evidence":
            store.path(rid, "process-count.json").write_text('{"module_count":5,"tampered":true}')
        elif defect == "stale_source":
            path = workspace.root / "feature-0.ts"
            path.write_text(path.read_text() + "\nexport const changedAfterValidation = true;")
        return {"passed": True, "source_sha256": build["source_sha256"], "spec_sha256": spec.sha256,
                "acceptance": acceptance, "errors": []}

    install_fake_workers(monkeypatch, report=real_process_report)
    await workflow.run(rid)
    if defect is None:
        assert store.get(rid)["status"] == "passed"
        assert store.read_json(rid, "manifest.json")["source_sha256"] == workspace.snapshot()["sha256"]
        assert packaged == [True]
    else:
        assert store.get(rid)["status"] == "needs_attention"
        assert not store.path(rid, "manifest.json").exists()
        assert not packaged


async def test_concurrent_cases_keep_source_and_progress_in_separate_workspaces(settings, store, tmp_path, monkeypatch):
    settings = replace(settings, project_repair_rounds=0)
    runs = [store.create(project_request(case))["id"] for case in ["GAME-TEST-001", "GAME-TEST-002"]]
    specs = [load_case("GAME-TEST-001"), load_case("GAME-TEST-002")]
    workflows = [ProjectOrchestrator(settings, store, agents=Planner(implementation_plan(spec))) for spec in specs]
    workspaces = {rid: Workspace(tmp_path / rid) for rid in runs}
    for workflow in workflows:
        monkeypatch.setattr(workflow, "workspace", lambda rid: workspaces[rid])
    calls = install_fake_workers(monkeypatch)
    await asyncio.gather(*(workflow.run(rid) for workflow, rid in zip(workflows, runs)))
    for workflow, rid in zip(workflows, runs):
        assert len(workflow.ledger.features(rid)) == 5
        assert store.request(rid)["case_id"] in store.path(rid, "specification.txt").read_text()
        assert len([call for call in calls if call[0] == rid]) == 5
    assert workspaces[runs[0]].root != workspaces[runs[1]].root


async def test_project_execution_uses_its_persisted_project_deadline(settings, store):
    settings = replace(settings, run_timeout=0, project_timeout=60)
    rid = store.create(project_request())["id"]
    store.claim()

    class Projects:
        async def run(self, run_id):
            store.update(run_id, status="needs_attention", stage="project_verification")

    harness = Harness(settings, store, None, None, None, projects=Projects())
    await harness.execute(rid)
    assert store.get(rid)["status"] == "needs_attention"
    assert any(event["kind"] == "execution_started" for event in store.events(rid))


async def test_project_model_uses_project_budget_and_preserves_actual_usage(settings, store):
    settings = replace(settings, max_calls=1, project_max_calls=3, project_max_tokens=20_000)
    rid = store.create(project_request())["id"]
    store.claim()
    workflow = ProjectOrchestrator(settings, store)
    client = workflow.agents
    await client.client.close()

    def respond(request):
        return httpx.Response(200, json={"id": "budget", "object": "chat.completion", "created": 0,
            "model": settings.model, "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                                                   "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100}})

    client.client = AsyncOpenAI(api_key="test", base_url="https://mock.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        for i in range(3):
            assert await client.complete(rid, f"project-step-{i}", "planner", "输出结果", {}, max_tokens=512) == "ok"
        with pytest.raises(RuntimeError, match="预算"):
            await client.complete(rid, "over-limit", "planner", "输出结果", {}, max_tokens=512)
    finally:
        await workflow.close()
    assert store.get(rid)["calls"] == 3
    assert store.get(rid)["tokens"] == 300


def test_long_spec_api_preserves_full_text_and_rejects_an_engine_downgrade(settings):
    # Do not start lifespan workers: this is a request validation test, not a
    # model-backed generation run and must never make an external API call.
    app = create_app(settings)
    client = TestClient(app)
    spec = load_case("GAME-TEST-002")
    response = client.post("/api/projects", json={"specification": spec.text, "engine": "phaser"})
    assert response.status_code == 202
    rid = response.json()["id"]
    assert app.state.store.request(rid)["specification"] == spec.text
    assert "specification" not in response.json()["request"]
    mismatch = client.post("/api/projects", json={"specification": spec.text, "engine": "babylon"})
    assert mismatch.status_code == 422


def test_delivery_zip_contains_referenced_evidence_and_excludes_execution_metadata(settings, store, tmp_path):
    rid = store.create(project_request())["id"]
    workflow = ProjectOrchestrator(settings, store, agents=Planner(implementation_plan(load_case("GAME-TEST-001"))))
    workspace = Workspace(tmp_path / "workspace")
    workspace.initialize()
    (workspace.root / "module.ts").write_text("export const module = true;")
    (workspace.root / ".env").write_text("UNIT_TEST_PRIVATE_VALUE=not-for-delivery")
    (workspace.root / ".harness").mkdir()
    (workspace.root / ".harness" / "process.log").write_text("private runtime metadata")
    digest = store.write(rid, "trace.json", {"project_sha256": workspace.snapshot()["sha256"], "observed": True})
    reference = {"path": "trace.json", "sha256": digest}
    store.write(rid, "project-validation.json", {"acceptance": {"tests": [{"id": "T01", "evidence": [reference]}]}})
    store.write(rid, "project-evidence-0.json", {"checks": [{"artifacts": [reference]}]})
    store.write(rid, "project-build-0.json", {"passed": True})
    store.write(rid, "specification.txt", "完整原文")
    workflow.package(rid, workspace)
    with zipfile.ZipFile(store.path(rid, "project-delivery.zip")) as archive:
        paths = archive.namelist()
        assert "evidence/trace.json" in paths
        assert "evidence/project-evidence-0.json" in paths
        assert "evidence/project-build-0.json" in paths
        assert "playable.html" in paths and "delivery-manifest.json" in paths
        manifest = json.loads(archive.read("delivery-manifest.json"))
        assert manifest["source_sha256"] == workspace.snapshot()["sha256"]
        assert manifest["playable"]["path"] == "playable.html"
        assert hashlib.sha256(archive.read("playable.html")).hexdigest() == manifest["playable"]["sha256"]
        assert archive.read("playable.html").decode() == workspace.inline_preview()
        assert hashlib.sha256(archive.read("evidence/trace.json")).hexdigest() == digest
        assert not any(".env" in path or ".harness" in path for path in paths)


def test_reviewed_repair_handoff_preserves_unapplied_work_and_never_replays_completed_patches(settings, store):
    rid = store.create(project_request())["id"]
    spec = load_case("GAME-TEST-001")
    workflow = ProjectOrchestrator(settings, store, agents=Planner(implementation_plan(spec)))
    proposal = {"patches": [{"path": "src/game/types.ts", "old": "before", "new": "after"}],
                "remaining_findings": ["Integrate real production renderer"],
                "integration_requirements": ["Pass actual event sink"]}
    digest = store.write(rid, "proposed-repairs.json", proposal)
    assert workflow.proposed_repairs(rid) == proposal
    journal_name = "reviewed-repair-" + digest[:20] + ".journal.json"
    store.write(rid, journal_name, {"status": "pending", "proposal_sha256": digest})
    assert workflow.proposed_repairs(rid) == proposal
    store.write(rid, journal_name, {"status": "complete", "proposal_sha256": "0" * 64})
    assert workflow.proposed_repairs(rid) == proposal
    store.write(rid, journal_name, {"status": "complete", "proposal_sha256": digest, "source_after": "a" * 64})
    handoff = workflow.proposed_repairs(rid)
    assert handoff["status"] == "already_applied" and handoff["do_not_reapply"] is True
    assert handoff["official_acceptance"] is False and handoff["proposal_sha256"] == digest
    assert "patches" not in handoff and handoff["remaining_findings"] == proposal["remaining_findings"]
    assert handoff["integration_requirements"] == proposal["integration_requirements"]
    assert store.get(rid)["calls"] == 0 and store.get(rid)["status"] == "queued"


@pytest.mark.parametrize("source_defect", [None, "stale_browser_source", "stale_ledger_source", "stale_spec"])
@pytest.mark.parametrize("visual_verdict", ["pass", "fail"])
async def test_visual_stage_follows_bound_browser_artifacts_and_is_re_evaluated(
        settings, store, tmp_path, monkeypatch, source_defect, visual_verdict):
    """Control fixtures never become official case execution evidence."""
    spec = load_case("GAME-TEST-001")
    rid = store.create(project_request())["id"]
    workspace = Workspace(tmp_path / "workspace")
    observed_order, qa_calls = [], []

    class VisionPlanner(Planner):
        settings = SimpleNamespace(role_model=lambda role: "qwen3.8-max")

        async def structured(self, run_id, stage, role, instruction, payload, schema, **kwargs):
            if schema is ImplementationPlan:
                return await super().structured(run_id, stage, role, instruction, payload, schema, **kwargs)
            assert schema.__name__ == "VisualReview" and role == "qa"
            assert observed_order == ["browser_artifacts_committed"]
            evidence = store.read_json(rid, "project-evidence-0.json")
            assert evidence["project_sha256"] == workspace.snapshot()["sha256"] == payload["project_sha256"]
            assert evidence["checks"] and all(c["producer"] == "playwright" for c in evidence["checks"])
            assert {shot["scene"] for shot in payload["screenshots"]} >= {"upgrade_cards", "unspecified"}
            assert len(kwargs["images"]) == 2
            observed_order.append("independent_vision")
            qa_calls.append(kwargs)
            image = payload["screenshots"][0]["path"]
            facts = [{"screenshot": image, "element": "顶部中文标签", "bbox": [0.1, 0.1, 0.2, 0.1],
                      "observed_text": "得分：128", "appearance": "白色数字标签位于深绿色背景上方，标签边缘完整。"},
                     {"screenshot": image, "element": "右侧状态标签", "bbox": [0.6, 0.2, 0.2, 0.1],
                      "observed_text": "生命：3", "appearance": "右侧红色图形与其中文标签之间有明显间距。"}]
            return schema.model_validate({"project_sha256": payload["project_sha256"], "spec_sha256": payload["spec_sha256"],
                "reviews": [{"requirement_id": c["id"],
                    "verdict": visual_verdict if c["test_id"] == "T54" else "insufficient",
                    "facts": facts if c["test_id"] == "T54" else [],
                    "rationale": "根据具体截图元素做出的独立单元测试审查结论，无法推断其他视口。",
                    "limitations": ["模型被测试替身控制，不构成真实游戏质量证据。"]} for c in payload["criteria"]]})

    workflow = ProjectOrchestrator(replace(settings, project_repair_rounds=0), store,
                                   agents=VisionPlanner(implementation_plan(spec)))
    monkeypatch.setattr(workflow, "workspace", lambda _: workspace)

    def browser_report(spec, workspace, build):
        source_hash = build["source_sha256"]
        trace_name = "browser-render.json"
        trace_sha = store.write(rid, trace_name, {"project_sha256": source_hash, "draws": 120})
        fixture_png = Path(__file__).parent / "fixtures" / "vision-probe.png"
        screen_sha = store.write(rid, "project-r0-desktop.png", fixture_png.read_bytes())
        card_sha = store.write(rid, "project-r0-upgrade-cards.png", fixture_png.read_bytes())
        evidence = {"project_sha256": "b" * 64 if source_defect == "stale_ledger_source" else source_hash,
            "spec_sha256": "b" * 64 if source_defect == "stale_spec" else spec.sha256,
            "artifact_root": str(store.directory(rid)), "checks": [{"requirement_id": spec.case_id + "/T54",
                "project_sha256": source_hash, "spec_sha256": spec.sha256, "executed": True,
                "producer": "playwright", "kind": "browser", "artifacts": [{"path": trace_name, "sha256": trace_sha}],
                "assertions": [{"artifact": trace_name, "pointer": "/draws", "op": "gt", "expected": 0}]}]}
        store.write(rid, "project-evidence-0.json", evidence)
        observed_order.append("browser_artifacts_committed")
        return {"passed": False, "source_sha256": "b" * 64 if source_defect == "stale_browser_source" else source_hash,
            "spec_sha256": spec.sha256, "errors": [], "acceptance": {"complete": False, "tests": []},
            "captured_screenshots": [{"path": "project-r0-upgrade-cards.png", "sha256": card_sha,
                "width": 720, "height": 480, "name": "desktop", "scene": "upgrade_cards",
                "project_sha256": source_hash, "spec_sha256": spec.sha256}],
            "browser": [{"name": "desktop", "width": 720, "height": 480,
                         "screenshot": "project-r0-desktop.png", "screenshot_sha256": screen_sha}]}

    install_fake_workers(monkeypatch, report=browser_report)
    await workflow.run(rid)
    report = store.read_json(rid, "project-validation.json")
    if source_defect is None:
        assert observed_order == ["browser_artifacts_committed", "independent_vision"]
        assert len(qa_calls) == 1 and qa_calls[0]["max_tokens"] == 4000
        checks = store.read_json(rid, "project-evidence-0.json")["checks"]
        assert [c["producer"] for c in checks] == ["playwright", "vision_reviewer"]
        expected = "passed" if visual_verdict == "pass" else "failed"
        assert next(t for t in report["acceptance"]["tests"] if t["test_id"] == "T54")["status"] == expected
    else:
        assert not qa_calls and observed_order == ["browser_artifacts_committed"]
    assert store.get(rid)["status"] == "needs_attention"
    assert not store.path(rid, "manifest.json").exists()


async def test_only_missing_executors_and_manual_observations_do_not_spend_repair_calls(
        settings, store, tmp_path, monkeypatch):
    spec = load_case("GAME-TEST-003")
    rid = store.create(project_request(spec.case_id))["id"]
    workflow = ProjectOrchestrator(replace(settings, project_repair_rounds=8), store,
                                   agents=Planner(implementation_plan(spec)))
    workspace = Workspace(tmp_path / "workspace")
    monkeypatch.setattr(workflow, "workspace", lambda _: workspace)
    validation_count = 0

    def incomplete_report(spec, workspace, build):
        nonlocal validation_count
        validation_count += 1
        evidence = {"project_sha256": build["source_sha256"], "spec_sha256": spec.sha256,
                    "artifact_root": str(store.directory(rid)), "checks": []}
        store.write(rid, "project-evidence-0.json", evidence)
        return {"passed": False, "source_sha256": build["source_sha256"], "spec_sha256": spec.sha256,
            "errors": [], "browser": [], "actionable_failures": [], "acceptance": evaluate_evidence(spec, evidence),
            "missing_executors": ["T01 normal-play scenario has not executed"],
            "manual_required": ["T69 needs a named physical mobile device observation"]}

    calls = install_fake_workers(monkeypatch, report=incomplete_report)
    await workflow.run(rid)
    assert validation_count == 1
    assert len(calls) == 5 and all(call[3]["role"] == "build" for call in calls)
    assert not store.path(rid, "project-repair-1.json").exists()
    assert any(event["kind"] == "verification_pending" for event in store.events(rid))
    final = store.read_json(rid, "project-validation.json")["acceptance"]
    assert final["counts"]["failed"] == 0 and final["counts"]["manual_required"] > 0


async def test_verification_rounds_append_after_interruption_and_repairs_remain_cumulative(settings, store, tmp_path, monkeypatch):
    """Interrupted fixture verification must preserve evidence, never imply case success."""
    spec = load_case("GAME-TEST-001")
    rid = store.create(project_request(spec.case_id))["id"]
    store.claim()
    store.reserve_call(rid, 600, 6_000_000)
    store.charge(rid, 123)
    workflow = ProjectOrchestrator(replace(settings, project_repair_rounds=2), store,
                                   agents=Planner(implementation_plan(spec)))
    workspace = Workspace(tmp_path / "workspace")
    monkeypatch.setattr(workflow, "workspace", lambda _: workspace)
    calls = install_fake_workers(monkeypatch)
    rounds, interrupted = [], False

    class Validator:
        def __init__(self, settings, store):
            pass

        async def validate(self, rid, spec, workspace, build, round_no):
            nonlocal interrupted
            rounds.append(round_no)
            trace = {"fixture_only": True, "round": round_no, "project_sha256": build["source_sha256"]}
            store.write(rid, f"round-{round_no}-trace.json", trace)
            store.write(rid, f"project-evidence-{round_no}.json", {
                "project_sha256": build["source_sha256"], "spec_sha256": spec.sha256,
                "artifact_root": str(store.directory(rid)), "checks": [], "round": round_no})
            if round_no == 1 and not interrupted:
                interrupted = True
                raise asyncio.CancelledError("fixture worker restart after its evidence was persisted")
            return {"passed": False, "source_sha256": build["source_sha256"], "spec_sha256": spec.sha256,
                "errors": ["fixture integration defect still unresolved"], "browser": []}

    sys.modules["game_harness.project_validation"].ProjectValidator = Validator
    with pytest.raises(asyncio.CancelledError):
        await workflow.run(rid)
    names = ["project-build-0.json", "project-build-1.json", "project-evidence-0.json",
             "project-evidence-1.json", "project-repair-1.json"]
    preserved = {name: store.path(rid, name).read_bytes() for name in names}
    await workflow.run(rid)
    assert rounds == [0, 1, 2, 3]
    assert [feature["id"] for _, feature, _, _ in calls if feature["id"].startswith("integration-repair-")] == [
        "integration-repair-1", "integration-repair-2"]
    assert all(store.path(rid, name).read_bytes() == content for name, content in preserved.items())
    assert store.path(rid, "project-build-3.json").is_file()
    await workflow.run(rid)
    assert rounds == [0, 1, 2, 3, 4]
    assert not store.path(rid, "project-repair-3.json").exists()
    assert store.get(rid)["calls"] == 1 and store.get(rid)["tokens"] == 123
    assert store.get(rid)["status"] == "needs_attention"
    assert not store.path(rid, "manifest.json").exists()


@pytest.mark.parametrize("status", ["failed", "needs_attention", "cancelled"])
def test_project_resume_api_keeps_attempts_budget_and_completed_artifacts(settings, status):
    # No lifespan workers or model calls: only the explicit resume control path.
    app = create_app(settings)
    store = app.state.store
    rid = store.create(project_request())["id"]
    ledger = ProjectLedger(store)
    feature = implementation_plan(load_case("GAME-TEST-001")).features[0].model_dump()
    ledger.register(rid, [feature])
    ledger.claim(rid)
    ledger.finish(rid, feature["id"], {"status": "needs_work", "stop_reason": "non_progress"})
    store.reserve_call(rid, 600, 6_000_000)
    store.charge(rid, 123)
    store.write(rid, "project-evidence-7.json", {"fixture_only": True, "round": 7})
    before = store.path(rid, "project-evidence-7.json").read_bytes()
    store.update(rid, status=status, error="fixture unresolved work")
    app.state.projects = SimpleNamespace(ledger=ledger)
    client = TestClient(app)
    response = client.post(f"/api/runs/{rid}/resume")
    assert response.status_code == 200
    assert response.json()["status"] == "queued" and response.json()["error"] is None
    saved = ledger.features(rid)[0]
    assert saved["status"] == "pending" and saved["attempts"] == 1
    assert saved["result"]["stop_reason"] == "non_progress"
    assert store.get(rid)["calls"] == 1 and store.get(rid)["tokens"] == 123
    assert store.path(rid, "project-evidence-7.json").read_bytes() == before
    assert any(e["kind"] == "feature_resume_requested" and e["payload"]["budget_reset"] is False for e in store.events(rid))
    assert client.post(f"/api/runs/{rid}/resume").status_code == 409


@pytest.mark.parametrize("stop_reason", ["action_limit", "non_progress"])
@pytest.mark.parametrize("mode", ["productive", "no_progress"])
async def test_continuation_leases_require_host_observed_source_progress(settings, store, tmp_path, monkeypatch, mode, stop_reason):
    rid = store.create(project_request())["id"]
    store.claim()
    settings = replace(settings, project_repair_rounds=0)
    spec = load_case("GAME-TEST-001")
    workflow = ProjectOrchestrator(settings, store, agents=Planner(implementation_plan(spec)))
    workspace = Workspace(tmp_path / "workspace")
    monkeypatch.setattr(workflow, "workspace", lambda _: workspace)
    install_fake_workers(monkeypatch)
    calls, sessions = [], []

    class LeaseCoder:
        def __init__(self, agents, store, workspace, **kwargs):
            self.workspace = workspace

        async def run_feature(self, run_id, feature, context, **kwargs):
            store.reserve_call(run_id, settings.project_max_calls, settings.project_max_tokens)
            store.charge(run_id, 100)
            calls.append((feature["id"], kwargs["role"]))
            if feature["id"].startswith("dependency-repair-"):
                return {"status": "implemented", "stop_reason": "implemented"}
            if feature["id"].startswith("feature-0"):
                sessions.append(context)
                step = len(sessions)
                if mode != "no_progress":
                    path = self.workspace.root / f"foundation-part-{step}.ts"
                    path.write_text(f"export const completedPart = {step};")
                if mode == "productive" and step == 4:
                    return {"status": "implemented", "stop_reason": "implemented"}
                return {"status": "needs_work", "stop_reason": stop_reason,
                    "memory": f"下一文件 foundation-part-{step + 1}.ts；已确认speed=6。", "read_receipts": [],
                    "changed_files": ["untrusted-model-change-claim.ts"],
                    "host_progress": {"source_changed": True}}  # Host must overwrite this claim.
            (self.workspace.root / (feature["id"] + ".ts")).write_text("export const finished = true;")
            return {"status": "implemented", "stop_reason": "implemented"}

    monkeypatch.setattr("game_harness.coding_agent.CodingAgent", LeaseCoder)
    if mode == "productive":
        await workflow.run(rid)
        assert len(sessions) == 4
        assert not any(role == "repair" for _, role in calls)
        assert sessions[1]["continuation"]["memory"].startswith("下一文件 foundation-part-2.ts")
        assert all(f["status"] == "implemented" for f in workflow.ledger.features(rid))
        assert store.get(rid)["calls"] == 8 and store.get(rid)["tokens"] == 800
        events = [e for e in store.events(rid) if e["kind"] == "feature_continuation"]
        assert len(events) == 3 and all(e["payload"]["budget_reset"] is False for e in events)
    else:
        with pytest.raises(RuntimeError, match="功能尚未完成"):
            await workflow.run(rid)
        assert len(sessions) == 3
        assert sum(role == "repair" for _, role in calls) == 2
        assert store.get(rid)["calls"] == 5 and store.get(rid)["tokens"] == 500
        assert not any(e["kind"] == "feature_continuation" for e in store.events(rid))
        progress = workflow.ledger.features(rid)[0]["result"]["host_progress"]
        assert progress["source_changed"] is (mode != "no_progress")
