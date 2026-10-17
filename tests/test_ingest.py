"""Meaningful importer checks, using tiny real files and temporary directories."""

import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from asset_agent.catalog import Catalog
from asset_agent.ingest import import_pack


def png(width=2, height=3):
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    pixels = b"".join(b"\0" + b"\xff\0\0\xff" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def glb(document, binary=None):
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    chunks = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded
    if binary is not None:
        binary += b"\0" * (-len(binary) % 4)
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.catalog = self.root / "catalog"
        self.options = dict(pack="Demo pack", style="flat", license_id="CC0-1.0",
                            author="Test artist", source_url="https://example.org/pack",
                            license_url="https://creativecommons.org/publicdomain/zero/1.0/")

    def svg(self, relative="coin.svg"):
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 32"><circle cx="12" cy="12" r="10"/></svg>', encoding="utf-8")
        return target

    def run_import(self, **overrides):
        return import_pack(self.source, self.catalog, **dict(self.options, **overrides))

    def test_real_svg_png_metadata_and_idempotence(self):
        self.svg("ui/coin.svg")
        (self.source / "player.png").write_bytes(png())
        (self.source / "pack.json").write_text(json.dumps({
            "defaults": {"tags": ["cartoon", "coin"]},
            "assets": {"ui/coin.svg": {"name": "Gold coin", "description": "Collectible", "type": "icon"}},
        }), encoding="utf-8")
        result = self.run_import()
        self.assertEqual(result["imported"], 2)
        catalog = Catalog(self.catalog)
        self.assertEqual(catalog.validate_files(), [])
        records = {item["format"]: item for item in catalog.assets}
        self.assertEqual((records["svg"]["width"], records["svg"]["height"]), (24, 32))
        self.assertEqual((records["png"]["width"], records["png"]["height"]), (2, 3))
        self.assertEqual(records["svg"]["name"], "Gold coin")
        self.assertEqual(records["svg"]["tags"], ["cartoon", "coin"])
        original = (self.catalog / "catalog.json").read_bytes()
        again = self.run_import()
        self.assertEqual(again, {"imported": 0, "skipped": 2, "ids": result["ids"]})
        self.assertEqual((self.catalog / "catalog.json").read_bytes(), original)

    def test_changed_existing_asset_is_not_overwritten(self):
        target = self.svg()
        self.run_import()
        original = (self.catalog / "catalog.json").read_bytes()
        stored = Catalog(self.catalog).resolve_path(Catalog(self.catalog).assets[0])
        data = stored.read_bytes()
        target.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50"/>', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.run_import()
        self.assertEqual(stored.read_bytes(), data)
        self.assertEqual((self.catalog / "catalog.json").read_bytes(), original)

    def test_unknown_existing_file_is_protected(self):
        self.svg()
        self.run_import()
        manifest = self.catalog / "catalog.json"
        file = Catalog(self.catalog).resolve_path(Catalog(self.catalog).assets[0])
        manifest.unlink()
        content = file.read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.run_import()
        self.assertEqual(file.read_bytes(), content)
        self.assertFalse(manifest.exists())

    def test_metadata_traversal_and_missing_assets_fail_before_writes(self):
        self.svg()
        for path in ("../escape.svg", "C:/escape.svg", "/escape.svg", "missing.svg"):
            with self.subTest(path=path):
                (self.source / "pack.json").write_text(json.dumps({"assets": {path: {"name": "bad"}}}))
                with self.assertRaises(ValueError):
                    self.run_import()
                self.assertFalse(self.catalog.exists())

    def test_missing_provenance_and_unknown_types_fail_before_writes(self):
        self.svg()
        for field in ("license_id", "author", "source_url", "license_url"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.run_import(**{field: ""})
                self.assertFalse(self.catalog.exists())
        with self.assertRaises(ValueError):
            self.run_import(asset_type="executable")
        (self.source / "pack.json").write_text(json.dumps({"assets": {"coin.svg": {"type": "unknown"}}}))
        with self.assertRaisesRegex(ValueError, "Unknown asset type"):
            self.run_import()
        self.assertFalse(self.catalog.exists())

    def test_ancestor_directories_are_forbidden(self):
        self.svg()
        for target in (self.source, self.source / "nested", self.root):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "contain"):
                    import_pack(self.source, target, **self.options)

    def test_external_gltf_reference_is_rejected_and_embedded_is_imported(self):
        model = {"asset": {"version": "2.0"}, "buffers": [{"uri": "missing.bin", "byteLength": 3}]}
        target = self.source / "hero.gltf"
        target.write_text(json.dumps(model))
        with self.assertRaisesRegex(ValueError, "external URI"):
            self.run_import()
        self.assertFalse(self.catalog.exists())
        model["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(b"abc").decode()
        model["animations"] = [{"name": "run"}, {}]
        target.write_text(json.dumps(model))
        self.run_import()
        self.assertEqual(Catalog(self.catalog).assets[0]["animations"], ["run"])

    def test_model_metadata_cannot_invent_animation_names(self):
        document = {"asset": {"version": "2.0"}, "animations": [{"name": "Run"}, {"name": "Idle"}]}
        (self.source / "hero.gltf").write_text(json.dumps(document))
        for overrides in ({"defaults": {"animations": ["Fly"]}},
                          {"assets": {"hero.gltf": {"animations": ["run"]}}}):
            with self.subTest(overrides=overrides):
                (self.source / "pack.json").write_text(json.dumps(overrides))
                with self.assertRaisesRegex(ValueError, "real clip names"):
                    self.run_import()
                self.assertFalse(self.catalog.exists())
        (self.source / "pack.json").write_text(json.dumps({"assets": {"hero.gltf": {"animations": ["Run"]}}}))
        self.run_import()
        self.assertEqual(Catalog(self.catalog).assets[0]["animations"], ["Run"])

    def test_sprite_metadata_can_declare_animation_names(self):
        (self.source / "runner.png").write_bytes(png())
        (self.source / "pack.json").write_text(json.dumps({"defaults": {"animations": ["run", "idle"]}}))
        self.run_import()
        self.assertEqual(Catalog(self.catalog).assets[0]["animations"], ["run", "idle"])

    def test_invalid_late_asset_leaves_existing_catalog_unchanged(self):
        self.svg()
        self.run_import()
        original = (self.catalog / "catalog.json").read_bytes()
        self.svg("new.svg")
        (self.source / "z-broken.png").write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "PNG"):
            self.run_import()
        self.assertEqual((self.catalog / "catalog.json").read_bytes(), original)
        self.assertEqual(len(list((self.catalog / "assets").iterdir())), 1)

    def test_glb_binary_buffer_and_external_uri_check(self):
        document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}],
                    "bufferViews": [{"buffer": 0, "byteLength": 4}]}
        target = self.source / "hero.glb"
        target.write_bytes(glb(document, b"\0\0\0\0"))
        self.run_import()
        self.assertEqual(Catalog(self.catalog).assets[0]["type"], "model")
        document["images"] = [{"uri": "texture.png"}]
        target.write_bytes(glb(document, b"\0\0\0\0"))
        with self.assertRaisesRegex(ValueError, "external URI"):
            self.run_import()

    def test_missing_embedded_buffer_reference_is_rejected(self):
        document = {"asset": {"version": "2.0"}, "images": [{"bufferView": 0}]}
        (self.source / "hero.gltf").write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "missing bufferView"):
            self.run_import()
        self.assertFalse(self.catalog.exists())

    def test_missing_model_accessor_and_texture_are_rejected_before_publication(self):
        invalid_documents = [
            {"asset": {"version": "2.0"}, "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}]},
            {"asset": {"version": "2.0"}, "materials": [{"normalTexture": {"index": 0}}]},
            {"asset": {"version": "2.0"}, "animations": [{"samplers": [{"input": 0, "output": 0}]}]},
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                (self.source / "hero.gltf").write_text(json.dumps(document))
                with self.assertRaisesRegex(ValueError, "missing resource"):
                    self.run_import()
                self.assertFalse(self.catalog.exists())

    def test_existing_import_lock_is_never_removed(self):
        self.svg()
        self.catalog.mkdir()
        lock = self.catalog / ".asset-import.lock"
        lock.write_text("reserved")
        with self.assertRaisesRegex(ValueError, "import is active"):
            self.run_import()
        self.assertEqual(lock.read_text(), "reserved")
        self.assertFalse((self.catalog / "assets").exists())

    def test_publication_failure_rolls_back_only_new_files(self):
        self.svg()
        self.run_import()
        original = (self.catalog / "catalog.json").read_bytes()
        self.svg("new.svg")
        with patch("asset_agent.ingest.os.replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                self.run_import()
        self.assertEqual((self.catalog / "catalog.json").read_bytes(), original)
        self.assertEqual(len(list((self.catalog / "assets").iterdir())), 1)
        self.assertFalse((self.catalog / ".asset-import.lock").exists())
        self.assertEqual(list(self.catalog.glob(".catalog-*.tmp")), [])

    def test_symlinks_are_rejected(self):
        self.svg()
        link = self.source / "linked.svg"
        try:
            link.symlink_to(self.source / "coin.svg")
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links require privileges on this platform")
        with self.assertRaisesRegex(ValueError, "links"):
            self.run_import()
        self.assertFalse(self.catalog.exists())

    def test_svg_external_resource_is_rejected(self):
        (self.source / "remote.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="missing.png"/></svg>')
        with self.assertRaisesRegex(ValueError, "external"):
            self.run_import()
        self.assertFalse(self.catalog.exists())


if __name__ == "__main__":
    unittest.main()
