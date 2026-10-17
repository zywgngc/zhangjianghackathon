"""Durable feature DAG and evidence ledger for multi-session project work."""
import json
import time


class ProjectLedger:
    def __init__(self, store):
        self.store = store
        with store.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS project_features (
                    run_id TEXT NOT NULL, feature_id TEXT NOT NULL, definition TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', owner TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    started REAL, updated REAL NOT NULL, result TEXT,
                    PRIMARY KEY(run_id, feature_id)
                );
                CREATE TABLE IF NOT EXISTS project_revisions (
                    run_id TEXT NOT NULL, revision INTEGER NOT NULL, source_sha256 TEXT NOT NULL,
                    manifest TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(run_id, revision)
                );
            """)

    @staticmethod
    def validate_plan(features):
        ids = [feature['id'] for feature in features]
        if not features or len(ids) != len(set(ids)):
            raise ValueError('功能清单为空或存在重复 ID')
        by_id = {f['id']: f for f in features}
        seen, visiting = set(), set()
        def visit(fid):
            if fid in visiting:
                raise ValueError('功能依赖存在环')
            if fid in seen:
                return
            visiting.add(fid)
            for dependency in by_id[fid].get('depends_on', []):
                if dependency not in by_id:
                    raise ValueError('功能引用未知依赖：' + dependency)
                visit(dependency)
            visiting.remove(fid)
            seen.add(fid)
        for fid in ids:
            visit(fid)

    def register(self, rid, features):
        self.validate_plan(features)
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            existing = con.execute('SELECT feature_id,definition,attempts FROM project_features WHERE run_id=?', (rid,)).fetchall()
            if existing:
                if {row['feature_id']: json.loads(row['definition']) for row in existing} == {f['id']: f for f in features}:
                    return
                if any(row['attempts'] for row in existing):
                    raise ValueError('已开始的功能清单不能被静默覆盖')
                con.execute('DELETE FROM project_features WHERE run_id=?', (rid,))
            for feature in features:
                con.execute('INSERT INTO project_features(run_id,feature_id,definition,updated) VALUES(?,?,?,?)',
                    (rid, feature['id'], json.dumps(feature, ensure_ascii=False), time.time()))

    def features(self, rid):
        with self.store.connect() as con:
            rows = con.execute('SELECT * FROM project_features WHERE run_id=? ORDER BY rowid', (rid,)).fetchall()
        return [{**dict(row), 'definition': json.loads(row['definition']),
                 'result': json.loads(row['result']) if row['result'] else None} for row in rows]

    def claim(self, rid):
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            rows = con.execute('SELECT * FROM project_features WHERE run_id=? ORDER BY rowid', (rid,)).fetchall()
            statuses = {row['feature_id']: row['status'] for row in rows}
            for row in rows:
                feature = json.loads(row['definition'])
                if row['status'] != 'pending' or not all(statuses.get(dep) in {'implemented', 'verified'} for dep in feature.get('depends_on', [])):
                    continue
                con.execute("UPDATE project_features SET status='running',owner=?,attempts=attempts+CASE WHEN started IS NULL OR result IS NOT NULL THEN 1 ELSE 0 END,started=?,updated=? WHERE run_id=? AND feature_id=?",
                    (feature.get('owner', 'implementation'), time.time(), time.time(), rid, row['feature_id']))
                return feature

    def finish(self, rid, fid, result):
        status = result.get('status', 'needs_work')
        if status not in {'implemented', 'verified', 'needs_work', 'blocked'}:
            status = 'needs_work'
        with self.store.connect() as con:
            con.execute('UPDATE project_features SET status=?,result=?,updated=? WHERE run_id=? AND feature_id=?',
                (status, json.dumps(result, ensure_ascii=False), time.time(), rid, fid))

    def recover(self, rid=None):
        with self.store.connect() as con:
            if rid:
                con.execute("UPDATE project_features SET status='pending',updated=? WHERE status='running' AND run_id=?", (time.time(), rid))
            else:
                con.execute("UPDATE project_features SET status='pending',updated=? WHERE status='running'", (time.time(),))

    def retry(self, rid, fid):
        with self.store.connect() as con:
            con.execute("UPDATE project_features SET status='pending',updated=? WHERE run_id=? AND feature_id=? AND status IN ('needs_work','blocked')",
                        (time.time(), rid, fid))

    def revision(self, rid, digest, manifest):
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            last = con.execute('SELECT revision,source_sha256 FROM project_revisions WHERE run_id=? ORDER BY revision DESC LIMIT 1', (rid,)).fetchone()
            if last and last['source_sha256'] == digest:
                return last['revision']
            revision = last['revision'] + 1 if last else 1
            con.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',
                (rid, revision, digest, json.dumps(manifest, ensure_ascii=False), time.time()))
            return revision

    def summary(self, rid):
        features = self.features(rid)
        counts = {status: sum(f['status'] == status for f in features)
                  for status in ['pending', 'running', 'implemented', 'verified', 'needs_work', 'blocked']}
        return {'counts': counts, 'features': features, 'completion_claim': 'Implementation status is separate from acceptance evidence'}
