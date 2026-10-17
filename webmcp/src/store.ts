import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { AppError } from './model.ts';
import type { Run } from './model.ts';

/** Local single-host store. BEGIN IMMEDIATE serializes reservations across connections/processes. */
export class RunStore {
  private db: DatabaseSync;
  constructor(path: string) {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path, { timeout: 5000 });
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, active_run TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, start_id TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(project_id,start_id));`);
  }
  private transaction<T>(work: () => T): T {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const value = work();
      this.db.exec('COMMIT');
      return value;
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }
  get(id: string): Run {
    const row = this.db.prepare('SELECT data FROM runs WHERE id=?').get(id) as
      { data: string } | undefined;
    if (!row) throw new AppError('NOT_FOUND', 'Unknown research run.', 404);
    return JSON.parse(row.data);
  }
  list(): Run[] {
    return (
      this.db.prepare('SELECT data FROM runs ORDER BY rowid').all() as Array<{ data: string }>
    ).map((r) => JSON.parse(r.data));
  }
  create(run: Run): Run {
    return this.transaction(() => {
      const previous = this.db
        .prepare('SELECT data FROM runs WHERE project_id=? AND start_id=?')
        .get(run.projectId, run.input.requestId) as { data: string } | undefined;
      if (previous) {
        const old: Run = JSON.parse(previous.data);
        if (old.startHash !== run.startHash)
          throw new AppError(
            'IDEMPOTENCY_CONFLICT',
            'Start request ID was reused with different input.',
          );
        return old;
      }
      const project = this.db
        .prepare('SELECT revision, active_run FROM projects WHERE id=?')
        .get(run.projectId) as { revision: number; active_run: string } | undefined;
      if (
        project &&
        ['queued', 'running', 'waiting_input'].includes(this.get(project.active_run).status)
      )
        throw new AppError(
          'RUN_ACTIVE',
          'Finish or cancel the active project run before starting another.',
        );
      run.revision = (project?.revision ?? 0) + 1;
      for (const job of run.jobs) job.request.projectRevision = run.revision;
      this.db
        .prepare('INSERT INTO runs(id,project_id,start_id,data) VALUES(?,?,?,?)')
        .run(run.id, run.projectId, run.input.requestId, JSON.stringify(run));
      this.db
        .prepare(
          'INSERT INTO projects(id,revision,active_run) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,active_run=excluded.active_run',
        )
        .run(run.projectId, run.revision, run.id);
      return run;
    });
  }
  mutate<T>(id: string, change: (run: Run) => T): T {
    return this.transaction(() => {
      const run = this.get(id);
      const project = this.db
        .prepare('SELECT active_run FROM projects WHERE id=?')
        .get(run.projectId) as { active_run: string };
      if (project.active_run !== id)
        throw new AppError('RUN_SUPERSEDED', 'A newer run owns this project.');
      const result = change(run);
      this.db.prepare('UPDATE runs SET data=? WHERE id=?').run(JSON.stringify(run), id);
      this.db.prepare('UPDATE projects SET revision=? WHERE id=?').run(run.revision, run.projectId);
      return result;
    });
  }
  close(): void {
    this.db.close();
  }
}
