"""Independent screenshot review with typed, source-bound visual evidence.

This reviewer receives authoritative visual criteria and host screenshots. It
never receives coding-agent claims and cannot certify physics, numerical rules,
user actions, animation timing, real-device frame rate, or human observations.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Literal

from .agents import COMMON


# Exact IDs are intentionally reviewed, not inferred from words like "visual".
# requires_mechanical is enforced again by acceptance.py, never by this model.
VISUAL_CRITERIA = {
    "GAME-TEST-001": {"T37": ("portrait_layout", True), "T38": ("landscape_layout", True),
                      "T54": ("warfare_theme", False)},
    "GAME-TEST-002": {"T63": ("upgrade_descriptions", True), "T64": ("evolution_feedback", True),
                      "T69": ("portrait_layout", True), "T70": ("landscape_layout", True),
                      "T87": ("wuxia_theme", False)},
    "GAME-TEST-003": {"T02": ("player_power_label", True), "T03": ("enemy_power_labels", True),
                      "T25": ("power_color_change", True), "T63": ("portrait_layout", True),
                      "T64": ("landscape_layout", True), "T70": ("power_readability", False),
                      "T74": ("overhead_power_labels", False), "T80": ("open_field", True),
                      "T81": ("no_tower_floors", True)},
}

SCOPES = {
    "upgrade_descriptions": "必须看见实际升级卡片及其中文内容；不能从游戏HUD推断卡片正确，数值语义另需机械校验。",
    "evolution_feedback": "必须看见实际终极进化卡片/反馈；描述具体金色或其他差异，不能从普通HUD推断。",
    "power_color_change": "必须提供同一敌人在战力变化前后的可比较画面与数值/颜色事实；动态正确性仍需机械轨迹。",
    "power_readability": "逐个已提供视口检查可见战力数字的字号、遮挡、对比度与对应角色；看不到数字须判insufficient。",
    "overhead_power_labels": "确认战力标签位于可见角色头顶并描述对应元素位置，不证明隐藏/未进入画面的角色。",
    "open_field": "仅判断当前画面的开阔场地构图；实际自由地面移动仍需浏览器/运行时证据。",
    "no_tower_floors": "仅判断画面不采用固定塔楼楼层布局；实际非塔层移动仍需独立机械证据。",
}


def visual_criteria(spec):
    data = spec.to_dict() if hasattr(spec, "to_dict") else spec
    allowed = VISUAL_CRITERIA.get(data["case_id"], {})
    result = []
    for test in data["acceptance_tests"]:
        if test["test_id"] not in allowed:
            continue
        category, mechanical = allowed[test["test_id"]]
        views = ["portrait"] if category == "portrait_layout" else ["landscape"] if category == "landscape_layout" else []
        result.append({"id": test["id"], "test_id": test["test_id"], "text": test["text"],
                       "source": test["source"], "category": category, "requires_mechanical": mechanical,
                       "required_viewports": views,
                       "scope": SCOPES.get(category, "仅根据真实截图判断主题或可见布局，不证明动作、规则或真机性能。")})
    return result


class VisualFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screenshot: str = Field(min_length=1, max_length=200)
    element: str = Field(min_length=2, max_length=120)
    # Normalized left, top, width, height, so the same schema supports all views.
    bbox: list[Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]] = Field(
        min_length=4, max_length=4,
        description="Normalized [left/image_width, top/image_height, box_width/image_width, box_height/image_height]. Every value is 0..1. Never pixels or [left,top,right,bottom].")
    observed_text: str = Field(default="", max_length=250)
    appearance: str = Field(min_length=8, max_length=600)


class VisualDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    verdict: Literal["pass", "fail", "insufficient"]
    facts: list[VisualFact] = Field(max_length=12)
    rationale: str = Field(min_length=12, max_length=1_500)
    limitations: list[str] = Field(min_length=1, max_length=6)


class VisualReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviews: list[VisualDecision] = Field(min_length=1, max_length=3)


VISUAL_INSTRUCTION = """你是独立视觉验收代理，只看主机真实截图和原始规格，不采纳编码代理自述。
逐条判断提供的视觉准则，输出严格JSON。不能新增准则、替数值/物理/动作测试下结论，不能声称真机帧率或真人已复验。
每条必须pass/fail/insufficient。画面未包含所需界面/元素/前后状态时用insufficient，不得凭经验补全。
pass/fail至少提供两个具体可观测事实：截图path、元素、归一化bbox[x,y,width,height]、实际文字及具体颜色/形状/遮挡关系。
bbox四个数必须在0到1之间，x+width和y+height不得超过1，禁止像素坐标或右下角坐标。
例如1280×800截图中左上角(640,320)、宽128高80的区域必须写[0.5,0.4,0.1,0.1]，不得写[640,320,768,400]。
通常每项只写2个事实；跨三视口可读性最多4个事实。rationale不超过100汉字，limitations写1至2条，避免重复描述。
不能只说“布局合理/符合要求/清晰美观/已通过”。引用可见中文或具体外形，并精确指出位于哪张图的哪个区域。
portrait_layout必须检查竖屏图，landscape_layout必须检查手机横屏图；不要用桌面横屏替代手机横屏。
战力可读性须逐个提供视口描述真实数字及其角色关联；颜色动态变化须有可比较前后图。
power_readability必须为required_viewports中的每一种视口分别提供至少一个事实；桌面不同场景不能代替手机横屏或竖屏。先覆盖全部必需视口，再补其他事实。
升级卡描述/终极进化反馈必须看见真实相应卡片。截图没有出现时必须insufficient。
主题判断结合给定原文的主题/角色/地形/配色要求。参考图片若未附，不得声称已经对比参考图。
每项limitations写明截图范围与不由本次视觉证明的行为。source hash必须原样返回。
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(raw):
    return hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode()).hexdigest()


def _screenshots(rid, store, supplied, project_sha256, spec_sha256):
    root = store.directory(rid).resolve()
    seen, result = set(), []
    for item in supplied:
        if not isinstance(item, dict):
            raise ValueError("screenshots require host descriptors with path and sha256")
        relative = item.get("path", item.get("screenshot"))
        claimed = item.get("sha256", item.get("screenshot_sha256"))
        if not relative or not isinstance(relative, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+\.png", relative):
            raise ValueError("screenshot must be a run-local PNG artifact")
        candidate = root / relative
        if candidate.is_symlink() or candidate.resolve().parent != root:
            raise ValueError("screenshot path escapes this run")
        raw = candidate.read_bytes()
        if not claimed or _hash(raw) != claimed:
            raise ValueError("screenshot SHA is missing or stale")
        if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 24:
            raise ValueError("screenshot is not a PNG")
        width, height = struct.unpack(">II", raw[16:24])
        if width < 100 or height < 100 or width > 10_000 or height > 10_000:
            raise ValueError("invalid screenshot dimensions")
        if item.get("width", width) != width or item.get("height", height) != height:
            raise ValueError("screenshot dimensions differ from host descriptor")
        if item.get("project_sha256", project_sha256) != project_sha256 or item.get("spec_sha256", spec_sha256) != spec_sha256:
            raise ValueError("screenshot source hash differs from this review")
        if relative in seen:
            continue
        seen.add(relative)
        name = item.get("name") or ("portrait" if width < height else "landscape" if width <= 900 else "desktop")
        if name == "portrait" and width >= height or name == "landscape" and (width <= height or width > 900):
            raise ValueError("screenshot viewport name is inconsistent with image geometry")
        result.append({"path": relative, "sha256": claimed, "name": name, "width": width, "height": height,
                       "scene": item.get("scene", "unspecified"), "project_sha256": project_sha256,
                       "spec_sha256": spec_sha256})
    return result


def _decision_errors(decision, criterion, shots):
    errors = []
    if decision.verdict == "insufficient":
        return ["reviewer could not observe the required scene"]
    if len(decision.facts) < 2:
        errors.append("visual decision requires at least two specific observations")
    lookup = {shot["path"]: shot for shot in shots}
    observed_views, identities = set(), set()
    generic = re.compile(r"^(?:整体|界面|画面|游戏|布局|视觉|效果|文本|全部|所有|要求|均|很|非常|良好|清晰|可读|美观|合理|符合|满足|通过|正常|无问题|未发现问题|[，。；、\s])+$")
    for fact in decision.facts:
        shot = lookup.get(fact.screenshot)
        if not shot:
            errors.append("observation references an unreviewed screenshot")
            continue
        observed_views.add(shot["name"])
        x, y, width, height = fact.bbox
        if not all(0 <= v <= 1 for v in fact.bbox) or width <= 0 or height <= 0 or x + width > 1.001 or y + height > 1.001:
            errors.append("observation location is outside the screenshot")
        identities.add((fact.screenshot, fact.element, tuple(fact.bbox)))
        if generic.fullmatch(fact.appearance.strip()) or generic.fullmatch(fact.element.strip()):
            errors.append("observation contains only a generic pass claim")
    if len(identities) < 2:
        errors.append("observations do not identify two distinct elements/locations")
    if set(criterion["required_viewports"]) - observed_views:
        errors.append("required viewport has no visual observation")
    if criterion["category"] == "power_readability" and {s["name"] for s in shots} - observed_views:
        errors.append("power readability has not been assessed in every supplied viewport")
    # A changing state cannot be established from the same paused scene at
    # different screen sizes. Require separately captured before/after scenes.
    if criterion["category"] == "power_color_change":
        scenes = {lookup[f.screenshot]["scene"] for f in decision.facts if f.screenshot in lookup}
        if not {"power_change_before", "power_change_after"} <= scenes:
            errors.append("power color change needs real before/after scene captures")
    for category, tag in (("upgrade_descriptions", "upgrade_cards"), ("evolution_feedback", "ultimate_evolution")):
        if criterion["category"] == category and not any(lookup[f.screenshot]["scene"] == tag for f in decision.facts if f.screenshot in lookup):
            errors.append("the relevant upgrade/evolution scene was not captured")
    return errors


async def review_visual(rid, spec, store, agents, project_sha256, screenshots):
    """Return artifact-backed checks; missing/insufficient visuals stay unexecuted.

    Screenshots: [{path, sha256, width, height, name, scene?}, ...]. Supported
    scene tags for specialized criteria: upgrade_cards, ultimate_evolution,
    power_change_before, power_change_after. Tags originate in the host browser.
    """
    data = spec.to_dict() if hasattr(spec, "to_dict") else spec
    criteria = visual_criteria(data)
    if not criteria:
        return []
    prefix = "project-visual-" + str(project_sha256)[:12] + "-" + uuid.uuid4().hex[:8]
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", project_sha256 or "") or not re.fullmatch(r"[0-9a-f]{64}", data.get("sha256", "")):
            raise ValueError("visual review requires project and specification SHA256 bindings")
        shots = _screenshots(rid, store, screenshots, project_sha256, data["sha256"])
        if not shots:
            raise ValueError("no verified host screenshots are available")
        settings = getattr(agents, "settings", None)
        model = settings.role_model("qa") if settings and hasattr(settings, "role_model") else getattr(settings, "model", None)
        if not model:
            raise ValueError("visual reviewer model provenance is unavailable")
    except (ValueError, OSError, TypeError) as exc:
        store.write(rid, prefix + ".rejected.json", {"producer": "vision_reviewer", "executed": False,
            "project_sha256": project_sha256, "spec_sha256": data.get("sha256"), "reason": str(exc)})
        store.event(rid, "visual_review", "visual_review_unavailable", {"reason": str(exc)})
        return []
    checks = []
    # Three criteria keep a 4k-token structured response complete and auditable.
    for offset in range(0, len(criteria), 3):
        batch = [{**criterion, "required_viewports": sorted({shot["name"] for shot in shots})}
                 if criterion["category"] == "power_readability" else criterion
                 for criterion in criteria[offset:offset + 3]]
        batch_prefix = prefix + "-" + str(offset // 3 + 1)
        visual_sections = [{"number": section["number"], "text": section["text"], "source": section["source"]}
                           for section in data.get("sections", []) if section["number"] == "12"]
        payload = {"project_sha256": project_sha256, "spec_sha256": data["sha256"], "case_id": data["case_id"],
                   "criteria": batch, "authoritative_visual_direction": visual_sections, "screenshots": shots,
                   "scope": "Independent still-image review; no gameplay, numerical, device performance or human claims"}
        contract = "\n只输出一个 JSON 对象，严格遵循此 JSON Schema：\n" + json.dumps(VisualReview.model_json_schema(), ensure_ascii=False)
        prompt_identity = {"system": COMMON + VISUAL_INSTRUCTION + contract,
                           "user": json.dumps(payload, ensure_ascii=False), "image_sha256": [s["sha256"] for s in shots]}
        prompt_sha = _hash(_json(prompt_identity))
        input_name = batch_prefix + ".input.json"
        input_sha = store.write(rid, input_name, {"prompt_sha256": prompt_sha,
            "prompt_encoding": "canonical-system-user-image-sha256/v1", "instruction": VISUAL_INSTRUCTION,
            "prompt_identity": prompt_identity, "schema": VisualReview.model_json_schema(),
            "payload": payload, "model": model, "role": "qa"})
        store.event(rid, "visual_review", "visual_review_started", {"model": model,
            "requirement_ids": [c["id"] for c in batch], "screenshots": shots, "prompt_sha256": prompt_sha})
        try:
            response = await agents.structured(rid, "visual_review", "qa", VISUAL_INSTRUCTION, payload,
                VisualReview, images=[store.path(rid, shot["path"]) for shot in shots], max_tokens=4000)
        except Exception as exc:
            store.write(rid, batch_prefix + ".error.json", {"error_type": type(exc).__name__,
                "prompt_sha256": prompt_sha, "model": model, "project_sha256": project_sha256})
            raise
        raw_name = batch_prefix + ".model.json"
        raw_sha = store.write(rid, raw_name, response.model_dump())
        expected = {c["id"]: c for c in batch}
        ids = [decision.requirement_id for decision in response.reviews]
        response_errors = []
        if response.project_sha256 != project_sha256 or response.spec_sha256 != data["sha256"]:
            response_errors.append("review returned a different source binding")
        if set(ids) - set(expected) or len(ids) != len(set(ids)):
            response_errors.append("review invented or duplicated a criterion")
        # Re-read every image after the request. A concurrently overwritten PNG
        # invalidates this review rather than silently citing new pixels.
        for shot in shots:
            if _hash(store.path(rid, shot["path"]).read_bytes()) != shot["sha256"]:
                response_errors.append("screenshot changed while the model was reviewing it")
        decisions = []
        for decision in response.reviews:
            criterion = expected.get(decision.requirement_id)
            errors = list(response_errors)
            if criterion:
                errors += _decision_errors(decision, criterion, shots)
            else:
                errors.append("criterion was not requested")
            decisions.append({**decision.model_dump(), "valid": not errors, "errors": errors,
                              "requires_mechanical": criterion["requires_mechanical"] if criterion else True})
        review_name = batch_prefix + ".review.json"
        review = {"version": 1, "producer": "vision_reviewer", "reviewer": "vision_reviewer", "executed": True,
                  "model": model, "role": "qa", "prompt_sha256": prompt_sha, "prompt_artifact": input_name,
                  "project_sha256": project_sha256, "spec_sha256": data["sha256"], "observed_at": time.time(),
                  "screenshots": shots, "reviews": decisions, "missing_criteria": sorted(set(expected) - set(ids)),
                  "scope": "AI visual observation of supplied screenshots; never a human or real-device test"}
        review_sha = store.write(rid, review_name, review)
        for index, decision in enumerate(decisions):
            if not decision["valid"]:
                continue
            criterion = expected[decision["requirement_id"]]
            artifacts = [{"path": input_name, "sha256": input_sha}, {"path": raw_name, "sha256": raw_sha},
                         {"path": review_name, "sha256": review_sha}]
            artifacts += [{"path": shot["path"], "sha256": shot["sha256"]} for shot in shots]
            checks.append({"requirement_id": criterion["id"], "kind": "visual", "producer": "vision_reviewer",
                "executed": True, "project_sha256": project_sha256, "spec_sha256": data["sha256"],
                "requires_mechanical": criterion["requires_mechanical"], "review_artifact": review_name,
                "model": model, "prompt_sha256": prompt_sha, "artifacts": artifacts,
                "assertions": [{"artifact": review_name, "pointer": f"/reviews/{index}/valid", "op": "eq", "expected": True},
                               {"artifact": review_name, "pointer": f"/reviews/{index}/verdict", "op": "eq", "expected": "pass"}]})
        store.event(rid, "visual_review", "visual_review_done", {"artifact": review_name,
            "valid_decisions": sum(d["valid"] for d in decisions), "criteria": len(batch), "model": model})
    return checks
