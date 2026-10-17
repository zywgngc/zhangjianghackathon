"""Executable Webmcp v0.2 material-discovery port with honest evidence boundaries."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
from pathlib import Path
import threading
import time
from urllib.error import HTTPError

from .reference_catalog import LocalReferenceProvider
from .research_contracts import validate_clarification_reply, validate_search_request, validate_search_result
from .research_store import ResearchRequestStore, RequestConflict


ERROR_CODES = {"UNAVAILABLE", "TIMEOUT", "RATE_LIMITED", "INVALID_INPUT", "UNSUPPORTED_INPUT", "AUTH_REQUIRED"}


class ProviderError(Exception):
    """A provider's expected, user-safe failure; never include credentials."""
    def __init__(self, code, message, *, retryable=False, retry_after_ms=None):
        if code not in ERROR_CODES:
            raise ValueError("Unknown portal error code")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Provider error needs a message")
        if not isinstance(retryable, bool):
            raise ValueError("retryable must be boolean")
        if retry_after_ms is not None and (isinstance(retry_after_ms, bool) or not isinstance(retry_after_ms, int) or retry_after_ms < 0):
            raise ValueError("retry_after_ms must be a nonnegative integer or None")
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable, self.retry_after_ms = retryable, retry_after_ms


class _Signal:
    def __init__(self, external=None):
        self.local = threading.Event()
        self.external = external

    def set(self):
        self.local.set()

    def is_set(self):
        return self.local.is_set() or (self.external is not None and self.external.is_set())


class ResearchMaterialPortal:
    """Search source/code/document references using the exact v0.2 wire schema.

    The provider is a synchronous ``search(request, cancel_event=...)`` object
    or callable. A completed search is discovery, never behavior verification.
    This class does not own MAS project revisions or run-wide follow-up slots.
    """
    def __init__(self, catalog_dir="catalogs/references", *, provider=None,
                 cache_path=".asset-agent/research-requests.sqlite3", timeout_seconds=10,
                 provider_name=None, allow_mock=False, max_workers=4, require_parent=False):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        self.provider = provider if provider is not None else LocalReferenceProvider(catalog_dir)
        self.provider_name = provider_name or getattr(self.provider, "name", "material-reference-provider")
        if not isinstance(self.provider_name, str) or not self.provider_name.strip():
            raise ValueError("provider_name must be nonempty")
        self.allow_mock = allow_mock
        if not isinstance(allow_mock, bool) or not isinstance(require_parent, bool):
            raise ValueError("allow_mock and require_parent must be booleans")
        self.require_parent = require_parent
        self.timeout_seconds = float(timeout_seconds)
        self.store = ResearchRequestStore(cache_path)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="material-research")
        self._closed = False

    def _metadata(self, request):
        # Invalid requests still receive a schema-valid INVALID_INPUT result.
        # Usable correlation fields are preserved; absent/invalid fields get
        # explicit sentinel values, rather than trusting malformed input.
        request = request if isinstance(request, dict) else {}
        discussion = request.get("discussion")
        discussion = discussion if isinstance(discussion, dict) else {}
        def identifier(value, fallback):
            return value if isinstance(value, str) and len(value) > 0 else fallback
        round_number = discussion.get("round")
        if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number not in range(4):
            round_number = 0
        return {
            "contractVersion": "0.2", "requestId": identifier(request.get("requestId"), "invalid-request"),
            "discussion": {"runId": identifier(discussion.get("runId"), "invalid-run"),
                           "threadId": identifier(discussion.get("threadId"), "invalid-thread"), "round": round_number},
            "provider": self.provider_name,
            "retrievedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "isMock": False,
        }

    def _failure(self, request, code, message, *, retryable=False, retry_after_ms=None):
        result = dict(self._metadata(request), status="failed", error={
            "code": code, "message": message, "retryable": retryable, "retryAfterMs": retry_after_ms,
        })
        return validate_search_result(result)

    @staticmethod
    def _signal(options):
        if options is None:
            return _Signal()
        if not isinstance(options, dict) or set(options) - {"signal"}:
            raise ValueError("options accepts only signal")
        external = options.get("signal")
        if external is not None and not callable(getattr(external, "is_set", None)):
            raise ValueError("signal must expose is_set(), such as threading.Event")
        return _Signal(external)

    def _parent_notes(self, request):
        discussion = request["discussion"]
        if discussion["round"] == 0:
            return []
        parent = self.store.get(self.provider_name, discussion["inReplyToRequestId"])
        if parent is None:
            if self.require_parent:
                raise ValueError("Follow-up parent is not present in this provider cache")
            # The port explicitly permits stateless, full-snapshot follow-ups.
            # The upstream orchestrator must enforce its run-wide ledger.
            return ["The parent request is not in this provider cache; upstream must validate the parent/clarification chain and reserve its run-wide follow-up slot."]
        if parent["request"]["projectRevision"] > request["projectRevision"]:
            raise ValueError("Follow-up projectRevision is older than its parent request")
        if parent["state"] != "finished":
            raise ValueError("Follow-up parent does not have a completed response")
        validate_clarification_reply(request, parent["request"], parent["result"])
        return []

    def _invoke_provider(self, request, signal):
        method = getattr(self.provider, "search", self.provider)
        if not callable(method):
            raise ProviderError("UNAVAILABLE", "No callable material provider is configured")
        parameters = inspect.signature(method).parameters.values()
        accepts_cancel = any(p.name == "cancel_event" or p.kind == p.VAR_KEYWORD for p in parameters)
        if accepts_cancel:
            result = method(copy.deepcopy(request), cancel_event=signal)
        else:
            result = method(copy.deepcopy(request))
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise ProviderError("UNSUPPORTED_INPUT", "This Python provider adapter requires a synchronous provider callable")
        return result

    def _convert(self, raw, request, notes):
        if not isinstance(raw, dict) or not isinstance(raw.get("isMock"), bool):
            raise ValueError("Provider must explicitly identify whether its data is mock")
        if raw["isMock"] and not self.allow_mock:
            raise ProviderError("UNSUPPORTED_INPUT", "Synthetic fixture providers require explicit allow_mock=True")
        if "contractVersion" in raw:
            result = validate_search_result(raw, request)
        else:
            status = raw.get("status") or ("partial" if raw.get("partial", False) else "completed")
            if status not in {"completed", "partial", "needs_clarification"}:
                raise ValueError("Provider returned an unsupported result variant")
            result = dict(self._metadata(request), status=status, isMock=raw["isMock"],
                          candidates=copy.deepcopy(raw.get("candidates", [])),
                          limitations=copy.deepcopy(raw.get("limitations", [])))
            if status == "needs_clarification":
                result["clarificationId"] = raw.get("clarificationId")
                result["questions"] = copy.deepcopy(raw.get("questions"))
        if result["status"] != "failed":
            result["limitations"].extend(notes)
            processed_images = raw.get("imagesProcessed", getattr(self.provider, "supports_images", False))
            if not isinstance(processed_images, bool):
                raise ValueError("Provider image capability must be explicit boolean")
            if request["images"] and not processed_images:
                note = "Reference images were not visually analyzed by this provider; results use the textual description and required behavior only."
                if note not in result["limitations"]:
                    result["limitations"].append(note)
                if result["status"] == "completed":
                    result["status"] = "partial"
            result["limitations"] = list(dict.fromkeys(result["limitations"]))
        return validate_search_result(result, request)

    def _execute(self, request, signal, notes):
        try:
            if signal.is_set():
                raise asyncio.CancelledError()
            raw = self._invoke_provider(request, signal)
            if signal.is_set():
                raise asyncio.CancelledError()
            result = self._convert(raw, request, notes)
        except asyncio.CancelledError:
            self.store.cancel(self.provider_name, request["requestId"])
            return
        except ProviderError as exc:
            result = self._failure(request, exc.code, exc.message, retryable=exc.retryable, retry_after_ms=exc.retry_after_ms)
        except TimeoutError:
            result = self._failure(request, "TIMEOUT", "The material provider timed out", retryable=True)
        except PermissionError:
            result = self._failure(request, "AUTH_REQUIRED", "The material provider requires configured credentials")
        except HTTPError as exc:
            code = "RATE_LIMITED" if exc.code == 429 else "AUTH_REQUIRED" if exc.code in {401, 403} else "UNAVAILABLE"
            retry_after = None
            try:
                seconds = float(exc.headers.get("Retry-After", ""))
                if math.isfinite(seconds) and seconds >= 0:
                    retry_after = min(3600000, int(seconds * 1000))
            except (ValueError, TypeError, AttributeError):
                pass
            result = self._failure(request, code, "The material provider returned an HTTP error", retryable=code in {"RATE_LIMITED", "UNAVAILABLE"}, retry_after_ms=retry_after)
        except (ValueError, TypeError):
            result = self._failure(request, "UNAVAILABLE", "The material provider returned invalid contract data")
        except (OSError, RuntimeError):
            result = self._failure(request, "UNAVAILABLE", "The material provider is unavailable", retryable=True)
        except Exception:
            result = self._failure(request, "UNAVAILABLE", "The material provider could not complete this request")
        self.store.finish(self.provider_name, request["requestId"], result)

    def search_sync(self, request, options=None):
        raw_request = request
        if self._closed:
            return self._failure(raw_request, "UNAVAILABLE", "This material portal instance is closed")
        try:
            request = validate_search_request(request)
            signal = self._signal(options)
            if signal.is_set():
                raise asyncio.CancelledError()
            notes = self._parent_notes(request)
            fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
            acquired, cached = self.store.reserve(self.provider_name, request, fingerprint)
        except (ValueError, RequestConflict) as exc:
            return self._failure(raw_request, "INVALID_INPUT", str(exc))
        if cached["state"] == "cancelled":
            raise asyncio.CancelledError()
        if acquired:
            try:
                self._executor.submit(self._execute, request, signal, notes)
            except RuntimeError:
                result = self._failure(request, "UNAVAILABLE", "The portal closed before provider dispatch; this operation did not start", retryable=True)
                self.store.finish(self.provider_name, request["requestId"], result)
                return result
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if signal.is_set():
                self.store.cancel(self.provider_name, request["requestId"])
                raise asyncio.CancelledError()
            current = self.store.get(self.provider_name, request["requestId"])
            if current["state"] == "finished":
                result = validate_search_result(current["result"], request)
                if result["isMock"] and not self.allow_mock:
                    return self._failure(request, "UNSUPPORTED_INPUT", "Cached synthetic fixture results require explicit allow_mock=True")
                return result
            if current["state"] == "cancelled":
                raise asyncio.CancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._failure(request, "TIMEOUT", "The operation is still pending; retry the identical request to retrieve its result without dispatching duplicate work", retryable=True, retry_after_ms=100)
            signal.local.wait(min(0.02, remaining))

    async def search(self, request, options=None):
        try:
            signal = self._signal(options)
        except ValueError as exc:
            return self._failure(request, "INVALID_INPUT", str(exc))
        try:
            return await asyncio.to_thread(self.search_sync, request, {"signal": signal})
        except asyncio.CancelledError:
            signal.set()
            raise

    def get_request_status(self, request_id):
        """Inspect cached/pending work without dispatching a new operation."""
        return self.store.get(self.provider_name, request_id)

    def close(self, *, wait=True):
        self._closed = True
        self._executor.shutdown(wait=wait)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
