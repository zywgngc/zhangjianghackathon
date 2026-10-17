"""Optional embeddings with explicit network access and model-scoped caching.

There is deliberately no pretend offline embedding implementation. Callers may
provide their own ``model_id`` / ``embed(texts)`` implementation, or explicitly
instantiate HTTPEmbeddingProvider for a compatible embeddings endpoint.
"""

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class EmbeddingError(ValueError):
    pass


def validate_vectors(vectors, expected_count, expected_dimension=None):
    if not isinstance(vectors, (list, tuple)) or len(vectors) != expected_count:
        raise EmbeddingError("Embedding provider returned the wrong vector count")
    dimension = expected_dimension
    result = []
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or not vector:
            raise EmbeddingError("Embedding vectors must be nonempty numeric lists")
        if dimension is None:
            dimension = len(vector)
        if len(vector) != dimension:
            raise EmbeddingError("Embedding vector dimensions do not match")
        converted = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingError("Embedding vector values must be finite numbers")
            try:
                number = float(value)
            except (ValueError, OverflowError) as exc:
                raise EmbeddingError("Embedding vector values must be finite numbers") from exc
            if not math.isfinite(number):
                raise EmbeddingError("Embedding vector values must be finite numbers")
            converted.append(number)
        result.append(converted)
    return result


def cosine_similarity(left, right):
    left, right = validate_vectors([left, right], 2)

    def unit(vector):
        scale = max(abs(number) for number in vector)
        if not scale:
            return [0.0] * len(vector)
        scaled = [number / scale for number in vector]
        norm = math.sqrt(math.fsum(number * number for number in scaled))
        return [number / norm for number in scaled]

    score = math.fsum(a * b for a, b in zip(unit(left), unit(right)))
    return max(-1.0, min(1.0, score))


class HTTPEmbeddingProvider:
    """An explicitly enabled HTTP provider; construction itself makes no request."""

    def __init__(self, base_url, model, api_key=None, timeout=20):
        if not isinstance(base_url, str) or not isinstance(model, str) or not model.strip():
            raise ValueError("Embedding base_url and model are required")
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("Embedding base_url must be an HTTP(S) URL without query or fragment")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Embedding base_url must not contain URL credentials; use api_key separately")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Embedding timeout must be positive and finite")
        self.endpoint = base_url.rstrip("/")
        if not self.endpoint.endswith("/embeddings"):
            self.endpoint += "/embeddings"
        self.model = model
        # Different servers may use the same model name for different spaces.
        self.model_id = "{}::{}".format(self.endpoint, model)
        self.api_key = api_key
        self.timeout = timeout

    def embed(self, texts):
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise EmbeddingError("Embedding input must be a list of strings")
        if not texts:
            return []
        payload = json.dumps({"model": self.model, "input": texts}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(self.endpoint, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise EmbeddingError("Embedding response exceeded 16 MiB")
            document = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            raise EmbeddingError("Embedding request failed with HTTP {}".format(exc.code)) from exc
        except (URLError, OSError, UnicodeError, ValueError) as exc:
            raise EmbeddingError("Embedding request or response failed: {}".format(type(exc).__name__)) from exc
        data = document.get("data") if isinstance(document, dict) else None
        if not isinstance(data, list) or len(data) != len(texts) or any(not isinstance(item, dict) for item in data):
            raise EmbeddingError("Embedding response needs one data item per input")
        if any("index" in item for item in data):
            indices = [item.get("index") for item in data]
            if any(isinstance(index, bool) or not isinstance(index, int) for index in indices) or sorted(indices) != list(range(len(texts))):
                raise EmbeddingError("Embedding response indices must be unique and complete")
            data = sorted(data, key=lambda item: item["index"])
        return validate_vectors([item.get("embedding") for item in data], len(texts))


class SQLiteEmbeddingCache:
    """Cache by provider model identity plus SHA-256 of the exact input text."""

    def __init__(self, cache_path=":memory:"):
        if str(cache_path) != ":memory:":
            Path(cache_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            cache_path = str(Path(cache_path).expanduser())
        self.connection = sqlite3.connect(str(cache_path))
        self.connection.execute("CREATE TABLE IF NOT EXISTS embedding_models (model_id TEXT PRIMARY KEY, dimension INTEGER NOT NULL)")
        self.connection.execute("CREATE TABLE IF NOT EXISTS embedding_vectors (model_id TEXT NOT NULL, text_hash TEXT NOT NULL, dimension INTEGER NOT NULL, vector_json TEXT NOT NULL, PRIMARY KEY (model_id, text_hash))")
        self.connection.commit()
        self.last_report = {"cache_hits": 0, "embedded_texts": 0}

    def embed(self, provider, texts):
        model_id = getattr(provider, "model_id", None)
        if not isinstance(model_id, str) or not model_id.strip():
            raise EmbeddingError("Embedding provider must expose a nonempty model_id")
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise EmbeddingError("Embedding input must be a list of strings")
        row = self.connection.execute("SELECT dimension FROM embedding_models WHERE model_id = ?", (model_id,)).fetchone()
        dimension = row[0] if row else None
        hashes = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
        found = {}
        missing = {}
        hits = 0
        for digest, text in zip(hashes, texts):
            if digest in found:
                hits += 1
                continue
            if digest in missing:
                continue
            row = self.connection.execute("SELECT vector_json, dimension FROM embedding_vectors WHERE model_id = ? AND text_hash = ?", (model_id, digest)).fetchone()
            if row:
                try:
                    vector = validate_vectors([json.loads(row[0])], 1, dimension)[0]
                except (ValueError, TypeError) as exc:
                    raise EmbeddingError("Invalid embedding cache entry") from exc
                if len(vector) != row[1]:
                    raise EmbeddingError("Embedding cache dimension metadata does not match")
                dimension = len(vector)
                found[digest] = vector
                hits += 1
            else:
                missing[digest] = text
        if missing:
            vectors = validate_vectors(provider.embed(list(missing.values())), len(missing), dimension)
            dimension = len(vectors[0])
            with self.connection:
                self.connection.execute("INSERT OR IGNORE INTO embedding_models(model_id, dimension) VALUES (?, ?)", (model_id, dimension))
                stored = self.connection.execute("SELECT dimension FROM embedding_models WHERE model_id = ?", (model_id,)).fetchone()[0]
                if stored != dimension:
                    raise EmbeddingError("Embedding model changed dimensions without changing model_id")
                for digest, vector in zip(missing, vectors):
                    self.connection.execute("INSERT OR REPLACE INTO embedding_vectors(model_id, text_hash, dimension, vector_json) VALUES (?, ?, ?, ?)", (model_id, digest, dimension, json.dumps(vector, allow_nan=False)))
                    found[digest] = vector
        self.last_report = {"cache_hits": hits, "embedded_texts": len(missing), "model_id": model_id, "dimensions": dimension}
        return [list(found[digest]) for digest in hashes]

    def close(self):
        self.connection.close()
