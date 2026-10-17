"""Offline tests. Fake embeddings validate integration, not real model quality."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from asset_agent.catalog import Catalog
from asset_agent.embeddings import (
    EmbeddingError, HTTPEmbeddingProvider, SQLiteEmbeddingCache,
    cosine_similarity, validate_vectors,
)
from asset_agent.retrieval import SearchEngine


def record(asset_id, **overrides):
    asset = {
        "id": asset_id, "type": "icon", "name": asset_id,
        "description": "A game asset", "tags": [], "style": "pixel",
        "pack": "starter", "path": asset_id + ".svg", "format": "svg",
        "license": "CC0-1.0", "author": "Test author",
        "source_url": "https://example.invalid/source",
        "license_url": "https://example.invalid/license",
    }
    asset.update(overrides)
    if "format" in overrides and "path" not in overrides:
        asset["path"] = asset_id + "." + overrides["format"]
    return asset


class CatalogFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "catalog"
        self.root.mkdir()

    def catalog(self, assets, schema="1.0", create_files=True):
        (self.root / "catalog.json").write_text(
            json.dumps({"schema_version": schema, "assets": assets}), encoding="utf-8"
        )
        if create_files:
            for asset in assets:
                path = self.root / asset["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test-asset")
        return Catalog(self.root)


class CatalogTests(CatalogFixture):
    def test_lookup_and_schema_validation(self):
        catalog = self.catalog([record("heart")])
        self.assertEqual(catalog.get("heart")["name"], "heart")
        self.assertIsNone(catalog.get("unknown"))
        self.assertEqual(catalog.validate_files(), [])
        with self.assertRaises(ValueError):
            self.catalog([record("x"), record("x")])
        with self.assertRaises(ValueError):
            self.catalog([], schema="2.0")
        with self.assertRaises(ValueError):
            self.catalog([record("x", tags="invalid")])
        with self.assertRaises(ValueError):
            self.catalog([record("x", type="unknown")])

    def test_paths_are_portably_confined_and_files_required(self):
        catalog = self.catalog([record("heart")])
        for unsafe in ("../outside.svg", "nested/../../outside.svg", "C:\\outside.svg", "C:outside.svg", "/outside.svg", "\\\\server\\share\\outside.svg", "heart.svg:secret", ".", "missing.svg"):
            with self.subTest(path=unsafe), self.assertRaises(ValueError):
                catalog.resolve_path({"path": unsafe})
        self.assertEqual(catalog.resolve_path(record("heart")), self.root / "heart.svg")
        (self.root / "heart.svg").unlink()
        self.assertEqual(catalog.validate_files()[0]["asset_id"], "heart")

    def test_symlinks_cannot_escape(self):
        outside = Path(self.temporary.name) / "outside.svg"
        outside.write_bytes(b"outside")
        catalog = self.catalog([record("link")], create_files=False)
        try:
            (self.root / "link.svg").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Creating symlinks is not permitted on this host")
        with self.assertRaises(ValueError):
            catalog.resolve_path(catalog.assets[0])

    def test_format_suffix_and_type_are_checked(self):
        invalid = [
            record("script", path="script.html", format="html"),
            record("mismatch", path="mismatch.html", format="svg"),
            record("fake-model", path="fake-model.png", type="model", format="png"),
            record("fake-icon", path="fake-icon.mp3", format="mp3"),
            record("vector-tiles", path="vector-tiles.svg", type="tileset"),
        ]
        for asset in invalid:
            with self.subTest(asset=asset["id"]), self.assertRaises(ValueError):
                self.catalog([asset])
        valid = [record("photo", path="photo.JPEG", format="jpg"),
                 record("vector", type="sprite"),
                 record("tiles", type="tileset", format="png"),
                 record("model", type="model", format="glb")]
        self.assertEqual(len(self.catalog(valid).assets), 4)

    def test_optional_checksum_reports_changed_asset(self):
        asset = record("checksum", sha256=hashlib.sha256(b"test-asset").hexdigest())
        catalog = self.catalog([asset])
        self.assertEqual(catalog.validate_files(), [])
        catalog.resolve_path(asset).write_bytes(b"modified")
        self.assertIn("SHA-256", catalog.validate_files()[0]["reason"])
        with self.assertRaises(ValueError):
            self.catalog([record("bad-hash", sha256="not-a-digest")])


class RetrievalTests(CatalogFixture):
    def test_chinese_english_synonyms_and_offline_reporting(self):
        catalog = self.catalog([
            record("pause", name="Pause icon", tags=["pause", "button"]),
            record("heart", name="Health heart", tags=["health"]),
            record("coin", name="Gold coin", tags=["coin"]),
        ])
        engine = SearchEngine(catalog)
        for query, expected in (("暂停按钮", "pause"), ("血量", "heart"), ("金币", "coin")):
            with self.subTest(query=query):
                self.assertEqual(engine.search(query)[0]["asset"]["id"], expected)
        self.assertEqual(engine.mode, "lexical")
        self.assertFalse(engine.report["embedding_used"])
        self.assertTrue(all(result["semantic_score"] is None for result in engine.search("金币")))

    def test_filters_are_hard_and_generic_style_is_accepted(self):
        catalog = self.catalog([
            record("match", name="Run", type="model", format="glb", animations=["idle", "run"], triangles=100),
            record("wrong-pack", name="Run", type="model", format="glb", animations=["run"], triangles=100, pack="other"),
            record("heavy", name="Run", type="model", format="glb", animations=["run"], triangles=1000),
            record("unknown-size", name="Run", type="model", format="glb", animations=["run"]),
            record("generic", name="Run", type="model", format="glb", animations=["run"], triangles=50, style="generic"),
            record("wrong-style", name="Run", type="model", format="glb", animations=["run"], triangles=50, style="realistic"),
            record("icon", name="Run", tags=["run"]),
        ])
        engine = SearchEngine(catalog)
        filters = {"type": "model", "formats": [".GLB"], "styles": ["pixel"], "packs": ["starter"], "licenses": ["CC0-1.0"], "required_animations": ["RUN"], "max_bytes": 10, "max_triangles": 200}
        self.assertEqual({result["asset"]["id"] for result in engine.search("run", filters, 20)}, {"match", "generic"})
        self.assertEqual(engine.search("run", {"max_bytes": 0}), [])
        self.assertEqual(engine.search("run", {"licenses": ["MIT"]}), [])
        with self.assertRaises(ValueError):
            engine.search("run", {"license": "MIT"})
        with self.assertRaises(ValueError):
            engine.search("run", {"max_triangles": -1})

    def test_format_filter_accepts_jpeg_alias(self):
        catalog = self.catalog([record("photo", path="photo.jpeg", format="jpeg")])
        self.assertEqual(SearchEngine(catalog).search("photo", {"formats": [".JPG"]})[0]["asset"]["id"], "photo")

    def test_deterministic_zero_results_and_bad_files_excluded(self):
        catalog = self.catalog([record("z"), record("a"), record("missing")])
        (self.root / "missing.svg").unlink()
        engine = SearchEngine(catalog)
        results = engine.search("unmatchedword")
        self.assertEqual([result["asset"]["id"] for result in results], ["a", "z"])
        self.assertEqual([result["score"] for result in results], [0.0, 0.0])
        self.assertEqual(engine.search("anything", top_k=0), [])

    def test_generic_query_words_do_not_select_an_unrelated_action(self):
        catalog = self.catalog([
            record("pause", name="Pause button icon", tags=["pause", "ui", "button"]),
            record("heart", name="Health button icon", tags=["health", "hud", "icon"]),
        ])
        engine = SearchEngine(catalog)
        for query in ("zebra exit button", "button icon ui hud asset", "按钮 图标 素材", "按钮图标素材", "未知按钮"):
            with self.subTest(query=query):
                results = engine.search(query)
                self.assertTrue(results)
                self.assertTrue(all(result["score"] == 0.0 for result in results))
        self.assertEqual(engine.search("暂停按钮")[0]["asset"]["id"], "pause")
        self.assertEqual(engine.search("血量图标")[0]["asset"]["id"], "heart")

    def test_english_function_words_cannot_establish_an_unrelated_match(self):
        catalog = self.catalog([
            record("settings-knobs", name="Settings knobs", description="Configuration and settings"),
            record("speedometer", name="Speedometer", description="Speed and acceleration gauge"),
            record("coins", name="Gold coins", description="Collectibles and currency"),
        ])
        engine = SearchEngine(catalog)
        for query in (
            "third person bicycle riding animation and IK pedals",
            "bicycle with pedals",
            "a bicycle for the rider",
            "and or the a an of for to with in is are",
        ):
            with self.subTest(query=query):
                results = engine.search(query)
                self.assertEqual(len(results), 3)
                self.assertTrue(all(result["lexical_score"] == 0.0 for result in results))
                self.assertTrue(all(result["score"] == 0.0 for result in results))
                self.assertTrue(all(result["reasons"] == ["No lexical match"] for result in results))

    def test_function_words_do_not_remove_requested_actions_or_states(self):
        catalog = self.catalog([
            record("pause", name="Pause", tags=["pause", "resume"]),
            record("heart", name="Heart", tags=["heart", "health"]),
            record("sound-on", name="Sound on", tags=["sound", "on"]),
            record("sound-off", name="Sound off", tags=["sound", "off"]),
        ])
        engine = SearchEngine(catalog)
        for query, expected in (
            ("pause and resume", "pause"),
            ("heart with health", "heart"),
            ("sound on", "sound-on"),
            ("sound off", "sound-off"),
            ("暂停按钮", "pause"),
            ("血量图标", "heart"),
        ):
            with self.subTest(query=query):
                result = engine.search(query)[0]
                self.assertEqual(result["asset"]["id"], expected)
                self.assertGreater(result["score"], 0.0)

    def test_generic_only_query_skips_optional_embeddings(self):
        class UnusedProvider:
            model_id = "must-not-run"

            def __init__(self):
                self.calls = 0

            def embed(self, texts):
                self.calls += 1
                raise AssertionError("Generic query must not invoke embeddings")

        provider = UnusedProvider()
        engine = SearchEngine(self.catalog([record("pause")]), provider)
        self.addCleanup(engine.close)
        for query in ("按钮图标素材 icon UI HUD", "and or the a an of for to with in is are"):
            with self.subTest(query=query):
                self.assertEqual(engine.search(query)[0]["score"], 0.0)
                self.assertEqual(engine.mode, "lexical")
                self.assertFalse(engine.report["embedding_success"])
                self.assertIsNone(engine.report["embedding_warning"])
        self.assertEqual(provider.calls, 0)

    def test_fake_provider_fusion_and_cache_reuse(self):
        class FakeProvider:
            model_id = "fake-test-only"

            def __init__(self):
                self.calls = 0

            def embed(self, texts):
                self.calls += 1
                return [[1.0, 0.0] if text == "survival" or "id: heart" in text.splitlines() else [0.0, 1.0] for text in texts]

        catalog = self.catalog([record("heart"), record("coin")])
        provider = FakeProvider()
        engine = SearchEngine(catalog, provider, Path(self.temporary.name) / "vectors.sqlite")
        self.addCleanup(engine.close)
        self.assertEqual(engine.search("survival")[0]["asset"]["id"], "heart")
        self.assertEqual(engine.mode, "hybrid")
        self.assertTrue(engine.report["embedding_success"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(engine.search("survival")[0]["semantic_score"], 1.0)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(engine.report["cache_hits"], 3)

    def test_invalid_provider_falls_back_without_claiming_embeddings(self):
        class InvalidProvider:
            model_id = "invalid"

            def embed(self, texts):
                return [[float("nan")]] * len(texts)

        engine = SearchEngine(self.catalog([record("pause")]), InvalidProvider())
        self.addCleanup(engine.close)
        results = engine.search("暂停")
        self.assertEqual(engine.mode, "lexical_fallback")
        self.assertFalse(engine.report["embedding_success"])
        self.assertIn("finite", engine.report["embedding_warning"])
        self.assertIsNone(results[0]["semantic_score"])


class EmbeddingTests(unittest.TestCase):
    def test_finite_dimensions_and_stable_cosine(self):
        for invalid in ([[float("nan")]], [[float("inf")]], [[True]], [[1], [1, 2]], [[]]):
            with self.subTest(vectors=invalid), self.assertRaises(EmbeddingError):
                validate_vectors(invalid, len(invalid))
        self.assertAlmostEqual(cosine_similarity([1e308, 1e308], [1e308, 1e308]), 1.0)
        self.assertEqual(cosine_similarity([0, 0], [1, 0]), 0.0)

    def test_cache_separates_model_spaces_and_validates_dimensions(self):
        class FakeProvider:
            def __init__(self, model_id, dimension):
                self.model_id, self.dimension, self.calls = model_id, dimension, 0

            def embed(self, texts):
                self.calls += 1
                return [[1.0] * self.dimension for _ in texts]

        cache = SQLiteEmbeddingCache()
        self.addCleanup(cache.close)
        first = FakeProvider("one", 2)
        second = FakeProvider("two", 3)
        self.assertEqual(len(cache.embed(first, ["same"])[0]), 2)
        self.assertEqual(len(cache.embed(second, ["same"])[0]), 3)
        self.assertEqual(second.calls, 1)
        first.dimension = 4
        with self.assertRaises(EmbeddingError):
            cache.embed(first, ["new-text"])

    def test_http_protocol_using_mock_response_only(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, size):
                return json.dumps({"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}).encode()

        provider = HTTPEmbeddingProvider("https://example.invalid/v1", "test-model", "test-key")
        with patch("asset_agent.embeddings.urlopen", return_value=Response()) as request:
            self.assertEqual(provider.embed(["first", "second"]), [[1.0, 0.0], [0.0, 1.0]])
        sent = request.call_args[0][0]
        self.assertEqual(sent.full_url, "https://example.invalid/v1/embeddings")
        self.assertEqual(json.loads(sent.data)["input"], ["first", "second"])
        self.assertEqual(sent.get_header("Authorization"), "Bearer test-key")

    def test_http_base_url_rejects_userinfo_without_echoing_secrets(self):
        urls = [
            "https://sensitive-user@example.invalid/v1",
            "https://user:sensitive-password@example.invalid/v1",
            "https://:sensitive-password@example.invalid/v1",
            "https://@example.invalid/v1",
        ]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                HTTPEmbeddingProvider(url, "model")
            self.assertNotIn("sensitive", str(caught.exception))
            self.assertNotIn("example.invalid", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
