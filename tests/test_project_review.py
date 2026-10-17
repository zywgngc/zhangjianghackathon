"""Visual-review control and evidence contracts; model output is mocked here.

These fixtures do not establish visual quality or any official game pass.
"""
import hashlib
import json
import struct
from types import SimpleNamespace
import zlib

import pytest

from game_harness.acceptance import evaluate_evidence
from game_harness.project_review import review_visual, visual_criteria
from game_harness.specifications import load_case


PROJECT_HASH = "a" * 64


def png(width, height):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    pixels = (b"\0" + b"\x22\x55\x33" * width) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def screenshots(store, rid, scenes=None):
    result = []
    for name, width, height in [("desktop", 1280, 800), ("portrait", 390, 844), ("landscape", 844, 390)]:
        path = "project-r0-" + name + ".png"
        digest = store.write(rid, path, png(width, height))
        result.append({"path": path, "sha256": digest, "width": width, "height": height, "name": name,
                       "scene": (scenes or {}).get(name, "gameplay")})
    return result


class Reviewer:
    def __init__(self, mode="pass"):
        self.mode, self.calls = mode, []
        self.settings = SimpleNamespace(role_model=lambda role: "qwen3.8-max")

    async def structured(self, rid, stage, role, instruction, payload, schema, **kwargs):
        self.calls.append({"role": role, "instruction": instruction, "payload": payload, **kwargs})
        decisions = []
        for criterion in payload["criteria"]:
            shots = payload["screenshots"]
            needed = criterion["required_viewports"]
            eligible = [s for s in shots if not needed or s["name"] in needed]
            if criterion["category"] in {"upgrade_descriptions", "evolution_feedback"}:
                tag = "upgrade_cards" if criterion["category"] == "upgrade_descriptions" else "ultimate_evolution"
                eligible = [s for s in shots if s["scene"] == tag] or eligible
            facts = []
            for shot in eligible:
                for index in range(2):
                    facts.append({"screenshot": shot["path"], "element": "敌人战力标签" if index else "顶部中文标题",
                        "bbox": [0.1 + index * 0.4, 0.1 + index * 0.3, 0.2, 0.1],
                        "observed_text": "战力：128" if index else "开始游戏",
                        "appearance": "白色中文文字位于深绿色矩形上方，边缘没有裁切。"})
                if criterion["category"] != "power_readability" and criterion["category"] != "power_color_change":
                    break
            if self.mode == "generic":
                for fact in facts:
                    fact.update(element="界面", appearance="整体布局合理，清晰美观，符合要求。")
            elif self.mode == "bad_location":
                facts[0]["bbox"] = [0.9, 0.9, 0.9, 0.9]
            elif self.mode == "foreign_image":
                facts[0]["screenshot"] = "another-run.png"
            elif self.mode == "single_fact":
                facts = facts[:1]
            decisions.append({"requirement_id": criterion["id"],
                "verdict": self.mode if self.mode in {"fail", "insufficient"} else "pass", "facts": facts,
                "rationale": "已定位真实截图中的文字与角色区域，当前结论只涉及可见画面。",
                "limitations": ["仅审查提供的静态截图，不证明行为正确、动画时序或真机帧率。"]})
        return schema.model_validate({"project_sha256": "b" * 64 if self.mode == "wrong_source" else payload["project_sha256"],
            "spec_sha256": payload["spec_sha256"], "reviews": decisions})


def make_run(store):
    return store.create({"description": "visual evidence unit", "engine": "babylon"})["id"]


async def test_independent_review_records_exact_prompt_images_and_typed_visual_evidence(store):
    rid = make_run(store)
    spec = load_case("GAME-TEST-001")
    shots = screenshots(store, rid)
    reviewer = Reviewer()
    checks = await review_visual(rid, spec, store, reviewer, PROJECT_HASH, shots)
    assert {c["requirement_id"] for c in checks} == {spec.case_id + "/" + test for test in ["T37", "T38", "T54"]}
    assert len(reviewer.calls) == 1
    call = reviewer.calls[0]
    assert call["role"] == "qa" and call["max_tokens"] == 4000
    assert [p.name for p in call["images"]] == [s["path"] for s in shots]
    assert not {"html", "code", "coder_claim", "build_notes"} & set(call["payload"])
    for check in checks:
        for artifact in check["artifacts"]:
            assert hashlib.sha256(store.path(rid, artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]
        review = store.read_json(rid, check["review_artifact"])
        source = store.read_json(rid, review["prompt_artifact"])
        identity_sha = hashlib.sha256(json.dumps(source["prompt_identity"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert identity_sha == check["prompt_sha256"] == review["prompt_sha256"]
        assert review["model"] == "qwen3.8-max" and review["reviewer"] == "vision_reviewer"
        assert "human" in review["scope"] and check["producer"] != "human"
    report = evaluate_evidence(spec, {"spec_sha256": spec.sha256, "project_sha256": PROJECT_HASH,
        "artifact_root": str(store.directory(rid)), "checks": checks})
    by_id = {t["test_id"]: t for t in report["tests"]}
    assert by_id["T54"]["status"] == "passed"
    assert by_id["T37"]["status"] != "passed"  # Real browser geometry/input is still required.
    assert report["complete"] is False


@pytest.mark.parametrize("defect", ["no_images", "missing_hash", "tampered_image", "wrong_dimensions", "wrong_binding", "invalid_source_hash"])
async def test_unbound_or_unavailable_images_do_not_call_the_model(store, defect):
    rid = make_run(store)
    spec = load_case("GAME-TEST-001")
    shots = screenshots(store, rid)
    project_hash = PROJECT_HASH
    if defect == "no_images":
        shots = []
    elif defect == "missing_hash":
        shots[0].pop("sha256")
    elif defect == "tampered_image":
        store.path(rid, shots[0]["path"]).write_bytes(png(1024, 768))
    elif defect == "wrong_dimensions":
        shots[0]["width"] += 1
    elif defect == "wrong_binding":
        shots[0]["project_sha256"] = "b" * 64
    else:
        project_hash = "unbound"
    reviewer = Reviewer()
    assert await review_visual(rid, spec, store, reviewer, project_hash, shots) == []
    assert not reviewer.calls


@pytest.mark.parametrize("mode", ["generic", "bad_location", "foreign_image", "single_fact", "wrong_source", "insufficient"])
async def test_generic_claims_bad_locations_and_unobserved_criteria_cannot_pass(store, mode):
    rid = make_run(store)
    spec = load_case("GAME-TEST-001")
    checks = await review_visual(rid, spec, store, Reviewer(mode), PROJECT_HASH, screenshots(store, rid))
    assert checks == []
    artifacts = store.get(rid)["artifacts"]
    assert any(name.endswith(".model.json") for name in artifacts)
    reviews = [store.read_json(rid, name) for name in artifacts if name.endswith(".review.json")]
    assert reviews and all(not d["valid"] for review in reviews for d in review["reviews"])


async def test_concrete_visual_failure_is_kept_as_failed_evidence(store):
    rid = make_run(store)
    spec = load_case("GAME-TEST-001")
    checks = await review_visual(rid, spec, store, Reviewer("fail"), PROJECT_HASH, screenshots(store, rid))
    assert len(checks) == 3
    report = evaluate_evidence(spec, {"spec_sha256": spec.sha256, "project_sha256": PROJECT_HASH,
        "artifact_root": str(store.directory(rid)), "checks": checks})
    assert next(t for t in report["tests"] if t["test_id"] == "T54")["status"] == "failed"


async def test_required_upgrade_scenes_are_not_inferred_from_paused_gameplay(store):
    rid = make_run(store)
    spec = load_case("GAME-TEST-002")
    checks = await review_visual(rid, spec, store, Reviewer(), PROJECT_HASH, screenshots(store, rid))
    ids = {c["requirement_id"] for c in checks}
    assert spec.case_id + "/T63" not in ids and spec.case_id + "/T64" not in ids
    tagged = screenshots(store, rid, {"desktop": "upgrade_cards", "portrait": "ultimate_evolution"})
    checks = await review_visual(rid, spec, store, Reviewer(), PROJECT_HASH, tagged)
    assert {spec.case_id + "/T63", spec.case_id + "/T64"} <= {c["requirement_id"] for c in checks}


async def test_power_color_change_needs_comparable_scenes_and_never_certifies_device_fps(store):
    rid = make_run(store)
    spec = load_case("GAME-TEST-003")
    allowed = {c["test_id"]: c for c in visual_criteria(spec)}
    assert "T69" not in allowed
    assert allowed["T80"]["requires_mechanical"] is True
    checks = await review_visual(rid, spec, store, Reviewer(), PROJECT_HASH, screenshots(store, rid))
    assert spec.case_id + "/T25" not in {c["requirement_id"] for c in checks}
    tagged = screenshots(store, rid, {"desktop": "power_change_before", "portrait": "power_change_after"})
    checks = await review_visual(rid, spec, store, Reviewer(), PROJECT_HASH, tagged)
    assert spec.case_id + "/T25" in {c["requirement_id"] for c in checks}
    report = evaluate_evidence(spec, {"spec_sha256": spec.sha256, "project_sha256": PROJECT_HASH,
        "artifact_root": str(store.directory(rid)), "checks": checks})
    assert next(t for t in report["tests"] if t["test_id"] == "T69")["status"] == "manual_required"
