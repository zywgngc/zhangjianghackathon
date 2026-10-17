import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import type { PortalErrorCode } from '../material-portal.ts';

export const githubLimits = Object.freeze({
  perIdea: 15,
  perMinute: 20,
  searchPerMinute: 4,
  perHour: 30,
  perDay: 120,
  intervalMs: 2000,
});
export class GitHubFailure extends Error {
  readonly code: PortalErrorCode;
  constructor(code: PortalErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

/** Shared by all local previews, regardless of their run database. Failure is fail-closed. */
export class GitHubNetworkStore {
  private db: DatabaseSync;
  readonly now: () => number;
  constructor(
    path = fileURLToPath(new URL('../.data/github-network.sqlite', import.meta.url)),
    now = Date.now,
  ) {
    this.now = now;
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path, { timeout: 5000 });
    this.db.exec(`PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS gate(id INTEGER PRIMARY KEY, until_ms INTEGER, reason TEXT, last_ms INTEGER, owner TEXT, lease_ms INTEGER);
      INSERT OR IGNORE INTO gate VALUES(1,0,'',0,NULL,0);
      CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, at_ms INTEGER);
      CREATE INDEX IF NOT EXISTS calls_time ON calls(at_ms);
      CREATE TABLE IF NOT EXISTS budgets(run_id TEXT PRIMARY KEY, used INTEGER);
      CREATE TABLE IF NOT EXISTS cache(path TEXT PRIMARY KEY, data TEXT, expires_ms INTEGER, saved_ms INTEGER);`);
  }
  close() {
    this.db.close();
  }
  status(runId?: string) {
    const gate = this.db.prepare('SELECT * FROM gate WHERE id=1').get()!;
    const count = (since: number) =>
      Number(this.db.prepare('SELECT count(*) AS n FROM calls WHERE at_ms>?').get(since)!.n);
    return {
      limits: githubLimits,
      cooldownUntil:
        Number(gate.until_ms) > this.now() ? new Date(Number(gate.until_ms)).toISOString() : null,
      cooldownReason: String(gate.reason),
      usedLastHour: count(this.now() - 3600000),
      usedLastDay: count(this.now() - 86400000),
      ...(runId
        ? {
            ideaRequests: Number(
              this.db.prepare('SELECT used FROM budgets WHERE run_id=?').get(runId)?.used ?? 0,
            ),
          }
        : {}),
    };
  }
  cached(path: string): { value: any; savedAt: number } | null {
    const row = this.db
      .prepare('SELECT * FROM cache WHERE path=? AND expires_ms>?')
      .get(path, this.now());
    return row ? { value: JSON.parse(String(row.data)), savedAt: Number(row.saved_ms) } : null;
  }
  cache(path: string, value: unknown, ttl: number) {
    this.db
      .prepare('INSERT OR REPLACE INTO cache VALUES(?,?,?,?)')
      .run(path, JSON.stringify(value), this.now() + ttl, this.now());
    this.db.exec(
      'DELETE FROM cache WHERE path IN (SELECT path FROM cache ORDER BY saved_ms DESC LIMIT -1 OFFSET 200)',
    );
  }
  reserve(runId: string, kind: 'search' | 'core'): { owner: string | null; waitMs: number } {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const now = this.now(),
        gate = this.db.prepare('SELECT * FROM gate WHERE id=1').get()!;
      const stop = (message: string): never => {
        throw new GitHubFailure('RATE_LIMITED', message);
      };
      if (Number(gate.until_ms) > now)
        stop(
          `GitHub 联网已暂停至 ${new Date(Number(gate.until_ms)).toISOString()}；${gate.reason}`,
        );
      const used = Number(
        this.db.prepare('SELECT used FROM budgets WHERE run_id=?').get(runId)?.used ?? 0,
      );
      if (used >= githubLimits.perIdea)
        stop('本创意已达到 15 次 GitHub HTTP 请求上限；追问不会重置额度。');
      const count = (ms: number, search = false) =>
        Number(
          this.db
            .prepare(
              `SELECT count(*) AS n FROM calls WHERE at_ms>?${search ? " AND kind='search'" : ''}`,
            )
            .get(now - ms)!.n,
        );
      if (
        count(60000) >= githubLimits.perMinute ||
        (kind === 'search' && count(60000, true) >= githubLimits.searchPerMinute) ||
        count(3600000) >= githubLimits.perHour ||
        count(86400000) >= githubLimits.perDay
      )
        stop('本机共享 GitHub 请求预算已达到上限；不自动重试，请稍后手动发起。');
      const waitMs = Math.max(
        Number(gate.last_ms) + githubLimits.intervalMs - now,
        gate.owner && Number(gate.lease_ms) > now ? Number(gate.lease_ms) - now : 0,
      );
      let result: { owner: string | null; waitMs: number };
      if (waitMs > 0) result = { owner: null, waitMs: Math.min(waitMs, 2000) };
      else {
        const owner = randomUUID();
        this.db
          .prepare('UPDATE gate SET owner=?, lease_ms=?, last_ms=? WHERE id=1')
          .run(owner, now + 30000, now);
        this.db.prepare('INSERT INTO calls VALUES(?,?,?,?)').run(owner, runId, kind, now);
        this.db
          .prepare('INSERT INTO budgets VALUES(?,1) ON CONFLICT(run_id) DO UPDATE SET used=used+1')
          .run(runId);
        this.db.prepare('DELETE FROM calls WHERE at_ms<=?').run(now - 86400000);
        result = { owner, waitMs: 0 };
      }
      this.db.exec('COMMIT');
      return result;
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }
  release(owner: string) {
    this.db.prepare('UPDATE gate SET owner=NULL, lease_ms=0 WHERE owner=?').run(owner);
  }
  pause(until: number, reason: string) {
    this.db
      .prepare('UPDATE gate SET until_ms=?, reason=? WHERE id=1 AND until_ms<?')
      .run(until, reason, until);
  }
  observe(response: Response) {
    const now = this.now();
    const retry = response.headers.get('retry-after'),
      reset = response.headers.get('x-ratelimit-reset');
    const retryAt =
      retry === null ? 0 : /^\d+$/.test(retry) ? now + Number(retry) * 1000 : Date.parse(retry);
    const resetAt = reset && /^\d+$/.test(reset) ? Number(reset) * 1000 : 0;
    const until = Math.max(
      now + 60000,
      Number.isFinite(retryAt) ? retryAt + 5000 : 0,
      resetAt + 5000,
    );
    if ([401, 403, 429].includes(response.status))
      this.pause(Math.max(now + 300000, until), `HTTP ${response.status}；禁止自动重试。`);
    else if (response.status >= 500) this.pause(now + 60000, 'GitHub 服务异常；暂停联网。');
    else if (
      response.headers.has('x-ratelimit-remaining') &&
      Number(response.headers.get('x-ratelimit-remaining')) <=
        (response.headers.get('x-ratelimit-resource') === 'search' ? 1 : 5)
    )
      this.pause(until, 'GitHub 剩余额度接近耗尽；预留安全余量。');
  }
}
