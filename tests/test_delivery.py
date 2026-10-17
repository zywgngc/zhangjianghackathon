import copy
import hashlib
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from asset_agent.delivery import deliver_plan
import asset_agent.delivery as delivery_module


class FakeCatalog:
    def __init__(self, root, records):
        self.root = root
        self.records = {record["id"]: record for record in records}

    def get(self, asset_id):
        return self.records.get(asset_id)

    def resolve_path(self, record):
        return self.root / record["path"]


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "catalog"
        self.root.mkdir()
        self.content = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
        (self.root / "icon.svg").write_bytes(self.content)
        self.record = {
            "id": "icon",
            "type": "image",
            "path": "icon.svg",
            "format": "svg",
            "name": "A small icon",
            "author": "Example artist",
            "license": "CC0-1.0",
            "source_url": "https://example.test/icon",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "attribution": "Original icon by Example artist",
        }
        self.catalog = FakeCatalog(self.root, [self.record])
        self.output = self.base / "bundle"
        self.plan = {
            "schema_version": "1.0",
            "request_id": "test-request",
            "status": "ready",
            "retrieval": {"mode": "offline"},
            "selections": [
                {
                    "slot_id": "start-button",
                    "asset_id": "icon",
                    "asset_type": "image",
                    "format": "svg",
                    "source": {},
                    "usage": {"fit": "contain", "render_as": "ui-svg"},
                    "score": 1.0,
                    "reason": "Matches the slot",
                    "alternatives": [],
                }
            ],
            "unresolved": [],
        }

    def test_copies_asset_writes_manifest_and_retains_cc0_credit(self):
        original_plan = copy.deepcopy(self.plan)
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(self.plan, original_plan)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text("utf-8")), result)
        selection = result["selections"][0]
        checksum = hashlib.sha256(self.content).hexdigest()
        self.assertEqual(selection["file"]["path"], "assets/{}.svg".format(checksum[:16]))
        self.assertEqual(selection["file"]["sha256"], checksum)
        self.assertEqual(selection["file"]["bytes"], len(self.content))
        self.assertEqual((self.output / selection["file"]["path"]).read_bytes(), self.content)
        self.assertEqual(selection["usage"]["runtime_url"], "./" + selection["file"]["path"])
        self.assertEqual(selection["source"]["license"], self.record["license"])
        self.assertEqual(result["bundle"]["asset_count"], 1)
        self.assertEqual(result["bundle"]["total_bytes"], len(self.content))
        credits = (self.output / "CREDITS.txt").read_text("utf-8")
        for expected in [
            self.record["name"], self.record["author"], self.record["source_url"],
            self.record["license"], self.record["license_url"], self.record["attribution"],
            "Modifications by this agent: none; files copied verbatim",
        ]:
            self.assertIn(expected, credits)

    def test_repeated_asset_is_copied_once(self):
        other_slot = copy.deepcopy(self.plan["selections"][0])
        other_slot["slot_id"] = "end-button"
        self.plan["selections"].append(other_slot)
        self.plan["constraints"] = {"max_total_bytes": len(self.content)}
        with patch("asset_agent.delivery._copy_asset", wraps=delivery_module._copy_asset) as copier:
            result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(copier.call_count, 1)
        self.assertEqual(len(list((self.output / "assets").iterdir())), 1)
        self.assertEqual(result["bundle"]["total_bytes"], len(self.content))
        self.assertEqual(result["selections"][0]["file"], result["selections"][1]["file"])

    def test_identical_bytes_share_one_file_and_byte_count(self):
        duplicate = dict(self.record, id="duplicate")
        self.catalog.records["duplicate"] = duplicate
        other_slot = copy.deepcopy(self.plan["selections"][0])
        other_slot.update(slot_id="other", asset_id="duplicate")
        self.plan["selections"].append(other_slot)
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(result["bundle"]["asset_count"], 1)
        self.assertEqual(result["bundle"]["total_bytes"], len(self.content))

    def test_nonempty_output_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "user-file.txt"
        sentinel.write_text("keep me", encoding="utf-8")
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                with self.assertRaises(ValueError):
                    deliver_plan(self.plan, self.catalog, self.output, overwrite=overwrite)
                self.assertEqual(sentinel.read_text("utf-8"), "keep me")
                self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_a_completed_bundle_is_also_protected(self):
        result = deliver_plan(self.plan, self.catalog, self.output)
        manifest = (self.output / "manifest.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite.*unsupported"):
            deliver_plan(self.plan, self.catalog, self.output, overwrite=True)
        self.assertEqual((self.output / "manifest.json").read_bytes(), manifest)
        self.assertEqual(json.loads(manifest), result)

    def test_empty_output_directory_can_be_used(self):
        self.output.mkdir()
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(result["status"], "ready")

    def test_missing_source_leaves_no_partial_bundle(self):
        missing = dict(self.record, id="missing", path="missing.svg")
        self.catalog.records["missing"] = missing
        other_slot = copy.deepcopy(self.plan["selections"][0])
        other_slot.update(slot_id="other", asset_id="missing")
        self.plan["selections"].append(other_slot)
        original = copy.deepcopy(self.plan)
        with self.assertRaises(ValueError):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob(".bundle.tmp-*")))
        self.assertEqual(self.plan, original)

    def test_install_failure_restores_empty_output_and_removes_staging(self):
        self.output.mkdir()
        real_replace = os.replace

        def fail_final_install(source, destination):
            if Path(source).is_dir():
                raise OSError("simulated failure")
            return real_replace(source, destination)

        with patch("asset_agent.delivery.os.replace", side_effect=fail_final_install):
            with self.assertRaisesRegex(ValueError, "simulated failure"):
                deliver_plan(self.plan, self.catalog, self.output)
        self.assertTrue(self.output.is_dir())
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertFalse(list(self.base.glob(".bundle.tmp-*")))

    def test_traversal_and_absolute_paths_are_rejected(self):
        outside = self.base / "outside.svg"
        outside.write_bytes(self.content)
        for unsafe in ["../outside.svg", "..\\outside.svg", str(outside), "C:\\outside.svg", "C:outside.svg"]:
            with self.subTest(path=unsafe):
                self.record["path"] = unsafe
                with self.assertRaises(ValueError):
                    deliver_plan(self.plan, self.catalog, self.output)
                self.assertFalse(self.output.exists())

    def test_catalog_resolver_cannot_redirect_a_record(self):
        outside = self.base / "outside.svg"
        outside.write_bytes(self.content)
        with patch.object(self.catalog, "resolve_path", return_value=outside):
            with self.assertRaises(ValueError):
                deliver_plan(self.plan, self.catalog, self.output)
        other = self.root / "other.svg"
        other.write_bytes(self.content)
        with patch.object(self.catalog, "resolve_path", return_value=other):
            with self.assertRaises(ValueError):
                deliver_plan(self.plan, self.catalog, self.output)

    def test_symlink_escape_is_rejected(self):
        outside = self.base / "outside.svg"
        outside.write_bytes(self.content)
        link = self.root / "link.svg"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Symlinks unavailable: {}".format(exc))
        self.record["path"] = "link.svg"
        with self.assertRaises(ValueError):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_output_symlink_is_rejected_without_touching_target(self):
        target = self.base / "user-output"
        target.mkdir()
        sentinel = target / "important.txt"
        sentinel.write_text("keep", encoding="utf-8")
        try:
            self.output.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("Symlinks unavailable: {}".format(exc))
        with self.assertRaises(ValueError):
            deliver_plan(self.plan, self.catalog, self.output, overwrite=True)
        self.assertEqual(sentinel.read_text("utf-8"), "keep")

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_directory_junction_cannot_escape_catalog(self):
        import _winapi

        outside = self.base / "outside"
        outside.mkdir()
        (outside / "icon.svg").write_bytes(self.content)
        link = self.root / "linked-directory"
        _winapi.CreateJunction(str(outside), str(link))
        try:
            self.record["path"] = "linked-directory/icon.svg"
            with self.assertRaises(ValueError):
                deliver_plan(self.plan, self.catalog, self.output)
            self.assertFalse(self.output.exists())
            self.assertEqual((outside / "icon.svg").read_bytes(), self.content)
        finally:
            link.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_output_junction_and_ancestor_junction_are_rejected(self):
        import _winapi

        target = self.base / "empty-user-directory"
        target.mkdir()
        _winapi.CreateJunction(str(target), str(self.output))
        try:
            for requested in (self.output, self.output / "nested-bundle"):
                with self.subTest(output=requested):
                    with self.assertRaisesRegex(ValueError, "symlink or junction"):
                        deliver_plan(self.plan, self.catalog, requested)
                    self.assertEqual(list(target.iterdir()), [])
        finally:
            self.output.rmdir()

    def test_catalog_and_output_cannot_overlap(self):
        for output in [self.root, self.root / "bundle", self.base]:
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "overlap"):
                    deliver_plan(self.plan, self.catalog, output)
        self.assertEqual((self.root / "icon.svg").read_bytes(), self.content)

    def test_external_gltf_and_glb_dependencies_are_rejected(self):
        document = {"asset": {"version": "2.0"}, "buffers": [{"uri": "mesh.bin"}]}
        gltf = self.root / "model.gltf"
        gltf.write_text(json.dumps(document), encoding="utf-8")
        payload = json.dumps(document).encode("utf-8")
        payload += b" " * (-len(payload) % 4)
        glb = self.root / "model.glb"
        glb.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(payload))
            + struct.pack("<II", len(payload), 0x4E4F534A) + payload
        )
        for extension in ["gltf", "glb"]:
            with self.subTest(format=extension):
                self.record.update(path="model." + extension, format=extension)
                self.plan["selections"][0]["format"] = extension
                with self.assertRaisesRegex(ValueError, "External glTF/GLB"):
                    deliver_plan(self.plan, self.catalog, self.output)
                self.assertFalse(self.output.exists())

    def test_embedded_gltf_is_delivered(self):
        (self.root / "model.gltf").write_text(json.dumps({
            "asset": {"version": "2.0"},
            "buffers": [{"uri": "data:application/octet-stream;base64,AAAA"}],
        }), encoding="utf-8")
        self.record.update(path="model.gltf", format="gltf")
        self.plan["selections"][0]["format"] = "gltf"
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertTrue(result["selections"][0]["file"]["path"].endswith(".gltf"))

    def test_unresolved_status_is_preserved_without_fake_assets(self):
        self.plan.update(status="unresolved", selections=[], unresolved=[{"slot_id": "missing"}])
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["bundle"]["asset_count"], 0)
        self.assertEqual(list((self.output / "assets").iterdir()), [])

    def test_ready_plan_with_unresolved_slots_is_rejected(self):
        self.plan["unresolved"] = [{"slot_id": "missing"}]
        with self.assertRaisesRegex(ValueError, "ready plan"):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_ready_plan_allows_unresolved_optional_slots(self):
        self.plan["unresolved"] = [{"slot_id": "optional-decoration", "required": False}]
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["unresolved"], self.plan["unresolved"])

    def test_matching_expected_file_is_accepted(self):
        self.plan["selections"][0]["expected_file"] = {
            "sha256": hashlib.sha256(self.content).hexdigest(),
            "bytes": len(self.content),
        }
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(result["selections"][0]["file"]["sha256"],
                         self.plan["selections"][0]["expected_file"]["sha256"])

    def test_changed_asset_fingerprint_leaves_no_bundle(self):
        expected = {"sha256": hashlib.sha256(self.content).hexdigest(), "bytes": len(self.content)}
        self.plan["selections"][0]["expected_file"] = expected
        (self.root / "icon.svg").write_bytes(self.content.replace(b"10", b"20"))
        original_plan = copy.deepcopy(self.plan)
        with self.assertRaisesRegex(ValueError, "expected_file mismatch"):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob(".bundle.tmp-*")))
        self.assertEqual(self.plan, original_plan)

    def test_expected_file_byte_count_must_also_match(self):
        self.plan["selections"][0]["expected_file"] = {
            "sha256": hashlib.sha256(self.content).hexdigest(),
            "bytes": len(self.content) + 1,
        }
        with self.assertRaisesRegex(ValueError, "expected_file mismatch"):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_catalog_fingerprint_prevents_relabeling_modified_source(self):
        self.record["sha256"] = hashlib.sha256(self.content).hexdigest()
        changed_content = self.content.replace(b"10", b"20")
        (self.root / "icon.svg").write_bytes(changed_content)
        # A newly planned fingerprint can match the changed file while its
        # upstream provenance still describes the original catalog contents.
        self.plan["selections"][0]["expected_file"] = {
            "sha256": hashlib.sha256(changed_content).hexdigest(),
            "bytes": len(changed_content),
        }
        original_plan = copy.deepcopy(self.plan)
        with self.assertRaisesRegex(ValueError, "Catalog sha256 mismatch"):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob(".bundle.tmp-*")))
        self.assertEqual(self.plan, original_plan)

    def test_matching_catalog_fingerprint_is_accepted(self):
        self.record["sha256"] = hashlib.sha256(self.content).hexdigest()
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(result["selections"][0]["file"]["sha256"], self.record["sha256"])

    def test_final_copied_size_obeys_per_asset_budget(self):
        self.plan["constraints"] = {"max_asset_bytes": len(self.content)}
        (self.root / "icon.svg").write_bytes(self.content + b"<!-- enlarged after planning -->")
        with self.assertRaisesRegex(ValueError, "max_asset_bytes"):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob(".bundle.tmp-*")))

    def test_final_unique_files_obey_total_budget(self):
        (self.root / "second.svg").write_bytes(self.content + b"<!-- second -->")
        self.catalog.records["second"] = dict(self.record, id="second", path="second.svg")
        other_slot = copy.deepcopy(self.plan["selections"][0])
        other_slot.update(slot_id="second-slot", asset_id="second")
        self.plan["selections"].append(other_slot)
        self.plan["constraints"] = {"max_total_bytes": len(self.content)}
        with self.assertRaisesRegex(ValueError, "max_total_bytes"):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_exact_size_budget_is_accepted(self):
        self.plan["constraints"] = {
            "max_asset_bytes": len(self.content), "max_total_bytes": len(self.content),
        }
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(result["bundle"]["total_bytes"], len(self.content))

    def test_duplicate_slot_ids_are_rejected(self):
        self.plan["selections"].append(copy.deepcopy(self.plan["selections"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate selection slot_id"):
            deliver_plan(self.plan, self.catalog, self.output)
        self.assertFalse(self.output.exists())

    def test_selection_type_and_format_must_match_catalog(self):
        for field, value in [("asset_type", "audio"), ("format", "png")]:
            with self.subTest(field=field):
                plan = copy.deepcopy(self.plan)
                plan["selections"][0][field] = value
                with self.assertRaisesRegex(ValueError, "does not match catalog"):
                    deliver_plan(plan, self.catalog, self.output)
                self.assertFalse(self.output.exists())

    def test_legacy_minimal_selection_fields_remain_supported(self):
        selection = self.plan["selections"][0]
        for field in ("slot_id", "asset_type", "format"):
            del selection[field]
        result = deliver_plan(self.plan, self.catalog, self.output)
        self.assertEqual(result["bundle"]["asset_count"], 1)


if __name__ == "__main__":
    unittest.main()
