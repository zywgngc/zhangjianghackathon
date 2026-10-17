import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { cleanupTemp } from './helpers.ts';

test('production startup uses the configured shared local state and remains offline with a token', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'webmcp-startup-'));
  const state = join(dir, 'shared', 'github.sqlite');
  const platform = Object.fromEntries(
    Object.entries(process.env).filter(([name]) =>
      ['PATH', 'Path', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR'].includes(
        name,
      ),
    ),
  );
  const child = spawn(process.execPath, ['src/server.ts'], {
    cwd: fileURLToPath(new URL('..', import.meta.url)),
    env: {
      ...platform,
      PORT: '0',
      WEBMCP_DB: join(dir, 'research.sqlite'),
      WEBMCP_GITHUB_STATE: state,
      WEBMCP_TOKEN: 'local-test-session-token',
      WEBMCP_GITHUB_TOKEN: 'not-a-real-token',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
  let output = '';
  try {
    const origin = await new Promise<string>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Local server startup timed out')), 10000);
      child.once('error', (e) => {
        clearTimeout(timeout);
        reject(e);
      });
      child.once('exit', () => {
        clearTimeout(timeout);
        reject(new Error(`Local server exited: ${output}`));
      });
      child.stdout.on('data', (chunk) => {
        output += chunk.toString();
        const match = /Research studio: (http:\/\/127\.0\.0\.1:\d+)/.exec(output);
        if (match) {
          clearTimeout(timeout);
          resolve(match[1]);
        }
      });
      child.stderr.on('data', (chunk) => {
        output += chunk.toString();
      });
    });
    const headers = {
      Authorization: 'Bearer local-test-session-token',
      'Content-Type': 'application/json',
    };
    const providers = await (await fetch(`${origin}/api/providers`, { headers })).json();
    assert.equal(providers.github.enabled, false);
    assert.equal(providers.github.usedLastHour, 0);
    assert.ok(existsSync(state));
    assert.ok(existsSync(join(dir, 'research.sqlite')));
    const response = await fetch(`${origin}/api/runs`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        requestId: 'offline-smoke',
        projectId: 'offline-smoke',
        mode: 'idea',
        brief: '简单跑酷游戏',
      }),
    });
    assert.equal(response.status, 202);
    const { runId } = await response.json();
    let run;
    const until = Date.now() + 5000;
    do {
      run = await (await fetch(`${origin}/api/runs/${runId}`, { headers })).json();
      if (run.status === 'waiting_input') break;
      await new Promise((resolve) => setTimeout(resolve, 30));
    } while (Date.now() < until);
    assert.equal(run.status, 'waiting_input');
    assert.equal(run.githubNetwork.ideaRequests, 0);
    assert.equal(run.jobs[0].result.status, 'failed');
    assert.match(run.jobs[0].result.error.message, /联网已关闭/);
  } finally {
    child.kill();
    await exited;
    cleanupTemp(dir);
  }
});
