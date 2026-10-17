"""SQLite queue, event log and atomic per-stage artifacts. One server owns the queue."""
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / "harness.sqlite3"
        with self.connect() as con:
            con.executescript("""
                PRAGMA journal_mode=DELETE;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
                    stage TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
                    approved_version INTEGER, error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    time REAL NOT NULL, stage TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
            """)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    def create(self, request: dict, initial_status="queued") -> dict:
        if initial_status not in {"queued", "preparing"}:
            raise ValueError("invalid initial status")
        rid, now = uuid.uuid4().hex, time.time()
        with self.connect() as con:
            con.execute("INSERT INTO runs(id,request,status,stage,created,updated) VALUES(?,?,?,?,?,?)",
                        (rid, json.dumps(request, ensure_ascii=False), initial_status, initial_status, now, now))
        self.event(rid, initial_status, initial_status, {"message": "已加入生成队列" if initial_status == "queued" else "完整规格已保存，准备项目"})
        return self.get(rid)

    def request(self, rid: str):
        """Internal complete input; public polling never repeatedly transfers a long specification."""
        with self.connect() as con:
            row = con.execute("SELECT request FROM runs WHERE id=?", (rid,)).fetchone()
        return json.loads(row["request"]) if row else None

    def get(self, rid: str):
        with self.connect() as con:
            row = con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["request"] = json.loads(result["request"])
        if result["request"].get("mode") == "project":
            specification = result["request"].pop("specification", "")
            result["request"]["specification_characters"] = len(specification)
            result["request"]["specification_sha256"] = hashlib.sha256(specification.encode()).hexdigest()
        result["artifacts"] = sorted(p.name for p in self.directory(rid).iterdir() if not p.name.startswith("."))
        return result

    def list(self, limit=40):
        with self.connect() as con:
            rows = con.execute("SELECT id FROM runs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [self.get(row["id"]) for row in rows]

    def update(self, rid: str, **fields):
        if not set(fields) <= {"status", "stage", "approved_version", "error"}:
            raise ValueError("invalid update")
        fields["updated"] = time.time()
        with self.connect() as con:
            con.execute("UPDATE runs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), rid))

    def claim(self):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT id FROM runs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                con.execute("UPDATE runs SET status='running',updated=? WHERE id=?", (time.time(), row["id"]))
                return row["id"]

    def recover(self):
        with self.connect() as con:
            rows = con.execute("SELECT id FROM runs WHERE status='running'").fetchall()
            con.execute("UPDATE runs SET status='queued' WHERE status='running'")
        for row in rows:
            self.event(row["id"], "recovery", "recovered", {"message": "服务重启，从已有检查点恢复"})

    def reserve_call(self, rid: str, max_calls: int, max_tokens: int):
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT calls,tokens,status FROM runs WHERE id=?", (rid,)).fetchone()
            if row["status"] == "cancelled":
                raise RuntimeError("run cancelled")
            if row["calls"] >= max_calls or row["tokens"] >= max_tokens:
                raise RuntimeError("模型调用或 token 预算已用尽")
            con.execute("UPDATE runs SET calls=calls+1,updated=? WHERE id=?", (time.time(), rid))
            return max_tokens - row["tokens"]

    def charge(self, rid: str, tokens: int):
        with self.connect() as con:
            con.execute("UPDATE runs SET tokens=tokens+?,updated=? WHERE id=?", (tokens, time.time(), rid))

    def event(self, rid: str, stage: str, kind: str, payload: dict):
        with self.connect() as con:
            con.execute("INSERT INTO events(run_id,time,stage,kind,payload) VALUES(?,?,?,?,?)",
                        (rid, time.time(), stage, kind, json.dumps(payload, ensure_ascii=False)))

    def events(self, rid: str, after=0):
        with self.connect() as con:
            rows = con.execute("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 500", (rid, after)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def directory(self, rid: str):
        if not re.fullmatch(r"[a-f0-9]{32}", rid):
            raise ValueError("invalid run id")
        path = self.root / "runs" / rid
        path.mkdir(parents=True, exist_ok=True)
        return path

    def path(self, rid: str, name: str):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name.startswith("."):
            raise ValueError("invalid artifact name")
        return self.directory(rid) / name

    def write(self, rid: str, name: str, value):
        path = self.path(rid, name)
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, indent=2)
        data = value if isinstance(value, bytes) else value.encode("utf-8")
        tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
        return hashlib.sha256(data).hexdigest()

    def read_json(self, rid: str, name: str):
        path = self.path(rid, name)
        return json.loads(path.read_text()) if path.exists() else None
