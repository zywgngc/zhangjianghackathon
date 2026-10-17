import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from game_harness.specifications import CASES, build_feature_plan, ingest_text, load_case


class SpecificationTests(unittest.TestCase):
    def test_all_authoritative_cases_preserve_every_character_and_test(self):
        root = Path(__file__).resolve().parents[1]
        for case_id, count in zip(CASES, [54, 88, 83]):
            with self.subTest(case=case_id):
                spec = load_case(case_id)
                directory, filename = CASES[case_id]
                source = (root / directory / filename).read_bytes()
                self.assertEqual(spec.text.encode("utf-8"), source)
                self.assertEqual(spec.sha256, hashlib.sha256(source).hexdigest())
                self.assertEqual(spec.reconstruct(), spec.text)
                self.assertEqual(len(spec.acceptance_tests), count)
                self.assertEqual([test["test_id"] for test in spec.acceptance_tests], [f"T{n:02}" for n in range(1, count + 1)])
                self.assertEqual([section["number"] for section in spec.sections if section["level"] == 1], [f"{n:02}" for n in range(1, 16)])
                for item in spec.chunks + spec.sections + spec.requirements + spec.acceptance_tests + spec.constraints:
                    span = item["source"]
                    selected = spec.text[span["start"]:span["end"]]
                    self.assertEqual(hashlib.sha256(selected.encode()).hexdigest(), span["sha256"])
                    self.assertEqual(spec.text.count("\n", 0, span["start"]) + 1, span["line_start"])
                self.assertTrue(build_feature_plan(spec)["all_requirements_covered"])

    def test_long_crlf_input_is_not_normalized_or_truncated(self):
        text = "中文完整长规格\r\n\r\n" + ("必须实现的动作与数值 200。\r\n" * 18000)
        document = ingest_text(text, chunk_chars=1024)
        self.assertEqual(document.reconstruct(), text)
        self.assertTrue(all(len(chunk["text"]) <= 1024 for chunk in document.chunks))
        self.assertEqual(document.chunks[-1]["source"]["end"], len(text))

    def test_content_ids_survive_unrelated_insertions(self):
        first = ingest_text("01. BASIC GAME INFORMATION\n\nExisting requirement 200.\n", case_id="CUSTOM")
        second = ingest_text("01. BASIC GAME INFORMATION\n\nNew requirement.\n\nExisting requirement 200.\n", case_id="CUSTOM")
        old = next(item for item in first.requirements if "Existing" in item["text"])
        new = next(item for item in second.requirements if "Existing" in item["text"])
        self.assertEqual(old["id"], new["id"])
        self.assertNotEqual(old["source"]["line_start"], new["source"]["line_start"])

    def test_skills_keep_all_26_entity_definitions_and_every_level(self):
        spec = load_case("GAME-TEST-002")
        self.assertEqual(len(spec.entities), 26)
        self.assertEqual([item["id"] for item in spec.entities if item["kind"] == "active"], [f"W{n:02}" for n in range(1, 13)])
        self.assertEqual([item["id"] for item in spec.entities if item["kind"] == "passive"], [f"P{n:02}" for n in range(1, 9)])
        self.assertEqual([item["id"] for item in spec.entities if item["kind"] == "ultimate"], [f"E{n:02}" for n in range(1, 7)])
        for entity in spec.entities:
            if entity["kind"] == "active":
                for level in range(1, 6):
                    self.assertIn(f"Level {level}:", entity["text"])

    def test_source_conflicting_names_are_visible_and_archive_is_reconstructable(self):
        with tempfile.TemporaryDirectory() as folder:
            spec = load_case("GAME-TEST-003", archive_dir=folder)
            self.assertEqual(spec.names, ["百炼英雄", "战力吞噬者"])
            self.assertTrue(spec.warnings)
            archive = Path(folder) / spec.case_id / spec.sha256
            self.assertEqual((archive / "source.txt").read_bytes(), spec.text.encode())
            manifest = json.loads((archive / "specification.json").read_text())
            self.assertEqual("".join(chunk["text"] for chunk in manifest["chunks"]), spec.text)

    def test_case_and_archive_path_cannot_be_spoofed(self):
        with self.assertRaises(ValueError):
            ingest_text("Test Case ID: GAME-TEST-001\n", case_id="GAME-TEST-002")
        with self.assertRaises(ValueError):
            ingest_text("A valid long spec", case_id="../../outside")


if __name__ == "__main__":
    unittest.main()
