"""Deterministic weighted BM25 with optional, explicitly reported embeddings."""

import math
import re
import unicodedata
from collections import Counter

from .catalog import canonical_format
from .embeddings import SQLiteEmbeddingCache, cosine_similarity


_SYNONYMS = (
    ("暂停", "pause", "paused"),
    ("血量", "生命", "爱心", "health", "heart", "hearts", "hp"),
    ("金币", "硬币", "coin", "coins", "gold"),
    ("射击", "开火", "fire", "shoot", "shooting"),
    ("跳跃", "跳起", "jump", "jumping"),
    ("加速", "氮气", "boost", "nitro", "accelerate"),
    ("按钮", "button", "buttons"),
    ("图标", "icon", "icons"),
    ("角色", "人物", "character", "player"),
    ("跑步", "奔跑", "run", "running"),
    ("攻击", "attack"),
    ("待机", "idle"),
)
_WEIGHTS = {"name": 4.0, "tags": 5.0, "animations": 2.0, "description": 1.0, "style": 0.7, "pack": 0.4, "type": 0.7, "id": 0.3}
_FILTERS = frozenset({"type", "formats", "styles", "packs", "licenses", "required_animations", "max_bytes", "max_triangles", "static_only"})
# Retain these in the index for metadata inspection, but they do not establish
# the requested visual/action semantics by themselves. Otherwise "zebra button"
# can rank an unrelated pause button at a misleading normalized score of 1.0.
_QUERY_STOPWORDS = frozenset({
    "button", "buttons", "icon", "icons", "ui", "hud", "asset", "assets",
    "game", "games", "按钮", "图标", "素材", "资源", "游戏",
    # Function words alone cannot establish which resource is requested.
    # Keep state/action words such as "on", "off", "up", and "down": they
    # distinguish sound toggles, directions, and other meaningful variants.
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "for", "from", "has", "have", "in", "is", "it", "its", "of", "or",
    "that", "the", "their", "these", "this", "those", "to", "was", "were",
    "with",
})
_CHINESE_QUERY_STOPWORDS = re.compile("|".join(sorted(
    (word for word in _QUERY_STOPWORDS if re.search(r"[\u3400-\u9fff]", word)),
    key=lambda word: (-len(word), word),
)))


def normalize(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


def tokenize(text):
    """Small bilingual dictionary plus Chinese bigrams; no NLP dependency."""
    normalized = normalize(text)
    tokens = []
    for part in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", normalized):
        tokens.append(part)
        if re.search(r"[\u3400-\u9fff]", part) and len(part) > 2:
            tokens.extend(part[index:index + 2] for index in range(len(part) - 1))
    expanded = set(tokens)
    for group in _SYNONYMS:
        if any(term in expanded or (re.search(r"[\u3400-\u9fff]", term) and term in normalized) for term in group):
            expanded.update(group)
    return sorted(expanded)


def _as_set(value, field):
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set, frozenset)) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("Filter {} must contain nonempty strings".format(field))
    return {canonical_format(normalize(item)) if field == "formats" else normalize(item) for item in value}


class SearchEngine:
    def __init__(self, catalog, embedding_provider=None, cache_path=None):
        self.catalog = catalog
        self.embedding_provider = embedding_provider
        self.cache = SQLiteEmbeddingCache(cache_path or ":memory:") if embedding_provider is not None else None
        self.mode = "lexical"
        self.report = {"mode": self.mode, "embedding_used": False, "embedding_success": False, "embedding_warning": None, "embedding_model": None, "cache_hits": 0, "embedded_texts": 0}
        self._documents = {}
        self._semantic_texts = {}
        self._frequencies = Counter()
        for asset in catalog.assets:
            terms = Counter()
            semantic = []
            for field, weight in _WEIGHTS.items():
                value = asset.get(field, "")
                text = " ".join(value) if isinstance(value, list) else str(value)
                semantic.append("{}: {}".format(field, text))
                for token in tokenize(text):
                    terms[token] += weight
            self._documents[asset["id"]] = terms
            self._semantic_texts[asset["id"]] = "\n".join(semantic)
            self._frequencies.update(terms.keys())
        lengths = [sum(terms.values()) for terms in self._documents.values()]
        self._average_length = sum(lengths) / len(lengths) if lengths else 1.0

    def _compile_filters(self, filters):
        if filters is None:
            filters = {}
        if not isinstance(filters, dict):
            raise ValueError("filters must be a dictionary")
        unknown = set(filters) - _FILTERS
        if unknown:
            raise ValueError("Unknown filters: {}".format(", ".join(sorted(unknown))))
        compiled = {}
        for key, value in filters.items():
            if key == "static_only":
                if not isinstance(value, bool):
                    raise ValueError("Filter static_only must be a boolean")
                compiled[key] = value
            elif key in ("max_bytes", "max_triangles"):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("Filter {} must be a nonnegative integer".format(key))
                compiled[key] = value
            else:
                compiled[key] = _as_set(value, key)
        return compiled

    def _matches(self, asset, filters):
        for field, key in (("type", "type"), ("format", "formats"), ("style", "styles"), ("pack", "packs"), ("license", "licenses")):
            accepted = filters.get(key)
            if accepted is None:
                continue
            value = normalize(asset[field])
            if key == "formats":
                value = canonical_format(value)
            if key == "styles" and value == "generic":
                continue
            if value not in accepted:
                return False
        required = filters.get("required_animations")
        available = {normalize(animation) for animation in asset.get("animations", [])}
        if filters.get("static_only") and available:
            return False
        if required is not None and not required.issubset(available):
            return False
        if "max_triangles" in filters:
            triangles = asset.get("triangles", 0 if asset["type"] != "model" else None)
            if triangles is None or triangles > filters["max_triangles"]:
                return False
        try:
            path = self.catalog.resolve_path(asset)
            if "max_bytes" in filters and path.stat().st_size > filters["max_bytes"]:
                return False
        except (ValueError, OSError):
            return False
        return True

    def _bm25(self, asset, query_tokens):
        terms = self._documents[asset["id"]]
        length = sum(terms.values())
        normalization = 1.2 * (0.25 + 0.75 * length / (self._average_length or 1.0))
        score = 0.0
        total = len(self._documents)
        matched = []
        for token in query_tokens:
            frequency = terms.get(token, 0)
            if not frequency:
                continue
            document_frequency = self._frequencies[token]
            inverse = math.log(1 + (total - document_frequency + 0.5) / (document_frequency + 0.5))
            score += inverse * frequency * 2.2 / (frequency + normalization)
            matched.append(token)
        return score, matched

    def search(self, query, filters=None, top_k=5):
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 0:
            raise ValueError("top_k must be a nonnegative integer")
        compiled = self._compile_filters(filters)
        candidates = sorted((asset for asset in self.catalog.assets if self._matches(asset, compiled)), key=lambda asset: asset["id"])
        self.mode = "lexical"
        self.report = {"mode": self.mode, "embedding_used": False, "embedding_success": False, "embedding_warning": None, "embedding_model": None, "cache_hits": 0, "embedded_texts": 0, "candidate_count": len(candidates), "scoring": "weighted BM25 normalized within candidates"}
        if not candidates or top_k == 0:
            return []
        # Chinese UI labels commonly have no spaces (e.g. 按钮图标素材).
        # Strip generic label components before forming bigrams so boundary
        # bigrams cannot accidentally restore an otherwise empty query.
        semantic_query = _CHINESE_QUERY_STOPWORDS.sub(" ", normalize(query))
        query_tokens = [token for token in tokenize(semantic_query) if token not in _QUERY_STOPWORDS]
        lexical = [self._bm25(asset, query_tokens) for asset in candidates]
        maximum = max((item[0] for item in lexical), default=0.0)
        normalized_lexical = [item[0] / maximum if maximum else 0.0 for item in lexical]
        semantic = None
        if self.embedding_provider is not None and query_tokens:
            try:
                texts = [query] + [self._semantic_texts[asset["id"]] for asset in candidates]
                vectors = self.cache.embed(self.embedding_provider, texts)
                semantic = [cosine_similarity(vectors[0], vector) for vector in vectors[1:]]
                self.mode = "hybrid"
                self.report.update(self.cache.last_report)
                self.report.update({"mode": self.mode, "embedding_used": True, "embedding_success": True, "embedding_model": self.embedding_provider.model_id, "scoring": "0.65 normalized BM25 + 0.35 max(0, cosine)"})
            except Exception as exc:
                self.mode = "lexical_fallback"
                warning = "Embedding unavailable: {}".format(str(exc))
                self.report.update({"mode": self.mode, "warning": warning, "embedding_warning": warning, "embedding_model": getattr(self.embedding_provider, "model_id", None)})
        results = []
        for index, asset in enumerate(candidates):
            lexical_score = normalized_lexical[index]
            semantic_score = semantic[index] if semantic is not None else None
            score = 0.65 * lexical_score + 0.35 * max(0.0, semantic_score) if semantic is not None else lexical_score
            reasons = ["Matched terms: {}".format(", ".join(lexical[index][1][:12]))] if lexical[index][1] else ["No lexical match"]
            if semantic_score is not None:
                reasons.append("Provider cosine similarity: {:.4f}".format(semantic_score))
            if compiled:
                reasons.append("Passed all hard filters")
            results.append({"asset": asset, "score": float(score), "lexical_score": float(lexical_score), "semantic_score": float(semantic_score) if semantic_score is not None else None, "reasons": reasons})
        results.sort(key=lambda item: (-item["score"], -item["lexical_score"], item["asset"]["id"]))
        return results[:top_k]

    def close(self):
        if self.cache is not None:
            self.cache.close()
