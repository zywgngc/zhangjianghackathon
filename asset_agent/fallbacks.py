"""Pure, structured suggestions for unresolved asset slots.

A generation brief is a request for another agent, never a generated asset.
This module performs no I/O, dispatch, placeholder creation, or policy changes.
"""

import copy
import json
import math


_REASONS = frozenset({
    "no_matching_asset", "selector_left_unassigned", "total_byte_budget", "reuse_disabled",
})
_DEFAULT_FORMATS = {
    "icon": ["svg", "png"], "sprite": ["png", "webp"],
    "tileset": ["png", "webp"], "model": ["glb"],
    "audio": ["ogg", "wav"], "font": ["woff2"],
}
_SLOT_CONTEXT_FIELDS = (
    "slot_id", "type", "description", "tags", "style", "formats", "packs",
    "required", "required_animations", "bounds", "resize", "anchor", "fit",
    "responsive", "tint",
)


def _strings(values, name, *, trim=True):
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError("{} must be a list of strings".format(name))
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("{} must contain nonempty strings".format(name))
        value = value.strip() if trim else value
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _dimensions(slot):
    source = slot.get("resize") if slot.get("resize") is not None else slot.get("bounds")
    if source is None:
        return None
    if not isinstance(source, dict):
        raise ValueError("slot.resize or slot.bounds must be an object")
    result = {}
    for key in ("width", "height"):
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Generation dimensions need positive finite width and height")
        result[key] = value
    return result


def _action(kind, slot_id, description, **details):
    result = {"type": kind, "status": "suggested", "slot_id": slot_id, "description": description}
    result.update(details)
    return result


def _brief(slot, reason, request):
    asset_type = slot["type"]
    formats = _strings(slot.get("formats", _DEFAULT_FORMATS[asset_type]), "slot.formats")
    animations = _strings(slot.get("required_animations", []), "slot.required_animations")
    prompt = "Game: {}\nAsset slot {}: {}".format(request.get("prompt", ""), slot["slot_id"], slot["description"])
    if animations:
        prompt += "\nRequired animation clips: {}.".format(", ".join(animations))
    prompt += "\nPreserve the supplied style, layout, formats, animation requirements, and constraints."
    result = {
        "status": "requested",
        "slot_id": slot["slot_id"],
        "asset_type": asset_type,
        "prompt": prompt,
        "style": slot.get("style", request.get("style")),
        "dimensions": _dimensions(slot),
        "formats": formats,
        "required_animations": animations,
        "transparent_background": True if asset_type in {"icon", "sprite"} else None,
        "context": {
            "dispatch_status": "not_dispatched",
            "reason": reason,
            "request_id": request.get("request_id"),
            "required": slot.get("required", True),
            "slot": {key: copy.deepcopy(slot[key]) for key in _SLOT_CONTEXT_FIELDS if key in slot},
            "viewport": copy.deepcopy(request.get("layout", {}).get("viewport")),
            "constraints": copy.deepcopy(request.get("constraints", {})),
        },
    }
    if asset_type == "icon" and "svg" in formats:
        result["method_hint"] = "procedural_svg"
    return result


def build_missing_report(slot, reason, request, *, attempted_queries=None, candidate_ids=None):
    """Return an unresolved-slot report without mutating inputs or executing work.

    The owning planner determines overall ``ready``/``partial``/``unresolved``
    status. Even an optional slot stays unresolved here until a caller decides
    to omit it. Budget and selector failures remain distinct from no matches.
    """
    if not isinstance(slot, dict) or not isinstance(request, dict):
        raise ValueError("slot and request must be objects")
    try:
        json.dumps({"slot": slot, "request": request}, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("slot and request must contain finite JSON-compatible values") from exc
    if reason not in _REASONS:
        raise ValueError("Unsupported missing-asset reason")
    for field in ("slot_id", "description"):
        if not isinstance(slot.get(field), str) or not slot[field].strip():
            raise ValueError("slot.{} must be nonempty text".format(field))
    if slot.get("type") not in _DEFAULT_FORMATS:
        raise ValueError("slot.type must be a supported asset type")
    required = slot.get("required", True)
    if not isinstance(required, bool):
        raise ValueError("slot.required must be a boolean")
    queries = _strings(attempted_queries, "attempted_queries")
    # IDs are opaque catalog identities; never rewrite them while deduplicating.
    candidates = _strings(candidate_ids, "candidate_ids", trim=False)
    slot_id = slot["slot_id"]
    actions = []
    brief = None

    if reason == "total_byte_budget":
        next_action = "Choose a smaller compatible candidate, or request an explicit change to the total byte budget."
        actions.append(_action("choose_smaller_candidate", slot_id,
                               "Try a smaller compatible asset or a lower-size variant; retain the original license, format and animation constraints.",
                               candidate_ids=list(candidates), preserve_constraints=True))
        actions.append(_action("request_budget_change", slot_id,
                               "Ask the owning planner for a revised byte budget; keep the current budget until that decision is supplied.",
                               current_max_total_bytes=request.get("constraints", {}).get("max_total_bytes"),
                               requires_decision=True))
    else:
        if reason == "selector_left_unassigned":
            next_action = "Review why the selector declined the candidates, then refine the query or request a better-fitting asset."
            actions.append(_action("review_selection", slot_id,
                                   "Review candidate suitability with the selecting agent; no candidate has been accepted for this slot.",
                                   candidate_ids=list(candidates)))
        elif reason == "reuse_disabled":
            next_action = "Find or request a distinct compatible asset while keeping asset reuse disabled."
        else:
            next_action = "Refine the asset query, import a compatible pack, or hand the generation brief to an appropriate agent."
        actions.append(_action("retry_query", slot_id,
                               "Search for the same object or action with clearer theme-specific terms and synonyms; do not repeat attempted queries or weaken hard filters.",
                               attempted_queries=list(queries), preserve_constraints=True,
                               require_distinct_asset=reason == "reuse_disabled"))
        actions.append(_action("import_pack", slot_id,
                               "Import a suitable asset pack with source and license metadata, retaining the requested style, formats and animation clips.",
                               preserve_constraints=True))
        brief = _brief(slot, reason, request)
        generation = _action("request_generation", slot_id,
                             "Pass the attached request to an appropriate generation or coding agent. Validate and catalog any resulting file before it can satisfy this slot.",
                             preserve_constraints=True, dispatch_status="not_dispatched")
        if reason == "selector_left_unassigned":
            generation["prerequisite"] = "Review the existing candidates and their rejection before dispatching this alternative."
        if brief.get("method_hint") == "procedural_svg":
            generation["method_hint"] = "procedural_svg"
            generation["description"] += " A simple UI icon may be authored as an SVG path by a coding agent."
        actions.append(generation)

    if not required:
        actions.append(_action("omit_optional", slot_id,
                               "The owning planner may explicitly omit this optional slot; no substitute has been assigned.",
                               requires_decision=True))
    return {
        "slot_id": slot_id,
        "required": required,
        "reason": reason,
        "description": slot["description"],
        "next_action": next_action,
        "candidate_ids": candidates,
        "attempted_queries": queries,
        "actions": actions,
        "generation_brief": brief,
    }
