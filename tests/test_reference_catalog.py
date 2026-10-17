"""Contract-shaped discovery, filtering and evidence honesty for local references."""

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from asset_agent.reference_catalog import LocalReferenceProvider


def candidate(candidate_id="movement", *, kind="source_code", **changes):
    result = {
        "candidateId": candidate_id, "kind": kind, "title": "Movement source",
        "summary": "An elapsed-time waypoint movement reference.",
        "source": {
            "uri": "https://example.invalid/source/" + candidate_id,
            "repositoryUrl": "https://example.invalid/project",
            "revision": "a" * 40, "paths": ["src/advance.js"], "symbols": ["advance"],
        },
        "coverageHints": ["Moves along waypoints"],
        "license": {"identifier": "MIT", "sourceUri": "https://example.invalid/LICENSE", "scope": "source_code"},
        "evidence": [{"description": "Source reference", "sourceUri": "https://example.invalid/source/" + candidate_id}],
        "limitations": ["Synthetic unit-test metadata"],
    }
    result.update(changes)
    return result


def record(candidate_id="movement", **changes):
    result = {
        "candidate": candidate(candidate_id), "searchText": "Distance along ordered waypoints",
        "tags": ["movement", "waypoint"], "engines": [], "languages": [], "inputModes": [],
    }
    result.update(changes)
    return result


def request(description="waypoint movement", **changes):
    result = {
        "contractVersion": "0.2", "requestId": "test-search", "projectRevision": 0,
        "purpose": "mechanic", "mechanicId": "movement", "requiredBehavior": ["Advance simulation"],
        "description": description, "images": [],
        "constraints": {"engine": None, "engineVersion": None, "platform": "web", "language": "any", "inputModes": ["pointer"]},
        "materialKinds": ["source_code", "algorithm_documentation"], "limit": 10,
        "discussion": {"runId": "test-run", "threadId": "test-thread", "round": 0, "inReplyToRequestId": None, "followUp": None},
    }
    result.update(changes)
    return result


class ReferenceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def provider(self, records, *, is_mock=True):
        document = {"schemaVersion": "0.2", "isMock": is_mock, "materials": records}
        (self.root / "materials.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return LocalReferenceProvider(self.root)

    def test_candidates_use_formal_contract_and_keep_provenance_without_mutation(self):
        item = record()
        before = copy.deepcopy(item)
        provider = self.provider([item])
        payload = request()
        input_before = copy.deepcopy(payload)
        first, second = provider.search(payload), provider(payload)
        self.assertEqual(first, second)
        self.assertEqual(payload, input_before)
        self.assertEqual(item, before)
        self.assertEqual(provider.count, 1)
        self.assertTrue(first["isMock"])
        self.assertFalse(first["partial"])
        hit = first["candidates"][0]
        self.assertEqual(set(hit), set(item["candidate"]))
        self.assertEqual(hit["source"], item["candidate"]["source"])
        self.assertEqual(hit["license"], item["candidate"]["license"])
        self.assertEqual(hit["coverageHints"], item["candidate"]["coverageHints"])
        self.assertIn("unverified claims", " ".join(hit["limitations"]))
        self.assertNotIn("score", hit)
        self.assertNotIn("verified", hit)
        hit["source"]["paths"].append("modified-by-caller")
        self.assertNotIn("modified-by-caller", provider.search(payload)["candidates"][0]["source"]["paths"])

    def test_bilingual_domain_terms_find_specific_references(self):
        terms = [
            ("pathfinding", "寻路"), ("waypoint", "路点"), ("movement", "移动"),
            ("survivors", "幸存者"), ("wave", "波次"), ("combat", "战斗"),
            ("target", "目标"), ("economy", "经济"), ("tower defense", "塔防"),
        ]
        items = []
        for index, (english, _) in enumerate(terms):
            identifier = "reference-{}".format(index)
            items.append(record(identifier, candidate=candidate(identifier, title=english, summary="Curated source", coverageHints=[]), searchText="", tags=[english]))
        provider = self.provider(items)
        for index, (_, chinese) in enumerate(terms):
            with self.subTest(term=chinese):
                result = provider.search(request(chinese, requiredBehavior=["需求"]))
                self.assertEqual([item["candidateId"] for item in result["candidates"]], ["reference-{}".format(index)])
        result = provider.search(request("A*", requiredBehavior=["需求"]))
        self.assertEqual(result["candidates"][0]["candidateId"], "reference-0")

    def test_required_behavior_and_follow_up_are_search_context(self):
        provider = self.provider([record()])
        missing = request("unrelated zebras", requiredBehavior=["unrelated llamas"])
        self.assertEqual(provider.search(missing)["candidates"], [])
        behavior = request("unrelated zebras", requiredBehavior=["Follow ordered waypoints"])
        self.assertEqual(len(provider.search(behavior)["candidates"]), 1)
        follow_up = copy.deepcopy(missing)
        follow_up["discussion"].update({"round": 1, "inReplyToRequestId": "previous", "followUp": {"message": "Provide waypoint movement alternatives", "clarificationId": None, "answers": []}})
        self.assertEqual(len(provider.search(follow_up)["candidates"]), 1)

    def test_engine_language_kind_and_purpose_filters_are_independent(self):
        items = [
            record("agnostic"), record("phaser-ts", engines=["phaser"], languages=["typescript"]),
            record("babylon-ts", engines=["babylon"], languages=["typescript"]),
            record("phaser-js", engines=["phaser"], languages=["javascript"]),
            record("archetype-only", purposes=["archetype"]),
            record("docs", candidate=candidate("docs", kind="algorithm_documentation")),
        ]
        provider = self.provider(items)
        constraints = {"engine": "phaser", "engineVersion": None, "platform": "web", "language": "typescript", "inputModes": ["pointer"]}
        result = provider.search(request(constraints=constraints, materialKinds=["source_code"]))
        self.assertEqual({hit["candidateId"] for hit in result["candidates"]}, {"agnostic", "phaser-ts"})
        constraints["engine"] = None
        constraints["language"] = "any"
        result = provider.search(request(constraints=constraints, materialKinds=["algorithm_documentation"]))
        self.assertEqual([hit["candidateId"] for hit in result["candidates"]], ["docs"])
        archetype = request(purpose="archetype", constraints=constraints)
        del archetype["mechanicId"], archetype["requiredBehavior"]
        self.assertIn("archetype-only", {hit["candidateId"] for hit in provider.search(archetype)["candidates"]})

    def test_unknown_input_modes_are_explained_instead_of_claiming_support(self):
        provider = self.provider([record("partial-input", inputModes=["keyboard"]), record("all-inputs", inputModes=["keyboard", "pointer", "touch"])])
        constraints = {"engine": "phaser", "engineVersion": "3.90.0", "platform": "web", "language": "javascript", "inputModes": ["keyboard", "touch"]}
        result = provider.search(request(constraints=constraints))
        hits = {item["candidateId"]: item for item in result["candidates"]}
        self.assertIn("undeclared for touch", " ".join(hits["partial-input"]["limitations"]))
        self.assertNotIn("undeclared", " ".join(hits["all-inputs"]["limitations"]))
        self.assertIn("engine version", " ".join(result["limitations"]))
        self.assertIn("3.90.0", " ".join(hits["all-inputs"]["limitations"]))

    def test_images_return_explicit_partial_without_mock_fallback(self):
        provider = self.provider([record()], is_mock=False)
        result = provider.search(request(images=[{"uri": "asset://agreed/image", "mimeType": "image/png", "description": "Reference"}]))
        self.assertTrue(result["partial"])
        self.assertFalse(result["isMock"])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIn("not resolved or visually searched", " ".join(result["limitations"]))

    def test_generic_text_and_function_words_cannot_establish_a_match(self):
        provider = self.provider([record()])
        for text in ("and or the with", "find compatible phaser source code game references", "source paths and file paths", "查找开源游戏代码素材", "unrelated zebras"):
            with self.subTest(text=text):
                self.assertEqual(provider.search(request(text, requiredBehavior=["for the source"]))["candidates"], [])
        generic = record(candidate=candidate(title="source code", summary="game reference", coverageHints=[]), searchText="", tags=[])
        empty_index = self.provider([generic])
        self.assertEqual(empty_index.search(request("and or the", requiredBehavior=["code"]))["candidates"], [])

    def test_mobile_word_does_not_claim_character_movement(self):
        provider = self.provider([record(candidate=candidate(title="Movement", summary="Movement", coverageHints=[]), searchText="", tags=["movement"])])
        self.assertEqual(provider.search(request("移动端", requiredBehavior=["需求"]))["candidates"], [])

    def test_item_spawning_does_not_become_an_enemy_wave_topic(self):
        items = [
            record("items", candidate=candidate("items", title="Item spawning", summary="Spawn items and experience gems", coverageHints=[]), tags=["spawn", "items"], searchText=""),
            record("waves", candidate=candidate("waves", title="Enemy waves", summary="Enemy spawn scheduling", coverageHints=[]), tags=["waves"], searchText=""),
        ]
        provider = self.provider(items)
        wave_request = request("enemy wave scheduling", mechanicId="enemy-wave-scheduling", requiredBehavior=["Spawn enemies from simulation time"])
        self.assertEqual([hit["candidateId"] for hit in provider.search(wave_request)["candidates"]], ["waves"])
        item_request = request("item spawn", mechanicId="opaque-942c7105", requiredBehavior=["Spawn experience gems"])
        result = provider.search(item_request)
        self.assertIn("items", [hit["candidateId"] for hit in result["candidates"]])
        self.assertNotIn("explicit mechanic topic hints", " ".join(result["limitations"]))
        for phrase in ("enemy spawn", "enemy spawning", "spawn enemies", "敌人生成", "刷怪"):
            with self.subTest(phrase=phrase):
                payload = request(phrase, mechanicId="opaque-942c7105", requiredBehavior=[phrase])
                self.assertEqual([hit["candidateId"] for hit in provider.search(payload)["candidates"]], ["waves"])

    def test_deduplication_and_limit_are_stable(self):
        a, z = record("a-movement"), record("z-movement")
        z["candidate"]["source"] = copy.deepcopy(a["candidate"]["source"])
        distinct = record("distinct", candidate=candidate("distinct", title="Movement", summary="Movement", coverageHints=[]), searchText="", tags=["movement"])
        provider = self.provider([z, a, copy.deepcopy(a), distinct])
        self.assertEqual(provider.count, 3)
        result = provider.search(request("movement"))
        self.assertEqual({item["candidateId"] for item in result["candidates"]}, {"a-movement", "distinct"})
        self.assertEqual(len(provider.search(request("movement", limit=1))["candidates"]), 1)
        provider = self.provider([distinct, a, z])
        self.assertEqual(result, provider.search(request("movement")))

    def test_conflicting_candidate_identity_is_rejected(self):
        first, second = record(), record()
        second["candidate"]["title"] = "Conflicting title"
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            self.provider([first, second])

    def test_missing_and_empty_real_catalog_are_valid_empty_discovery(self):
        provider = LocalReferenceProvider(self.root)
        result = provider.search(request())
        self.assertEqual(result["candidates"], [])
        self.assertFalse(result["isMock"])
        self.assertFalse(result["partial"])
        self.assertIn("No materials.json", " ".join(result["limitations"]))
        empty = self.provider([], is_mock=False).search(request())
        self.assertEqual(empty["candidates"], [])
        self.assertNotIn("No materials.json", " ".join(empty["limitations"]))

    def test_unknown_license_and_revision_remain_unknown(self):
        item = record()
        item["candidate"]["license"] = None
        item["candidate"]["source"]["revision"] = None
        result = self.provider([item]).search(request())
        hit = result["candidates"][0]
        self.assertIsNone(hit["source"]["revision"])
        self.assertIsNone(hit["license"])
        self.assertIn("License evidence is unknown", " ".join(hit["limitations"]))
        self.assertIn("No immutable source revision", " ".join(hit["limitations"]))

    def test_reference_paths_are_metadata_and_never_read(self):
        item = record()
        item["candidate"]["source"]["paths"] = ["C:/never-open/waypoint.js", "../../outside/movement.py"]
        provider = self.provider([item])
        before = {path.name for path in self.root.iterdir()}
        result = provider.search(request())
        self.assertEqual(result["candidates"][0]["source"]["paths"], item["candidate"]["source"]["paths"])
        self.assertEqual({path.name for path in self.root.iterdir()}, before)

    def test_unsafe_or_invalid_source_uris_are_rejected(self):
        for uri in ("file:///C:/secret", "C:/secret", "../source.js", "javascript:alert(1)", "data:text/plain,source", "https://user:password@example.invalid/source", "https://", "https://example.invalid:bad/source", "https://example.invalid/has space", "https://example.invalid\\source"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                item = record()
                item["candidate"]["source"]["uri"] = uri
                self.provider([item])

    def test_corrupt_record_shapes_are_rejected_before_search(self):
        corrupt = []
        item = record()
        item["candidate"]["verified"] = True
        corrupt.append(item)
        item = record()
        item["candidate"]["kind"] = "icon"
        corrupt.append(item)
        item = record()
        item["candidate"]["kind"] = []
        corrupt.append(item)
        item = record()
        del item["candidate"]["source"]["revision"]
        corrupt.append(item)
        item = record(engines=["unity"])
        corrupt.append(item)
        corrupt.append(record(languages="typescript"))
        corrupt.append(record(inputModes=["touch", "touch"]))
        corrupt.append(record(purposes=[]))
        corrupt.append(record(contentPath="../../source"))
        for item in corrupt:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.provider([item])

    def test_manifest_requires_explicit_version_and_mock_marker(self):
        path = self.root / "materials.json"
        documents = [
            {"schemaVersion": "0.1", "isMock": False, "materials": []},
            {"schemaVersion": "0.2", "isMock": "false", "materials": []},
            {"schemaVersion": "0.2", "materials": []},
            {"schemaVersion": "0.2", "isMock": False, "materials": {}},
        ]
        for document in documents:
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.subTest(document=document), self.assertRaises(ValueError):
                LocalReferenceProvider(self.root)
        path.write_text('{"schemaVersion":"0.2","isMock":false,"materials":[],"isMock":true}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate object key"):
            LocalReferenceProvider(self.root)
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            LocalReferenceProvider(self.root)

    def test_consumed_request_fields_are_validated(self):
        provider = self.provider([])
        invalid = [
            None, {}, request(contractVersion="0.1"), request(purpose=[]), request(description=" "),
            request(materialKinds=["icon"]), request(limit=True), request(limit=11), request(images="image.png"),
            request(requiredBehavior=[]), request(discussion={"followUp": {"message": " "}}),
        ]
        for key, value in (("engine", []), ("engine", "unity"), ("platform", "native"), ("language", []), ("inputModes", [])):
            item = request()
            item["constraints"][key] = value
            invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                provider.search(item)

    def test_explicit_cancellation_propagates_before_and_during_discovery(self):
        provider = self.provider([record("a"), record("b")])
        event = threading.Event()
        event.set()
        with self.assertRaises(asyncio.CancelledError):
            provider.search(request(), cancel_event=event)

        class CancelDuringScan:
            calls = 0

            def is_set(self):
                self.calls += 1
                return self.calls >= 3

        with self.assertRaises(asyncio.CancelledError):
            provider.search(request(), cancel_event=CancelDuringScan())
        with self.assertRaises(ValueError):
            provider.search(request(), cancel_event=object())


class RealReferenceRelevanceTests(unittest.TestCase):
    """Regress relevance against the actual curated catalog and upstream input.

    These assertions check discovery ordering only. They do not execute any
    source reference or validate the functional claims in coverageHints.
    """

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.provider = LocalReferenceProvider(root / "catalogs/references")
        self.payload = json.loads((root / "examples/webmcp-v02/mechanic-request.json").read_text(encoding="utf-8"))

    def test_official_waypoint_request_returns_navigation_without_background_fillers(self):
        original = copy.deepcopy(self.payload)
        result = self.provider.search(self.payload)
        ids = [candidate["candidateId"] for candidate in result["candidates"]]
        self.assertEqual(self.payload, original)
        self.assertFalse(result["isMock"])
        self.assertEqual(ids[:2], ["ref-phaser-path-follower", "ref-pathfinding-grid-astar"])
        self.assertNotIn("ref-wukong-wave-spawning", ids)
        self.assertNotIn("ref-wukong-combat-targeting", ids)
        self.assertNotIn("ref-babylon-recast-navigation", ids)
        self.assertIn("topic matching does not verify required behavior", " ".join(result["limitations"]))
        for candidate in result["candidates"]:
            self.assertIn("Coverage hints are unverified claims", " ".join(candidate["limitations"]))

    def test_named_wave_target_and_combat_remain_relevant(self):
        for topic, expected in (
            ("wave", "ref-wukong-wave-spawning"),
            ("target", "ref-phaser-closest-target"),
            ("combat", "ref-phaser-topdown-combat"),
        ):
            with self.subTest(topic=topic):
                payload = copy.deepcopy(self.payload)
                payload.update(mechanicId="enemy-" + topic, description=topic, requiredBehavior=[topic])
                result = self.provider.search(payload)
                ids = [candidate["candidateId"] for candidate in result["candidates"]]
                self.assertEqual(ids[0], expected)
                self.assertNotIn("ref-phaser-path-follower", ids)
                self.assertNotIn("ref-pathfinding-grid-astar", ids)

    def test_research_case_enemy_waves_excludes_experience_item_spawning(self):
        root = Path(__file__).resolve().parents[1]
        cases = json.loads((root / "examples/research-cases.json").read_text(encoding="utf-8"))["cases"]
        case = next(item for item in cases if item["id"] == "enemy-wave-scheduling")
        payload = copy.deepcopy(self.payload)
        for key in ("purpose", "description", "requiredBehavior", "materialKinds", "limit"):
            payload[key] = copy.deepcopy(case[key])
        payload["mechanicId"] = case["id"]
        payload["constraints"]["engine"] = case["engine"]
        result = self.provider.search(payload)
        ids = [candidate["candidateId"] for candidate in result["candidates"]]
        self.assertIn("ref-wukong-wave-spawning", ids)
        self.assertNotIn("ref-wukong-collectibles-xp", ids)
        self.assertIn("topic matching does not verify required behavior", " ".join(result["limitations"]))

    def test_opaque_mechanic_id_falls_back_to_full_text_intent(self):
        for identifier in ("m-942c7105", "record-waveform-123"):
            with self.subTest(identifier=identifier):
                payload = copy.deepcopy(self.payload)
                payload["mechanicId"] = identifier
                ids = [candidate["candidateId"] for candidate in self.provider.search(payload)["candidates"]]
                self.assertEqual(ids[:2], ["ref-phaser-path-follower", "ref-pathfinding-grid-astar"])
        payload = copy.deepcopy(self.payload)
        payload.update(mechanicId="m-942c7105", description="波次刷怪", requiredBehavior=["按时间驱动敌人波次生成"])
        self.assertEqual(self.provider.search(payload)["candidates"][0]["candidateId"], "ref-wukong-wave-spawning")

    def test_generic_request_does_not_return_any_real_reference(self):
        payload = copy.deepcopy(self.payload)
        payload.update(mechanicId="m-942c7105", description="Find compatible Phaser source code game references and source paths", requiredBehavior=["and or the with"])
        self.assertEqual(self.provider.search(payload)["candidates"], [])


if __name__ == "__main__":
    unittest.main()
