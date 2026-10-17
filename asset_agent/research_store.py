"""Durable request identity and result cache; MAS follow-up budgets live upstream."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3


class RequestConflict(ValueError):
    pass


class ResearchRequestStore:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS material_requests (
                namespace TEXT NOT NULL, request_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, request_json TEXT NOT NULL,
                state TEXT NOT NULL, result_json TEXT,
                PRIMARY KEY (namespace, request_id))""")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        return {
            "fingerprint": row["fingerprint"],
            "request": json.loads(row["request_json"]),
            "state": row["state"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def get(self, namespace, request_id):
        with self._connect() as connection:
            return self._decode(connection.execute(
                "SELECT * FROM material_requests WHERE namespace=? AND request_id=?",
                (namespace, request_id),
            ).fetchone())

    def reserve(self, namespace, request, fingerprint):
        """Atomically reserve one operation, or return the existing operation.

        Pending operations are never redispatched after a timeout or restart.
        Explicitly retryable terminal provider errors may be tried again by a
        caller using the same ID; this method never performs automatic retries.
        """
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._decode(connection.execute(
                "SELECT * FROM material_requests WHERE namespace=? AND request_id=?",
                (namespace, request["requestId"]),
            ).fetchone())
            if row:
                if row["fingerprint"] != fingerprint:
                    raise RequestConflict("The requestId was already used with different input")
                result = row["result"]
                retry = row["state"] == "finished" and result and result.get("status") == "failed" and result["error"]["retryable"]
                if not retry:
                    return False, row
                connection.execute(
                    "UPDATE material_requests SET state='pending', result_json=NULL WHERE namespace=? AND request_id=?",
                    (namespace, request["requestId"]),
                )
            else:
                connection.execute(
                    "INSERT INTO material_requests VALUES (?, ?, ?, ?, 'pending', NULL)",
                    (namespace, request["requestId"], fingerprint, json.dumps(request, ensure_ascii=False, allow_nan=False)),
                )
            return True, {"fingerprint": fingerprint, "request": request, "state": "pending", "result": None}

    def finish(self, namespace, request_id, result):
        with self._connect() as connection:
            connection.execute(
                "UPDATE material_requests SET state='finished', result_json=? WHERE namespace=? AND request_id=? AND state='pending'",
                (json.dumps(result, ensure_ascii=False, allow_nan=False), namespace, request_id),
            )

    def cancel(self, namespace, request_id):
        with self._connect() as connection:
            connection.execute(
                "UPDATE material_requests SET state='cancelled', result_json=NULL WHERE namespace=? AND request_id=? AND state='pending'",
                (namespace, request_id),
            )
