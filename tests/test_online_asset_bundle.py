"""Security and offline handoff checks for the actual standalone material portal."""

import binascii
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
import zipfile
import zlib


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("online_bundle_helpers", ROOT / "game_harness/asset_material_portal.py")
try:
    BUNDLE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(BUNDLE)
except ImportError:
    BUNDLE = None


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)


def png(seed=0, *, width=1, height=1, raw=None):
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    if raw is None:
        raw = (b"\x00" + bytes((seed % 256, 12, 24, 255)) * width) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def archive(entries):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as handle:
        for name, payload in entries:
            handle.writestr(name, payload)
    return target.getvalue()


@unittest.skipUnless(BUNDLE is not None, "Requires optional harness httpx/jsonschema dependencies")
class OnlineAssetBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "bundle"

    def tearDown(self):
        self.temporary.cleanup()

    def collect(self, payload=None, **options):
        return BUNDLE.collect_asset_bytes(
            png() if payload is None else payload, self.root,
            source_url="https://kenney.nl/assets/example",
            license_info={"spdx": "CC0-1.0", "author": "Example Author"}, **options,
        )

    def test_single_png_preserves_exact_bytes_and_records_relative_content_path(self):
        payload = png(width=3, height=2)
        manifest = self.collect(payload, original_filename="hero.png")
        item = manifest["assets"][0]
        self.assertEqual((item["width"], item["height"], item["mimeType"]), (3, 2, "image/png"))
        self.assertEqual(item["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(item["assetId"], "png-" + item["sha256"][:24])
        self.assertEqual(item["path"], "assets/" + item["sha256"] + ".png")
        self.assertEqual((self.root / item["path"]).read_bytes(), payload)
        self.assertEqual(item["originalFilename"], "hero.png")
        self.assertEqual(json.loads((self.root / "manifest.json").read_text(encoding="utf-8")), manifest)

    def test_multiple_calls_merge_and_deduplicate_content(self):
        self.collect(png(1))
        self.collect(png(1))
        manifest = self.collect(png(2))
        self.assertEqual(len(manifest["assets"]), 2)
        self.assertEqual(len(list((self.root / "assets").glob("*.png"))), 2)

    def test_zip_selects_at_most_six_and_warns(self):
        data = archive([(f"PNG/hero-{number}.png", png(number)) for number in range(8)])
        manifest = self.collect(data, original_filename="pack.zip")
        self.assertEqual(len(manifest["assets"]), 6)
        self.assertIn("omitted 2", manifest["warnings"][0])

    def test_zip_accepts_large_realistic_member_count_with_exact_selection(self):
        data = archive([(f"PNG/tile-{number}.png", png(number % 256)) for number in range(600)])
        manifest = self.collect(data, include_paths=["PNG/tile-599.png", "PNG/tile-598.png"])
        self.assertEqual({item["originalFilename"] for item in manifest["assets"]}, {"PNG/tile-599.png", "PNG/tile-598.png"})
        self.assertEqual(manifest["warnings"], [])

    def test_zip_selection_rejects_missing_or_excessive_members(self):
        data = archive([("hero.png", png())])
        for options in ({"include_paths": ["absent.png"]}, {"include_paths": []}, {"include_paths": ["hero.png"], "limits": {"max_files": 1, "max_members": 1}}):
            if options.get("limits"):
                self.assertEqual(len(self.collect(data, **options)["assets"]), 1)
            else:
                with self.assertRaises(BUNDLE.AssetBundleError):
                    self.collect(data, **options)

    def test_zip_rejects_unsafe_paths_even_for_unselected_members(self):
        for name in ("../outside.png", "/absolute.png", "C:/drive.png", "a/./hero.png", "dir /hero.png", "folder/../hero.png"):
            with self.subTest(name=name), self.assertRaises(BUNDLE.AssetBundleError):
                self.collect(archive([("hero.png", png()), (name, b"ignored")]), include_paths=["hero.png"])
        self.assertFalse(self.root.exists())

    def test_zip_rejects_raw_backslashes_even_on_windows(self):
        # Python's ZIP writer normalizes Windows backslashes; mutate both headers.
        data = archive([("dir/hero.png", png())]).replace(b"dir/hero.png", b"dir\\hero.png")
        with self.assertRaises(BUNDLE.AssetBundleError):
            self.collect(data)

    def test_zip_rejects_symbolic_links(self):
        link = zipfile.ZipInfo("symlink.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(BUNDLE.AssetBundleError):
            self.collect(archive([(link, b"../../outside")]))

    def test_zip_rejects_duplicate_case_insensitive_names(self):
        with self.assertRaises(BUNDLE.AssetBundleError):
            self.collect(archive([("hero.png", png()), ("HERO.PNG", png(1))]))

    def test_zip_rejects_compression_bomb_before_reading(self):
        data = archive([("bomb.txt", b"0" * 1_000_000), ("hero.png", png())])
        with self.assertRaisesRegex(BUNDLE.AssetBundleError, "compression ratio"):
            self.collect(data)
        self.assertFalse(self.root.exists())

    def test_zip_total_uncompressed_and_member_budgets_are_enforced(self):
        data = archive([("a.png", png()), ("b.png", png(1))])
        for limits in ({"max_members": 1}, {"max_uncompressed_bytes": 50}, {"max_file_bytes": 50}):
            with self.subTest(limits=limits), self.assertRaises(BUNDLE.AssetBundleError):
                self.collect(data, limits=limits)

    def test_non_png_archive_has_no_usable_asset(self):
        with self.assertRaisesRegex(BUNDLE.AssetBundleError, "no selected PNG"):
            self.collect(archive([("readme.txt", b"hello")]))

    def test_wrong_formats_and_download_limits_are_rejected(self):
        for payload in (b"<svg></svg>", b"", b"JPEG data"):
            with self.subTest(payload=payload), self.assertRaises(BUNDLE.AssetBundleError):
                self.collect(payload)
        with self.assertRaises(BUNDLE.AssetBundleError):
            self.collect(limits={"max_download_bytes": 8})

    def test_png_crc_corruption_is_rejected(self):
        data = bytearray(png())
        data[29] ^= 1
        with self.assertRaisesRegex(BUNDLE.AssetBundleError, "checksum"):
            self.collect(bytes(data))

    def test_png_requires_iend_and_no_trailing_payload(self):
        for data in (png()[:-12], png() + b"<script>", png()[:-1]):
            with self.subTest(length=len(data)), self.assertRaises(BUNDLE.AssetBundleError):
                self.collect(data)

    def test_png_dimensions_decoded_size_and_filters_are_checked(self):
        for data, limits in ((png(width=2), {"max_pixels": 1}), (png(width=2), {"max_dimension": 1}), (png(), {"max_decoded_bytes": 4}), (png(raw=b"\x05\x00\x00\x00\xff"), None)):
            with self.subTest(limits=limits), self.assertRaises(BUNDLE.AssetBundleError):
                self.collect(data, limits=limits)

    def test_png_idat_bomb_and_truncated_zlib_are_rejected(self):
        with self.assertRaises(BUNDLE.AssetBundleError):
            self.collect(png(raw=b"\x00" * 1_000_000))
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00" * 5)[:-2]) + chunk(b"IEND", b"")
        with self.assertRaises(BUNDLE.AssetBundleError):
            self.collect(data)

    def test_animated_png_is_rejected(self):
        data = png()
        data = data[:33] + chunk(b"acTL", struct.pack(">II", 2, 0)) + data[33:]
        with self.assertRaisesRegex(BUNDLE.AssetBundleError, "Animated"):
            self.collect(data)

    def test_license_and_source_url_are_required(self):
        for url in ("javascript:alert(1)", "https://user:secret@example.com/a", "https://example.com/\nsecret"):
            with self.subTest(url=url), self.assertRaises(BUNDLE.AssetBundleError):
                BUNDLE.collect_asset_bytes(png(), self.root, source_url=url, license_info="CC0-1.0")
        with self.assertRaises(BUNDLE.AssetBundleError):
            BUNDLE.collect_asset_bytes(png(), self.root, source_url="https://example.com", license_info={})

    def test_embed_uses_verified_data_uri_and_visible_escaped_credits(self):
        manifest = self.collect(original_filename='<hero&".png')
        item = manifest["assets"][0]
        output = BUNDLE.embed_assets('<html><body><img src="asset://' + item["assetId"] + '"></body></html>', self.root / "manifest.json")
        self.assertIn("data:image/png;base64,", output)
        self.assertNotIn("asset://", output)
        self.assertIn('data-material-asset-credits="true"', output)
        self.assertIn("CC0-1.0", output)
        self.assertIn("Example Author", output)
        self.assertIn("&lt;hero&amp;&quot;.png", output)
        self.assertNotIn("href=", output)
        self.assertNotIn("target=", output)
        self.assertNotIn("position:fixed", output)
        self.assertLess(output.index("material-assets-credits:end"), output.index("</body>"))

    def test_embed_only_referenced_assets_and_does_nothing_without_reference(self):
        self.collect(png(1))
        manifest = self.collect(png(2))
        used = manifest["assets"][0]
        html = '<img src="asset://' + used["assetId"] + '">'
        output = BUNDLE.embed_assets(html, self.root / "manifest.json")
        self.assertEqual(output.count("data:image/png;base64,"), 1)
        self.assertEqual(BUNDLE.embed_assets("<body>no sprites</body>", self.root / "manifest.json"), "<body>no sprites</body>")

    def test_embed_rejects_unknown_id_and_tampering(self):
        manifest = self.collect()
        with self.assertRaisesRegex(BUNDLE.AssetBundleError, "unknown"):
            BUNDLE.embed_assets('<img src="asset://png-000000000000000000000000">', self.root / "manifest.json")
        item = manifest["assets"][0]
        (self.root / item["path"]).write_bytes(png(99))
        with self.assertRaisesRegex(BUNDLE.AssetBundleError, "checksum"):
            BUNDLE.embed_assets('<img src="asset://' + item["assetId"] + '">', self.root / "manifest.json")

    def test_embed_rejects_malformed_residual_references(self):
        manifest = self.collect()
        identifier = manifest["assets"][0]["assetId"]
        for html in ('<img src="asset://">', '<img src="asset://' + identifier + '/other">', '<img src="asset://' + identifier + '"><img src="asset://bad/path">'):
            with self.subTest(html=html), self.assertRaises(BUNDLE.AssetBundleError):
                BUNDLE.embed_assets(html, self.root / "manifest.json")

    def test_embed_enforces_utf8_output_size(self):
        manifest = self.collect()
        identifier = manifest["assets"][0]["assetId"]
        with self.assertRaises(BUNDLE.AssetBundleError):
            BUNDLE.embed_assets('<img src="asset://' + identifier + '">', self.root / "manifest.json", limits={"max_html_bytes": 100})
        with self.assertRaises(BUNDLE.AssetBundleError):
            BUNDLE.embed_assets("汉" * 100001, self.root / "manifest.json")
        repeated = ('<img src="asset://' + identifier + '">') * 20
        self.assertLess(len(repeated.encode("utf-8")), 2000)
        with self.assertRaisesRegex(BUNDLE.AssetBundleError, "Embedded images"):
            BUNDLE.embed_assets(repeated, self.root / "manifest.json", limits={"max_html_bytes": 2000})

    def test_embed_result_passes_main_harness_static_rules(self):
        spec = importlib.util.spec_from_file_location("bundle_harness_validation", ROOT / "game_harness/validation.py")
        validator = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(validator)
        except ImportError:
            self.skipTest("Requires optional Playwright harness dependencies")
        manifest = self.collect()
        identifier = manifest["assets"][0]["assetId"]
        html = ('<html><head><meta charset="utf-8"></head><body><main data-testid="game-root">小游戏'
                '<span data-testid="score">0</span><img src="asset://' + identifier + '"></main>'
                '<button data-action="start">开始</button><button data-action="pause">暂停</button>'
                '<button data-action="resume">继续</button><button data-action="restart">重开</button>'
                '<script>window.__GAME__={state:"ready",score:0,ticks:0};</script></body></html>')
        output = BUNDLE.embed_assets(html, self.root / "manifest.json")
        self.assertEqual(validator.inspect_html(output), [])

    def test_embed_rejects_manifest_traversal_absolute_paths_and_forged_dimensions(self):
        original = self.collect()
        identifier = original["assets"][0]["assetId"]
        for mutation in ({"path": "../outside.png"}, {"path": "C:/outside.png"}, {"path": "/outside.png"}, {"width": 200}):
            manifest = json.loads(json.dumps(original))
            manifest["assets"][0].update(mutation)
            (self.root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.subTest(mutation=mutation), self.assertRaises(BUNDLE.AssetBundleError):
                BUNDLE.embed_assets('<img src="asset://' + identifier + '">', self.root / "manifest.json")

    def test_existing_content_addressed_files_must_be_unchanged(self):
        manifest = self.collect()
        item = manifest["assets"][0]
        (self.root / item["path"]).write_bytes(b"not a PNG")
        with self.assertRaises(BUNDLE.AssetBundleError):
            self.collect()

    def test_asset_directory_symlink_is_rejected_when_available(self):
        self.root.mkdir()
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        try:
            (self.root / "assets").symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            self.skipTest("Symbolic links require permission on this host")
        with self.assertRaises(BUNDLE.AssetBundleError):
            self.collect()
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
