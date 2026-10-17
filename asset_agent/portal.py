"""MaterialPortal interface for phase-aware, local art-resource retrieval.

The phase is context for the caller, not a request for game implementation
code. This adapter neither fabricates a layout nor runs asset delivery.
"""

import asyncio
import copy
import hashlib
import re
import threading
from collections import Counter

from .catalog import Catalog
from .contracts import DEFAULT_LICENSES
from .retrieval import SearchEngine


def _nonempty_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a nonempty string".format(field))
    return value.strip()


def _validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    result = copy.deepcopy(request)
    if result.get("purpose") not in ("archetype", "mechanic"):
        raise ValueError("purpose must be archetype or mechanic")
    result["description"] = _nonempty_text(result.get("description"), "description")
    if "imageRefs" in result:
        references = result["imageRefs"]
        if not isinstance(references, list):
            raise ValueError("imageRefs must be a list of nonempty strings")
        result["imageRefs"] = [_nonempty_text(item, "imageRefs item") for item in references]
    constraints = result.get("constraints")
    if not isinstance(constraints, dict) or constraints.get("platform") != "web":
        raise ValueError("constraints.platform must be web")
    for field in ("engine", "language"):
        if field in constraints:
            constraints[field] = _nonempty_text(constraints[field], "constraints." + field)
    limit = result.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError("limit must be an integer from 1 to 30")
    return result


def _word(pattern, text):
    # ASCII boundaries allow phrases such as "3D角色" while preventing
    # "model" from matching "remodel" or "icon" from matching "iconic".
    return re.search(r"(?<![a-z0-9_])(?:" + pattern + r")(?![a-z0-9_])", text) is not None


def _infer_type(description):
    text = description.casefold()
    if "图标" in text or _word(r"icons?", text):
        return "icon"
    if "字体" in text or _word(r"fonts?", text):
        return "font"
    if any(term in text for term in ("音效", "音乐")) or _word(r"audio|sound\s+effects?|music", text):
        return "audio"
    if "图块" in text or _word(r"tilesets?|tilemaps?", text):
        return "tileset"
    if "三维" in text or _word(r"3d(?:\s+models?)?|models?|glb|gltf", text):
        return "model"
    if any(term in text for term in ("精灵", "二维", "角色动画")) or _word(r"sprites?|2d", text):
        return "sprite"
    return None


def _file_info(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"kind": "local_file", "path": str(path), "sha256": digest.hexdigest(), "bytes": size}


class ArtMaterialPortal:
    """Search real art files using the upstream MaterialPortalPort contract.

    No provider is constructed implicitly. An explicitly injected embedding
    provider may perform network requests according to that provider's policy.
    Every call owns its SearchEngine and SQLite connection, including calls
    dispatched through ``asyncio.to_thread``.
    """

    def __init__(self, catalog_dir, *, embedding_provider=None, cache_path=None):
        self.catalog = Catalog(catalog_dir)
        self.embedding_provider = embedding_provider
        self.cache_path = cache_path

    def search_sync(self, request):
        request = _validate_request(request)
        asset_type = _infer_type(request["description"])
        filters = {"licenses": list(DEFAULT_LICENSES)}
        if asset_type:
            filters["type"] = asset_type
        counts = Counter(asset["type"] for asset in self.catalog.assets)
        coverage = ", ".join("{}={}".format(kind, count) for kind, count in sorted(counts.items())) or "no asset types"
        limitations = [
            "Local catalog coverage: {} records ({}); retrieval cannot provide resources absent from this catalog.".format(len(self.catalog.assets), coverage),
            "Only catalog assets with licenses {} are eligible.".format(", ".join(DEFAULT_LICENSES)),
            "Engine, language, and browser runtime compatibility have not been tested; engine/language are context, not compatibility filters.",
            "Search returns existing local files only; it does not generate, download, resize, or build a delivery bundle.",
        ]
        if asset_type:
            limitations.append("Text rules inferred asset type '{}'; this restricts candidates and is not visual or LLM analysis.".format(asset_type))
        else:
            limitations.append("No explicit art-resource type was identified; candidates may span catalog types and do not establish full request coverage.")
        if request.get("imageRefs"):
            limitations.append("imageRefs were not opened, visually analyzed, or used in ranking; only description text was searched.")
        if request["purpose"] == "archetype":
            limitations.append("archetype candidates are individual art resources, not a complete or consistently styled art kit.")

        engine = SearchEngine(self.catalog, embedding_provider=self.embedding_provider, cache_path=self.cache_path)
        try:
            # Check every ranked hit so damaged high-ranked files do not hide a
            # valid lower-ranked candidate within the caller's requested limit.
            results = engine.search(request["description"], filters=filters, top_k=len(self.catalog.assets))
            report = copy.deepcopy(engine.report)
        finally:
            engine.close()

        if report["mode"] == "hybrid":
            limitations.append("Ranking used the explicitly configured text-embedding provider and BM25; scores are relative rankings, not match confidence or coverage guarantees.")
        else:
            limitations.append("Ranking used offline text BM25; scores are normalized relative rankings, not match confidence or coverage guarantees.")
            if report["mode"] == "lexical_fallback":
                limitations.append("The configured text-embedding provider failed; retrieval fell back to BM25.")

        candidates = []
        for result in results:
            if result["score"] <= 0:
                continue
            asset = result["asset"]
            try:
                path = self.catalog.resolve_path(asset)
                resource = _file_info(path)
                if asset.get("sha256") and resource["sha256"] != asset["sha256"].casefold():
                    raise ValueError("catalog SHA-256 does not match file")
            except (OSError, ValueError) as exc:
                limitations.append("Skipped asset '{}': {}.".format(asset["id"], str(exc)))
                continue
            candidates.append({
                "id": asset["id"],
                "title": asset["name"],
                "description": asset["description"],
                "assetType": asset["type"],
                "format": asset["format"],
                "score": result["score"],
                "reasons": copy.deepcopy(result["reasons"]),
                "resource": resource,
                "source": {
                    "url": asset["source_url"],
                    "author": asset["author"],
                    "license": asset["license"],
                    "license_url": asset["license_url"],
                },
            })
            if len(candidates) == request["limit"]:
                break
        if not candidates:
            limitations.append("No positive-score, eligible, verified local art resource matched the description.")
        return {"candidates": candidates, "limitations": limitations}

    async def search(self, request):
        return await asyncio.to_thread(self.search_sync, request)


class MaterialPortal:
    """Dispatch formal v0.2 materials and legacy art requests independently.

    Constructing the facade does not open catalogs or access network services.
    The exact contractVersion/requestId form selects research; legacy layout-
    free art requests retain their previous response shape.
    """
    def __init__(self, catalog_dir="catalogs/demo", *, embedding_provider=None,
                 cache_path=None, reference_catalog_dir="catalogs/references",
                 research_provider=None,
                 research_cache_path=".asset-agent/research-requests.sqlite3",
                 research_timeout_seconds=10, allow_mock_research=False,
                 require_research_parent=False):
        self._art_args = (catalog_dir, embedding_provider, cache_path)
        self._reference_catalog_dir = reference_catalog_dir
        self._research_options = {
            "provider": research_provider, "cache_path": research_cache_path,
            "timeout_seconds": research_timeout_seconds, "allow_mock": allow_mock_research,
            "require_parent": require_research_parent,
        }
        self._art = None
        self._research = None
        self._init_lock = threading.Lock()

    @property
    def catalog(self):
        return self._art_portal().catalog

    def _art_portal(self):
        with self._init_lock:
            if self._art is None:
                catalog_dir, provider, cache = self._art_args
                self._art = ArtMaterialPortal(catalog_dir, embedding_provider=provider, cache_path=cache)
            return self._art

    def _research_portal(self):
        with self._init_lock:
            if self._research is None:
                from .research_portal import ResearchMaterialPortal
                self._research = ResearchMaterialPortal(self._reference_catalog_dir, **self._research_options)
            return self._research

    @staticmethod
    def _is_research(request):
        return isinstance(request, dict) and ("contractVersion" in request or "requestId" in request)

    def search_sync(self, request, options=None):
        if self._is_research(request):
            return self._research_portal().search_sync(request, options)
        if options is not None:
            raise ValueError("Legacy art search does not accept research options")
        return self._art_portal().search_sync(request)

    async def search(self, request, options=None):
        if self._is_research(request):
            return await self._research_portal().search(request, options)
        if options is not None:
            raise ValueError("Legacy art search does not accept research options")
        return await self._art_portal().search(request)

    def close(self, *, wait=True):
        with self._init_lock:
            if self._research is not None:
                self._research.close(wait=wait)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
