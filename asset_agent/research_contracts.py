"""Validation for the upstream WebMCP material-research contract v0.2.

The vendored schema defines the wire format.  The additional checks here cover
relationships between records, without claiming to enforce the MAS's durable
run-wide discussion budget or its project-revision policy.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit


_SCHEMA_PATH = Path(__file__).with_name("specs") / "material-portal-0.2.schema.json"


@lru_cache(maxsize=3)
def _definition_validator(definition: str) -> Any:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:
        raise RuntimeError(
            "Research-contract validation requires the research extra; "
            "install game-asset-agent[research]"
        ) from exc

    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    # Keep every upstream definition intact; select this particular boundary
    # rather than accepting any of the three records allowed at the root.
    selected = {
        "$schema": schema["$schema"],
        "$defs": schema["$defs"],
        "$ref": "#/$defs/" + definition,
    }
    Draft202012Validator.check_schema(selected)
    checker = FormatChecker()
    # jsonschema's format implementations are optional dependencies. An empty
    # FormatChecker silently accepts unknown formats, which is inappropriate
    # for portal URIs and provenance timestamps.
    if not {"uri", "date-time"}.issubset(checker.checkers):
        raise RuntimeError(
            "Research-contract validation requires URI and date-time checkers; "
            "install game-asset-agent[research] (jsonschema[format-nongpl])"
        )
    return Draft202012Validator(selected, format_checker=checker)


def _validate_definition(value: Any, definition: str) -> Dict[str, Any]:
    copied = deepcopy(value)
    validator = _definition_validator(definition)
    from jsonschema.exceptions import best_match
    error = best_match(validator.iter_errors(copied))
    if error is not None:
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ValueError("{} at {}: {}".format(definition, location, error.message))
    return copied


def _validate_image_references(request: Dict[str, Any]) -> None:
    for index, image in enumerate(request["images"]):
        uri = image["uri"]
        scheme = urlsplit(uri).scheme.lower()
        # `file:` and drive-letter paths can satisfy JSON Schema's URI format,
        # but the official portal contract explicitly forbids local paths.
        if not scheme or scheme == "file" or (
            len(scheme) == 1 and len(uri) > 2 and uri[1] == ":"
        ):
            raise ValueError(
                "images[{}].uri must be portal-accessible, not a local filesystem path".format(index)
            )


def validate_search_request(value: Any) -> Dict[str, Any]:
    """Return an independent validated request; never silently drop fields.

    Missing required fields and unknown fields are governed by the exact
    upstream JSON Schema. URI schemes other than local-file references remain
    a provider capability decision rather than being guessed here.
    """
    result = _validate_definition(value, "MaterialSearchRequest")
    _validate_image_references(result)
    return result


def _validate_questions(result: Dict[str, Any]) -> None:
    question_ids = set()
    for question in result.get("questions", []):
        question_id = question["questionId"]
        if question_id in question_ids:
            raise ValueError("clarification questionId must be unique: " + question_id)
        question_ids.add(question_id)
        option_ids = [option["optionId"] for option in question["options"]]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("clarification optionId must be unique within question: " + question_id)
        if not question["required"] and question["defaultOptionId"] not in option_ids:
            raise ValueError("clarification defaultOptionId is not an option: " + question_id)


def validate_search_result(
    value: Any, request: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Validate a result and, when supplied, its originating request.

    This validates provider claims as claims. It does not verify source code,
    licenses, coverage, or whether a candidate can satisfy a production build.
    """
    result = _validate_definition(value, "MaterialSearchResult")
    candidates = result.get("candidates", [])
    candidate_ids = [candidate["candidateId"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidateId must be unique within a result")
    _validate_questions(result)

    if request is not None:
        expected = validate_search_request(request)
        for field in ("contractVersion", "requestId"):
            if result[field] != expected[field]:
                raise ValueError("result {} does not match request".format(field))
        for field in ("runId", "threadId", "round"):
            if result["discussion"][field] != expected["discussion"][field]:
                raise ValueError("result discussion.{} does not match request".format(field))
        if len(candidates) > expected["limit"]:
            raise ValueError("result candidate count exceeds request limit")
        for candidate in candidates:
            if candidate["kind"] not in expected["materialKinds"]:
                raise ValueError("candidate kind was not requested: " + candidate["kind"])
    return result


def validate_clarification_reply(
    request: Any, parent_request: Any, parent_result: Any
) -> None:
    """Validate a follow-up against one accepted parent exchange.

    Rounds must increase, but need not be consecutive in this thread: the MAS
    reserves slots across all material threads in a run. Reservation and
    idempotency remain the orchestrator's responsibility.
    """
    current = validate_search_request(request)
    parent = validate_search_request(parent_request)
    previous_result = validate_search_result(parent_result, parent)
    discussion = current["discussion"]
    previous = parent["discussion"]

    if discussion["round"] == 0:
        raise ValueError("a clarification reply or refinement must be a follow-up")
    if current["requestId"] == parent["requestId"]:
        raise ValueError("a follow-up must have a new requestId")
    if discussion["inReplyToRequestId"] != parent["requestId"]:
        raise ValueError("follow-up inReplyToRequestId does not identify the parent request")
    for field in ("runId", "threadId"):
        if discussion[field] != previous[field]:
            raise ValueError("follow-up discussion.{} does not match parent".format(field))
    if discussion["round"] <= previous["round"]:
        raise ValueError("follow-up round must be greater than the parent round")

    follow_up = discussion["followUp"]
    if follow_up["clarificationId"] is None:
        # This is an unsolicited refinement. The schema already requires its
        # answers to be empty, so it need not pretend to answer pending questions.
        return
    if previous_result["status"] != "needs_clarification":
        raise ValueError("parent result does not contain a clarification batch")
    if follow_up["clarificationId"] != previous_result["clarificationId"]:
        raise ValueError("follow-up clarificationId does not match parent result")

    questions = {question["questionId"]: question for question in previous_result["questions"]}
    answered = set()
    for answer in follow_up["answers"]:
        question_id = answer["questionId"]
        if question_id in answered:
            raise ValueError("duplicate clarification answer: " + question_id)
        if question_id not in questions:
            raise ValueError("answer does not identify a parent question: " + question_id)
        answered.add(question_id)
        question = questions[question_id]
        options = {option["optionId"] for option in question["options"]}
        if answer["optionId"] is not None and answer["optionId"] not in options:
            raise ValueError("answer optionId is not an option for question: " + question_id)
        if answer["source"] == "system_default" and (
            question["required"]
            or answer["text"] is not None
            or answer["optionId"] != question["defaultOptionId"]
        ):
            raise ValueError("system_default answer must use the question's valid default: " + question_id)
    missing = [
        question_id
        for question_id, question in questions.items()
        if question["required"] and question_id not in answered
    ]
    if missing:
        raise ValueError("required clarification answers are missing: " + ", ".join(missing))
