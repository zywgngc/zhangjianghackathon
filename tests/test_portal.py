"""MaterialPortal contract and grounded local art retrieval tests."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from asset_agent.portal import MaterialPortal


def record(asset_id, *, asset_type="icon", format="svg", **overrides):
    value = {
        "id": asset_id, "type": asset_type, "name": asset_id,
        "description": "Pause and resume", "tags": ["pause"],
        "style": "generic", "pack": "tests", "path": asset_id + "." + format,
        "format": format, "license": "CC0-1.0", "author": "Test author",
        "source_url": "https://example.invalid/" + asset_id,
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    }
    value.update(overrides)
    return value


def request(description="Pause icon", **overrides):
    value = {
        "purpose": "mechanic", "description": description,
        "constraints": {"engine": "Phaser", "platform": "web", "language": "TypeScript"},
        "limit": 5,
    }
    value.update(overrides)
    return value


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "catalog"
        self.root.mkdir()

    def portal(self, records, **kwargs):
        for asset in records:
            path = self.root / asset["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("real file for " + asset["id"]).encode("utf-8"))
        (self.root / "catalog.json").write_text(json.dumps({"schema_version": "1.0", "assets": records}), encoding="utf-8")
        return MaterialPortal(self.root, **kwargs)

    def test_returns_verified_local_file_and_provenance_without_mutating_request(self):
        portal = self.portal([record("pause")])
        original = request(imageRefs=["https://example.invalid/reference.png"])
        before = copy.deepcopy(original)
        result = portal.search_sync(original)
        self.assertEqual(original, before)
        self.assertEqual(set(result), {"candidates", "limitations"})
        candidate = result["candidates"][0]
        self.assertEqual(set(candidate), {"id", "title", "description", "assetType", "format", "score", "reasons", "resource", "source"})
        resource = candidate["resource"]
        self.assertEqual(resource["kind"], "local_file")
        self.assertTrue(Path(resource["path"]).is_absolute())
        data = Path(resource["path"]).read_bytes()
        self.assertEqual(resource["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(resource["bytes"], len(data))
        self.assertEqual(candidate["source"]["license"], "CC0-1.0")
        self.assertEqual(candidate["source"]["author"], "Test author")
        self.assertGreater(candidate["score"], 0)
        limitations = " ".join(result["limitations"])
        self.assertIn("1 records (icon=1)", limitations)
        self.assertIn("imageRefs were not opened", limitations)
        self.assertIn("offline text BM25", limitations)
        self.assertIn("compatibility have not been tested", limitations)
        self.assertIn("does not generate, download, resize", limitations)

    def test_purpose_is_art_stage_context_and_archetype_warns_about_kit_coverage(self):
        portal = self.portal([record("pause")])
        mechanic = portal.search_sync(request())
        archetype = portal.search_sync(request(purpose="archetype"))
        self.assertEqual(mechanic["candidates"], archetype["candidates"])
        self.assertIn("not a complete", " ".join(archetype["limitations"]))

    def test_explicit_model_type_cannot_return_an_icon(self):
        portal = self.portal([record("pause", name="3D bicycle", description="A bicycle model", tags=["bicycle", "model"])])
        for description in ("3D bicycle model", "三维 bicycle", "bicycle.glb", "3D自行车 bicycle"):
            with self.subTest(description=description):
                result = portal.search_sync(request(description))
                self.assertEqual(result["candidates"], [])
                self.assertIn("type 'model'", " ".join(result["limitations"]))

    def test_type_rules_are_conservative_and_icon_has_priority(self):
        records = [
            record("target-icon", name="Target", tags=["target"]),
            record("target-model", asset_type="model", format="glb", name="Target", tags=["target"]),
            record("target-sprite", asset_type="sprite", format="png", name="Target", tags=["target"]),
            record("target-font", asset_type="font", format="woff2", name="Target", tags=["target"]),
            record("target-audio", asset_type="audio", format="wav", name="Target", tags=["target"]),
            record("target-tiles", asset_type="tileset", format="png", name="Target", tags=["target"]),
        ]
        portal = self.portal(records)
        for description, expected in (
            ("3D target icon", "icon"), ("target 图标", "icon"),
            ("target font", "font"), ("target 字体", "font"),
            ("target sound effect", "audio"), ("target 音效", "audio"),
            ("target tileset", "tileset"), ("target 图块", "tileset"),
            ("target model", "model"), ("target glTF", "model"),
            ("target 2D", "sprite"), ("target 角色动画", "sprite"),
        ):
            with self.subTest(description=description):
                candidates = portal.search_sync(request(description))["candidates"]
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["assetType"], expected)
        for description in ("target remodel iconic", "target"):
            with self.subTest(description=description):
                result = portal.search_sync(request(description, limit=30))
                self.assertEqual(len(result["candidates"]), 6)
                self.assertIn("No explicit art-resource type", " ".join(result["limitations"]))

    def test_function_words_and_absent_semantics_produce_no_candidates(self):
        portal = self.portal([record("settings", description="Configuration and settings", tags=["settings"])])
        for description in ("third person bicycle riding animation and IK pedals", "and or the with"):
            with self.subTest(description=description):
                self.assertEqual(portal.search_sync(request(description))["candidates"], [])

    def test_changed_checksum_is_skipped_and_does_not_hide_valid_lower_hit(self):
        bad = record("a-pause", sha256="0" * 64)
        portal = self.portal([bad, record("z-pause")])
        result = portal.search_sync(request(limit=1))
        self.assertEqual([item["id"] for item in result["candidates"]], ["z-pause"])
        self.assertIn("SHA-256 does not match", " ".join(result["limitations"]))
        (self.root / "z-pause.svg").unlink()
        self.assertEqual(portal.search_sync(request())["candidates"], [])

    def test_valid_uppercase_catalog_checksum_is_accepted(self):
        asset = record("pause")
        asset["sha256"] = hashlib.sha256(b"real file for pause").hexdigest().upper()
        portal = self.portal([asset])
        self.assertEqual(len(portal.search_sync(request())["candidates"]), 1)

    def test_limit_and_default_license_filter_apply(self):
        portal = self.portal([record("pause-{}".format(index)) for index in range(4)] + [record("pause-mit", license="MIT")])
        for limit in (1, 2, 30):
            with self.subTest(limit=limit):
                result = portal.search_sync(request(limit=limit))
                self.assertEqual(len(result["candidates"]), min(limit, 4))
                self.assertNotIn("pause-mit", [row["id"] for row in result["candidates"]])

    def test_input_validation(self):
        portal = self.portal([])
        invalid = [None, [], {}, request(purpose="asset"), request(description=" "), request(description=7),
                   request(imageRefs="reference.png"), request(imageRefs=[None]), request(imageRefs=[""]),
                   request(constraints={}), request(constraints={"platform": "pc"}),
                   request(constraints={"platform": "web", "engine": 1}),
                   request(constraints={"platform": "web", "language": " "})]
        invalid.extend(request(limit=value) for value in (True, 0, 31, 1.5, "2"))
        for value in invalid:
            with self.subTest(request=value), self.assertRaises(ValueError):
                portal.search_sync(value)
        self.assertEqual(portal.search_sync(request(constraints={"platform": "web"}, imageRefs=[]))["candidates"], [])

    def test_async_search_reuses_portal_concurrently_with_thread_local_sqlite(self):
        class Provider:
            model_id = "test-fixed-provider"

            def embed(self, texts):
                return [[1.0, 0.0] for _ in texts]

        portal = self.portal([record("pause")], embedding_provider=Provider(), cache_path=self.root / "cache.sqlite")

        async def run_searches():
            first = await portal.search(request())
            concurrent = await asyncio.gather(*(portal.search(request()) for _ in range(6)))
            second = await portal.search(request())
            return [first, *concurrent, second]

        results = asyncio.run(run_searches())
        self.assertEqual(len(results), 8)
        for result in results:
            self.assertEqual(result["candidates"][0]["id"], "pause")
            self.assertIn("configured text-embedding provider and BM25", " ".join(result["limitations"]))
            self.assertNotIn("failed", " ".join(result["limitations"]))

    def test_embedding_failure_reports_lexical_fallback(self):
        class Provider:
            model_id = "test-failed-provider"

            def embed(self, texts):
                raise RuntimeError("service unavailable")

        portal = self.portal([record("pause")], embedding_provider=Provider())
        result = portal.search_sync(request())
        self.assertEqual(result["candidates"][0]["id"], "pause")
        self.assertIn("fell back to BM25", " ".join(result["limitations"]))


if __name__ == "__main__":
    unittest.main()
