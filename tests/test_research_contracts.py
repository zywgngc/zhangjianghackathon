"""Official v0.2 wire fixtures and cross-record acceptance checks."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from asset_agent.research_contracts import (
    _validate_definition,
    _definition_validator,
    validate_clarification_reply,
    validate_search_request,
    validate_search_result,
)


_FIXTURES = Path(__file__).resolve().parents[1] / "examples" / "webmcp-v02"


def fixture(name):
    return json.loads((_FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


@unittest.skipUnless(importlib.util.find_spec("jsonschema"), "requires the optional research extra")
class ResearchContractTests(unittest.TestCase):
    def setUp(self):
        self.request = fixture("mechanic-request")
        self.result = fixture("mechanic-response")
        self.questions = fixture("portal-clarification-response")
        self.reply = fixture("portal-follow-up-request")

    def test_all_eight_official_fixtures_match_their_specific_definition(self):
        definitions = {
            "archetype-request": "MaterialSearchRequest",
            "mechanic-request": "MaterialSearchRequest",
            "mechanic-response": "MaterialSearchResult",
            "failure-response": "MaterialSearchResult",
            "portal-clarification-response": "MaterialSearchResult",
            "portal-follow-up-request": "MaterialSearchRequest",
            "portal-follow-up-response": "MaterialSearchResult",
            "main-follow-up": "MainFollowUpRequest",
        }
        self.assertEqual(len(list(_FIXTURES.glob("*.json"))), len(definitions))
        for name, definition in definitions.items():
            with self.subTest(fixture=name):
                value = fixture(name)
                self.assertEqual(_validate_definition(value, definition), value)

    def test_official_search_and_clarification_exchange_correlates(self):
        validate_search_request(fixture("archetype-request"))
        validate_search_result(self.result, self.request)
        validate_search_result(self.questions, self.request)
        validate_clarification_reply(self.reply, self.request, self.questions)
        validate_search_result(fixture("portal-follow-up-response"), self.reply)

    def test_validation_returns_deep_copies_without_normalizing_unknown_values(self):
        original = deepcopy(self.request)
        normalized = validate_search_request(self.request)
        normalized["constraints"]["inputModes"].append("keyboard")
        self.assertEqual(self.request, original)
        validated = validate_search_result(self.result, self.request)
        validated["candidates"][0]["limitations"].append("caller edit")
        self.assertNotEqual(validated, self.result)
        self.assertIsNone(self.result["candidates"][0]["license"])
        self.assertTrue(validated["isMock"])

    def test_bad_request_fields_do_not_get_silently_dropped(self):
        mutations = [
            ("unknown field", lambda value: value.update(extra=True)),
            ("missing version", lambda value: value.pop("contractVersion")),
            ("legacy version", lambda value: value.update(contractVersion="0.1")),
            ("legacy images", lambda value: value.update(imageRefs=[])),
            ("wrong engine case", lambda value: value["constraints"].update(engine="Phaser")),
            ("duplicate input mode", lambda value: value["constraints"].update(inputModes=["touch", "touch"])),
            ("boolean limit", lambda value: value.update(limit=True)),
            ("oversized limit", lambda value: value.update(limit=11)),
            ("missing behavior", lambda value: value.pop("requiredBehavior")),
            ("unknown discussion field", lambda value: value["discussion"].update(extra=True)),
        ]
        for label, mutate in mutations:
            with self.subTest(case=label):
                invalid = deepcopy(self.request)
                mutate(invalid)
                with self.assertRaises(ValueError):
                    validate_search_request(invalid)

    def test_archetype_cannot_smuggle_mechanic_fields(self):
        value = fixture("archetype-request")
        for fields in ({"mechanicId": "test"}, {"requiredBehavior": ["test"]}):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    validate_search_request(dict(value, **fields))

    def test_local_image_paths_are_rejected_even_when_they_are_valid_uris(self):
        for uri in (
            "file:///tmp/ref.png", "FILE:///C:/ref.png", "C:/images/ref.png",
            "C:\\images\\ref.png", "C:ref.png", "/tmp/ref.png", "./ref.png", "\\\\server\\ref.png",
        ):
            with self.subTest(uri=uri):
                value = fixture("archetype-request")
                value["images"][0]["uri"] = uri
                with self.assertRaises(ValueError):
                    validate_search_request(value)

    def test_agreed_opaque_and_https_image_uris_remain_valid(self):
        for uri in ("asset://reference/tower-defense-01", "https://example.org/ref.png"):
            value = fixture("archetype-request")
            value["images"][0]["uri"] = uri
            self.assertEqual(validate_search_request(value), value)

    def test_result_echo_must_match_each_correlation_field(self):
        for field in ("requestId", "runId", "threadId", "round"):
            with self.subTest(field=field):
                value = deepcopy(self.result)
                container = value if field == "requestId" else value["discussion"]
                container[field] = 1 if field == "round" else "different"
                with self.assertRaises(ValueError):
                    validate_search_result(value, self.request)

    def test_fixture_failure_is_a_different_thread_and_cannot_echo_mechanic_request(self):
        failure = fixture("failure-response")
        validate_search_result(failure)
        with self.assertRaises(ValueError):
            validate_search_result(failure, self.request)

    def test_result_version_timestamp_and_unknown_fields_are_validated(self):
        for fields in (
            {"contractVersion": "0.1"}, {"retrievedAt": "yesterday"},
            {"retrievedAt": "2026-10-17"}, {"verified": True},
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    validate_search_result(dict(self.result, **fields), self.request)

    def test_duplicate_candidate_ids_are_rejected_without_a_request_too(self):
        value = deepcopy(self.result)
        value["candidates"][1]["candidateId"] = value["candidates"][0]["candidateId"]
        with self.assertRaises(ValueError):
            validate_search_result(value)

    def test_candidates_must_obey_request_limit_and_requested_kinds(self):
        with self.assertRaises(ValueError):
            validate_search_result(self.result, dict(self.request, limit=1))
        with self.assertRaises(ValueError):
            validate_search_result(self.result, dict(self.request, materialKinds=["source_code"]))

    def test_partial_requires_a_nonempty_limitation_but_completed_can_be_empty(self):
        value = dict(self.result, candidates=[], limitations=[], status="completed")
        validate_search_result(value, self.request)
        with self.assertRaises(ValueError):
            validate_search_result(dict(value, status="partial"), self.request)
        validate_search_result(dict(value, status="partial", limitations=["Source provider unavailable"]), self.request)

    def test_failed_variant_cannot_include_candidates_or_omit_error(self):
        failure = fixture("failure-response")
        with self.assertRaises(ValueError):
            validate_search_result(dict(failure, candidates=[]))
        failure.pop("error")
        with self.assertRaises(ValueError):
            validate_search_result(failure)

    def test_real_failed_result_keeps_false_mock_marker(self):
        value = fixture("failure-response")
        value["isMock"] = False
        self.assertFalse(validate_search_result(value)["isMock"])

    def test_question_ids_option_ids_and_defaults_must_be_consistent(self):
        for mutation in ("question", "option", "default"):
            with self.subTest(mutation=mutation):
                value = deepcopy(self.questions)
                question = value["questions"][0]
                if mutation == "question":
                    value["questions"].append(deepcopy(question))
                elif mutation == "option":
                    question["options"][1]["optionId"] = question["options"][0]["optionId"]
                else:
                    question["defaultOptionId"] = "not-offered"
                with self.assertRaises(ValueError):
                    validate_search_result(value)

    def test_optional_questions_need_two_options_and_required_has_no_default(self):
        value = deepcopy(self.questions)
        value["questions"][0]["options"] = value["questions"][0]["options"][:1]
        with self.assertRaises(ValueError):
            validate_search_result(value)
        value = deepcopy(self.questions)
        value["questions"][0]["required"] = True
        with self.assertRaises(ValueError):
            validate_search_result(value)

    def test_result_variant_fields_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            validate_search_result(dict(self.result, clarificationId="unexpected", questions=[]))
        with self.assertRaises(ValueError):
            validate_search_result(dict(self.questions, error={}))

    def test_follow_up_must_identify_parent_run_thread_and_increasing_round(self):
        changes = (
            ("inReplyToRequestId", "wrong"), ("runId", "wrong"), ("threadId", "wrong"),
            ("round", 0), ("round", 4),
        )
        for field, replacement in changes:
            with self.subTest(field=field, replacement=replacement):
                value = deepcopy(self.reply)
                value["discussion"][field] = replacement
                with self.assertRaises(ValueError):
                    validate_clarification_reply(value, self.request, self.questions)
        with self.assertRaises(ValueError):
            validate_clarification_reply(dict(self.reply, requestId=self.request["requestId"]), self.request, self.questions)

    def test_follow_up_may_skip_run_wide_slots(self):
        value = deepcopy(self.reply)
        value["discussion"]["round"] = 3
        validate_clarification_reply(value, self.request, self.questions)

    def test_later_follow_up_must_advance_parent_round(self):
        child = deepcopy(self.reply)
        child["requestId"] = "another-request"
        child["discussion"]["inReplyToRequestId"] = self.reply["requestId"]
        with self.assertRaises(ValueError):
            validate_clarification_reply(child, self.reply, fixture("portal-follow-up-response"))

    def test_refinement_does_not_require_a_clarification_batch(self):
        value = deepcopy(self.reply)
        value["discussion"]["followUp"].update(clarificationId=None, answers=[])
        validate_clarification_reply(value, self.request, self.result)
        validate_clarification_reply(value, self.request, self.questions)

    def test_answer_batch_requires_matching_clarification_and_question_ids(self):
        for field, replacement in (("clarificationId", "wrong"), ("questionId", "wrong"), ("optionId", "wrong")):
            with self.subTest(field=field):
                value = deepcopy(self.reply)
                follow_up = value["discussion"]["followUp"]
                target = follow_up if field == "clarificationId" else follow_up["answers"][0]
                target[field] = replacement
                with self.assertRaises(ValueError):
                    validate_clarification_reply(value, self.request, self.questions)
        with self.assertRaises(ValueError):
            validate_clarification_reply(self.reply, self.request, self.result)

    def test_duplicate_answers_are_rejected(self):
        value = deepcopy(self.reply)
        value["discussion"]["followUp"]["answers"] *= 2
        with self.assertRaises(ValueError):
            validate_clarification_reply(value, self.request, self.questions)

    def test_answers_have_one_representation_and_a_known_provenance(self):
        for fields in (
            {"optionId": None, "text": None}, {"text": "also text"},
            {"source": "user"}, {"optionId": None, "text": ""},
        ):
            with self.subTest(fields=fields):
                value = deepcopy(self.reply)
                value["discussion"]["followUp"]["answers"][0].update(fields)
                with self.assertRaises(ValueError):
                    validate_clarification_reply(value, self.request, self.questions)

    def test_default_provenance_must_select_the_actual_optional_default(self):
        value = deepcopy(self.reply)
        value["discussion"]["followUp"]["answers"][0]["optionId"] = "bidirectional"
        with self.assertRaises(ValueError):
            validate_clarification_reply(value, self.request, self.questions)
        value["discussion"]["followUp"]["answers"][0]["source"] = "developer_choice"
        validate_clarification_reply(value, self.request, self.questions)

    def test_required_free_text_question_must_be_answered_without_default(self):
        questions = deepcopy(self.questions)
        questions["questions"].append({
            "questionId": "engine-fact", "prompt": "Which runtime revision is required?",
            "options": [], "required": True, "defaultOptionId": None,
        })
        with self.assertRaises(ValueError):
            validate_clarification_reply(self.reply, self.request, questions)
        reply = deepcopy(self.reply)
        reply["discussion"]["followUp"]["answers"].append({
            "questionId": "engine-fact", "optionId": None, "text": "Phaser 3.90",
            "source": "research_context",
        })
        validate_clarification_reply(reply, self.request, questions)
        reply["discussion"]["followUp"]["answers"][-1]["source"] = "system_default"
        with self.assertRaises(ValueError):
            validate_clarification_reply(reply, self.request, questions)

    def test_nondefault_free_text_is_not_artificially_forbidden_for_a_choice_question(self):
        value = deepcopy(self.reply)
        value["discussion"]["followUp"]["answers"][0].update(
            optionId=None, text="Forward only except during a scripted rewind", source="developer_choice"
        )
        validate_clarification_reply(value, self.request, self.questions)

    def test_null_clarification_and_answer_list_are_mutually_exclusive(self):
        value = deepcopy(self.reply)
        value["discussion"]["followUp"]["clarificationId"] = None
        with self.assertRaises(ValueError):
            validate_search_request(value)
        value = deepcopy(self.reply)
        value["discussion"]["followUp"]["answers"] = []
        with self.assertRaises(ValueError):
            validate_search_request(value)

    def test_round_four_is_rejected_on_both_official_boundaries(self):
        value = deepcopy(self.reply)
        value["discussion"]["round"] = 4
        with self.assertRaises(ValueError):
            validate_search_request(value)
        main = fixture("main-follow-up")
        main["discussion"]["round"] = 4
        with self.assertRaises(ValueError):
            _validate_definition(main, "MainFollowUpRequest")

    def test_missing_format_dependencies_fail_explicitly_instead_of_accepting_invalid_uris(self):
        from jsonschema import FormatChecker

        _definition_validator.cache_clear()
        self.addCleanup(_definition_validator.cache_clear)
        with patch.dict(FormatChecker.checkers, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "URI and date-time checkers"):
                validate_search_request(self.request)


if __name__ == "__main__":
    unittest.main()
