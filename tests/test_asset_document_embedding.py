"""Actual multi-bundle references reach offline HTML without model Base64 copying."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
try:
    spec = importlib.util.spec_from_file_location("asset_document_embedding", ROOT / "game_harness/material_portal.py")
    PORTAL = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(PORTAL)
except ImportError:
    PORTAL = None


def png(color):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + bytes(color) * 2)) + chunk(b"IEND", b""))


@unittest.skipUnless(PORTAL is not None, "Requires harness httpx/jsonschema")
class AssetDocumentEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.documents = []
        for index, color in enumerate(((12, 34, 56, 255), (55, 21, 89, 255))):
            directory = self.root / str(index)
            manifest = PORTAL.collect_asset_bytes(png(color), directory,
                source_url="https://kenney.nl/assets/example-" + str(index),
                license_info={"identifier": "CC0-1.0", "scope": "assets", "sourceUri": "https://kenney.nl/assets/example"},
                original_filename="soldier" + str(index) + ".png")
            self.documents.append({"id": "doc" + str(index), "verification": "downloaded_hash_checked_art",
                "asset_manifest": str(directory / "manifest.json"),
                "assets": [{"dataUri": "not-to-model"}], "title": "Sprite"})
        self.prepared = PORTAL.prepare_asset_documents(self.documents)

    def test_prepare_exposes_real_short_references_without_base64_or_local_paths(self):
        self.assertEqual(self.documents[0]["assets"][0]["dataUri"], "not-to-model")
        for doc in self.prepared:
            self.assertNotIn("asset_manifest", doc)
            self.assertNotIn("dataUri", json.dumps(doc))
            asset = doc["assets"][0]
            self.assertEqual(asset["uri"], "asset://" + asset["assetId"])
            self.assertEqual((asset["width"], asset["height"]), (2, 1))
            self.assertFalse(asset["isAtlas"])
            self.assertNotIn("absolutePath", asset)

    def test_two_bundles_embed_original_bytes_and_keep_offline_credits(self):
        refs = [d["assets"][0]["uri"] for d in self.prepared]
        source = '<html><body><img src="' + refs[0] + '"><img src="' + refs[1] + '"></body></html>'
        output = PORTAL.embed_asset_documents(source, self.documents)
        self.assertNotIn("asset://", output)
        self.assertEqual(output.count('src="data:image/png;base64,'), 2)
        self.assertNotIn('href="https://', output)
        self.assertEqual(output.count('data-material-asset-credits="true"'), 1)
        self.assertIn('<template data-material-asset-credits="true"', output)
        self.assertNotIn('<footer', output)
        for doc, color in zip(self.prepared, ((12, 34, 56, 255), (55, 21, 89, 255))):
            self.assertIn(base64.b64encode(png(color)).decode(), output)
            self.assertEqual(doc["assets"][0]["assetId"], "png-" + hashlib.sha256(png(color)).hexdigest()[:24])
        self.assertEqual(PORTAL.embed_asset_documents(output, self.documents), output)

    def test_unknown_or_malformed_reference_fails_before_artifact_delivery(self):
        for html in ('<img src="asset://imaginary">', '<img src="asset://">'):
            with self.subTest(html=html), self.assertRaises(PORTAL.AssetBundleError):
                PORTAL.embed_asset_documents(html, self.documents)

    def test_unused_images_are_not_copied_and_budget_counts_repeated_references(self):
        ref = self.prepared[0]["assets"][0]["uri"]
        output = PORTAL.embed_asset_documents('<img src="' + ref + '">', self.documents)
        self.assertEqual(output.count('data:image/png;base64,'), 1)
        with self.assertRaises(PORTAL.AssetBundleError):
            PORTAL.embed_asset_documents(('<img src="' + ref + '">') * 30, self.documents, max_html_bytes=1000)

    def test_tampered_source_bytes_cannot_reach_model_or_output(self):
        path = Path(self.documents[0]["asset_manifest"])
        manifest = json.loads(path.read_text("utf-8"))
        (path.parent / manifest["assets"][0]["path"]).write_bytes(png((0, 0, 0, 255)))
        with self.assertRaises(PORTAL.AssetBundleError):
            PORTAL.prepare_asset_documents(self.documents)
        with self.assertRaises(PORTAL.AssetBundleError):
            PORTAL.embed_asset_documents('<img src="' + self.prepared[0]["assets"][0]["uri"] + '">', self.documents)
