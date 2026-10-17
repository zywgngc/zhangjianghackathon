"""Public-provider art discovery and byte delivery, with no real network calls.

The HTML fixtures model provider fields rather than a private retrieval index.
Image and archive fixtures are valid, tiny PNG/ZIP files built with stdlib.
"""

import asyncio
import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import random
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
import zipfile
import zlib

try:
    import httpx
    import jsonschema  # noqa: F401 -- optional harness dependencies
except ImportError:
    httpx = None


ROOT = Path(__file__).resolve().parents[1]
MODULE_FILE = ROOT / "game_harness/material_portal.py"
SCHEMA_FILE = ROOT / "asset_agent/specs/material-portal-0.2.schema.json"


def png_bytes(width=2, height=3, *, noise=False):
    """Create a valid RGBA PNG, including CRCs, without image dependencies."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    rng = random.Random(23)
    rows = []
    for _ in range(height):
        pixels = (bytes(rng.randrange(256) for _ in range(width * 4)) if noise
                  else bytes((37, 147, 61, 255)) * width)
        rows.append(b"\x00" + pixels)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def png_archive(files=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in (files or {"PNG/forest_hero.png": png_bytes()}).items():
            archive.writestr(name, data)
        archive.writestr("License.txt", "CC0 1.0 Universal. Test fixture artwork.")
    return output.getvalue()


class PublicArtFixture:
    """A provider-shaped HTTP fixture that records all outbound requests."""

    kenney_page = "https://kenney.nl/assets/forest-test-pack"
    kenney_file = "https://kenney.nl/media/pages/assets/forest-test-pack/123/forest-pack.zip"
    oga_page = "https://opengameart.org/content/forest-hero-test"
    oga_file = "https://opengameart.org/sites/default/files/forest-hero.png"

    def __init__(self, *, kenney=False, oga=True, licensed=True,
                 kenney_data=None, oga_data=None, redirect=None, oga_category="2d"):
        self.kenney, self.oga, self.licensed = kenney, oga, licensed
        self.oga_category = oga_category
        self.kenney_data = kenney_data if kenney_data is not None else png_archive()
        self.oga_data = oga_data if oga_data is not None else png_bytes()
        self.redirect = redirect
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        uri, path = str(request.url), request.url.path
        if request.url.host in {"kenney.nl", "www.kenney.nl"} and path == "/assets/category:2D":
            text = ('<h2><a href="/assets/forest-test-pack">Forest Test Pack</a></h2>'
                    if self.kenney else "<h1>Assets</h1>")
            return httpx.Response(200, text=text)
        if request.url.host == "opengameart.org" and path == "/art-search-advanced":
            # Model the public provider's art-type filter. Unfiltered requests
            # can return concept art or 3D entries with PNG previews as well.
            category_matches = (request.url.params.get("field_art_type_tid[0]") != "9"
                                or self.oga_category == "2d")
            text = ('<div class="art-preview-title"><a href="/content/forest-hero-test">Forest Hero</a></div>'
                    if self.oga and category_matches else "<h1>No artwork found</h1>")
            return httpx.Response(200, text=text)
        if uri == self.kenney_page:
            license_link = ('<a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0</a>'
                            if self.licensed else "<p>License information unavailable</p>")
            return httpx.Response(200, text=("<h1>Forest Test Pack</h1>" + license_link
                + '<a id="donate-text" href="' + self.kenney_file + '">Download</a>'))
        if uri == self.oga_page:
            license_link = ('<a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0</a>'
                            if self.licensed else "<span>Unknown</span>")
            return httpx.Response(200, text=('<h1>Forest Hero</h1>'
                '<a class="username" href="/users/test-author">Test Author</a>'
                '<div class="field-name-body">Top-down forest hero and trees.</div>'
                '<div class="field-name-field-art-licenses">' + license_link + '</div>'
                '<div class="field-name-field-art-files"><a download href="'
                + self.oga_file + '">forest-hero.png</a></div>'))
        if uri in {self.kenney_file, self.oga_file}:
            if self.redirect:
                return httpx.Response(302, headers={"Location": self.redirect})
            data = self.kenney_data if uri == self.kenney_file else self.oga_data
            return httpx.Response(200, content=data)
        return httpx.Response(404, text="Unexpected URL in offline test")

    @property
    def searches(self):
        return [r for r in self.requests if r.url.path in {"/assets/category:2D", "/art-search-advanced"}]

    @property
    def downloads(self):
        return [r for r in self.requests if str(r.url) in {self.kenney_file, self.oga_file}]


@unittest.skipUnless(httpx is not None, "Optional httpx/jsonschema dependencies are not installed")
class HarnessWebArtTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        name = "web_material_portal_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(name, MODULE_FILE)
        cls.portal_module = importlib.util.module_from_spec(spec)
        sys.modules[name] = cls.portal_module
        spec.loader.exec_module(cls.portal_module)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop(cls.portal_module.__name__, None)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.settings = SimpleNamespace(
            data_dir=Path(self.temporary.name) / "state", portal_url="web://assets",
            portal_token="portal-token-must-not-leave-the-machine",
            api_key="model-key-must-not-leave-the-machine", portal_schema=SCHEMA_FILE,
        )

    def client(self, handler):
        return self.portal_module.MaterialPortalClient(self.settings, transport=httpx.MockTransport(handler))

    def request(self, client, *, description="forest hero sprite", request_id="web-art-request", engine="html"):
        request = client.request_for(description, 4, "web-art-run")
        request["requestId"] = request_id
        request["constraints"]["engine"] = engine
        request["materialKinds"] = ["reference_media"]
        return request

    async def test_live_query_pages_and_downloads_are_anonymous_and_correlated(self):
        fixture = PublicArtFixture(kenney=True, oga=True)
        portal = self.client(fixture)
        request = self.request(portal)
        original = copy.deepcopy(request)
        result = await portal.search_request(request)
        portal.validate_result(request, result)
        self.assertEqual(request, original)
        self.assertFalse(result["isMock"])
        self.assertEqual(result["requestId"], request["requestId"])
        self.assertEqual({c["source"]["uri"] for c in result["candidates"]},
                         {fixture.kenney_page, fixture.oga_page})
        self.assertTrue(fixture.downloads, "Search must deliver bytes, not only scrape source links")
        search_hosts = {r.url.host for r in fixture.searches}
        self.assertEqual(search_hosts, {"kenney.nl", "opengameart.org"})
        for outbound in fixture.requests:
            self.assertEqual(outbound.method, "GET")
            self.assertEqual(outbound.url.scheme, "https")
            self.assertNotIn("authorization", outbound.headers)
            self.assertNotIn("cookie", outbound.headers)
            self.assertNotIn("x-api-key", outbound.headers)
            self.assertNotIn("idempotency-key", outbound.headers)
            self.assertNotIn(self.settings.portal_token, str(outbound.url))
            self.assertNotIn(self.settings.api_key, str(outbound.url))
        for candidate in result["candidates"]:
            self.assertEqual(candidate["kind"], "reference_media")
            self.assertEqual(candidate["license"]["identifier"], "CC0-1.0")
            self.assertEqual(candidate["license"]["scope"], "assets")
            self.assertTrue(candidate["source"]["revision"].startswith("sha256:"))
            self.assertTrue(candidate["evidence"])
        encoded = json.dumps(result)
        self.assertNotIn(self.settings.portal_token, encoded)
        self.assertNotIn(self.settings.api_key, encoded)

    async def test_search_parameters_change_with_requested_art_instead_of_fixed_catalog(self):
        fixture = PublicArtFixture(oga=False)
        portal = self.client(fixture)
        for i, description in enumerate(("forest sprite", "racing vehicle sprite")):
            await portal.search_request(self.request(portal, description=description, request_id="topic-" + str(i)))
        kenney_queries = {r.url.params["search"] for r in fixture.searches if r.url.host == "kenney.nl"}
        oga_queries = {r.url.params["keys"] for r in fixture.searches if r.url.host == "opengameart.org"}
        self.assertIn("nature", kenney_queries)
        self.assertIn("racing", kenney_queries)
        self.assertIn("forest", oga_queries)
        self.assertIn("racing", oga_queries)
        self.assertNotIn("forest sprite", kenney_queries)

    async def test_oga_search_filters_to_2d_and_excludes_concept_or_3d_entries(self):
        for category in ("2d", "concept", "3d"):
            with self.subTest(category=category):
                self.settings.data_dir = Path(self.temporary.name) / category
                fixture = PublicArtFixture(oga_category=category)
                portal = self.client(fixture)
                result = await portal.search_request(self.request(portal, request_id="category-" + category))
                oga_searches = [r for r in fixture.searches if r.url.host == "opengameart.org"]
                self.assertTrue(oga_searches)
                for request in oga_searches:
                    self.assertEqual(request.url.params.get("field_art_type_tid[0]"), "9")
                if category == "2d":
                    self.assertTrue(result["candidates"])
                    self.assertTrue(fixture.downloads)
                else:
                    self.assertEqual(result["candidates"], [])
                    self.assertEqual(fixture.downloads, [])
                    self.assertEqual(result["status"], "partial")

    async def test_broader_pack_query_is_disclosed_with_original_art_need(self):
        fixture = PublicArtFixture(kenney=True, oga=False)
        portal = self.client(fixture)
        result = await portal.search_request(self.request(portal, description="forest sprite"))
        self.assertTrue(result["candidates"])
        candidate = result["candidates"][0]
        limitations = " ".join(candidate["limitations"]).casefold()
        self.assertIn("forest", limitations)
        self.assertIn("nature", limitations)
        self.assertIn("not established", limitations)

    async def test_collected_png_is_real_local_file_with_matching_hash_and_dimensions(self):
        fixture = PublicArtFixture()
        portal = self.client(fixture)
        handoff = await portal.collect_assets(self.request(portal))
        self.assertTrue(handoff["manifests"])
        self.assertTrue(handoff["assets"])
        self.assertEqual(len(handoff["result"]["candidates"]), 1)
        asset = handoff["assets"][0]
        actual = Path(asset["absolutePath"]).read_bytes()
        self.assertEqual(actual, fixture.oga_data)
        self.assertEqual(asset["sha256"], hashlib.sha256(actual).hexdigest())
        self.assertEqual((asset["width"], asset["height"]), (2, 3))
        self.assertEqual(asset["mimeType"], "image/png")
        self.assertEqual(asset["license"]["scope"], "assets")
        for manifest_file in handoff["manifests"]:
            manifest = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
            self.assertTrue(manifest["assets"])
            self.assertTrue((Path(manifest_file).parent / "source-page.html").is_file())
        # A repeated request must validate delivery without downloading again.
        before = len(fixture.requests)
        second = await portal.collect_assets(self.request(portal))
        self.assertEqual(second["assets"][0]["sha256"], asset["sha256"])
        self.assertEqual(len(fixture.requests), before)

    async def test_same_art_is_eligible_for_html_and_phaser_without_code_filtering(self):
        fixture = PublicArtFixture()
        portal = self.client(fixture)
        candidates = []
        for engine in ("html", "phaser"):
            result = await portal.search_request(self.request(portal, engine=engine, request_id="engine-" + engine))
            self.assertTrue(result["candidates"])
            candidates.append({c["candidateId"] for c in result["candidates"]})
            self.assertTrue(all(c["kind"] == "reference_media" for c in result["candidates"]))
        self.assertEqual(candidates[0], candidates[1])

    async def test_mechanic_request_can_search_stage_art_without_claiming_behavior_coverage(self):
        fixture = PublicArtFixture()
        portal = self.client(fixture)
        request = self.request(portal)
        request.update(purpose="mechanic", mechanicId="forest-player", requiredBehavior=["show a forest hero sprite"])
        result = await portal.search_request(request)
        portal.validate_result(request, result)
        self.assertTrue(result["candidates"])
        for candidate in result["candidates"]:
            self.assertEqual(candidate["kind"], "reference_media")
            self.assertIn("not gameplay/code evidence", " ".join(candidate["limitations"]))

    async def test_non_art_request_makes_no_network_calls(self):
        def forbidden(request):
            self.fail("A request excluding reference_media made a network call")
        portal = self.client(forbidden)
        request = self.request(portal, description="2048 sliding numbers source code")
        request["materialKinds"] = ["source_code", "game_repository"]
        result = await portal.search_request(request)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["limitations"])

    async def test_missing_asset_license_prevents_every_download(self):
        fixture = PublicArtFixture(kenney=True, licensed=False)
        portal = self.client(fixture)
        result = await portal.search_request(self.request(portal))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(fixture.downloads, [])
        self.assertTrue(any("Skipped art candidate" in note for note in result["limitations"]))

    async def test_unrelated_footer_cc0_link_is_not_asset_license_evidence_on_oga(self):
        fixture = PublicArtFixture(licensed=False)

        def handler(request):
            response = fixture(request)
            if str(request.url) == fixture.oga_page:
                body = response.text + '<footer><a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0</a></footer>'
                return httpx.Response(200, text=body)
            return response

        portal = self.client(handler)
        result = await portal.search_request(self.request(portal))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(fixture.downloads, [])

    async def test_kenney_license_url_must_be_the_real_creative_commons_host(self):
        fixture = PublicArtFixture(kenney=True, oga=False)

        def handler(request):
            response = fixture(request)
            if str(request.url) == fixture.kenney_page:
                return httpx.Response(200, text=response.text.replace(
                    "https://creativecommons.org/publicdomain/zero/1.0/",
                    "https://not-creativecommons.org/publicdomain/zero/1.0/"))
            return response

        portal = self.client(handler)
        result = await portal.search_request(self.request(portal))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(fixture.downloads, [])

    async def test_oversized_download_cannot_become_successful_candidate(self):
        fixture = PublicArtFixture(oga_data=b"x" * 8_000_001)
        portal = self.client(fixture)
        result = await portal.search_request(self.request(portal))
        self.assertTrue(fixture.downloads)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(list(self.settings.data_dir.rglob("manifest.json")), [])

    async def test_redirect_to_unapproved_host_is_rejected_before_request(self):
        fixture = PublicArtFixture(redirect="https://unapproved.example/forest.png")
        portal = self.client(fixture)
        result = await portal.search_request(self.request(portal))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(fixture.downloads)
        self.assertNotIn("unapproved.example", {r.url.host for r in fixture.requests})

    async def test_png_extension_without_valid_image_bytes_is_not_delivered(self):
        fixture = PublicArtFixture(oga_data=b"<html>not an image</html>")
        portal = self.client(fixture)
        result = await portal.search_request(self.request(portal))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(list(self.settings.data_dir.rglob("manifest.json")), [])

    async def test_downloaded_zip_with_parent_paths_is_rejected_without_extraction(self):
        archive = png_archive({"../../escaped.png": png_bytes()})
        fixture = PublicArtFixture(kenney=True, oga=False, kenney_data=archive)
        portal = self.client(fixture)
        result = await portal.search_request(self.request(portal))
        self.assertTrue(fixture.downloads)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(list(Path(self.temporary.name).rglob("escaped.png")), [])
        self.assertEqual(list(self.settings.data_dir.rglob("manifest.json")), [])

    async def test_preview_and_sample_only_archives_are_not_delivered_as_game_art(self):
        for label, names in (
                ("files", ("Preview.png", "Sample.png")),
                ("directories", ("Preview/scene.png", "Sample/scene.png")),
                ("numbered_files", ("Sample1.png", "Preview2.png")),
                ("plural_directories", ("Previews/env-preview-big.png", "Samples/scene.png"))):
            with self.subTest(archive_shape=label):
                self.settings.data_dir = Path(self.temporary.name) / label
                data = png_archive({name: png_bytes(i + 2, 3) for i, name in enumerate(names)})
                fixture = PublicArtFixture(kenney=True, oga=False, kenney_data=data)
                portal = self.client(fixture)
                result = await portal.search_request(self.request(portal, request_id="preview-only-" + label))
                self.assertTrue(fixture.downloads)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["candidates"], [])
                self.assertEqual(list(self.settings.data_dir.rglob("manifest.json")), [])

    async def test_appledouble_pseudo_png_does_not_prevent_valid_png_delivery(self):
        original = png_bytes(5, 4)
        data = png_archive({
            "PNG/forest_hero.png": original,
            "PNG/._forest_hero.png": b"\x00\x05\x16\x07AppleDouble metadata, not a PNG",
            "__MACOSX/PNG/._forest_hero.png": b"AppleDouble resource fork",
        })
        fixture = PublicArtFixture(kenney=True, oga=False, kenney_data=data)
        portal = self.client(fixture)
        handoff = await portal.collect_assets(self.request(portal))
        self.assertTrue(handoff["result"]["candidates"])
        self.assertEqual(len(handoff["assets"]), 1)
        self.assertEqual(handoff["assets"][0]["originalFilename"], "PNG/forest_hero.png")
        self.assertEqual(Path(handoff["assets"][0]["absolutePath"]).read_bytes(), original)

    async def test_candidate_title_excludes_later_donation_modal_headings(self):
        fixture = PublicArtFixture(kenney=True, oga=False)

        def handler(request):
            response = fixture(request)
            if str(request.url) == fixture.kenney_page:
                return httpx.Response(200, text=response.text
                    + '<div class="modal"><h1>Support Kenney</h1><h1>Download Complete</h1></div>')
            return response

        portal = self.client(handler)
        handoff = await portal.collect_assets(self.request(portal))
        self.assertTrue(handoff["result"]["candidates"])
        self.assertEqual(handoff["result"]["candidates"][0]["title"], "Forest Test Pack")
        for manifest_path in handoff["manifests"]:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(manifest["title"], "Forest Test Pack")

    async def test_image_input_is_disclosed_as_partial_and_never_fetched(self):
        fixture = PublicArtFixture()
        portal = self.client(fixture)
        request = self.request(portal)
        request["images"] = [{"uri": "https://private.example/layout.png", "mimeType": "image/png", "description": "forest hero"}]
        result = await portal.search_request(request)
        self.assertTrue(result["candidates"])
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any("not downloaded/analyzed" in text for text in result["limitations"]))
        self.assertNotIn("private.example", {r.url.host for r in fixture.requests})

    async def test_provider_timeouts_return_explicit_partial_with_no_fake_local_hits(self):
        calls = []

        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout("Synthetic source timeout", request=request)

        portal = self.client(timeout)
        result = await portal.search_request(self.request(portal))
        self.assertTrue(calls)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["candidates"], [])
        self.assertFalse(result["isMock"])
        self.assertTrue(any("ReadTimeout" in note for note in result["limitations"]))
        self.assertTrue(all("2048" not in c["title"] for c in result["candidates"]))

    async def test_low_level_timeout_is_not_transformed_into_image_bytes(self):
        def timeout(request):
            raise httpx.ReadTimeout("Synthetic timeout", request=request)
        collector = self.portal_module._WebAssetCollector(self.settings, httpx.MockTransport(timeout))
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout), trust_env=False) as client:
            with self.assertRaises(httpx.ReadTimeout):
                await collector._get(client, PublicArtFixture.oga_file)

    async def test_cancellation_propagates_and_does_not_cache_a_success(self):
        entered = asyncio.Event()
        unblock = asyncio.Event()

        async def blocked(request):
            entered.set()
            await unblock.wait()
            return httpx.Response(200, text="No results")

        portal = self.client(blocked)
        request = self.request(portal)
        task = asyncio.create_task(portal.search_request(request))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        # Anonymous GET work may be safely repeated, unlike uncertain paid POSTs.
        fixture = PublicArtFixture(oga=False)
        retry = self.client(fixture)
        result = await retry.search_request(request)
        self.assertTrue(fixture.searches)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["candidates"], [])

    async def test_no_provider_hits_never_fills_slots_with_old_2048_references(self):
        fixture = PublicArtFixture(oga=False)
        portal = self.client(fixture)
        result = await portal.search("2048 forest character artwork", run_id="no-art-found")
        self.assertTrue(fixture.searches)
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["trace"]["result"]["candidates"], [])
        self.assertFalse(result["trace"]["result"]["isMock"])

    async def test_cached_manifest_with_modified_image_is_not_claimed_as_deliverable(self):
        fixture = PublicArtFixture()
        portal = self.client(fixture)
        request = self.request(portal)
        handoff = await portal.collect_assets(request)
        self.assertTrue(handoff["assets"])
        Path(handoff["assets"][0]["absolutePath"]).write_bytes(b"corrupted image bytes")
        second = await portal.collect_assets(request)
        self.assertEqual(second["assets"], [])
        self.assertTrue(any("unavailable" in note.casefold() for note in second["limitations"]))

    async def test_rag_delivers_bounded_actual_data_uri_and_manifest_for_remaining_art(self):
        files = {"PNG/forest_small_" + str(i) + ".png": png_bytes(i + 2, 3) for i in range(4)}
        files["PNG/forest_large.png"] = png_bytes(40, 40, noise=True)
        self.assertGreater(len(files["PNG/forest_large.png"]), 2500)
        fixture = PublicArtFixture(kenney=True, oga=False, kenney_data=png_archive(files))
        portal = self.client(fixture)
        result = await portal.search("forest artwork", run_id="rag-art-handoff")
        self.assertTrue(result["documents"])
        document = result["documents"][0]
        self.assertTrue(document["assets"])
        self.assertLessEqual(len(document["assets"]), 2)
        self.assertTrue(Path(document["asset_manifest"]).is_file())
        manifest = json.loads(Path(document["asset_manifest"]).read_text(encoding="utf-8"))
        self.assertGreater(len(manifest["assets"]), len(document["assets"]))
        total = 0
        for asset in document["assets"]:
            prefix, data = asset["dataUri"].split(",", 1)
            self.assertEqual(prefix, "data:image/png;base64")
            decoded = base64.b64decode(data, validate=True)
            self.assertLessEqual(len(decoded), 2500)
            self.assertEqual(asset["sha256"], hashlib.sha256(decoded).hexdigest())
            self.assertEqual(decoded[:8], b"\x89PNG\r\n\x1a\n")
            total += len(decoded)
        self.assertLessEqual(total, 6000)


if __name__ == "__main__":
    unittest.main()
