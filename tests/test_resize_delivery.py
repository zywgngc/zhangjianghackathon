"""Exercise resize through the real plan-to-bundle handoff, not mocks of pixels."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from asset_agent import AssetAgent
from asset_agent.contracts import validate_request
from asset_agent.delivery import deliver_plan

try:
    from PIL import Image
except ImportError:
    Image = None


class ResizeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "catalog"
        self.root.mkdir()
        self.svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="128" viewBox="0 0 256 128"><path d="M0 0h256v128H0z"/></svg>'
        (self.root / "pause.svg").write_bytes(self.svg)
        self.records = [{
            "id": "pause", "type": "icon", "name": "pause", "description": "pause",
            "tags": ["pause"], "style": "flat", "pack": "fixture",
            "path": "pause.svg", "format": "svg", "width": 256, "height": 128,
            "license": "CC0-1.0", "author": "Fixture artist",
            "source_url": "https://example.test/pause",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "sha256": hashlib.sha256(self.svg).hexdigest(),
        }]
        self.write_catalog()
        self.request = {
            "request_id": "resize-integration", "prompt": "pause game", "style": "flat",
            "layout": {"viewport": {"width": 800, "height": 600}, "slots": [
                {"slot_id": "pause-ui", "type": "icon", "description": "pause",
                 "bounds": {"x": 20, "y": 20, "width": 48, "height": 48},
                 "resize": {"width": 96, "height": 96, "mode": "contain"}}
            ]},
        }
        self.output = self.base / "bundle"

    def write_catalog(self):
        (self.root / "catalog.json").write_text(json.dumps({"schema_version": "1.0", "assets": self.records}), encoding="utf-8")

    def make_agent(self):
        agent = AssetAgent(self.root)
        self.addCleanup(agent.close)
        return agent

    def test_plan_keeps_original_fingerprint_and_only_declares_resize(self):
        before = copy.deepcopy(self.request)
        with patch("asset_agent.delivery.transform_asset", side_effect=AssertionError("plan must not resize")):
            plan = self.make_agent().plan(self.request)
        selected = plan["selections"][0]
        self.assertEqual(self.records[0]["sha256"], selected["expected_file"]["sha256"])
        self.assertEqual(96, selected["transform_request"]["resize"]["width"])
        self.assertNotIn("file", selected)
        self.assertNotIn("transforms", selected)
        self.assertEqual(before, self.request)
        self.assertFalse(self.output.exists())

    def test_svg_bundle_has_real_output_metadata_hash_and_modification_credit(self):
        result = self.make_agent().run(self.request, self.output)
        selected = result["selections"][0]
        output = self.output / selected["file"]["path"]
        data = output.read_bytes()
        svg = ET.fromstring(data)
        self.assertEqual(("96", "96", "0 0 256 128", "xMidYMid meet"),
                         (svg.get("width"), svg.get("height"), svg.get("viewBox"), svg.get("preserveAspectRatio")))
        self.assertEqual(hashlib.sha256(data).hexdigest(), selected["file"]["sha256"])
        self.assertEqual(len(data), selected["file"]["bytes"])
        self.assertEqual(self.records[0]["sha256"], selected["source_file"]["sha256"])
        self.assertNotIn("path", selected["source_file"])
        self.assertNotEqual(selected["source_file"]["sha256"], selected["file"]["sha256"])
        self.assertEqual({"width": 96, "height": 96}, selected["metadata"])
        self.assertEqual({"width": 256, "height": 128}, selected["source_metadata"])
        self.assertEqual(48, selected["usage"]["bounds"]["width"])
        self.assertEqual("./" + selected["file"]["path"], selected["usage"]["runtime_url"])
        self.assertEqual("resize", selected["transforms"][0]["operation"])
        self.assertEqual(self.svg, (self.root / "pause.svg").read_bytes())
        self.assertEqual(1, len(list((self.output / "assets").iterdir())))
        self.assertEqual(len(data), result["bundle"]["total_bytes"])
        self.assertEqual(result, json.loads((self.output / "manifest.json").read_text("utf-8")))
        credits = (self.output / "CREDITS.txt").read_text("utf-8")
        self.assertIn("resize", credits)
        self.assertIn(selected["file"]["path"], credits)
        self.assertIn("Fixture artist", credits)
        self.assertNotIn("files copied verbatim", credits)

    def test_one_source_can_deliver_multiple_sizes_and_deduplicate_identical_variants(self):
        first = self.request["layout"]["slots"][0]
        second = dict(copy.deepcopy(first), slot_id="small", resize={"width": 32, "height": 32})
        same = dict(copy.deepcopy(first), slot_id="large-again")
        original = copy.deepcopy(first)
        original["slot_id"] = "original"
        del original["resize"]
        self.request["layout"]["slots"].extend([second, same, original])
        result = self.make_agent().run(self.request, self.output)
        large, small, again, raw = result["selections"]
        self.assertEqual(large["file"], again["file"])
        self.assertNotEqual(large["file"]["path"], small["file"]["path"])
        self.assertEqual(self.svg, (self.output / raw["file"]["path"]).read_bytes())
        self.assertNotIn("source_file", raw)
        self.assertEqual(3, result["bundle"]["asset_count"])
        self.assertEqual(3, len(list((self.output / "assets").iterdir())))
        unique = {item["file"]["path"]: item["file"]["bytes"] for item in result["selections"]}
        self.assertEqual(sum(unique.values()), result["bundle"]["total_bytes"])

    def test_changed_source_is_rejected_before_transform_and_leaves_no_bundle(self):
        agent = self.make_agent()
        plan = agent.plan(self.request)
        (self.root / "pause.svg").write_bytes(self.svg.replace(b"256", b"512"))
        with patch("asset_agent.delivery.transform_asset", side_effect=AssertionError("must verify before resize")):
            with self.assertRaisesRegex(ValueError, "sha256 mismatch|expected_file mismatch"):
                deliver_plan(plan, agent.catalog, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual([], list(self.base.glob(".bundle.tmp-*")))

    def test_final_derived_file_is_subject_to_both_budgets(self):
        agent = self.make_agent()
        plan = agent.plan(self.request)
        # XML serialization adds a declaration and namespace prefix, making
        # the real derivative larger than the original in this fixture.
        result = deliver_plan(plan, agent.catalog, self.output)
        actual = result["bundle"]["total_bytes"]
        self.assertGreater(actual, len(self.svg))
        for budget in ("max_asset_bytes", "max_total_bytes"):
            limited = copy.deepcopy(plan)
            limited["constraints"][budget] = actual - 1
            target = self.base / budget
            with self.assertRaisesRegex(ValueError, budget):
                deliver_plan(limited, agent.catalog, target)
            self.assertFalse(target.exists())
            self.assertEqual([], list(self.base.glob(".{}.tmp-*".format(budget))))

    def test_invalid_transform_leaves_catalog_and_existing_empty_output_intact(self):
        agent = self.make_agent()
        plan = agent.plan(self.request)
        plan["selections"][0]["transform_request"]["resize"]["output_format"] = "png"
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "SVG-to-raster"):
            deliver_plan(plan, agent.catalog, self.output)
        self.assertEqual(self.svg, (self.root / "pause.svg").read_bytes())
        self.assertTrue(self.output.is_dir())
        self.assertEqual([], list(self.output.iterdir()))
        self.assertEqual([], list(self.base.glob(".bundle.tmp-*")))

    def test_model_and_animated_slot_resize_are_rejected_at_input(self):
        for changes in ({"type": "model"}, {"required_animations": ["idle"]}):
            invalid = copy.deepcopy(self.request)
            invalid["layout"]["slots"][0].update(changes)
            with self.assertRaisesRegex(ValueError, "static icon/sprite"):
                validate_request(invalid)

    def test_svg_sprite_keeps_texture_binding_after_resize(self):
        self.records[0]["type"] = "sprite"
        self.write_catalog()
        self.request["layout"]["slots"][0]["type"] = "sprite"
        item = self.make_agent().run(self.request, self.output)["selections"][0]
        self.assertEqual("svg", item["format"])
        self.assertEqual("texture", item["usage"]["render_as"])

    def test_resize_filters_animation_before_top_k_instead_of_failing_on_first_match(self):
        self.records[0]["type"] = "sprite"
        self.records[0]["tags"] = []
        animated = dict(self.records[0], id="aaa-animated", tags=["pause"], animations=["idle"])
        self.records.insert(0, animated)
        self.write_catalog()
        self.request["layout"]["slots"][0]["type"] = "sprite"
        self.request["constraints"] = {"top_k": 1}
        agent = self.make_agent()
        with self.assertRaisesRegex(ValueError, "static_only"):
            agent.search("pause", filters={"static_only": "true"})
        plan = agent.plan(self.request)
        self.assertEqual("ready", plan["status"])
        self.assertEqual("pause", plan["selections"][0]["asset_id"])
        self.assertEqual([], plan["selections"][0]["alternatives"])

    @unittest.skipIf(Image is None, "Raster resize uses the optional Pillow extra")
    def test_raster_padding_conversion_updates_format_and_runtime_binding(self):
        with Image.new("RGB", (8, 4), (255, 0, 0)) as image:
            image.save(self.root / "coin.jpg", format="JPEG")
        content = (self.root / "coin.jpg").read_bytes()
        self.records.append(dict(self.records[0], id="coin", name="coin", description="coin", tags=["coin"],
                                 path="coin.jpg", format="jpg", width=8, height=4,
                                 sha256=hashlib.sha256(content).hexdigest()))
        self.write_catalog()
        self.request["layout"]["slots"] = [{
            "slot_id": "coin", "type": "icon", "description": "coin", "formats": ["jpg"],
            "resize": {"width": 4, "height": 4, "resample": "nearest"},
        }]
        result = self.make_agent().run(self.request, self.output)
        item = result["selections"][0]
        self.assertEqual("png", item["format"])
        self.assertEqual("jpg", item["source_file"]["format"])
        self.assertEqual("texture", item["usage"]["render_as"])
        self.assertEqual(["resize", "format_conversion"], [op["operation"] for op in item["transforms"]])
        with Image.open(self.output / item["file"]["path"]) as image:
            self.assertEqual((4, 4), image.size)
            self.assertEqual("PNG", image.format)
            self.assertEqual(0, image.getpixel((0, 0))[3])
            self.assertEqual(255, image.getpixel((0, 1))[3])
        self.assertEqual(content, (self.root / "coin.jpg").read_bytes())


if __name__ == "__main__":
    unittest.main()
