"""Local, metadata-only discovery for the WebMCP v0.2 material contract.

This catalog is independent of the art-file Catalog. It returns discovered
source references, never verified implementations. Source URIs are data: this
module does not fetch them, read source paths, or execute retrieved content.
"""

import asyncio
import copy
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


_KINDS = frozenset({"game_repository", "source_code", "algorithm_documentation", "reference_media"})
_ENGINES = frozenset({"html", "phaser", "babylon"})
_LANGUAGES = frozenset({"typescript", "javascript"})
_INPUT_MODES = frozenset({"keyboard", "pointer", "touch"})
_PURPOSES = frozenset({"archetype", "mechanic"})
_MAX_CATALOG_BYTES = 8 * 1024 * 1024
_DISCOVERED_NOTE = (
    "Discovered source reference only; behavior, dependencies, browser execution, "
    "and license applicability require downstream review. Coverage hints are unverified claims."
)
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can",
    "compatible", "could", "find", "for", "from", "has", "have", "in", "into", "is",
    "it", "its", "of", "or", "return", "search", "several", "should", "that", "the",
    "their", "these", "this", "those", "to", "use", "used", "using", "was", "were",
    "which", "will", "with", "without", "would", "you", "your", "please",
    "game", "games", "source", "code", "implementation", "implementations", "implement",
    "reference", "references", "material", "materials", "repository", "repositories",
    "open", "available", "web", "browser", "platform", "engine", "phaser", "babylon",
    "html", "javascript", "typescript", "js", "ts", "2d", "3d", "mechanic", "archetype",
    "algorithm", "algorithms", "documentation", "document", "documents", "example", "examples",
})
_CHINESE_STOPWORDS = re.compile("|".join(sorted((
    "请帮我", "请查找", "帮我", "查找", "搜索", "返回", "若有", "需要", "可以", "以及", "或者",
    "一个", "一些", "相关", "用于", "使用", "支持", "素材", "资源", "游戏", "代码", "源码",
    "实现", "开源", "参考", "兼容", "网页", "浏览器", "机制", "原型", "文档", "算法",
), key=lambda item: (-len(item), item))))
_SYNONYMS = (
    ("pathfinding", "寻路", "路径规划", "astar", "a*", "a star", "pathfinder"),
    ("waypoint", "路点", "路径点", "路径", "waypoints", "path", "paths"),
    ("movement", "移动", "行进", "运动", "move", "moving", "motion", "traversal"),
    ("survivors", "幸存者", "幸存", "survivor", "survival"),
    # Spawning an item or XP gem is not an enemy-wave mechanic. Require an
    # explicit wave term or enemy context before adding this domain concept.
    ("wave", "波次", "刷怪", "敌人生成", "怪物生成", "waves", "enemy spawn",
     "enemy spawns", "enemy spawning", "enemy spawner", "spawn enemy", "spawn enemies",
     "spawning enemies"),
    ("combat", "战斗", "攻击", "作战", "attack", "attacks", "fighting", "fight"),
    ("target", "目标", "索敌", "瞄准", "targets", "targeting", "aim", "aiming"),
    ("economy", "经济", "商店", "金币", "economic", "currency", "coins", "shop"),
    ("towerdefense", "塔防", "tower defense", "tower-defense"),
    ("projectile", "弹丸", "子弹", "弹幕", "projectiles", "bullet", "bullets"),
    ("collision", "碰撞", "碰撞检测", "collisions", "collide"),
    ("touchcontrols", "触控", "触摸", "移动端", "手机", "touch", "mobile", "touchscreen"),
    ("time", "时间步", "帧率无关", "delta time", "elapsed time", "timestep", "delta"),
    ("enemy", "敌人", "怪物", "enemies", "monster", "monsters"),
    ("upgrade", "升级", "进阶", "upgrades", "upgrading", "levelup"),
)
# Enemy and time describe many unrelated mechanics. Movement is similarly
# broad. Specific, explicitly named domains can constrain relevance instead
# of allowing those background matches to fill a small candidate limit.
_TOPIC_FAMILIES = {
    "navigation": frozenset({"concept:waypoint", "concept:pathfinding"}),
    "wave": frozenset({"concept:wave"}),
    "combat": frozenset({"concept:combat"}),
    "target": frozenset({"concept:target"}),
    "economy": frozenset({"concept:economy"}),
    "survivors": frozenset({"concept:survivors"}),
    "towerdefense": frozenset({"concept:towerdefense"}),
    "projectile": frozenset({"concept:projectile"}),
    "collision": frozenset({"concept:collision"}),
    "touchcontrols": frozenset({"concept:touchcontrols"}),
    "upgrade": frozenset({"concept:upgrade"}),
}


def _text(value, label, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError("{} must be {}text".format(label, "" if empty else "nonempty "))
    return value


def _object(value, fields, label, *, optional=()):
    if not isinstance(value, dict):
        raise ValueError("{} must be an object".format(label))
    if set(value) - set(fields) - set(optional) or set(fields) - set(value):
        raise ValueError("{} has missing or unsupported fields".format(label))
    return value


def _strings(value, label, *, choices=None, nonempty=False, unique=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError("{} must be a{} list".format(label, " nonempty" if nonempty else ""))
    for item in value:
        _text(item, label + " item")
        if choices is not None and item not in choices:
            raise ValueError("{} contains an unsupported value".format(label))
    if unique and len(value) != len(set(value)):
        raise ValueError("{} must not contain duplicates".format(label))
    return value


def _uri(value, label):
    """Check an HTTP(S) reference's structure without contacting its host."""
    _text(value, label)
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or any(character.isspace() or ord(character) < 32 for character in value)
                or "\\" in value):
            raise ValueError
        # Accessing port also checks malformed values and invalid IPv6 syntax.
        parts.port
    except ValueError:
        raise ValueError("{} must be an absolute HTTP(S) URI without credentials".format(label)) from None
    return value


def _candidate(value):
    _object(value, {"candidateId", "kind", "title", "summary", "source", "coverageHints", "license", "evidence", "limitations"}, "candidate")
    for key in ("candidateId", "title", "summary"):
        _text(value[key], "candidate." + key)
    if not isinstance(value["kind"], str) or value["kind"] not in _KINDS:
        raise ValueError("candidate.kind is unsupported")
    source = _object(value["source"], {"uri", "repositoryUrl", "revision", "paths", "symbols"}, "candidate.source")
    _uri(source["uri"], "candidate.source.uri")
    if source["repositoryUrl"] is not None:
        _uri(source["repositoryUrl"], "candidate.source.repositoryUrl")
    if source["revision"] is not None:
        _text(source["revision"], "candidate.source.revision")
    for key in ("paths", "symbols"):
        _strings(source[key], "candidate.source." + key)
    for key in ("coverageHints", "limitations"):
        _strings(value[key], "candidate." + key)
    license_evidence = value["license"]
    if license_evidence is not None:
        _object(license_evidence, {"identifier", "sourceUri", "scope"}, "candidate.license")
        if license_evidence["identifier"] is not None:
            _text(license_evidence["identifier"], "candidate.license.identifier")
        _uri(license_evidence["sourceUri"], "candidate.license.sourceUri")
        if not isinstance(license_evidence["scope"], str) or license_evidence["scope"] not in {"source_code", "assets", "mixed", "unknown"}:
            raise ValueError("candidate.license.scope is unsupported")
    if not isinstance(value["evidence"], list):
        raise ValueError("candidate.evidence must be a list")
    for evidence in value["evidence"]:
        _object(evidence, {"description", "sourceUri"}, "candidate.evidence item")
        _text(evidence["description"], "candidate.evidence.description")
        _uri(evidence["sourceUri"], "candidate.evidence.sourceUri")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Catalog JSON contains a duplicate object key")
        result[key] = value
    return result


def _normalize(text):
    # Split source symbols before case folding: moveToTarget -> move To Target.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return unicodedata.normalize("NFKC", text).casefold()


def _tokens(text):
    normalized = _CHINESE_STOPWORDS.sub(" ", _normalize(text))
    normalized = re.sub(r"\b(?:source|file)\s+paths?\b", " ", normalized)
    expanded = set()
    # 手机/移动端 describe an input device, not character movement.
    lexical_text = normalized.replace("移动端", "手机")
    for part in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", lexical_text):
        if part not in _STOPWORDS:
            expanded.add(part)
        if re.search(r"[\u3400-\u9fff]", part) and len(part) > 2:
            expanded.update(part[index:index + 2] for index in range(len(part) - 1))
    for group in _SYNONYMS:
        found = False
        for term in group:
            if re.search(r"[\u3400-\u9fff]", term):
                found = term in lexical_text
            else:
                found = re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", lexical_text) is not None
            if found:
                expanded.add("concept:" + group[0])
                break
    return expanded - _STOPWORDS


def _cancelled(cancel_event):
    if cancel_event is not None:
        if not callable(getattr(cancel_event, "is_set", None)):
            raise ValueError("cancel_event must provide is_set()")
        if cancel_event.is_set():
            raise asyncio.CancelledError()


def _topics(terms):
    return {topic for topic, concepts in _TOPIC_FAMILIES.items() if terms.intersection(concepts)}


def _query_topics(request, texts):
    if request["purpose"] != "mechanic":
        return set()
    # Recognize only whole dictionary terms in an ID; UUIDs and arbitrary
    # identifiers supply no topic. A specific name such as enemy-waypoint-
    # movement is a useful intent hint, while "movement" alone is too broad.
    mechanic_id = request.get("mechanicId")
    if isinstance(mechanic_id, str):
        explicit = _topics(_tokens(re.sub(r"[-_/]+", " ", mechanic_id)))
        if explicit:
            return explicit
    # "source paths" refers to file locations, not a navigation requirement.
    context = re.sub(r"\b(?:source|file)\s+paths?\b", " ", " ".join(texts), flags=re.IGNORECASE)
    return _topics(_tokens(context))


class LocalReferenceProvider:
    """Discover source/code/document references in a curated local catalog.

    ``materials.json`` contains explicit provenance and optional matching hints.
    There is no network fallback and no implicit mock mode. An existing empty
    directory is a valid, empty real catalog. This is a stateless provider;
    run-wide discussion budgets and request idempotency belong to the caller.
    """

    name = "local-reference-catalog"

    def __init__(self, catalog_dir):
        self.catalog_dir = Path(catalog_dir).expanduser().resolve()
        if not self.catalog_dir.is_dir():
            raise ValueError("Reference catalog directory does not exist")
        path = self.catalog_dir / "materials.json"
        self.is_mock = False
        self._records = []
        self._documents = {}
        self._frequencies = Counter()
        self._missing_manifest = not path.exists()
        if path.exists():
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(self.catalog_dir)
                if not resolved.is_file() or resolved.stat().st_size > _MAX_CATALOG_BYTES:
                    raise ValueError("Reference catalog must be a file no larger than 8 MiB")
                document = json.loads(resolved.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_object)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("Cannot read materials.json: {}".format(exc)) from exc
            _object(document, {"schemaVersion", "isMock", "materials"}, "reference catalog")
            if document["schemaVersion"] != "0.2" or not isinstance(document["isMock"], bool):
                raise ValueError("Reference catalog requires schemaVersion 0.2 and boolean isMock")
            if not isinstance(document["materials"], list):
                raise ValueError("Reference catalog materials must be a list")
            self.is_mock = document["isMock"]
            seen = {}
            for record in document["materials"]:
                _object(record, {"candidate", "searchText", "tags", "engines", "languages", "inputModes"}, "material record", optional={"purposes"})
                candidate = _candidate(record["candidate"])
                _text(record["searchText"], "material.searchText", empty=True)
                _strings(record["tags"], "material.tags")
                for key, choices in (("engines", _ENGINES), ("languages", _LANGUAGES), ("inputModes", _INPUT_MODES), ("purposes", _PURPOSES)):
                    if key in record:
                        _strings(record[key], "material." + key, choices=choices, nonempty=key == "purposes", unique=True)
                candidate_id = candidate["candidateId"]
                if candidate_id in seen:
                    if record != seen[candidate_id]:
                        raise ValueError("Conflicting duplicate candidateId in reference catalog")
                    continue
                seen[candidate_id] = record
                self._records.append(copy.deepcopy(record))
        for record in self._records:
            candidate = record["candidate"]
            source = candidate["source"]
            fields = (
                (candidate["title"], 4.0), (candidate["summary"], 2.0),
                (" ".join(record["tags"]), 5.0), (record["searchText"], 1.0),
                (" ".join(candidate["coverageHints"]), 1.0),
                (" ".join(source["paths"] + source["symbols"]), 2.0),
            )
            terms = Counter()
            for text, weight in fields:
                for term in _tokens(text):
                    terms[term] += weight
            self._documents[candidate["candidateId"]] = terms
            self._frequencies.update(terms.keys())
        lengths = [sum(terms.values()) for terms in self._documents.values()]
        self._average_length = (sum(lengths) / len(lengths) if lengths else 0) or 1.0

    @property
    def count(self):
        return len(self._records)

    def search(self, request, *, cancel_event=None):
        """Return only positive-evidence candidates in the official v0.2 shape."""
        _cancelled(cancel_event)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        if request.get("contractVersion") != "0.2" or not isinstance(request.get("purpose"), str) or request["purpose"] not in _PURPOSES:
            raise ValueError("Reference provider requires a v0.2 archetype or mechanic request")
        description = _text(request.get("description"), "request.description")
        kinds = _strings(request.get("materialKinds"), "request.materialKinds", choices=_KINDS, nonempty=True, unique=True)
        limit = request.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("request.limit must be an integer from 1 to 10")
        constraints = request.get("constraints")
        if not isinstance(constraints, dict) or constraints.get("platform") != "web":
            raise ValueError("request.constraints.platform must be web")
        engine, language = constraints.get("engine"), constraints.get("language")
        if engine is not None and (not isinstance(engine, str) or engine not in _ENGINES):
            raise ValueError("request.constraints.engine is unsupported")
        if not isinstance(language, str) or language not in _LANGUAGES | {"any"}:
            raise ValueError("request.constraints.language is unsupported")
        modes = _strings(constraints.get("inputModes"), "request.constraints.inputModes", choices=_INPUT_MODES, nonempty=True, unique=True)
        texts = [description]
        if request["purpose"] == "mechanic":
            texts.extend(_strings(request.get("requiredBehavior"), "request.requiredBehavior", nonempty=True))
        discussion = request.get("discussion", {})
        if not isinstance(discussion, dict):
            raise ValueError("request.discussion must be an object")
        follow_up = discussion.get("followUp")
        if follow_up is not None:
            if not isinstance(follow_up, dict):
                raise ValueError("request.discussion.followUp must be an object or null")
            texts.append(_text(follow_up.get("message"), "request.discussion.followUp.message"))
        images = request.get("images", [])
        if not isinstance(images, list):
            raise ValueError("request.images must be a list")
        query_terms = _tokens(" ".join(texts))
        requested_topics = _query_topics(request, texts)
        limitations = [
            "Discovery searched a local metadata catalog with {} references; remote source content was not fetched or executed.".format(self.count),
            _DISCOVERED_NOTE,
        ]
        if self._missing_manifest:
            limitations.append("No materials.json is configured in the reference catalog directory.")
        if self.is_mock:
            limitations.append("The configured catalog is explicitly marked isMock; candidates cannot satisfy production evidence requirements.")
        if images:
            limitations.append("Image references were not resolved or visually searched by this local provider; only textual request context was searched.")
        if constraints.get("engineVersion"):
            limitations.append("Requested engine version compatibility has not been validated.")
        if requested_topics:
            limitations.append(
                "Relevance was narrowed by explicit mechanic topic hints ({}); topic matching does not verify required behavior.".format(", ".join(sorted(requested_topics)))
            )
        ranked = []
        for record in self._records:
            _cancelled(cancel_event)
            candidate = record["candidate"]
            if candidate["kind"] not in kinds or request["purpose"] not in record.get("purposes", _PURPOSES):
                continue
            if engine is not None and record["engines"] and engine not in record["engines"]:
                continue
            if language != "any" and record["languages"] and language not in record["languages"]:
                continue
            terms = self._documents[candidate["candidateId"]]
            if requested_topics and not requested_topics.intersection(_topics(set(terms))):
                continue
            length = sum(terms.values())
            normalization = 1.2 * (0.25 + 0.75 * length / self._average_length)
            score = 0.0
            for term in query_terms:
                frequency = terms.get(term, 0)
                if frequency:
                    document_frequency = self._frequencies[term]
                    inverse = math.log(1 + (self.count - document_frequency + 0.5) / (document_frequency + 0.5))
                    score += inverse * frequency * 2.2 / (frequency + normalization)
            if score > 0:
                ranked.append((score, candidate["candidateId"], record))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        candidates, identities = [], set()
        for _, _, record in ranked:
            _cancelled(cancel_event)
            candidate = copy.deepcopy(record["candidate"])
            source = candidate["source"]
            identity = (candidate["kind"], source["uri"], source["repositoryUrl"], source["revision"], tuple(sorted(source["paths"])), tuple(sorted(source["symbols"])))
            if identity in identities:
                continue
            identities.add(identity)
            if _DISCOVERED_NOTE not in candidate["limitations"]:
                candidate["limitations"].append(_DISCOVERED_NOTE)
            missing_modes = sorted(set(modes) - set(record["inputModes"]))
            if missing_modes:
                candidate["limitations"].append("Input support is undeclared for {}; no runtime compatibility is established.".format(", ".join(missing_modes)))
            if constraints.get("engineVersion"):
                candidate["limitations"].append("Compatibility with engine version '{}' is unverified.".format(constraints["engineVersion"]))
            if source["revision"] is None:
                candidate["limitations"].append("No immutable source revision is recorded.")
            if candidate["license"] is None:
                candidate["limitations"].append("License evidence is unknown; reuse permissions require review.")
            candidates.append(candidate)
            if len(candidates) == limit:
                break
        _cancelled(cancel_event)
        if not candidates:
            limitations.append("No positive-evidence reference matched the description and hard filters.")
        return {"candidates": candidates, "limitations": limitations, "partial": bool(images), "isMock": self.is_mock}

    def __call__(self, request, *, cancel_event=None):
        return self.search(request, cancel_event=cancel_event)
