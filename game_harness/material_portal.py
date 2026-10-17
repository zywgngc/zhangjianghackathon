"""Drop-in MaterialPortal v0.2 client, online art collector and reference index.

Keep this file at game_harness/material_portal.py in codex/game-harness.
Set the EXISTING MATERIAL_PORTAL_URL setting to ``web://assets`` for live
Kenney/OpenGameArt art search and bounded downloads, ``local://references`` for
offline code references, or to your HTTP(S) portal endpoint. Empty configuration
still disables the portal in the unmodified Retriever. No asset_agent package,
catalog directory, model key, server, or extra dependency is needed locally.

``search(query, limit, run_id)`` is the unchanged harness/RAG interface.
``search_request(request)`` also accepts complete archetype/mechanic v0.2
requests, including caller-authored follow-ups. It returns the provider result;
transport errors and invalid contracts raise rather than inventing results.

Local data below is pinned source metadata, not downloaded game/asset files.
It is searched with bilingual terms and BM25; images and source execution are
not supported. Discovered candidates always require downstream verification.
The main harness owns global discussion budgets, model selection and builds.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections import Counter
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html as _asset_html
from html.parser import HTMLParser
from io import BytesIO
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import struct
import tempfile
import time
import unicodedata
from urllib.parse import urlencode, urljoin, urlsplit
import uuid
from weakref import WeakKeyDictionary, WeakValueDictionary
import zipfile
import zlib

import httpx
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


_MAX_RESPONSE_BYTES = 1_000_000
_LOCAL_URL = "local://references"
_WEB_URL = "web://assets"
_WEB_VERSION = "online-art-v9"
_LOCAL_RELEVANCE_VERSION = "archetype-gameplay-evidence-v2"
_DISCOVERED_NOTE = (
    "Discovered source reference only; behavior, dependencies, browser execution "
    "and license applicability require downstream review. Coverage hints are unverified."
)
_ARCHETYPE_TERMS = {
    "sliding_numbers": ("2048", "数字合并", "合并相同数字", "数字滑块", "同值合并",
                        "sliding-number", "sliding number", "merge numbers", "number merging",
                        "merge equal-valued", "merge equal valued", "equal-value merging"),
    "falling_blocks": ("tetris", "俄罗斯方块", "消行", "下落方块", "方块下落", "落块",
                       "falling-block", "falling block", "falling blocks", "tetromino", "tetrominoes",
                       "row clearing", "line clearing"),
    "survivors": ("survivor", "survivors", "幸存者", "割草"),
    "tower_defense": ("tower defense", "tower-defense", "塔防"),
    "racing": ("racing", "赛车", "飞车"),
    "shooter": ("shooter", "shooting", "fps", "射击", "枪手"),
    "platformer": ("platformer", "平台跳跃", "横版跳跃"),
    "snake": ("snake", "贪吃蛇"),
    "breakout": ("breakout", "arkanoid", "打砖块"),
    "power_absorption": ("战力比较", "战力吞噬", "战力不足", "吞噬成长", "实力比较",
                         "power comparison", "combat power", "absorb weaker", "eat weaker", "consume weaker"),
}

# These describe delivery, controls, presentation or shared lifecycle behavior,
# rather than a game archetype. They may still contribute to ordinary BM25
# ranking and mechanic searches after a real gameplay-theme match exists.
_ARCHETYPE_GENERIC_TERMS = frozenset("""
    native html5 canvas css dom java script type ecma webgl webgpu api offline single file files standalone simple small
    keyboard key keys arrow arrows enter mouse pointer touch touchscreen mobile
    desktop screen viewport responsive input inputs control controls button buttons
    swipe swipes sliding slide rotate rotation move movement cursor click clicking
    start pause paused resume restart reset ready running won lost win wins lose
    loss over state states lifecycle score scores scoring point points timer time
    countdown seconds ticks frame frames loop updates update display render rendering
    realtime real elapsed delta duration animation ui interface feedback local storage
    integer integers number numbers value values condition conditions support
    原生 单文件 离线 简体 中文 画布 屏幕 界面 画面 显示 实时 整数 数字 数值
    键盘 鼠标 触摸 触屏 触控 手机 方向 方向键 光标 点击 确认 按钮 操作 控制 滑动 旋转 移动
    开始 暂停 继续 恢复 重开 重新 结束 终局 状态 计时 时间 秒数 胜利 失败 胜负
    分数 得分 计分 积分 反馈 适配 布局 可见 滚动 更新 渲染 动画
    concept:movement concept:touchcontrols concept:time concept:target concept:collision
""".split())


def _archetypes(text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return {name for name, aliases in _ARCHETYPE_TERMS.items()
            if any((alias in normalized if re.search(r"[\u3400-\u9fff]", alias)
                    else re.search(r"(?<![a-z0-9_])" + re.escape(alias) + r"(?![a-z0-9_])", normalized))
                   for alias in aliases)}


def _archetype_gameplay_terms(text):
    return {term for term in _tokens(text) - _ARCHETYPE_GENERIC_TERMS
            if re.search(r"[a-z\u3400-\u9fff]", term)
            and (len(term) >= 3 or re.search(r"[\u3400-\u9fff]", term))}


class PortalContractError(ValueError):
    """Invalid contract, conflicting idempotency key, or unresolved dispatch."""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PortalContractError("duplicate JSON object key")
        result[key] = value
    return result


def _format_checker():
    # Base jsonschema does not install its optional URI/time format packages.
    # Keep installed RFC checkers; provide explicit stdlib checks if absent.
    checker = FormatChecker()
    if "uri" not in checker.checkers:
        @checker.checks("uri", raises=ValueError)
        def uri(value):
            if not isinstance(value, str):
                return True
            if (not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value)
                    or re.search(r"[^\x21-\x7e]|[<>\"{}|\\^`]", value)
                    or re.search(r"%(?![0-9a-fA-F]{2})", value)
                    or value.count("#") > 1):
                return False
            parts = urlsplit(value)
            if parts.scheme.lower() in {"http", "https"}:
                parts.port  # Reject malformed ports/IPv6 authorities.
                return bool(parts.hostname)
            return True
    if "date-time" not in checker.checkers:
        @checker.checks("date-time", raises=ValueError)
        def date_time(value):
            if not isinstance(value, str):
                return True
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value):
                return False
            return datetime.fromisoformat(value.upper().replace("Z", "+00:00")).tzinfo is not None
    return checker


class MaterialPortalClient:
    """Compatible harness client with bounded I/O and durable request identity.

    Completed/partial/clarification responses are cached under data_dir. Failed
    dispatches are not blindly retried: a timeout/cancelled remote request may
    still be running at the provider. Reconcile it there before issuing a new
    request. Each HTTP call also carries the original Idempotency-Key. SQLite
    connections and HTTP clients are scoped to calls, with no close hook needed.
    """

    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport
        schema = json.loads(Path(settings.portal_schema).read_text(encoding="utf-8-sig"))
        Draft202012Validator.check_schema(schema)
        self.validators = {
            name: Draft202012Validator(
                {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/" + name},
                format_checker=_format_checker(),
            ) for name in ("MaterialSearchRequest", "MaterialSearchResult")
        }
        self._url = settings.portal_url or _LOCAL_URL
        if self._url not in {_LOCAL_URL, _WEB_URL}:
            parts = urlsplit(self._url)
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username is not None or parts.password is not None
                    or parts.fragment):
                raise PortalContractError("portal URL must be web://assets, local://references or an HTTP(S) endpoint")
        self._token = getattr(settings, "portal_token", "") or ""
        self._cache_path = Path(getattr(settings, "data_dir", "data")) / "material-portal.sqlite3"
        # HTTP identity must survive local catalog/schema updates: an unknown
        # paid remote job must not become eligible for another dispatch merely
        # because this file was upgraded. Local metadata lookup is side-effect
        # free and can safely use data/schema revisions as its cache boundary.
        boundary = ["material-portal-v0.2-http", self._url, _digest(self._token)]
        if self._url == _LOCAL_URL:
            boundary = ["material-portal-v0.2-local", _LOCAL_RELEVANCE_VERSION, schema, _REFERENCES]
        elif self._url == _WEB_URL:
            boundary = ["material-portal-v0.2-web", _WEB_VERSION, schema]
        self._namespace = _digest(_json(boundary))
        self._locks = WeakKeyDictionary()
        self._index = None

    def validate_request(self, request):
        try:
            self.validators["MaterialSearchRequest"].validate(request)
        except ValidationError as exc:
            raise PortalContractError("invalid request at " + "/".join(map(str, exc.absolute_path))) from None
        for image in request["images"]:
            uri = image["uri"]
            scheme = urlsplit(uri).scheme.lower()
            if not scheme or scheme == "file" or (len(scheme) == 1 and uri[1:2] == ":"):
                raise PortalContractError("image URI must be portal-accessible, not a local file path")

    def validate_result(self, request, result):
        try:
            self.validators["MaterialSearchResult"].validate(result)
        except ValidationError as exc:
            raise PortalContractError("invalid result at " + "/".join(map(str, exc.absolute_path))) from None
        if result["requestId"] != request["requestId"] or result["contractVersion"] != request["contractVersion"]:
            raise PortalContractError("request/version mismatch")
        if result["discussion"] != {k: request["discussion"][k] for k in ("runId", "threadId", "round")}:
            raise PortalContractError("discussion correlation mismatch")
        candidates = result.get("candidates", [])
        if len(candidates) > request["limit"]:
            raise PortalContractError("candidate limit exceeded")
        if len({c["candidateId"] for c in candidates}) != len(candidates):
            raise PortalContractError("duplicate candidate IDs")
        if any(c["kind"] not in request["materialKinds"] for c in candidates):
            raise PortalContractError("unrequested material kind")
        questions = result.get("questions", [])
        if len({q["questionId"] for q in questions}) != len(questions):
            raise PortalContractError("duplicate clarification question IDs")
        for question in questions:
            options = [option["optionId"] for option in question["options"]]
            if len(set(options)) != len(options):
                raise PortalContractError("duplicate clarification options")
            if not question["required"] and question["defaultOptionId"] not in options:
                raise PortalContractError("clarification default is not an option")

    def request_for(self, query, limit, run_id):
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise PortalContractError("limit must be an integer")
        request = {"contractVersion": "0.2", "requestId": "pending", "projectRevision": 0,
            "discussion": {"runId": run_id, "threadId": "archetypes", "round": 0,
                           "inReplyToRequestId": None, "followUp": None},
            "purpose": "archetype", "description": query, "images": [],
            "constraints": {"engine": "html", "engineVersion": None, "platform": "web", "language": "any",
                            "inputModes": ["keyboard", "pointer", "touch"]},
            "materialKinds": ["game_repository", "source_code", "algorithm_documentation", "reference_media"],
            "limit": min(10, max(1, limit))}
        # Preserve the upstream algorithm so existing request identities survive.
        request["requestId"] = _digest(_json(request))
        self.validate_request(request)
        return request

    def _connect(self):
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._cache_path), timeout=5)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS portal_exchanges (
                namespace TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                request_json TEXT NOT NULL, result_json TEXT, state TEXT NOT NULL,
                PRIMARY KEY(namespace, request_id))""")
            return conn
        except BaseException:
            conn.close()
            raise

    def _reserve(self, request):
        request_json = _json(request)
        fingerprint = _digest(request_json)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT fingerprint,result_json,state FROM portal_exchanges WHERE namespace=? AND request_id=?",
                (self._namespace, request["requestId"]),
            ).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise PortalContractError("requestId already belongs to different request content")
                if row[1] is not None:
                    result = json.loads(row[1])
                    self.validate_result(request, result)
                    retryable = result["status"] == "failed" and result["error"]["retryable"]
                    retry_at = float(row[2].split(":", 1)[1]) if row[2].startswith("retry_after:") else 0
                    if not retryable or time.time() < retry_at:
                        return result
                    # This is a new explicit caller invocation after a known
                    # terminal failure, not a blind retry of an unknown job.
                    conn.execute("UPDATE portal_exchanges SET result_json=NULL,state='pending' WHERE namespace=? AND request_id=?",
                                 (self._namespace, request["requestId"]))
                elif self._url not in {_LOCAL_URL, _WEB_URL}:
                    raise PortalContractError("previous remote dispatch unresolved; reconcile at provider before retrying")
                # Local metadata lookup is deterministic and has no paid job.
            else:
                conn.execute("INSERT INTO portal_exchanges VALUES(?,?,?,?,NULL,'pending')",
                             (self._namespace, request["requestId"], fingerprint, request_json))
        return None

    def _save(self, request, result=None, state="uncertain"):
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE portal_exchanges SET result_json=?,state=? WHERE namespace=? AND request_id=?",
                         (_json(result) if result is not None else None, state, self._namespace, request["requestId"]))

    def _validate_follow_up(self, request):
        discussion = request["discussion"]
        if discussion["round"] == 0:
            return True
        if discussion["inReplyToRequestId"] == request["requestId"]:
            raise PortalContractError("follow-up cannot identify itself as parent")
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT request_json,result_json FROM portal_exchanges WHERE namespace=? AND request_id=?",
                (self._namespace, discussion["inReplyToRequestId"]),
            ).fetchone()
        if not row or row[1] is None:
            # A stateless remote portal may have the parent in its own store.
            # Local results disclose that cross-record answers were not checked.
            return False
        parent, previous = json.loads(row[0]), json.loads(row[1])
        self.validate_request(parent)
        self.validate_result(parent, previous)
        old = parent["discussion"]
        if (request["requestId"] == parent["requestId"]
                or any(discussion[k] != old[k] for k in ("runId", "threadId"))
                or discussion["round"] <= old["round"]):
            raise PortalContractError("follow-up does not correlate with parent discussion")
        follow_up = discussion["followUp"]
        if follow_up["clarificationId"] is None:
            return True
        if previous["status"] != "needs_clarification" or follow_up["clarificationId"] != previous["clarificationId"]:
            raise PortalContractError("follow-up clarification does not identify parent questions")
        questions = {q["questionId"]: q for q in previous["questions"]}
        answered = set()
        for answer in follow_up["answers"]:
            qid = answer["questionId"]
            if qid in answered or qid not in questions:
                raise PortalContractError("unknown or duplicate clarification answer")
            answered.add(qid)
            question = questions[qid]
            options = {option["optionId"] for option in question["options"]}
            if answer["optionId"] is not None and answer["optionId"] not in options:
                raise PortalContractError("unknown clarification option")
            if answer["source"] == "system_default" and (
                    question["required"] or answer["text"] is not None
                    or answer["optionId"] != question["defaultOptionId"]):
                raise PortalContractError("invalid system-default clarification answer")
        if any(q["required"] and qid not in answered for qid, q in questions.items()):
            raise PortalContractError("required clarification answers are missing")
        return True

    async def _http_search(self, request):
        headers = {"Idempotency-Key": request["requestId"], "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        # Never follow redirects with credentials or retry a potentially paid job.
        async with httpx.AsyncClient(timeout=12, trust_env=False, follow_redirects=False,
                                     transport=self.transport) as client:
            async with client.stream("POST", self._url, json=request, headers=headers) as response:
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isdecimal() or int(declared) > _MAX_RESPONSE_BYTES):
                    raise PortalContractError("invalid or oversized response length")
                body = bytearray()
                # Bound decoded bytes too, including compressed/chunked responses.
                async for chunk in response.aiter_bytes(chunk_size=16384):
                    if len(body) + len(chunk) > _MAX_RESPONSE_BYTES:
                        raise PortalContractError("response too large")
                    body.extend(chunk)
        try:
            return json.loads(body, object_pairs_hook=_unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(PortalContractError("non-finite JSON number")))
        except (UnicodeError, RecursionError) as exc:
            raise PortalContractError("invalid response JSON") from exc

    async def search_request(self, request):
        """Search a complete v0.2 request without fabricating follow-up answers.

        Raises PortalContractError/httpx.HTTPError/OSError on client failures.
        Cancellation propagates. A provider's formal failed response is returned
        unchanged. All responses, including cache hits, are validated anew.
        """
        request = deepcopy(request)
        self.validate_request(request)
        loop = asyncio.get_running_loop()
        locks = self._locks.setdefault(loop, WeakValueDictionary())
        key = request["requestId"]
        lock = locks.setdefault(key, asyncio.Lock())
        async with lock:
            parent_known = self._validate_follow_up(request)
            cached = self._reserve(request)
            if cached is not None:
                return deepcopy(cached)
            try:
                if self._url == _LOCAL_URL:
                    # Tiny in-memory index; no blocking network, worker pool,
                    # source execution or file reads for candidate metadata.
                    result = self._local_search(request, parent_known)
                elif self._url == _WEB_URL:
                    result = await _WebAssetCollector(self.settings, self.transport).search(request, parent_known)
                else:
                    result = await self._http_search(request)
                self.validate_result(request, result)
                # Retain questions/failures/mock as provider claims; the RAG
                # adapter below never promotes them to implementation evidence.
                state = "finished"
                if result["status"] == "failed" and result["error"]["retryable"]:
                    state = "retry_after:" + str(time.time() + (result["error"]["retryAfterMs"] or 0) / 1000)
                self._save(request, result, state)
                return deepcopy(result)
            except BaseException:
                self._save(request)
                raise

    async def collect_assets(self, request):
        """Return the formal result plus a verified, local art handoff.

        v0.2 deliberately has no asset-manifest fields. This Python convenience
        wrapper leaves that contract intact and exposes files to the next agent.
        Cache hits recheck local bytes instead of claiming deleted files exist.
        """
        if self._url != _WEB_URL:
            raise PortalContractError("collect_assets requires web://assets")
        result = await self.search_request(request)
        manifests, assets, warnings = [], [], []
        for candidate in result.get("candidates", []):
            for path in candidate["source"]["paths"]:
                if Path(path).name != "manifest.json":
                    continue
                try:
                    manifest = _read_asset_manifest(path)
                    manifests.append(str(Path(path).resolve()))
                    assets.extend(manifest["assets"])
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    warnings.append("Downloaded asset bundle unavailable: " + type(exc).__name__)
        return {"result": result, "assets": assets, "manifests": manifests,
                "limitations": list(result.get("limitations", [])) + warnings}

    def _local_search(self, request, parent_known=True):
        if self._index is None:
            self._index = _ReferenceIndex(_REFERENCES)
        candidates, limitations = self._index.search(request)
        partial = bool(request["images"])
        if not parent_known:
            partial = True
            limitations.append("Parent exchange is absent locally; clarification answers could not be correlated with parent questions.")
        return {"contractVersion": "0.2", "requestId": request["requestId"],
                "provider": "embedded-reference-catalog", "isMock": False,
                "retrievedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "status": "partial" if partial else "completed", "candidates": candidates,
                "limitations": limitations,
                "discussion": {k: request["discussion"][k] for k in ("runId", "threadId", "round")}}

    async def search(self, query, limit=4, run_id=None):
        trace = {}
        try:
            request = self.request_for(query, limit, run_id if run_id is not None else "lookup-" + uuid.uuid4().hex)
            trace["request"] = request
            result = await self.search_request(request)
            trace["result"] = result
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
            # No URLs, credentials, response bodies or exception text in warnings.
            return {"documents": [], "warnings": ["素材门户请求或契约校验失败：" + type(exc).__name__], "trace": trace}
        if result["status"] == "failed":
            return {"documents": [], "warnings": ["素材门户返回失败：" + result["error"]["code"]], "trace": trace}
        if result["isMock"]:
            return {"documents": [], "warnings": ["素材门户返回 mock 样例，未用作真实检索证据"], "trace": trace}
        if result["status"] == "needs_clarification":
            trace["discussion_outcome"] = "unresolved_clarification"
            return {"documents": [], "warnings": ["素材门户需要澄清；问题与候选已保留，可由主流程构造 follow-up 后调用 search_request；未伪造答复"], "trace": trace}
        documents, identities = [], set()
        for candidate in result["candidates"]:
            source = candidate["source"]
            identity = _json([result["provider"], candidate["kind"], source])
            if identity in identities:
                continue
            identities.add(identity)
            content = (candidate["summary"] + "\n候选覆盖提示（待核实）：" + "；".join(candidate["coverageHints"])
                       + "\n源码路径：" + "；".join(source["paths"])
                       + "\n符号：" + "；".join(source["symbols"])
                       + "\n局限：" + "；".join(candidate["limitations"]))
            # A short generic verification note always survives truncation.
            documents.append({"id": "portal-" + _digest(identity)[:16], "title": candidate["title"],
                "content": ("发现候选，未经运行验证。许可范围和依赖需核实。\n" + content)[:2500],
                "source": source["uri"], "origin": "material_portal", "verification": "unverified_candidate",
                "candidate_id": candidate["candidateId"], "provider": result["provider"],
                "revision": source["revision"], "license": deepcopy(candidate["license"]),
                "paths": list(source["paths"]), "symbols": list(source["symbols"]),
                "evidence": deepcopy(candidate["evidence"])})
            if self._url == _WEB_URL:
                # Bounded actual bytes for the unmodified Builder, plus local
                # manifests for callers that prefer deterministic embedding.
                _attach_asset_handoff(documents[-1], candidate)
        return {"documents": documents, "warnings": [str(x)[:300] for x in result["limitations"]], "trace": trace}


_ART_QUERY_GROUPS = (
    ("samurai", ("武侠", "侠客", "武林", "忍者", "samurai", "ninja")),
    ("shooter", ("枪", "射击", "士兵", "弹丸", "shooter", "soldier", "bullet")),
    ("soldier", ("士兵", "soldier")),
    ("forest", ("森林", "竹林", "草地", "树林", "forest", "grass", "tree")),
    ("monster", ("怪物", "巨魔", "monster", "troll", "goblin", "zombie")),
    ("sword", ("武器", "飞剑", "木剑", "铁剑", "剑术", "sword", "weapon")),
    ("racing", ("赛车", "飞车", "汽车", "驾驶", "racing", "vehicle", "car")),
    ("road", ("道路", "公路", "跑道", "road", "track")),
    ("dungeon", ("地牢", "地下城", "dungeon")),
    ("platformer", ("跳跃", "横版", "平台", "platformer")),
    ("rpg", ("角色", "英雄", "战力", "吞噬", "幸存者", "rpg", "character", "survivor")),
    ("interface", ("图标", "按钮", "界面素材", "ui", "interface", "icon", "hud")),
    ("space", ("太空", "宇宙", "飞船", "space", "spaceship")),
    ("bike", ("自行车", "骑车", "bike", "bicycle")),
    ("animal", ("动物", "鹈鹕", "animal", "pelican")),
)


def _asset_queries(request):
    """Bounded bilingual query expansion, with no hidden model/API charges."""
    parts = [request["description"], *[i["description"] for i in request["images"]],
             *request.get("requiredBehavior", [])]
    follow_up = request["discussion"]["followUp"]
    if follow_up:
        parts += [follow_up["message"], *[a["text"] for a in follow_up["answers"] if a["text"]]]
    text = unicodedata.normalize("NFKC", " ".join(parts)).casefold()
    queries = [query for query, aliases in _ART_QUERY_GROUPS
               if any(alias in text if re.search(r"[\u3400-\u9fff]", alias)
                      else re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", text)
                      for alias in aliases)]
    if queries:
        if "shooter" in queries and ("俯视" in text or re.search(r"top[ -]?down", text)):
            queries[queries.index("shooter")] = "topdown shooter"
        return queries[:3]
    # Preserve unfamiliar requested objects, rather than replacing them with
    # generic icons. The entire prompt is never sent as a search-engine URL.
    terms = [x for x in re.findall(r"[a-z][a-z-]{2,}", text)
             if x not in _ARCHETYPE_GENERIC_TERMS and x not in {
                 "game", "assets", "asset", "need", "want", "find", "with", "and", "the", "for",
                 "png", "sprite", "sprites", "image", "images", "html", "canvas", "web", "code"}]
    if terms:
        return list(dict.fromkeys(terms))[:3]
    chinese = re.findall(r"[\u3400-\u9fff]{2,12}", text)
    return chinese[:1]


class _AssetHTML(HTMLParser):
    """Read links in their semantic HTML fields; ignore scripts and comments."""

    _void = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack, self.links, self.title, self.description = [], [], [], []
        self.title_done = False
        self.current = None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        context = " ".join(x[1] for x in self.stack) + " " + attrs.get("class", "")
        if tag == "a":
            self.current = {"href": attrs.get("href", ""), "text": "", "context": context,
                            "download": "download" in attrs, "id": attrs.get("id", ""),
                            "in_h2": any(x[0] == "h2" for x in self.stack)}
            self.links.append(self.current)
        if tag not in self._void:
            self.stack.append((tag, attrs.get("class", "") + " " + attrs.get("id", "")))

    def handle_endtag(self, tag):
        if tag == "h1":
            self.title_done = True
        if tag == "a":
            self.current = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(x[0] in {"script", "style"} for x in self.stack):
            return
        if self.current is not None:
            self.current["text"] += data
        if not self.title_done and any(x[0] == "h1" for x in self.stack):
            self.title.append(data)
        if any("field-name-body" in x[1] for x in self.stack) and not any("comment" in x[1] for x in self.stack):
            self.description.append(data)


class _WebAssetCollector:
    """Live public-site search, license checks, and small PNG art bundles.

    All network work is anonymous GET to fixed public hosts. No portal token,
    saved GitHub credential or model key can be forwarded to these providers.
    Site/API failures become explicit partial results, never fake local hits.
    """

    _hosts = frozenset({"kenney.nl", "www.kenney.nl", "opengameart.org"})

    def __init__(self, settings, transport=None):
        self.settings, self.transport = settings, transport
        self.root = Path(getattr(settings, "data_dir", "data")).resolve() / "material-assets"
        self.cache = self.root / "http-cache"
        self.requests, self.bytes, self.cache_hits = 0, 0, 0
        self.events, self.warnings, self.candidates = [], [], []
        self.queries = []
        self.top_down = False

    @classmethod
    def _check_url(cls, url):
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname not in cls._hosts
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 443} or parts.fragment):
            raise PortalContractError("asset source is outside the public provider allowlist")

    async def _get(self, client, url, *, cap=1_000_000):
        self._check_url(url)
        key = _digest(url)
        cached = self.cache / (key + ".bin")
        meta = self.cache / (key + ".json")
        try:
            metadata = json.loads(meta.read_text(encoding="utf-8"))
            if (0 <= time.time() - metadata["retrievedAt"] < 86400
                    and cached.stat().st_size <= cap):
                data = cached.read_bytes()
                if _digest_bytes(data) == metadata["sha256"]:
                    self.cache_hits += 1
                    self.events.append({"url": url, "cache": True, "bytes": len(data)})
                    return data
        except (OSError, ValueError, KeyError, TypeError):
            pass
        # Follow at most two explicitly checked redirects, without auth headers.
        for redirect in range(3):
            if self.requests >= 22:
                raise PortalContractError("online search request budget exhausted")
            self.requests += 1
            self._check_url(url)
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    if redirect == 2 or "location" not in response.headers:
                        raise PortalContractError("asset redirect limit exceeded")
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-length", "")
                if declared.isdecimal() and int(declared) > cap:
                    raise PortalContractError("download exceeds the per-file byte limit")
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16384):
                    self.bytes += len(chunk)
                    if len(data) + len(chunk) > cap or self.bytes > 40_000_000:
                        raise PortalContractError("online asset byte budget exceeded")
                    data.extend(chunk)
                break
        data = bytes(data)
        self.cache.mkdir(parents=True, exist_ok=True)
        # Unique temporary names make independent client GETs safe to cache.
        temp = self.cache / (key + "." + uuid.uuid4().hex + ".tmp")
        temp.write_bytes(data)
        temp.replace(cached)
        metadata = {"retrievedAt": time.time(), "sha256": _digest_bytes(data), "url": url}
        temp = self.cache / (key + "." + uuid.uuid4().hex + ".tmp")
        temp.write_text(_json(metadata), encoding="utf-8")
        temp.replace(meta)
        self.events.append({"url": url, "cache": False, "bytes": len(data)})
        return data

    async def _discover(self, client, provider, query):
        if provider == "kenney":
            # Kenney uses broad pack/category words rather than every object
            # name. Keep both terms in evidence; broadening is never hidden.
            provider_query = {"samurai": "rpg", "forest": "nature", "sword": "roguelike"}.get(query, query)
            url = "https://kenney.nl/assets/category:2D?" + urlencode({"search": provider_query})
        else:
            provider_query = query
            url = "https://opengameart.org/art-search-advanced?" + urlencode({
                "keys": query, "field_art_licenses_tid[0]": "4", "field_art_type_tid[0]": "9",
                "items_per_page": "24"})
        page = _AssetHTML((await self._get(client, url)).decode("utf-8"))
        found = []
        for link in page.links:
            uri = urljoin(url, link["href"])
            if provider == "kenney":
                valid = link["in_h2"] and re.fullmatch(r"https://(?:www\.)?kenney\.nl/assets/[a-z0-9-]+", uri)
            else:
                valid = "art-preview-title" in link["context"] and uri.startswith("https://opengameart.org/content/")
            title = " ".join(link["text"].split())[:200]
            if valid and title and not any(x["uri"] == uri for x in found):
                if (self.top_down and re.search(r"isometric|side[ -]?scroller|platformer", title, re.I)):
                    continue
                # Rank results across all searches; direct title matches count
                # more than a provider's broad body-text keyword match.
                aliases = {"forest": ("forest", "foliage", "nature", "grass"),
                           "samurai": ("samurai", "ninja", "character", "rpg"),
                           "sword": ("sword", "weapon"),
                           "topdown shooter": ("topdown shooter", "top-down shooter", "top down shooter")}.get(query, (query,))
                title_normalized = re.sub(r"[^a-z0-9]", "", title.casefold())
                score = 12 if any(re.sub(r"[^a-z0-9]", "", a) in title_normalized for a in aliases) else 1
                if provider == "kenney":
                    score += 4  # Prefer complete, consistently licensed game-art packs.
                if re.search(r"builder|parts|body parts", title, re.I):
                    score -= 6
                found.append({"uri": uri, "title": title, "provider": provider,
                              "query": query, "providerQuery": provider_query, "score": score})
        return found[:8]

    async def _download_candidate(self, client, hit):
        uri = hit["uri"]
        page_bytes = await self._get(client, uri)
        page = _AssetHTML(page_bytes.decode("utf-8"))
        if hit["provider"] == "kenney":
            licenses = [x for x in page.links if _is_cc0_link(x["href"])
                        and "CC0" in x["text"]]
            downloads = [x for x in page.links if x["id"] == "donate-text" and urlsplit(x["href"]).path.endswith(".zip")]
            author = "Kenney"
        else:
            licenses = [x for x in page.links if "field-name-field-art-licenses" in x["context"]
                        and (_is_cc0_link(x["href"]) or x["text"].strip() == "CC0")]
            downloads = [x for x in page.links if "field-name-field-art-files" in x["context"]
                         and x["download"] and urlsplit(x["href"]).path.lower().endswith((".png", ".zip"))]
            authors = [x["text"] for x in page.links if "username" in x["context"] or "/users/" in x["href"]]
            author = " ".join(authors[:1]) or "OpenGameArt contributor (see source page)"
        if not licenses:
            raise PortalContractError("asset-specific CC0 license evidence missing")
        if not downloads:
            raise PortalContractError("no supported PNG or ZIP download on the asset page")
        # Prefer directly usable PNG to an archive, never thumbnails/previews.
        downloads.sort(key=lambda x: (not urlsplit(x["href"]).path.lower().endswith(".png"), x["href"]))
        download = urljoin(uri, downloads[0]["href"])
        data = await self._get(client, download, cap=8_000_000)
        archive_hash = _digest_bytes(data)
        license_info = {"identifier": "CC0-1.0", "sourceUri": uri, "scope": "assets"}
        title = " ".join(page.title).strip()[:200] or hit["title"]
        manifest_path = _write_download_bundle(
            self.root / "bundles", data=data, filename=urlsplit(download).path.rsplit("/", 1)[-1],
            source_url=uri, download_url=download, title=title, author=author,
            license_info=license_info, query=hit["query"], max_assets=6,
        )
        manifest = _read_asset_manifest(manifest_path)
        if not manifest["assets"]:
            raise PortalContractError("no validated PNG artwork in the download")
        license_copy = Path(manifest_path).parent / "source-page.html"
        license_copy.write_bytes(page_bytes)
        summary = ("Downloaded game artwork from " + hit["provider"] + ": " + title + ". "
                   + " ".join(page.description).strip()[:500]
                   + " Available PNGs: " + "; ".join(a["originalFilename"] for a in manifest["assets"])[:500])
        limitations = ["Visual relevance is based on text metadata; images were not assessed by a vision model.",
                       "Artwork is not gameplay/code evidence. Animation frames, tile semantics and aesthetic fit need review.",
                       "Local manifest is a Python handoff. Offline HTML must embed image bytes; local/remote image URLs cannot be used directly."]
        if hit["providerQuery"] != hit["query"]:
            limitations.append("Search broadened " + hit["query"] + " to " + hit["providerQuery"]
                               + " on Kenney; an exact object, culture or art-style match is not established.")
        if re.search(r"builder|parts", title, re.I):
            limitations.append("This is a component/parts pack; components may need assembly before use as complete characters.")
        return {"candidateId": "art-" + _digest(uri + archive_hash)[:20], "kind": "reference_media",
                "title": title, "summary": summary[:1500],
                "source": {"uri": uri, "repositoryUrl": None, "revision": "sha256:" + archive_hash,
                           "paths": [str(Path(manifest_path).resolve())], "symbols": []},
                "coverageHints": ["Art need keyword: " + hit["query"] + "; provider search: " + hit["providerQuery"],
                                  "PNG artwork for Canvas/DOM, independent of game engine",
                                  "Downloaded " + str(len(manifest["assets"])) + " validated PNG files"],
                "license": license_info,
                "evidence": [{"description": "Asset page explicitly declares CC0; saved alongside the manifest.", "sourceUri": uri},
                             {"description": "Downloaded original file; SHA-256 " + archive_hash, "sourceUri": download}],
                "limitations": limitations}

    async def search(self, request, parent_known):
        self.root.mkdir(parents=True, exist_ok=True)
        self.top_down = bool(re.search(r"俯视|top[ -]?down", request["description"], re.I))
        self.queries = _asset_queries(request)
        if "reference_media" not in request["materialKinds"]:
            self.warnings.append("Online art search skipped: reference_media was not requested.")
        elif not self.queries:
            self.warnings.append("No useful visual search terms; describe the character, environment, object or UI art needed.")
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(14, connect=7), trust_env=False,
                    transport=self.transport, follow_redirects=False,
                    headers={"User-Agent": "ZhangjiangHackathon-MaterialPortal/0.3", "Accept": "*/*"}) as client:
                async def work():
                    # Independent search pages in parallel, bounded to six.
                    results = await asyncio.gather(*[self._discover(client, provider, query)
                        for query in self.queries for provider in ("kenney", "opengameart")], return_exceptions=True)
                    hits = {}
                    for result in results:
                        if isinstance(result, BaseException):
                            if isinstance(result, asyncio.CancelledError):
                                raise result
                            self.warnings.append("A public search source failed: " + type(result).__name__)
                            continue
                        for hit in result:
                            if hit["uri"] in hits:
                                hits[hit["uri"]]["score"] += hit["score"]
                            else:
                                hits[hit["uri"]] = hit
                    ranked = sorted(hits.values(), key=lambda h: (-h["score"], h["provider"], h["uri"]))
                    # Round-robin query coverage prevents the first topic from
                    # consuming all slots (e.g. forest art hiding all weapons).
                    selected, seen = [], set()
                    groups = [[h for h in ranked if h["query"] == query] for query in self.queries]
                    for offset in range(8):
                        for group in groups:
                            if offset < len(group) and group[offset]["uri"] not in seen:
                                selected.append(group[offset])
                                seen.add(group[offset]["uri"])
                    for hit in selected[:8]:
                        if len(self.candidates) >= min(4, request["limit"]):
                            break
                        try:
                            self.candidates.append(await self._download_candidate(client, hit))
                        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
                            reason = (str(exc)[:160] if isinstance(exc, (PortalContractError, AssetBundleError))
                                      else type(exc).__name__)
                            self.warnings.append("Skipped art candidate " + hit["title"][:80] + ": " + reason)
                try:
                    await asyncio.wait_for(work(), timeout=65)
                except asyncio.TimeoutError:
                    self.warnings.append("Online art search reached its 65-second time budget; retained completed downloads.")
        limitations = list(dict.fromkeys(self.warnings))[:20]
        limitations += ["Live provider search with a 24-hour HTTP cache; no general search-engine API or hidden LLM calls.",
                        "PNG and small ZIP art only, CC0 verified per asset page. 3D models, audio, font files and automatic visual matching are not implemented.",
                        "Text-based art matching does not guarantee the requested style or complete coverage of all art needs."]
        if request["images"]:
            limitations.append("Image URIs were not downloaded/analyzed; only their descriptions contributed to search terms.")
        if not parent_known:
            limitations.append("Parent clarification was unavailable locally.")
        if not self.candidates:
            limitations.append("No downloadable, license-checked matching PNG bundle was found; no unrelated local reference was substituted.")
        result = {"contractVersion": "0.2", "requestId": request["requestId"],
                  "provider": "public-web-art-collector", "isMock": False,
                  "retrievedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                  "status": "partial" if self.warnings or request["images"] or not parent_known or not self.candidates else "completed",
                  "candidates": self.candidates, "limitations": limitations,
                  "discussion": {k: request["discussion"][k] for k in ("runId", "threadId", "round")}}
        audit = {"requestId": request["requestId"], "collectorVersion": _WEB_VERSION, "queries": self.queries,
                 "networkRequests": self.requests, "networkBytes": self.bytes,
                 "cacheHits": self.cache_hits, "events": self.events, "result": result}
        audit_dir = self.root / "searches"
        audit_dir.mkdir(exist_ok=True)
        (audit_dir / (_digest(request["requestId"]) + ".json")).write_text(_json(audit), encoding="utf-8")
        return result


def _digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _is_cc0_link(uri):
    parts = urlsplit(uri)
    return (parts.scheme in {"http", "https"} and parts.hostname in {"creativecommons.org", "www.creativecommons.org"}
            and parts.username is None and parts.password is None
            and parts.path.rstrip("/") == "/publicdomain/zero/1.0")


def _write_download_bundle(root, *, data, filename, source_url, download_url,
                           title, author, license_info, query, max_assets):
    include_paths = None
    if zipfile.is_zipfile(BytesIO(data)):
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = [i.filename for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith(".png")
                     and "__MACOSX/" not in i.filename and not i.filename.rsplit("/", 1)[-1].startswith("._")
                     and not re.search(r"(?:^|[/_. -])(?:previews?|samples?)(?:\d+)?(?:[/_. -]|$)", i.filename, re.I)]
            if not names:
                raise PortalContractError("download contains only previews or unsupported art formats")
            # Numeric tile IDs have no semantic role. Prefer an intact atlas
            # over inventing which anonymous tile depicts a player or enemy.
            semantic = [n for n in names if not re.search(r"(?:tile|sprite)[_-]?\d+\.png$", n, re.I)]
            preferred = semantic or names
            def score(name):
                lower = name.casefold()
                terms = set(re.findall(r"[a-z]+", lower))
                return (8 * (query in lower) + 5 * bool(terms & {"player", "character", "characters", "enemy", "enemies", "stand", "sword", "tree"})
                        + 3 * any(x in lower for x in ("spritesheet", "tilesheet", "tilemap"))
                        - 4 * bool(terms & {"preview", "sample", "background"}))
            ranked = sorted(preferred, key=lambda n: (-score(n), n))
            include_paths, groups = [], set()
            for name in ranked:
                # Avoid spending every slot on color/frame variants of one
                # character, while keeping separate directories and objects.
                group = re.sub(r"(?:red|blue|green|yellow|black|white|_packed|\d+)", "", name.casefold())
                if group in groups:
                    continue
                groups.add(group)
                include_paths.append(name)
                if len(include_paths) >= max_assets:
                    break
    # A changed selection gets a fresh bundle. Otherwise a merged manifest
    # from an older selector could silently retain now-excluded preview files.
    bundle_dir = Path(root) / _digest(_json([source_url, _digest_bytes(data), query, include_paths]))[:24]
    manifest = collect_asset_bytes(data, bundle_dir, source_url=source_url,
        license_info=license_info, original_filename=filename,
        limits={"max_files": max_assets}, include_paths=include_paths)
    manifest.update(title=title, author=author, downloadUrl=download_url,
                    downloadSha256=_digest_bytes(data), searchQuery=query,
                    semanticSelection="Filename metadata only; numeric tile roles are unassigned.")
    for asset in manifest["assets"]:
        asset.update(author=author, downloadUrl=download_url)
        label = asset["originalFilename"].casefold()
        if re.search(r"(?:arm|leg|hand|foot|feet|head|body)[_/. -]", label):
            role = "character_part"
        elif "sword" in label or "weapon" in label:
            role = "weapon"
        elif "player" in label or "character" in label or "stand" in label:
            role = "character_or_atlas"
        elif "enemy" in label or "enemies" in label or "monster" in label:
            role = "enemy_or_atlas"
        elif any(x in label for x in ("tree", "grass", "foliage", "terrain", "road")):
            role = "environment"
        else:
            role = "unassigned"
        asset["suggestedRole"] = role
    path = bundle_dir / "manifest.json"
    path.write_text(_json(manifest), encoding="utf-8")
    return str(path.resolve())


def _read_asset_manifest(path):
    path = Path(path)
    if path.is_symlink():
        raise PortalContractError("asset manifest must not be a symbolic link")
    path = path.resolve()
    if path.stat().st_size > 1_000_000:
        raise PortalContractError("asset manifest too large")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("assets"), list) or len(manifest["assets"]) > 32:
        raise PortalContractError("invalid asset manifest")
    for asset in manifest["assets"]:
        _asset_read_entry(path.parent, asset, _asset_limits())
        target = (path.parent / asset["path"]).resolve()
        asset["bundleRoot"] = str(path.parent)
        asset["absolutePath"] = str(target)
    return manifest


def _attach_asset_handoff(document, candidate):
    document["assets"] = []
    used = 0
    for path in candidate["source"]["paths"]:
        if Path(path).name != "manifest.json":
            continue
        try:
            manifest = _read_asset_manifest(path)
            document["asset_manifest"] = str(Path(path).resolve())
            document["verification"] = "downloaded_hash_checked_art"
            document["content"] = ("已下载并核对字节、尺寸与来源页CC0声明的美术素材；视觉贴合度及玩法未验证。\n"
                                   + candidate["summary"] + "\n" + "; ".join(candidate["limitations"]))[:2500]
            for asset in manifest["assets"]:
                payload = Path(asset["absolutePath"]).read_bytes()
                if len(payload) > 2500 or used + len(payload) > 6000 or len(document["assets"]) >= 2:
                    continue
                document["assets"].append({**asset, "dataUri": "data:image/png;base64," + base64.b64encode(payload).decode("ascii"),
                    "usage": "Actual PNG bytes; Canvas drawImage(image,x,y,width,height) scales at render time without runtime network access."})
                used += len(payload)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            document["verification"] = "asset_bundle_unavailable"
            document["content"] += "\nLocal art handoff unavailable: " + type(exc).__name__


# BEGIN STANDALONE ART BUNDLE HELPERS
_ASSET_DEFAULT_LIMITS = {
    "max_download_bytes": 16 * 1024 * 1024,
    "max_file_bytes": 4 * 1024 * 1024,
    "max_files": 6,
    "max_members": 4096,
    "max_uncompressed_bytes": 48 * 1024 * 1024,
    "max_compression_ratio": 200,
    "max_pixels": 16 * 1024 * 1024,
    "max_dimension": 8192,
    "max_decoded_bytes": 64 * 1024 * 1024,
    "max_manifest_bytes": 2 * 1024 * 1024,
    "max_manifest_assets": 512,
    "max_html_bytes": 300000,
}


class AssetBundleError(ValueError):
    """A downloaded file or bundle failed the local safety checks."""


def _asset_limits(limits=None):
    result = dict(_ASSET_DEFAULT_LIMITS)
    if limits:
        unknown = set(limits) - set(result)
        if unknown:
            raise AssetBundleError("Unknown asset limits: " + ", ".join(sorted(unknown)))
        result.update(limits)
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in result.values()):
        raise AssetBundleError("Asset limits must be positive integers")
    return result


def _asset_source_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise AssetBundleError("An HTTP(S) source URL is required")
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise AssetBundleError("An HTTP(S) source URL without credentials is required")
    if any(ord(character) < 32 for character in value):
        raise AssetBundleError("Source URL contains control characters")
    return value


def _asset_png_metadata(payload, limits=None):
    """Validate static PNG chunks and bounded decoded scanlines; return dimensions."""
    budget = _asset_limits(limits)
    if not isinstance(payload, bytes) or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssetBundleError("The asset is not a PNG")
    if len(payload) > budget["max_file_bytes"]:
        raise AssetBundleError("PNG exceeds the single-file size limit")
    offset, chunk_count = 8, 0
    header = None
    compressed = bytearray()
    seen_palette = seen_idat = idat_ended = ended = False
    while offset < len(payload):
        chunk_count += 1
        if chunk_count > 4096 or len(payload) - offset < 12:
            raise AssetBundleError("Invalid PNG chunk sequence")
        length = struct.unpack_from(">I", payload, offset)[0]
        kind = payload[offset + 4:offset + 8]
        end = offset + 12 + length
        if length > budget["max_file_bytes"] or end > len(payload):
            raise AssetBundleError("PNG chunk exceeds the available bytes")
        if not re.fullmatch(rb"[A-Za-z]{4}", kind) or kind[2] & 32:
            raise AssetBundleError("Invalid PNG chunk type")
        data = payload[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack_from(">I", payload, offset + 8 + length)[0]
        if binascii.crc32(kind + data) & 0xFFFFFFFF != expected_crc:
            raise AssetBundleError("PNG chunk checksum mismatch")
        if header is None and kind != b"IHDR":
            raise AssetBundleError("PNG must begin with IHDR")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise AssetBundleError("Invalid or duplicate PNG header")
            header = struct.unpack(">IIBBBBB", data)
            width, height, depth, color, compression, filtering, interlace = header
            valid_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if color not in valid_depths or depth not in valid_depths[color] or compression or filtering or interlace not in (0, 1):
                raise AssetBundleError("Unsupported PNG header")
            if not width or not height or max(width, height) > budget["max_dimension"] or width * height > budget["max_pixels"]:
                raise AssetBundleError("PNG dimensions exceed the image budget")
        elif kind == b"PLTE":
            if seen_palette or seen_idat or not length or length % 3 or length > 768 or header[3] in (0, 4):
                raise AssetBundleError("Invalid PNG palette")
            if header[3] == 3 and length // 3 > 2 ** header[2]:
                raise AssetBundleError("PNG palette exceeds the bit depth")
            seen_palette = True
        elif kind == b"IDAT":
            if idat_ended or (header[3] == 3 and not seen_palette):
                raise AssetBundleError("Invalid PNG image-data ordering")
            seen_idat = True
            compressed.extend(data)
        elif kind == b"IEND":
            if length or not seen_idat or end != len(payload):
                raise AssetBundleError("Invalid PNG terminator or trailing bytes")
            ended = True
        elif kind in (b"acTL", b"fcTL", b"fdAT"):
            raise AssetBundleError("Animated PNG is not supported")
        elif not kind[0] & 32:
            raise AssetBundleError("Unknown critical PNG chunk")
        if seen_idat and kind not in (b"IDAT", b"IEND"):
            idat_ended = True
        offset = end
    if not ended or header is None:
        raise AssetBundleError("PNG is missing its terminator")
    width, height, depth, color, _, _, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = [(0, 0, 1, 1)] if interlace == 0 else [
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8),
        (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
    ]
    scanlines = []
    expected_length = 0
    for start_x, start_y, step_x, step_y in passes:
        columns = max(0, (width - start_x + step_x - 1) // step_x)
        rows = max(0, (height - start_y + step_y - 1) // step_y)
        if columns and rows:
            row_length = (columns * channels * depth + 7) // 8 + 1
            scanlines.append((rows, row_length))
            expected_length += rows * row_length
    if expected_length > budget["max_decoded_bytes"]:
        raise AssetBundleError("PNG decoded data exceeds the image budget")
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(bytes(compressed), expected_length + 1)
    except zlib.error as error:
        raise AssetBundleError("Invalid PNG compressed image data") from error
    if len(decoded) != expected_length or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        raise AssetBundleError("PNG decoded data has an invalid or excessive length")
    cursor = 0
    for rows, row_length in scanlines:
        for _ in range(rows):
            if decoded[cursor] > 4:
                raise AssetBundleError("PNG scanline has an invalid filter")
            cursor += row_length
    return {"width": width, "height": height, "mimeType": "image/png"}


def _asset_zip_members(archive, budget):
    members = archive.infolist()
    if len(members) > budget["max_members"]:
        raise AssetBundleError("ZIP exceeds the member-count budget")
    total, seen, pngs = 0, set(), []
    for info in members:
        name = info.orig_filename
        parts = name.rstrip("/").split("/")
        if not name or "\\" in name or "\x00" in name or name.startswith("/") or any(
            part in ("", ".", "..") or ":" in part or part.endswith((" ", ".")) or any(ord(char) < 32 for char in part)
            for part in parts
        ):
            raise AssetBundleError("ZIP contains an unsafe member path")
        folded = name.rstrip("/").casefold()
        if folded in seen:
            raise AssetBundleError("ZIP contains duplicate or conflicting member paths")
        seen.add(folded)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise AssetBundleError("ZIP links and special files are not accepted")
        if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise AssetBundleError("ZIP encryption or compression method is not supported")
        total += info.file_size
        if total > budget["max_uncompressed_bytes"]:
            raise AssetBundleError("ZIP exceeds the total uncompressed budget")
        if info.file_size > max(info.compress_size, 1) * budget["max_compression_ratio"]:
            raise AssetBundleError("ZIP member has an excessive compression ratio")
        if not info.is_dir() and name.lower().endswith(".png"):
            if info.file_size > budget["max_file_bytes"]:
                raise AssetBundleError("ZIP PNG exceeds the single-file budget")
            pngs.append(info)
    return sorted(pngs, key=lambda item: item.filename.casefold())


def _asset_write_atomic(path, payload):
    if path.is_symlink():
        raise AssetBundleError("Bundle file must not be a symbolic link")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".asset-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _asset_bundle_root(bundle_dir):
    directory = Path(bundle_dir)
    if directory.is_symlink():
        raise AssetBundleError("Bundle root must not be a symbolic link")
    directory.mkdir(parents=True, exist_ok=True)
    root = directory.resolve()
    assets = root / "assets"
    if assets.is_symlink():
        raise AssetBundleError("Asset directory must not be a symbolic link")
    assets.mkdir(exist_ok=True)
    if assets.resolve().parent != root:
        raise AssetBundleError("Asset directory must remain inside the bundle")
    return root


def collect_asset_bytes(payload, bundle_dir, *, source_url, license_info, original_filename="asset.png", limits=None, include_paths=None):
    """Validate a PNG/ZIP, save original PNGs by SHA256, and merge manifest.json.

    All manifest asset paths are relative to bundle_dir. ZIP filenames are metadata
    only and are never extracted. At most max_files sorted PNGs are selected unless
    include_paths explicitly selects members. Returns the saved manifest dictionary.
    """
    budget = _asset_limits(limits)
    source_url = _asset_source_url(source_url)
    if not isinstance(payload, bytes) or not payload or len(payload) > budget["max_download_bytes"]:
        raise AssetBundleError("Download is empty or exceeds the download budget")
    if not isinstance(original_filename, str) or len(original_filename) > 4096 or "\x00" in original_filename:
        raise AssetBundleError("Invalid original filename")
    if not isinstance(license_info, (str, dict)) or not license_info:
        raise AssetBundleError("Explicit license information is required")
    try:
        serialized_license = json.dumps(license_info, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise AssetBundleError("License information must be JSON serializable") from error
    if len(serialized_license.encode("utf-8")) > 8192:
        raise AssetBundleError("License metadata is too large")
    warnings, selected = [], []
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        if include_paths is not None:
            raise AssetBundleError("include_paths applies only to ZIP downloads")
        selected.append((original_filename, payload, _asset_png_metadata(payload, budget)))
    elif payload.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                pngs = _asset_zip_members(archive, budget)
                if include_paths is not None:
                    wanted = set(include_paths)
                    if not wanted or len(wanted) > budget["max_files"] or not all(isinstance(name, str) for name in wanted):
                        raise AssetBundleError("Invalid ZIP member selection")
                    pngs = [info for info in pngs if info.filename in wanted]
                    if {info.filename for info in pngs} != wanted:
                        raise AssetBundleError("A requested PNG is absent from the ZIP")
                elif len(pngs) > budget["max_files"]:
                    warnings.append("Selected first %d PNGs; omitted %d additional PNGs" % (budget["max_files"], len(pngs) - budget["max_files"]))
                    pngs = pngs[:budget["max_files"]]
                if not pngs:
                    raise AssetBundleError("ZIP has no selected PNG files")
                for info in pngs:
                    with archive.open(info) as member:
                        content = member.read(budget["max_file_bytes"] + 1)
                    if len(content) != info.file_size or len(content) > budget["max_file_bytes"]:
                        raise AssetBundleError("ZIP member size differs from its declared size")
                    selected.append((info.filename, content, _asset_png_metadata(content, budget)))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as error:
            raise AssetBundleError("Invalid or unsupported ZIP archive") from error
    else:
        raise AssetBundleError("Only PNG files and ZIP archives containing PNGs are supported")
    root = _asset_bundle_root(bundle_dir)
    manifest_path = root / "manifest.json"
    existing = []
    if manifest_path.exists() or manifest_path.is_symlink():
        if manifest_path.is_symlink() or manifest_path.stat().st_size > budget["max_manifest_bytes"]:
            raise AssetBundleError("Unsafe or excessive existing asset manifest")
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as error:
            raise AssetBundleError("Existing manifest is invalid") from error
        if not isinstance(old, dict) or old.get("schemaVersion") != "1.0" or not isinstance(old.get("assets"), list):
            raise AssetBundleError("Existing manifest has an incompatible format")
        existing = old["assets"]
        if not all(isinstance(item, dict) and isinstance(item.get("assetId"), str) for item in existing):
            raise AssetBundleError("Existing manifest contains invalid entries")
        if len(existing) > budget["max_manifest_assets"]:
            raise AssetBundleError("Existing manifest exceeds the entry-count budget")
        if len({item["assetId"] for item in existing}) != len(existing):
            raise AssetBundleError("Existing manifest contains duplicate asset identifiers")
        for item in existing:
            _asset_read_entry(root, item, budget)
    indexed = {item["assetId"]: item for item in existing}
    for filename, content, metadata in selected:
        digest = hashlib.sha256(content).hexdigest()
        identifier = "png-" + digest[:24]
        relative = "assets/" + digest + ".png"
        destination = root / relative
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or destination.stat().st_size != len(content) or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise AssetBundleError("Existing content-addressed asset is unsafe or corrupted")
        else:
            _asset_write_atomic(destination, content)
        item = dict(metadata, assetId=identifier, path=relative, sha256=digest,
                    originalFilename=filename, sourceUrl=source_url, license=license_info,
                    byteLength=len(content))
        if identifier in indexed and indexed[identifier].get("sha256") != digest:
            raise AssetBundleError("Asset identifier collision")
        indexed.setdefault(identifier, item)
    if len(indexed) > budget["max_manifest_assets"]:
        raise AssetBundleError("Asset manifest exceeds the entry-count budget")
    manifest = {"schemaVersion": "1.0", "manifestPath": "manifest.json", "assets": list(indexed.values()), "warnings": warnings}
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(encoded) > budget["max_manifest_bytes"]:
        raise AssetBundleError("Asset manifest exceeds the size budget")
    _asset_write_atomic(manifest_path, encoded)
    return manifest


def _asset_read_entry(root, item, budget):
    if not isinstance(item, dict) or not re.fullmatch(r"png-[0-9a-f]{24}", str(item.get("assetId", ""))):
        raise AssetBundleError("Invalid manifest asset identifier")
    relative = item.get("path")
    if not isinstance(relative, str) or "\\" in relative or ":" in relative or "\x00" in relative:
        raise AssetBundleError("Invalid manifest asset path")
    parts = relative.split("/")
    if relative.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise AssetBundleError("Manifest asset path escapes its bundle")
    path = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise AssetBundleError("Manifest asset path traverses a symbolic link")
    try:
        path.resolve().relative_to(root)
    except ValueError as error:
        raise AssetBundleError("Manifest asset path escapes its bundle") from error
    if not path.is_file() or path.stat().st_size > budget["max_file_bytes"]:
        raise AssetBundleError("Manifest asset is missing or excessive")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if item.get("sha256") != digest or item["assetId"] != "png-" + digest[:24]:
        raise AssetBundleError("Manifest asset checksum mismatch")
    metadata = _asset_png_metadata(content, budget)
    if any(item.get(key) != metadata[key] for key in ("width", "height", "mimeType")):
        raise AssetBundleError("Manifest dimensions or media type differ from the asset")
    _asset_source_url(item.get("sourceUrl"))
    if not isinstance(item.get("license"), (dict, str)) or not item["license"]:
        raise AssetBundleError("Manifest asset is missing license metadata")
    return content


def embed_assets(html, manifest_path, *, limits=None):
    """Replace asset://<assetId> with verified PNG data URIs and visible credits.

    Only referenced assets are embedded or credited. The source PNGs remain intact;
    use canvas.drawImage(image, x, y, targetWidth, targetHeight) to display a resize.
    """
    if not isinstance(html, str):
        raise AssetBundleError("HTML input must be a string")
    budget = _asset_limits(limits)
    if len(html.encode("utf-8")) > budget["max_html_bytes"]:
        raise AssetBundleError("HTML exceeds the offline artifact size limit")
    path = Path(manifest_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > budget["max_manifest_bytes"]:
        raise AssetBundleError("Asset manifest is missing, excessive, or a symbolic link")
    root = path.parent.resolve()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise AssetBundleError("Asset manifest is invalid JSON") from error
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != "1.0" or not isinstance(manifest.get("assets"), list):
        raise AssetBundleError("Asset manifest has an incompatible format")
    assets = manifest["assets"]
    if len(assets) > budget["max_manifest_assets"]:
        raise AssetBundleError("Asset manifest exceeds the entry-count budget")
    indexed = {}
    for item in assets:
        if not isinstance(item, dict) or not isinstance(item.get("assetId"), str) or item["assetId"] in indexed:
            raise AssetBundleError("Manifest contains invalid or duplicate asset identifiers")
        indexed[item["assetId"]] = item
    used, replacements = [], {}
    pattern = re.compile(r'''asset://([A-Za-z0-9_-]+)(?=[\s"'`<>)},;]|$)''')
    occurrences = {}
    for match in pattern.finditer(html):
        identifier = match.group(1)
        occurrences[identifier] = occurrences.get(identifier, 0) + 1
    encoded_size = len(html.encode("utf-8"))
    for identifier in occurrences:
        if identifier not in indexed:
            raise AssetBundleError("HTML references an unknown asset: " + identifier)
        if identifier not in replacements:
            content = _asset_read_entry(root, indexed[identifier], budget)
            encoded_size += (((len(content) + 2) // 3) * 4 + 22 - len("asset://" + identifier)) * occurrences[identifier]
            if encoded_size > budget["max_html_bytes"]:
                raise AssetBundleError("Embedded images exceed the offline artifact size limit")
            replacements[identifier] = "data:image/png;base64," + base64.b64encode(content).decode("ascii")
            used.append(indexed[identifier])
    if not used:
        if "asset://" in html:
            raise AssetBundleError("HTML contains a malformed asset reference")
        return html
    output = pattern.sub(lambda match: replacements[match.group(1)], html)
    if "asset://" in output:
        raise AssetBundleError("HTML contains an unresolved asset reference")
    credits = []
    for item in used:
        license_info = item["license"]
        if isinstance(license_info, dict):
            label = license_info.get("spdx") or license_info.get("identifier") or license_info.get("id") or license_info.get("name") or "See source license"
            author = license_info.get("attribution") or license_info.get("author") or license_info.get("creator") or license_info.get("credit") or ""
        else:
            label, author = license_info, ""
        text = str(item.get("originalFilename", item["assetId"])) + " — " + str(label)
        if author:
            text += " — " + str(author)
        credits.append("<li>" + _asset_html.escape(text + " — " + item["sourceUrl"]) + "</li>")
    credit_html = ('<!-- material-assets-credits:start --><footer data-material-asset-credits="true" aria-label="素材来源与许可" '
                   'style="display:block;visibility:visible;position:relative;max-width:100%;overflow-wrap:anywhere;padding:4px 8px;box-sizing:border-box;color:#111;background:#fff;font:11px/1.3 sans-serif">'
                   '<details><summary>素材来源与许可（' + str(len(used)) + '项）</summary><ul style="margin:2px 0;padding-left:16px">' + "".join(credits) + '</ul></details></footer><!-- material-assets-credits:end -->')
    output = re.sub(r"<!-- material-assets-credits:start -->.*?<!-- material-assets-credits:end -->", "", output, flags=re.DOTALL)
    closing = re.search(r"</body\s*>", output, flags=re.IGNORECASE)
    if closing:
        output = output[:closing.start()] + credit_html + output[closing.start():]
    else:
        output += credit_html
    if len(output.encode("utf-8")) > budget["max_html_bytes"]:
        raise AssetBundleError("HTML with asset credits exceeds the offline artifact size limit")
    return output

# END STANDALONE ART BUNDLE HELPERS


def prepare_asset_documents(documents, *, max_asset_bytes=65536):
    """Give a Builder verified, short image references without Base64 payloads.

    Original retrieval documents are retained by the host for deterministic
    embedding. No image is generated, edited, resized or semantically guessed.
    """
    prepared = deepcopy(documents)
    seen = set()
    for document in prepared:
        path = document.get("asset_manifest")
        document.pop("assets", None)
        if not path or document.get("verification") != "downloaded_hash_checked_art":
            continue
        manifest = _read_asset_manifest(path)
        document.pop("asset_manifest", None)
        document["assets"] = []
        omitted = 0
        for asset in manifest["assets"]:
            if asset["byteLength"] > max_asset_bytes:
                omitted += 1
                continue
            if asset["assetId"] in seen:
                continue
            seen.add(asset["assetId"])
            name = asset["originalFilename"]
            document["assets"].append({
                "assetId": asset["assetId"], "uri": "asset://" + asset["assetId"],
                "width": asset["width"], "height": asset["height"], "byteLength": asset["byteLength"],
                "originalFilename": name, "suggestedRole": asset.get("suggestedRole", "unassigned"),
                "isAtlas": bool(re.search(r"spritesheet|tilesheet|tilemap|tileset|atlas", name, re.I)),
                "license": deepcopy(asset["license"]), "sourceUrl": asset["sourceUrl"],
                "author": asset.get("author", ""),
            })
        if omitted:
            document["inline_omitted"] = str(omitted) + " images exceed the per-image inline budget."
    return prepared


def embed_asset_documents(html, documents, *, max_html_bytes=300000):
    """Resolve references across verified portal bundles before host validation.

    Only actual references are embedded. Existing HTML/data URIs survive repair;
    unknown IDs and oversized output fail before a playable artifact is saved.
    """
    if not isinstance(html, str):
        raise AssetBundleError("HTML input must be a string")
    indexed = {}
    for document in documents:
        path = document.get("asset_manifest")
        if not path or document.get("verification") != "downloaded_hash_checked_art":
            continue
        manifest = _read_asset_manifest(path)
        for asset in manifest["assets"]:
            identifier = asset["assetId"]
            if identifier in indexed and indexed[identifier][0]["sha256"] != asset["sha256"]:
                raise AssetBundleError("Conflicting portal asset IDs")
            indexed.setdefault(identifier, (asset, Path(path).resolve().parent))
    pattern = re.compile(r"asset://([A-Za-z0-9_-]+)")
    matches = list(pattern.finditer(html))
    if not matches:
        if "asset://" in html:
            raise AssetBundleError("Unresolved portal asset reference")
        if len(html.encode("utf-8")) > max_html_bytes:
            raise AssetBundleError("Embedded HTML exceeds the host byte limit")
        return html
    identifiers = list(dict.fromkeys(m.group(1) for m in matches))
    replacements, used = {}, []
    estimated = len(html.encode("utf-8"))
    for identifier in identifiers:
        if identifier not in indexed:
            raise AssetBundleError("HTML references an unknown portal asset: " + identifier)
        asset, root = indexed[identifier]
        content = _asset_read_entry(root, asset, _asset_limits())
        count = sum(m.group(1) == identifier for m in matches)
        replacement_size = 22 + 4 * ((len(content) + 2) // 3)
        estimated += count * (replacement_size - len("asset://" + identifier))
        if estimated > max_html_bytes:
            raise AssetBundleError("Embedded HTML exceeds the host byte limit")
        replacements[identifier] = "data:image/png;base64," + base64.b64encode(content).decode("ascii")
        used.append(asset)
    output = pattern.sub(lambda match: replacements[match.group(1)], html)
    if "asset://" in output:
        raise AssetBundleError("Unresolved portal asset reference")
    # Replacing a prior footer avoids duplicate credits on repair. Credits are
    # text only so they satisfy the host's no-external-URL HTML contract.
    output = re.sub(r"<!-- material-assets-credits:start -->.*?<!-- material-assets-credits:end -->", "", output, flags=re.DOTALL)
    if used:
        items = []
        for asset in used:
            license_info = asset["license"]
            identifier = license_info.get("identifier", "See source license") if isinstance(license_info, dict) else license_info
            label = " · ".join([asset.get("author") or "素材作者见来源", asset["originalFilename"], str(identifier)])
            items.append("<li>" + _asset_html.escape(label) + "<br>" + _asset_html.escape(asset["sourceUrl"]) + "</li>")
        # Current public providers deliver CC0 assets. Preserve their source
        # information inside the artifact without adding a body flex/grid item
        # that could shrink the generated game's viewport or obscure controls.
        credits = ('<!-- material-assets-credits:start --><template data-material-asset-credits="true" '
                   'style="display:none!important"><ul>' + "".join(items)
                   + '</ul></template><!-- material-assets-credits:end -->')
        closing = re.search(r"</body\s*>", output, re.I)
        output = output[:closing.start()] + credits + output[closing.start():] if closing else output + credits
    if len(output.encode("utf-8")) > max_html_bytes:
        raise AssetBundleError("Embedded HTML exceeds the host byte limit")
    return output


class _ReferenceIndex:
    """Small deterministic bilingual BM25 index with explicit hard filters."""

    def __init__(self, records):
        self.records = deepcopy(records)
        self.documents = {}
        self.archetype_evidence = {}
        self.frequencies = Counter()
        for record in self.records:
            candidate, source = record["candidate"], record["candidate"]["source"]
            fields = ((candidate["title"], 4), (candidate["summary"], 2),
                      (" ".join(record["tags"]), 5), (record["searchText"], 1),
                      (" ".join(candidate["coverageHints"]), 1),
                      (" ".join(source["paths"] + source["symbols"]), 2))
            terms = Counter()
            for text, weight in fields:
                for term in _tokens(text):
                    terms[term] += weight
            self.documents[candidate["candidateId"]] = terms
            # Source paths, symbol names, compatibility caveats and control/UI
            # prose are not positive evidence of a shared game archetype.
            gameplay = " ".join([candidate["title"], *record["tags"], *candidate["coverageHints"]])
            self.archetype_evidence[candidate["candidateId"]] = (
                _archetypes(gameplay), _archetype_gameplay_terms(gameplay),
            )
            self.frequencies.update(terms.keys())
        lengths = [sum(terms.values()) for terms in self.documents.values()]
        self.average_length = sum(lengths) / max(1, len(lengths)) or 1

    def search(self, request):
        constraints = request["constraints"]
        texts = [request["description"]]
        texts.extend(image["description"] for image in request["images"])
        if request["purpose"] == "mechanic":
            texts.extend(request["requiredBehavior"])
        follow_up = request["discussion"]["followUp"]
        if follow_up:
            texts.append(follow_up["message"])
            # Explicit caller answers can refine retrieval. They remain claims,
            # and are never generated by the collector itself.
            texts.extend(answer["text"] for answer in follow_up["answers"] if answer["text"] is not None)
        query_terms = _tokens(" ".join(texts))
        requested_topics = _query_topics(request, texts)
        requested_archetypes = _archetypes(" ".join(texts)) if request["purpose"] == "archetype" else set()
        gameplay_terms = _archetype_gameplay_terms(" ".join(texts)) if request["purpose"] == "archetype" else set()
        limitations = [
            "Discovery searched {} embedded, pinned source references; remote content was not fetched or executed.".format(len(self.records)),
            _DISCOVERED_NOTE,
            "This is a finite metadata catalog, not live web search or packaged art/source delivery.",
        ]
        if request["images"]:
            limitations.append("Image URIs were not resolved or visually searched; retrieval used text only.")
        if constraints["engineVersion"]:
            limitations.append("Requested engine version compatibility has not been validated.")
        if requested_topics:
            limitations.append("Relevance was narrowed by explicit mechanic topics: " + ", ".join(sorted(requested_topics)))
        if requested_archetypes:
            limitations.append("Relevance was narrowed by explicitly named game archetypes: " + ", ".join(sorted(requested_archetypes)))
        if request["purpose"] == "archetype":
            limitations.append("Archetype relevance requires shared gameplay-theme evidence; controls, lifecycle, scoring and technical delivery terms alone are insufficient.")
        ranked = []
        for record in self.records:
            candidate = record["candidate"]
            if (candidate["kind"] not in request["materialKinds"]
                    or request["purpose"] not in record.get("purposes", ("archetype", "mechanic"))):
                continue
            engine, language = constraints["engine"], constraints["language"]
            if engine is not None and record["engines"] and engine not in record["engines"]:
                continue
            if language != "any" and record["languages"] and language not in record["languages"]:
                continue
            if request["purpose"] == "archetype":
                families, candidate_gameplay = self.archetype_evidence[candidate["candidateId"]]
                if requested_archetypes:
                    if not requested_archetypes.intersection(families):
                        continue
                elif len(gameplay_terms.intersection(candidate_gameplay)) < 2:
                    # Unnamed archetypes need multiple substantive clues, not
                    # one incidental grid/number/control match in a long prompt.
                    continue
            terms = self.documents[candidate["candidateId"]]
            if requested_topics and not requested_topics.intersection(_topics(set(terms))):
                continue
            normalization = 1.2 * (0.25 + 0.75 * sum(terms.values()) / self.average_length)
            score = 0.0
            for term in query_terms:
                frequency = terms.get(term, 0)
                if frequency:
                    df = self.frequencies[term]
                    inverse = math.log(1 + (len(self.records) - df + 0.5) / (df + 0.5))
                    score += inverse * frequency * 2.2 / (frequency + normalization)
            if score > 0:
                ranked.append((score, candidate["candidateId"], record))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        candidates, identities = [], set()
        for _, _, record in ranked:
            candidate = deepcopy(record["candidate"])
            identity = _json([candidate["kind"], candidate["source"]])
            if identity in identities:
                continue
            identities.add(identity)
            candidate["limitations"].append(_DISCOVERED_NOTE)
            missing_modes = sorted(set(constraints["inputModes"]) - set(record["inputModes"]))
            if missing_modes:
                candidate["limitations"].append("Input support is undeclared for {}; no runtime compatibility is established.".format(", ".join(missing_modes)))
            if constraints["engineVersion"]:
                candidate["limitations"].append("Compatibility with engine version '{}' is unverified.".format(constraints["engineVersion"]))
            candidates.append(candidate)
            if len(candidates) == request["limit"]:
                break
        if not candidates:
            limitations.append("No positive-evidence reference matched the description and hard filters; the engine/purpose constraints were not relaxed.")
        return candidates, limitations

# BEGIN EMBEDDED REFERENCE DATA
# Matching vocabulary shared with the existing statically checked catalog.
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

# Immutable source references; no cached source bytes or media are embedded.
# Reviewed 2026-10-17; update these records to expand local coverage.
_REFERENCES = json.loads(r'''
[
  {
    "candidate": {
      "candidateId": "ref-wukong-survivors",
      "kind": "game_repository",
      "title": "Wukong Survivors — Phaser/React survivor game repository",
      "summary": "真实悟空题材幸存者游戏仓库：俯视角角色移动、自动武器、敌人波次、掉落拾取、局内升级和永久成长。Repository and source evidence are pinned, without claiming a tested playable build.",
      "source": {
        "uri": "https://github.com/nusr/wukong-survivors/tree/bb28e7091ca6072731d74a71a36c5d38877c761c",
        "repositoryUrl": "https://github.com/nusr/wukong-survivors",
        "revision": "bb28e7091ca6072731d74a71a36c5d38877c761c",
        "paths": [
          "README.md",
          "package.json",
          "src/game/GameScene.ts",
          "src/game/weapon.ts",
          "src/game/enemy.ts",
          "src/game/experience.ts",
          "src/store/save.ts"
        ],
        "symbols": [
          "GameScene",
          "WeaponManager",
          "EnemySpawner",
          "ExperienceManager",
          "useSaveStore"
        ]
      },
      "coverageHints": [
        "survivors-like archetype",
        "top-down automatic combat",
        "enemy waves",
        "experience pickups",
        "permanent upgrades"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "Author describes a survivor game built with Phaser, React and Vite.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/README.md#L11"
        },
        {
          "description": "GameScene composes player, enemies, weapons, collectibles and UI.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/GameScene.ts#L42"
        },
        {
          "description": "Pinned manifest declares Phaser ^4.2.1, rather than assuming Phaser 3 compatibility.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/package.json#L34"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Pinned package.json declares Phaser ^4.2.1 with React/Zustand dependencies; compatibility with another engine version and independent reuse of named Wukong-themed media were not verified."
      ]
    },
    "searchText": "Wukong Survivors — Phaser/React survivor game repository 真实悟空题材幸存者游戏仓库：俯视角角色移动、自动武器、敌人波次、掉落拾取、局内升级和永久成长。Repository and source evidence are pinned, without claiming a tested playable build. survivors-like archetype top-down automatic combat enemy waves experience pickups permanent upgrades survivors vampire survivors roguelike archetype 幸存者 割草 吸血鬼幸存者 肉鸽 局内成长",
    "tags": [
      "survivors",
      "vampire survivors",
      "roguelike",
      "archetype",
      "幸存者",
      "割草",
      "吸血鬼幸存者",
      "肉鸽",
      "局内成长"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "typescript"
    ],
    "inputModes": [
      "keyboard",
      "touch"
    ],
    "purposes": [
      "archetype"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-wukong-combat-targeting",
      "kind": "source_code",
      "title": "Wukong — cooldown attacks and nearest-enemy targeting",
      "summary": "WeaponManager filters dead enemies and sorts by player distance before weapon updates; base Weapon fires on cooldown, creates projectiles and aims at sorted targets. 供自动攻击、最近敌人选取和弹道机制审查。",
      "source": {
        "uri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/weapon.ts",
        "repositoryUrl": "https://github.com/nusr/wukong-survivors",
        "revision": "bb28e7091ca6072731d74a71a36c5d38877c761c",
        "paths": [
          "src/game/weapon.ts",
          "src/game/GameScene.ts"
        ],
        "symbols": [
          "WeaponManager.update",
          "Weapon.update",
          "Weapon.fire",
          "Weapon.generateProjectileAngle"
        ]
      },
      "coverageHints": [
        "automatic cooldown attack",
        "nearest target ordering",
        "projectile direction",
        "damage collision integration"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "WeaponManager.update sorts active targets by Euclidean distance.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/weapon.ts#L1739"
        },
        {
          "description": "Weapon.update gates fire calls by cooldown.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/weapon.ts#L138"
        },
        {
          "description": "GameScene contains collision and damage integration for weapons and enemies.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/GameScene.ts#L607"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Pinned package.json declares Phaser ^4.2.1 with React/Zustand dependencies; compatibility with another engine version and independent reuse of named Wukong-themed media were not verified."
      ]
    },
    "searchText": "Wukong — cooldown attacks and nearest-enemy targeting WeaponManager filters dead enemies and sorts by player distance before weapon updates; base Weapon fires on cooldown, creates projectiles and aims at sorted targets. 供自动攻击、最近敌人选取和弹道机制审查。 automatic cooldown attack nearest target ordering projectile direction damage collision integration combat target targeting auto attack cooldown projectile 自动攻击 最近敌人 选取目标 战斗 弹道 攻击间隔",
    "tags": [
      "combat",
      "target",
      "targeting",
      "auto attack",
      "cooldown",
      "projectile",
      "自动攻击",
      "最近敌人",
      "选取目标",
      "战斗",
      "弹道",
      "攻击间隔"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "typescript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-wukong-wave-spawning",
      "kind": "source_code",
      "title": "Wukong — timed enemy waves with increasing difficulty",
      "summary": "EnemySpawner accumulates delta time, spawns bounded waves, chooses enemy types and increases wave size/frequency and population cap over time. 适合波次生成、难度曲线与屏外刷怪参考。",
      "source": {
        "uri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/enemy.ts",
        "repositoryUrl": "https://github.com/nusr/wukong-survivors",
        "revision": "bb28e7091ca6072731d74a71a36c5d38877c761c",
        "paths": [
          "src/game/enemy.ts"
        ],
        "symbols": [
          "EnemySpawner.update",
          "EnemySpawner.spawnWave",
          "EnemySpawner.increaseDifficulty",
          "EnemySpawner.selectEnemyType"
        ]
      },
      "coverageHints": [
        "timed wave spawning",
        "time-scaled difficulty",
        "active enemy population cap",
        "offscreen spawn position"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "spawnWave computes a time-scaled count bounded by the population cap.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/enemy.ts#L230"
        },
        {
          "description": "increaseDifficulty changes count, spawn interval and population bounds.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/enemy.ts#L215"
        },
        {
          "description": "spawnEnemy calculates positions around the player.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/enemy.ts#L251"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Pinned package.json declares Phaser ^4.2.1 with React/Zustand dependencies; compatibility with another engine version and independent reuse of named Wukong-themed media were not verified."
      ]
    },
    "searchText": "Wukong — timed enemy waves with increasing difficulty EnemySpawner accumulates delta time, spawns bounded waves, chooses enemy types and increases wave size/frequency and population cap over time. 适合波次生成、难度曲线与屏外刷怪参考。 timed wave spawning time-scaled difficulty active enemy population cap offscreen spawn position wave spawn survival enemy difficulty director 波次 刷怪 怪物生成 敌人数量 难度递增 时间驱动",
    "tags": [
      "wave",
      "spawn",
      "survival",
      "enemy",
      "difficulty",
      "director",
      "波次",
      "刷怪",
      "怪物生成",
      "敌人数量",
      "难度递增",
      "时间驱动"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "typescript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-wukong-collectibles-xp",
      "kind": "source_code",
      "title": "Wukong — magnetized experience gems and gold pickups",
      "summary": "CollectibleItem checks player distance, attracts items in a magnet radius and collects nearby drops; ExperienceManager routes gems to player experience and coins to saved gold. 供击杀掉落、拾取半径与经验经济循环参考。",
      "source": {
        "uri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/experience.ts",
        "repositoryUrl": "https://github.com/nusr/wukong-survivors",
        "revision": "bb28e7091ca6072731d74a71a36c5d38877c761c",
        "paths": [
          "src/game/experience.ts",
          "src/game/player.ts",
          "src/game/GameScene.ts"
        ],
        "symbols": [
          "CollectibleItem.update",
          "CollectibleItem.collect",
          "ExperienceManager.update",
          "ExperienceManager.spawnGem",
          "ExperienceManager.spawnCoin"
        ]
      },
      "coverageHints": [
        "magnetized pickup attraction",
        "experience gem collection",
        "gold coin collection",
        "drop spawning hooks"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "CollectibleItem.update activates attraction inside its adjusted magnet radius.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/experience.ts#L93"
        },
        {
          "description": "ExperienceManager applies collected gem value to player experience.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/experience.ts#L149"
        },
        {
          "description": "Collected coins increment persistent gold.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/experience.ts#L119"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Pinned package.json declares Phaser ^4.2.1 with React/Zustand dependencies; compatibility with another engine version and independent reuse of named Wukong-themed media were not verified."
      ]
    },
    "searchText": "Wukong — magnetized experience gems and gold pickups CollectibleItem checks player distance, attracts items in a magnet radius and collects nearby drops; ExperienceManager routes gems to player experience and coins to saved gold. 供击杀掉落、拾取半径与经验经济循环参考。 magnetized pickup attraction experience gem collection gold coin collection drop spawning hooks pickup collect magnet experience xp economy 掉落 经验球 金币 磁吸 拾取 收集半径",
    "tags": [
      "pickup",
      "collect",
      "magnet",
      "experience",
      "xp",
      "economy",
      "掉落",
      "经验球",
      "金币",
      "磁吸",
      "拾取",
      "收集半径"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "typescript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-wukong-run-progression",
      "kind": "source_code",
      "title": "Wukong — experience thresholds and in-run upgrade choice",
      "summary": "Player.levelUp carries excess XP, scales the next threshold and opens a choice menu; WeaponManager exposes upgrade options and GameScene applies the selected upgrade. 局内等级、武器成长和升级选择界面的源码参考。",
      "source": {
        "uri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/player.ts",
        "repositoryUrl": "https://github.com/nusr/wukong-survivors",
        "revision": "bb28e7091ca6072731d74a71a36c5d38877c761c",
        "paths": [
          "src/game/player.ts",
          "src/game/weapon.ts",
          "src/game/GameScene.ts"
        ],
        "symbols": [
          "Player.addExperience",
          "Player.levelUp",
          "WeaponManager.getUpgradeOptions",
          "GameScene.showLevelUpMenu",
          "GameScene.selectUpgrade"
        ]
      },
      "coverageHints": [
        "experience level threshold",
        "carry-over experience",
        "weapon upgrade options",
        "pause and select upgrade"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "Player.levelUp updates level, XP carry-over and next threshold.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/player.ts#L200"
        },
        {
          "description": "WeaponManager offers available weapon and upgrade options.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/weapon.ts#L1789"
        },
        {
          "description": "GameScene applies a chosen weapon upgrade or acquisition.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/game/GameScene.ts#L912"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Pinned package.json declares Phaser ^4.2.1 with React/Zustand dependencies; compatibility with another engine version and independent reuse of named Wukong-themed media were not verified.",
        "The inspected addExperience implementation checks one level threshold per call; repeated multi-level gains were not behavior-tested."
      ]
    },
    "searchText": "Wukong — experience thresholds and in-run upgrade choice Player.levelUp carries excess XP, scales the next threshold and opens a choice menu; WeaponManager exposes upgrade options and GameScene applies the selected upgrade. 局内等级、武器成长和升级选择界面的源码参考。 experience level threshold carry-over experience weapon upgrade options pause and select upgrade progression level up upgrade growth reward choice 升级 经验阈值 成长 局内升级 升级选项 武器升级",
    "tags": [
      "progression",
      "level up",
      "upgrade",
      "growth",
      "reward choice",
      "升级",
      "经验阈值",
      "成长",
      "局内升级",
      "升级选项",
      "武器升级"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "typescript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-wukong-persistent-economy",
      "kind": "source_code",
      "title": "Wukong — persistent gold economy and permanent upgrades",
      "summary": "Zustand save state supplies addGold/spendGold, checks upgrade cost and level caps, records permanent upgrades and persists JSON to localStorage. 可用于金币消费、永久属性成长、退款与存档方案审查。",
      "source": {
        "uri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/store/save.ts",
        "repositoryUrl": "https://github.com/nusr/wukong-survivors",
        "revision": "bb28e7091ca6072731d74a71a36c5d38877c761c",
        "paths": [
          "src/store/save.ts",
          "src/constant/rewards.ts",
          "src/game/GameScene.ts"
        ],
        "symbols": [
          "useSaveStore",
          "spendGold",
          "upgradePermanent",
          "resetPermanentUpgrades"
        ]
      },
      "coverageHints": [
        "gold balance",
        "cost-gated permanent upgrade",
        "upgrade level cap",
        "localStorage persistence",
        "upgrade refund"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "spendGold debits only when the stored balance covers the amount.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/store/save.ts#L14"
        },
        {
          "description": "upgradePermanent checks cost and maximum level before spending gold.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/store/save.ts#L17"
        },
        {
          "description": "Save store explicitly persists JSON in browser localStorage.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/store/save.ts#L130"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Pinned package.json declares Phaser ^4.2.1 with React/Zustand dependencies; compatibility with another engine version and independent reuse of named Wukong-themed media were not verified.",
        "This is client-side game state, not a server-authoritative or monetary payment system."
      ]
    },
    "searchText": "Wukong — persistent gold economy and permanent upgrades Zustand save state supplies addGold/spendGold, checks upgrade cost and level caps, records permanent upgrades and persists JSON to localStorage. 可用于金币消费、永久属性成长、退款与存档方案审查。 gold balance cost-gated permanent upgrade upgrade level cap localStorage persistence upgrade refund economy currency shop purchase permanent progression 金币 经济 消费 商店 永久升级 存档 退款",
    "tags": [
      "economy",
      "currency",
      "shop",
      "purchase",
      "permanent progression",
      "金币",
      "经济",
      "消费",
      "商店",
      "永久升级",
      "存档",
      "退款"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "typescript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-wukong-unlock-progression",
      "kind": "source_code",
      "title": "Wukong — character and chapter unlock conditions",
      "summary": "checkUnlocks computes available characters and chapters from kills, survival time, gold and completed chapters; selection actions check unlocked IDs. 供长期成就、章节解锁与角色成长结构参考。",
      "source": {
        "uri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/store/app.ts",
        "repositoryUrl": "https://github.com/nusr/wukong-survivors",
        "revision": "bb28e7091ca6072731d74a71a36c5d38877c761c",
        "paths": [
          "src/store/app.ts",
          "src/store/save.ts"
        ],
        "symbols": [
          "checkUnlocks",
          "useAppStore",
          "selectCharacter",
          "selectMap"
        ]
      },
      "coverageHints": [
        "character unlock gates",
        "chapter unlock gates",
        "kill/time/gold achievements",
        "unlocked selection enforcement"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "checkUnlocks derives available maps and characters from saved milestones.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/store/app.ts#L34"
        },
        {
          "description": "Character unlock switch checks kills, time, gold and chapter conditions.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/store/app.ts#L75"
        },
        {
          "description": "Character selection checks whether the requested ID is unlocked.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/src/store/app.ts#L127"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/nusr/wukong-survivors/blob/bb28e7091ca6072731d74a71a36c5d38877c761c/LICENSE"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Pinned package.json declares Phaser ^4.2.1 with React/Zustand dependencies; compatibility with another engine version and independent reuse of named Wukong-themed media were not verified."
      ]
    },
    "searchText": "Wukong — character and chapter unlock conditions checkUnlocks computes available characters and chapters from kills, survival time, gold and completed chapters; selection actions check unlocked IDs. 供长期成就、章节解锁与角色成长结构参考。 character unlock gates chapter unlock gates kill/time/gold achievements unlocked selection enforcement progression unlock achievement meta progression 解锁 成就 角色解锁 章节解锁 长期成长",
    "tags": [
      "progression",
      "unlock",
      "achievement",
      "meta progression",
      "解锁",
      "成就",
      "角色解锁",
      "章节解锁",
      "长期成长"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "typescript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-phaser-path-follower",
      "kind": "source_code",
      "title": "Official Phaser example — spline route following",
      "summary": "Builds Phaser.Curves.Spline from route coordinates, creates follower sprites and calls startFollow with a duration. 预定义路径/路点跟随的最小参考，不包含障碍物寻路。",
      "source": {
        "uri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/paths/followers/basic%20follower.js",
        "repositoryUrl": "https://github.com/phaserjs/examples",
        "revision": "68140eca72525d5034b783a262270715ddc17b78",
        "paths": [
          "public/src/paths/followers/basic follower.js"
        ],
        "symbols": [
          "Example.create",
          "Phaser.Curves.Spline",
          "startFollow"
        ]
      },
      "coverageHints": [
        "predefined waypoint route",
        "spline path following",
        "duration-based follower movement"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/README.md",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "Example creates a spline from a flat coordinate list.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/paths/followers/basic%20follower.js#L13"
        },
        {
          "description": "Follower starts a four-second traversal of that route.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/paths/followers/basic%20follower.js#L34"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/README.md"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "README explicitly states source code is MIT and assets are not; no referenced example asset files were downloaded.",
        "README identifies the current site as Phaser 4 examples/development builds, while package.json retains a Phaser 3 name and ISC metadata; exact engine-release compatibility and license metadata discrepancy require review.",
        "This example follows a predefined spline and does not compute obstacle-avoiding routes or dynamically replan a path."
      ]
    },
    "searchText": "Official Phaser example — spline route following Builds Phaser.Curves.Spline from route coordinates, creates follower sprites and calls startFollow with a duration. 预定义路径/路点跟随的最小参考，不包含障碍物寻路。 predefined waypoint route spline path following duration-based follower movement waypoint navigation path path following route spline 路径 路点 导航 沿路径移动 巡逻路线",
    "tags": [
      "waypoint",
      "navigation",
      "path",
      "path following",
      "route",
      "spline",
      "路径",
      "路点",
      "导航",
      "沿路径移动",
      "巡逻路线"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "javascript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-phaser-closest-target",
      "kind": "source_code",
      "title": "Official Phaser example — nearest and furthest physics-body query",
      "summary": "Calls Arcade physics.closest and furthest around a pointer-controlled cursor and draws connections to the selected bodies. 供最近目标查询和空间选择参考，非完整敌我过滤策略。",
      "source": {
        "uri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/closest%20furthest.js",
        "repositoryUrl": "https://github.com/phaserjs/examples",
        "revision": "68140eca72525d5034b783a262270715ddc17b78",
        "paths": [
          "public/src/physics/arcade/closest furthest.js"
        ],
        "symbols": [
          "Example.update",
          "physics.closest",
          "physics.furthest"
        ]
      },
      "coverageHints": [
        "nearest body query",
        "furthest body query",
        "pointer-driven target point"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/README.md",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "update queries the closest physics body to the cursor.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/closest%20furthest.js#L41"
        },
        {
          "description": "update also queries the furthest physics body.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/closest%20furthest.js#L42"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/README.md"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "README explicitly states source code is MIT and assets are not; no referenced example asset files were downloaded.",
        "README identifies the current site as Phaser 4 examples/development builds, while package.json retains a Phaser 3 name and ISC metadata; exact engine-release compatibility and license metadata discrepancy require review.",
        "The example demonstrates a body query, not team filtering, attack range checks, line of sight, or lock-on persistence."
      ]
    },
    "searchText": "Official Phaser example — nearest and furthest physics-body query Calls Arcade physics.closest and furthest around a pointer-controlled cursor and draws connections to the selected bodies. 供最近目标查询和空间选择参考，非完整敌我过滤策略。 nearest body query furthest body query pointer-driven target point target targeting nearest closest distance 选敌 目标选择 最近目标 距离查询 自动瞄准",
    "tags": [
      "target",
      "targeting",
      "nearest",
      "closest",
      "distance",
      "选敌",
      "目标选择",
      "最近目标",
      "距离查询",
      "自动瞄准"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "javascript"
    ],
    "inputModes": [
      "pointer"
    ],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-phaser-topdown-combat",
      "kind": "source_code",
      "title": "Official Phaser example — top-down shooter combat mechanics",
      "summary": "Demonstrates keyboard movement, pointer aim, pooled player/enemy bullets, collision callbacks, health damage and enemy firing cooldown. 俯视角射击的输入、伤害与敌人攻击实现参考。",
      "source": {
        "uri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/topdown%20shooter%20combat%20mechanics.js",
        "repositoryUrl": "https://github.com/phaserjs/examples",
        "revision": "68140eca72525d5034b783a262270715ddc17b78",
        "paths": [
          "public/src/physics/arcade/topdown shooter combat mechanics.js"
        ],
        "symbols": [
          "Example.enemyHitCallback",
          "Example.playerHitCallback",
          "Example.enemyFire",
          "Bullet.fire"
        ]
      },
      "coverageHints": [
        "top-down shooter",
        "bullet collision damage",
        "health reduction",
        "enemy firing cooldown",
        "keyboard and mouse aim"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/README.md",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "Enemy collision callback checks active objects, subtracts health and disables the bullet.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/topdown%20shooter%20combat%20mechanics.js#L158"
        },
        {
          "description": "enemyFire emits a bullet when its cooldown elapses.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/topdown%20shooter%20combat%20mechanics.js#L206"
        },
        {
          "description": "The demo explicitly requests mouse pointer lock.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/topdown%20shooter%20combat%20mechanics.js#L116"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/README.md"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "README explicitly states source code is MIT and assets are not; no referenced example asset files were downloaded.",
        "README identifies the current site as Phaser 4 examples/development builds, while package.json retains a Phaser 3 name and ISC metadata; exact engine-release compatibility and license metadata discrepancy require review.",
        "The mouse pointer-lock/WASD example does not establish touch controls or mobile gameplay compatibility."
      ]
    },
    "searchText": "Official Phaser example — top-down shooter combat mechanics Demonstrates keyboard movement, pointer aim, pooled player/enemy bullets, collision callbacks, health damage and enemy firing cooldown. 俯视角射击的输入、伤害与敌人攻击实现参考。 top-down shooter bullet collision damage health reduction enemy firing cooldown keyboard and mouse aim combat damage bullet shooter health cooldown 战斗 伤害 碰撞 生命值 子弹 射击",
    "tags": [
      "combat",
      "damage",
      "bullet",
      "shooter",
      "health",
      "cooldown",
      "战斗",
      "伤害",
      "碰撞",
      "生命值",
      "子弹",
      "射击"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "javascript"
    ],
    "inputModes": [
      "keyboard",
      "pointer"
    ],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-phaser-bullet-pool",
      "kind": "source_code",
      "title": "Official Phaser example — reusable projectile group",
      "summary": "Preallocates five Bullet sprites, obtains inactive projectiles with getFirstDead, reactivates on fire and disables them beyond the screen. 适用于弹幕、自动武器与低分配开销的子弹池实现参考。",
      "source": {
        "uri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/bullets%20group.js",
        "repositoryUrl": "https://github.com/phaserjs/examples",
        "revision": "68140eca72525d5034b783a262270715ddc17b78",
        "paths": [
          "public/src/physics/arcade/bullets group.js"
        ],
        "symbols": [
          "Bullet.fire",
          "Bullet.preUpdate",
          "Bullets.fireBullet"
        ]
      },
      "coverageHints": [
        "projectile pooling",
        "inactive object reuse",
        "out-of-bounds deactivation",
        "pointer fire input"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/README.md",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "Bullets preallocates a bounded pool of five projectiles.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/bullets%20group.js#L37"
        },
        {
          "description": "fireBullet obtains an inactive projectile instead of growing the pool.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/bullets%20group.js#L47"
        },
        {
          "description": "Bullet.preUpdate disables sprites after they pass the upper boundary.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/public/src/physics/arcade/bullets%20group.js#L22"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/phaserjs/examples/blob/68140eca72525d5034b783a262270715ddc17b78/README.md"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "README explicitly states source code is MIT and assets are not; no referenced example asset files were downloaded.",
        "README identifies the current site as Phaser 4 examples/development builds, while package.json retains a Phaser 3 name and ISC metadata; exact engine-release compatibility and license metadata discrepancy require review."
      ]
    },
    "searchText": "Official Phaser example — reusable projectile group Preallocates five Bullet sprites, obtains inactive projectiles with getFirstDead, reactivates on fire and disables them beyond the screen. 适用于弹幕、自动武器与低分配开销的子弹池实现参考。 projectile pooling inactive object reuse out-of-bounds deactivation pointer fire input combat projectile bullet pool object reuse 子弹池 对象池 复用 弹幕 射击 性能",
    "tags": [
      "combat",
      "projectile",
      "bullet",
      "pool",
      "object reuse",
      "子弹池",
      "对象池",
      "复用",
      "弹幕",
      "射击",
      "性能"
    ],
    "engines": [
      "phaser"
    ],
    "languages": [
      "javascript"
    ],
    "inputModes": [
      "pointer"
    ],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-babylon-recast-navigation",
      "kind": "source_code",
      "title": "Official Babylon.js — Recast navigation paths and crowd destinations",
      "summary": "RecastJSPlugin builds a navmesh, projects points and computes paths; RecastJSCrowd adds agents and assigns destinations with agentGoto. 3D导航网格、路径点和群集代理移动的源码参考。",
      "source": {
        "uri": "https://github.com/BabylonJS/Babylon.js/blob/1431d93dbd9dd6051575fafcc43db9806d0483c4/packages/dev/core/src/Navigation/Plugins/recastJSPlugin.ts",
        "repositoryUrl": "https://github.com/BabylonJS/Babylon.js",
        "revision": "1431d93dbd9dd6051575fafcc43db9806d0483c4",
        "paths": [
          "packages/dev/core/src/Navigation/Plugins/recastJSPlugin.ts"
        ],
        "symbols": [
          "RecastJSPlugin.createNavMesh",
          "RecastJSPlugin.getClosestPoint",
          "RecastJSPlugin.computePath",
          "RecastJSCrowd.addAgent",
          "RecastJSCrowd.agentGoto"
        ]
      },
      "coverageHints": [
        "3D navigation mesh",
        "path waypoint query",
        "destination-driven agent movement",
        "crowd navigation"
      ],
      "license": {
        "identifier": "Apache-2.0",
        "sourceUri": "https://github.com/BabylonJS/Babylon.js/blob/1431d93dbd9dd6051575fafcc43db9806d0483c4/license.md",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "createNavMesh exposes navmesh construction from meshes and parameters.",
          "sourceUri": "https://github.com/BabylonJS/Babylon.js/blob/1431d93dbd9dd6051575fafcc43db9806d0483c4/packages/dev/core/src/Navigation/Plugins/recastJSPlugin.ts#L137"
        },
        {
          "description": "computePath converts a Recast route to Babylon Vector3 points.",
          "sourceUri": "https://github.com/BabylonJS/Babylon.js/blob/1431d93dbd9dd6051575fafcc43db9806d0483c4/packages/dev/core/src/Navigation/Plugins/recastJSPlugin.ts#L370"
        },
        {
          "description": "RecastJSCrowd forwards an agent destination into the crowd engine.",
          "sourceUri": "https://github.com/BabylonJS/Babylon.js/blob/1431d93dbd9dd6051575fafcc43db9806d0483c4/packages/dev/core/src/Navigation/Plugins/recastJSPlugin.ts#L733"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/BabylonJS/Babylon.js/blob/1431d93dbd9dd6051575fafcc43db9806d0483c4/license.md"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Requires a separately supplied/initialized Recast runtime and scene/navmesh setup; those dependencies were not fetched or run, and their licenses are outside this recorded source-code evidence."
      ]
    },
    "searchText": "Official Babylon.js — Recast navigation paths and crowd destinations RecastJSPlugin builds a navmesh, projects points and computes paths; RecastJSCrowd adds agents and assigns destinations with agentGoto. 3D导航网格、路径点和群集代理移动的源码参考。 3D navigation mesh path waypoint query destination-driven agent movement crowd navigation waypoint navigation navmesh pathfinding recast crowd 3d 路径 路点 寻路 导航网格 群集 代理移动",
    "tags": [
      "waypoint",
      "navigation",
      "navmesh",
      "pathfinding",
      "recast",
      "crowd",
      "3d",
      "路径",
      "路点",
      "寻路",
      "导航网格",
      "群集",
      "代理移动"
    ],
    "engines": [
      "babylon"
    ],
    "languages": [
      "typescript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-pathfinding-grid-astar",
      "kind": "algorithm_documentation",
      "title": "PathFinding.js — A* grid routes and path smoothing documentation",
      "summary": "README documents grid-based A* with heuristic/diagonal options and smoothing; inspected findPath uses a heap/open set, grid neighbors and backtrace. 引擎无关的网格障碍寻路与路径点压缩/平滑参考。",
      "source": {
        "uri": "https://github.com/qiao/PathFinding.js/blob/2904a9afba483c02b5a61eb60fefd63e9b2db78c/README.md",
        "repositoryUrl": "https://github.com/qiao/PathFinding.js",
        "revision": "2904a9afba483c02b5a61eb60fefd63e9b2db78c",
        "paths": [
          "README.md",
          "src/finders/AStarFinder.js",
          "src/core/Util.js"
        ],
        "symbols": [
          "AStarFinder.findPath",
          "smoothenPath",
          "compressPath"
        ]
      },
      "coverageHints": [
        "grid obstacle pathfinding",
        "A* heuristic search",
        "diagonal movement options",
        "path smoothing and compression"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/qiao/PathFinding.js/blob/2904a9afba483c02b5a61eb60fefd63e9b2db78c/README.md",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "README explains construction and use of the A* finder.",
          "sourceUri": "https://github.com/qiao/PathFinding.js/blob/2904a9afba483c02b5a61eb60fefd63e9b2db78c/README.md#L110"
        },
        {
          "description": "findPath implements heap-based grid search and backtraces a reached target.",
          "sourceUri": "https://github.com/qiao/PathFinding.js/blob/2904a9afba483c02b5a61eb60fefd63e9b2db78c/src/finders/AStarFinder.js#L54"
        },
        {
          "description": "smoothenPath checks grid walkability along proposed shortcuts.",
          "sourceUri": "https://github.com/qiao/PathFinding.js/blob/2904a9afba483c02b5a61eb60fefd63e9b2db78c/src/core/Util.js#L136"
        },
        {
          "description": "compressPath removes redundant intermediate route points.",
          "sourceUri": "https://github.com/qiao/PathFinding.js/blob/2904a9afba483c02b5a61eb60fefd63e9b2db78c/src/core/Util.js#L186"
        },
        {
          "description": "Upstream source-code license evidence; this does not establish rights to third-party artwork, audio, or media.",
          "sourceUri": "https://github.com/qiao/PathFinding.js/blob/2904a9afba483c02b5a61eb60fefd63e9b2db78c/README.md"
        }
      ],
      "limitations": [
        "Static source inspection only: upstream code was not installed, built, played, or behavior-tested; coverageHints remain provider claims for reviewer investigation.",
        "This single-file portal contains source metadata and evidence links only; no upstream source bytes, runnable checkout or game/asset package is delivered.",
        "Code license evidence does not establish permissions for artwork, sprites, music, trademarks, or other assets; those require separate review.",
        "Returns grid-coordinate routes, not game-engine movement or character steering. CommonJS imports and the heap dependency need integration; dynamic obstacles and current build compatibility were not tested."
      ]
    },
    "searchText": "PathFinding.js — A* grid routes and path smoothing documentation README documents grid-based A* with heuristic/diagonal options and smoothing; inspected findPath uses a heap/open set, grid neighbors and backtrace. 引擎无关的网格障碍寻路与路径点压缩/平滑参考。 grid obstacle pathfinding A* heuristic search diagonal movement options path smoothing and compression waypoint navigation pathfinding astar a* grid route 寻路 路径 路点 导航 网格 障碍物 路径平滑",
    "tags": [
      "waypoint",
      "navigation",
      "pathfinding",
      "astar",
      "a*",
      "grid",
      "route",
      "寻路",
      "路径",
      "路点",
      "导航",
      "网格",
      "障碍物",
      "路径平滑"
    ],
    "engines": [],
    "languages": [
      "javascript"
    ],
    "inputModes": [],
    "purposes": [
      "mechanic"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-html-2048",
      "kind": "game_repository",
      "title": "2048 — native HTML/CSS and JavaScript sliding-number puzzle",
      "summary": "真实原生浏览器益智游戏仓库。A four-by-four sliding-number game separates grid movement and equal-value merging in GameManager, DOM tile rendering in HTMLActuator, and keyboard/swipe input. Static source evidence supports considering it as a small, turn-based grid-puzzle archetype.",
      "source": {
        "uri": "https://github.com/gabrielecirulli/2048/tree/478b6ec346e3787f589e4af751378d06ded4cbbc",
        "repositoryUrl": "https://github.com/gabrielecirulli/2048",
        "revision": "478b6ec346e3787f589e4af751378d06ded4cbbc",
        "paths": [
          "README.md",
          "LICENSE.txt",
          "index.html",
          "js/application.js",
          "js/game_manager.js",
          "js/keyboard_input_manager.js",
          "js/html_actuator.js"
        ],
        "symbols": [
          "GameManager",
          "GameManager.move",
          "GameManager.findFarthestPosition",
          "GameManager.movesAvailable",
          "KeyboardInputManager.listen",
          "HTMLActuator.addTile"
        ]
      },
      "coverageHints": [
        "turn-based grid puzzle archetype",
        "directional tile sliding and equal-value merging",
        "score and win/loss conditions",
        "DOM/CSS tile rendering",
        "keyboard and touch-swipe input"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/gabrielecirulli/2048/blob/478b6ec346e3787f589e4af751378d06ded4cbbc/LICENSE.txt",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "Pinned README identifies this as the 2048 game repository and links its MIT license. README and complete LICENSE.txt were read from the pinned source.",
          "sourceUri": "https://github.com/gabrielecirulli/2048/blob/478b6ec346e3787f589e4af751378d06ded4cbbc/README.md"
        },
        {
          "description": "Entry HTML loads local CSS and plain JavaScript modules; application.js constructs GameManager with board size 4, keyboard input, HTML actuator and local storage manager. No Phaser/Babylon runtime is referenced by this inspected entry.",
          "sourceUri": "https://github.com/gabrielecirulli/2048/blob/478b6ec346e3787f589e4af751378d06ded4cbbc/js/application.js#L3"
        },
        {
          "description": "GameManager.move traverses tiles in the requested direction, merges adjacent equal values once, adds merged value to score, recognizes 2048 and checks remaining moves. Entire game_manager.js was read; SHA-256 b02baa6b75f8c8cad8606e680b6f73b7c843c61e3a643ee48410022499a649f0.",
          "sourceUri": "https://github.com/gabrielecirulli/2048/blob/478b6ec346e3787f589e4af751378d06ded4cbbc/js/game_manager.js#L130"
        },
        {
          "description": "HTMLActuator.addTile creates div elements, sets position/value classes and writes the tile number as text; the playing tiles are rendered with DOM/CSS rather than a game engine sprite dependency.",
          "sourceUri": "https://github.com/gabrielecirulli/2048/blob/478b6ec346e3787f589e4af751378d06ded4cbbc/js/html_actuator.js#L49"
        },
        {
          "description": "KeyboardInputManager.listen binds keydown and touch start/move/end gestures and translates swipes into directional moves. Source inspected; browser/device behavior was not executed.",
          "sourceUri": "https://github.com/gabrielecirulli/2048/blob/478b6ec346e3787f589e4af751378d06ded4cbbc/js/keyboard_input_manager.js#L34"
        },
        {
          "description": "Full MIT source-code license and copyright attribution were read from this immutable revision.",
          "sourceUri": "https://github.com/gabrielecirulli/2048/blob/478b6ec346e3787f589e4af751378d06ded4cbbc/LICENSE.txt"
        }
      ],
      "limitations": [
        "Static source inspection only: the repository was not installed, built, opened in a browser, played or behavior-tested. Coverage hints are discovery claims for reviewer investigation, not verified functionality.",
        "The inspected entry depends on additional local grid/tile/storage/polyfill JavaScript and style/main.css. Only the listed entry and core implementation files were read; this record is not a complete runnable delivery.",
        "Native HTML/JavaScript metadata describes the inspected implementation and does not establish compatibility with a target browser version, accessibility requirement or mobile device.",
        "License evidence is scoped to source code; no separate permission for fonts, icons, startup images, screenshots, trademarks or other assets is inferred. No art or media was downloaded.",
        "This is a sliding-number puzzle archetype, not a survivor/combat implementation or a generic match for every HTML game description."
      ]
    },
    "searchText": "2048 native HTML CSS JavaScript browser game repository archetype puzzle sliding tiles grid merge numbers score win lose keyboard swipe touch 原生网页 小游戏 益智 数字 合并 滑动 棋盘 网格 回合制 方向键 触摸 手势 四乘四",
    "tags": [
      "2048",
      "html",
      "javascript",
      "puzzle",
      "grid",
      "sliding",
      "merge",
      "numbers",
      "keyboard",
      "touch",
      "益智",
      "数字合并",
      "网格",
      "滑动"
    ],
    "engines": [
      "html"
    ],
    "languages": [
      "javascript"
    ],
    "inputModes": [
      "keyboard",
      "touch"
    ],
    "purposes": [
      "archetype"
    ]
  },
  {
    "candidate": {
      "candidateId": "ref-html-tetris",
      "kind": "game_repository",
      "title": "JavaScript Tetris — native HTML5 Canvas falling-block game",
      "summary": "真实原生 HTML5 Canvas 俄罗斯方块仓库。The entry HTML includes the game loop, block occupancy, piece rotation, timed falling, row clearing, score updates and Canvas drawing. It is a compact real-time falling-block archetype implemented with plain JavaScript and browser APIs.",
      "source": {
        "uri": "https://github.com/jakesgordon/javascript-tetris/tree/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed",
        "repositoryUrl": "https://github.com/jakesgordon/javascript-tetris",
        "revision": "e5c0c42f7dac0f3514a55eff656c6e22e95d68ed",
        "paths": [
          "README.md",
          "LICENSE",
          "index.html",
          "stats.js"
        ],
        "symbols": [
          "run",
          "update",
          "eachblock",
          "occupied",
          "move",
          "rotate",
          "drop",
          "removeLines",
          "drawBlock"
        ]
      },
      "coverageHints": [
        "real-time falling-block puzzle archetype",
        "grid occupancy and piece rotation",
        "timed falling and piece locking",
        "complete-row removal and scoring",
        "native Canvas rendering and keyboard input"
      ],
      "license": {
        "identifier": "MIT",
        "sourceUri": "https://github.com/jakesgordon/javascript-tetris/blob/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed/LICENSE",
        "scope": "source_code"
      },
      "evidence": [
        {
          "description": "Pinned README describes an HTML5 Tetris game and lists touch support among future work rather than current functionality. README and complete LICENSE were read.",
          "sourceUri": "https://github.com/jakesgordon/javascript-tetris/blob/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed/README.md"
        },
        {
          "description": "Entry HTML declares the main/upcoming Canvas elements, references local stats.js, and contains inline game JavaScript. The entire 17,883-byte entry was read; SHA-256 b18ad94ae7c0c3199d1b42507a20a7c30de35b2e5393c8a05b22bf2ee8e9b468.",
          "sourceUri": "https://github.com/jakesgordon/javascript-tetris/blob/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed/index.html#L32"
        },
        {
          "description": "run establishes the frame loop using requestAnimationFrame, elapsed time, update and draw; the implementation uses browser Canvas APIs without a Phaser/Babylon runtime in its inspected entry.",
          "sourceUri": "https://github.com/jakesgordon/javascript-tetris/blob/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed/index.html#L173"
        },
        {
          "description": "move and rotate test unoccupied cells before changing piece position/orientation. drop locks a piece when downward movement fails, clears lines and advances the upcoming piece.",
          "sourceUri": "https://github.com/jakesgordon/javascript-tetris/blob/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed/index.html#L287"
        },
        {
          "description": "removeLines finds complete rows, shifts remaining blocks through removeLine and increments row count and score.",
          "sourceUri": "https://github.com/jakesgordon/javascript-tetris/blob/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed/index.html#L333"
        },
        {
          "description": "Entry source registers document keydown input. Its inspected code and README do not establish touch controls.",
          "sourceUri": "https://github.com/jakesgordon/javascript-tetris/blob/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed/index.html#L199"
        },
        {
          "description": "Full MIT source-code license and Jake Gordon/contributors copyright attribution were read at the pinned revision.",
          "sourceUri": "https://github.com/jakesgordon/javascript-tetris/blob/e5c0c42f7dac0f3514a55eff656c6e22e95d68ed/LICENSE"
        }
      ],
      "limitations": [
        "Static source inspection only: the game was not installed, built, opened in a browser, played or behavior-tested. Coverage hints are discovery claims awaiting review.",
        "The inspected HTML references local stats.js for performance display and texture.jpg for its background. stats.js was read and identifies itself as stats.js r6 from mrdoob/stats.js; the historical third-party dependency's separate licensing was not verified. texture.jpg was not downloaded and its asset rights remain unknown.",
        "The repository implements keyboard controls; the README lists touch support as future work. No mobile-playability or touch-compatibility claim is made.",
        "MIT evidence is scoped to project source code, not an independent rights grant for the bundled texture, third-party files, Tetris name, artwork or other assets.",
        "The record identifies a falling-block game reference, not compliance with a modern Tetris ruleset, rotation system, browser matrix or target mechanic contract."
      ]
    },
    "searchText": "Tetris native HTML5 Canvas JavaScript browser game repository archetype falling block puzzle grid rotation occupancy collision line clear scoring game loop keyboard 俄罗斯方块 原生网页 小游戏 落块 益智 方块 网格 旋转 消行 得分 游戏循环 方向键",
    "tags": [
      "tetris",
      "html",
      "canvas",
      "javascript",
      "falling-block",
      "puzzle",
      "grid",
      "rotation",
      "line-clear",
      "keyboard",
      "俄罗斯方块",
      "落块",
      "消行"
    ],
    "engines": [
      "html"
    ],
    "languages": [
      "javascript"
    ],
    "inputModes": [
      "keyboard"
    ],
    "purposes": [
      "archetype"
    ]
  }
]
''')
