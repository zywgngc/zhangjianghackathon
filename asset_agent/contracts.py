"""Validation for the upstream layout + prompt contract, independent of an SDK."""

import copy
import json
import math
import re

from .transforms import normalize_resize


ASSET_TYPES = {"icon", "sprite", "tileset", "model", "audio", "font"}
FORMATS = {"svg", "png", "webp", "jpg", "jpeg", "glb", "gltf", "wav", "ogg", "mp3", "woff", "woff2", "ttf", "otf"}
DEFAULT_LICENSES = ["CC0-1.0", "CC-BY-3.0"]


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty string" % field)
    return value.strip()


def _number(value, field, minimum=0, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s must be a finite number" % field)
    if value < minimum or (positive and value <= minimum):
        raise ValueError("%s is out of range" % field)
    return value


def _string_list(value, field, nonempty=False):
    if not isinstance(value, list):
        raise ValueError("%s must be a list of strings" % field)
    result = list(dict.fromkeys(_text(item, field) for item in value))
    if nonempty and not result:
        raise ValueError("%s must not be empty" % field)
    return result


def validate_request(value):
    """Return a normalized deep copy. Layout coordinates are design CSS pixels.

    The sub-agent preserves positions; it does not redesign the upstream layout.
    All slots, even optional ones, must have a stable slot_id and asset type.
    """
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("request must contain only JSON-compatible values and finite numbers") from exc
    result = copy.deepcopy(value)
    if result.get("schema_version", "1.0") != "1.0":
        raise ValueError("unsupported request schema_version")
    result["schema_version"] = "1.0"
    request_id = _text(result.get("request_id"), "request_id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", request_id):
        raise ValueError("request_id must be a simple identifier (letters, digits, _, -, .)")
    result["request_id"] = request_id
    result["prompt"] = _text(result.get("prompt"), "prompt")
    if "style" in result:
        result["style"] = _text(result["style"], "style")
    layout = result.get("layout")
    if not isinstance(layout, dict):
        raise ValueError("layout must be an object")
    viewport = layout.get("viewport")
    if not isinstance(viewport, dict):
        raise ValueError("layout.viewport must contain width and height")
    for key in ("width", "height"):
        _number(viewport.get(key), "layout.viewport." + key, positive=True)
    slots = layout.get("slots")
    if not isinstance(slots, list) or not slots or len(slots) > 200:
        raise ValueError("layout.slots must contain between 1 and 200 slots")
    seen = set()
    for slot in slots:
        if not isinstance(slot, dict):
            raise ValueError("every slot must be an object")
        slot_id = _text(slot.get("slot_id"), "slot_id")
        if slot_id in seen:
            raise ValueError("duplicate slot_id: " + slot_id)
        seen.add(slot_id)
        slot["slot_id"] = slot_id
        if slot.get("type") not in ASSET_TYPES:
            raise ValueError("slot %s has an unsupported type" % slot_id)
        slot["description"] = _text(slot.get("description"), "slot.description")
        slot["tags"] = _string_list(slot.get("tags", []), "slot.tags")
        slot["required_animations"] = _string_list(slot.get("required_animations", []), "required_animations")
        slot["required"] = slot.get("required", True)
        if not isinstance(slot["required"], bool):
            raise ValueError("slot.required must be a boolean")
        if "style" in slot:
            slot["style"] = _text(slot["style"], "slot.style")
        for name in ("formats", "packs"):
            if name in slot:
                slot[name] = _string_list(slot[name], "slot." + name, nonempty=True)
        if "formats" in slot and not set(slot["formats"]).issubset(FORMATS):
            raise ValueError("slot.formats contains an unsupported format")
        if "bounds" in slot:
            bounds = slot["bounds"]
            if not isinstance(bounds, dict):
                raise ValueError("slot.bounds must be an object")
            for key in ("x", "y", "width", "height"):
                _number(bounds.get(key), "slot.bounds." + key, positive=key in ("width", "height"))
            if bounds["x"] + bounds["width"] > viewport["width"] + 1e-6 or bounds["y"] + bounds["height"] > viewport["height"] + 1e-6:
                raise ValueError("slot %s extends outside the design viewport" % slot_id)
        fit = slot.get("fit", "contain")
        if fit not in {"contain", "cover", "tile"}:
            raise ValueError("slot.fit must be contain, cover, or tile")
        slot["fit"] = fit
        for key in ("anchor", "tint"):
            if key in slot:
                slot[key] = _text(slot[key], "slot." + key)
        if "responsive" in slot and not isinstance(slot["responsive"], dict):
            raise ValueError("slot.responsive must be an object")
        if "resize" in slot:
            if slot["type"] not in {"icon", "sprite"} or slot["required_animations"]:
                raise ValueError("resize is supported only for static icon/sprite slots")
            slot["resize"] = normalize_resize(slot["resize"])
    policy = result.setdefault("missing_policy", {})
    if not isinstance(policy, dict) or set(policy) - {"retry_rounds"}:
        raise ValueError("missing_policy accepts only retry_rounds")
    rounds = policy.get("retry_rounds", 0)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 0 <= rounds <= 2:
        raise ValueError("missing_policy.retry_rounds must be an integer from 0 to 2")
    policy["retry_rounds"] = rounds
    constraints = result.setdefault("constraints", {})
    if not isinstance(constraints, dict):
        raise ValueError("constraints must be an object")
    constraints["licenses"] = _string_list(constraints.get("licenses", DEFAULT_LICENSES), "constraints.licenses", nonempty=True)
    for key, default in (("max_asset_bytes", 8 * 1024 * 1024), ("max_total_bytes", 32 * 1024 * 1024)):
        constraints[key] = _number(constraints.get(key, default), "constraints." + key, positive=True)
        if not isinstance(constraints[key], int):
            raise ValueError("constraints.%s must be an integer" % key)
    if "max_triangles" in constraints:
        _number(constraints["max_triangles"], "constraints.max_triangles", positive=True)
        if not isinstance(constraints["max_triangles"], int):
            raise ValueError("constraints.max_triangles must be an integer")
    top_k = constraints.get("top_k", 5)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 30:
        raise ValueError("constraints.top_k must be an integer from 1 to 30")
    constraints["top_k"] = top_k
    constraints["min_score"] = _number(constraints.get("min_score", 0.01), "constraints.min_score")
    constraints["allow_reuse"] = constraints.get("allow_reuse", True)
    if not isinstance(constraints["allow_reuse"], bool):
        raise ValueError("constraints.allow_reuse must be boolean")
    return result
