import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { GitHubNetworkStore } from '../src/github-network.ts';
import { GitHubArchetypeResearcher, githubQueries } from '../src/github-archetypes.ts';
import { cleanupTemp, setup } from './helpers.ts';
import { MockMaterialPortal } from '../src/portal.ts';
import type { MaterialSearchRequest } from '../material-portal.ts';

const request: MaterialSearchRequest = {
  contractVersion: '0.2',
  requestId: 'query',
  projectRevision: 1,
  purpose: 'archetype',
  materialKinds: ['game_repository'],
  limit: 3,
  description: 'archetype=runner',
  images: [],
  discussion: {
    runId: 'idea',
    threadId: 'archetypes',
    round: 0,
    inReplyToRequestId: null,
    followUp: null,
  },
  constraints: {
    engine: null,
    engineVersion: null,
    platform: 'web',
    language: 'typescript',
    inputModes: ['touch'],
  },
};
const limited = (fn: () => unknown) =>
  assert.throws(fn, (e: unknown) => e instanceof Error && 'code' in e && e.code === 'RATE_LIMITED');

test('default cache-only sends zero requests even with a token; persisted cached queries still work', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'webmcp-network-'));
  const path = join(dir, 'network.sqlite');
  let calls = 0;
  let state = new GitHubNetworkStore(path);
  let provider = new GitHubArchetypeResearcher({
    network: state,
    token: 'fake-test-token',
    fetch: async () => {
      calls++;
      throw new Error('Network must remain off');
    },
  });
  try {
    assert.equal((await provider.search(request)).status, 'failed');
    assert.equal(calls, 0);
    for (const q of githubQueries(request))
      state.cache(
        `/search/repositories?${new URLSearchParams({ q, per_page: '30' })}`,
        { items: [] },
        86400000,
      );
    provider.close();
    state = new GitHubNetworkStore(path);
    provider = new GitHubArchetypeResearcher({
      network: state,
      fetch: async () => {
        calls++;
        throw new Error('No network');
      },
    });
    const result = await provider.search(request);
    assert.equal(result.status, 'partial');
    assert.ok('limitations' in result && result.limitations.some((s) => s.includes('缓存')));
    assert.equal(calls, 0);
    assert.equal(state.status('idea').ideaRequests, 0);
  } finally {
    provider.close();
    cleanupTemp(dir);
  }
});

test('cross-connection lease, pacing and per-idea budget survive reopening the database', () => {
  const dir = mkdtempSync(join(tmpdir(), 'webmcp-network-')),
    path = join(dir, 'network.sqlite');
  let now = 1_800_000_000_000;
  let a = new GitHubNetworkStore(path, () => now);
  const b = new GitHubNetworkStore(path, () => now);
  try {
    const first = a.reserve('idea', 'core');
    assert.ok(first.owner);
    assert.equal(b.reserve('different-idea', 'core').owner, null);
    a.release(first.owner!);
    assert.equal(b.reserve('idea', 'core').waitMs, 2000);
    for (let i = 1; i < 15; i++) {
      now += 4000;
      const ticket = b.reserve('idea', 'core');
      assert.ok(ticket.owner);
      b.release(ticket.owner!);
    }
    a.close();
    a = new GitHubNetworkStore(path, () => now);
    limited(() => a.reserve('idea', 'core'));
    assert.equal(a.status('idea').ideaRequests, 15);
  } finally {
    a.close();
    b.close();
    cleanupTemp(dir);
  }
});

test('global search, hourly and daily limits cannot be bypassed by new run IDs', () => {
  let now = 1_800_000_000_000;
  const search = new GitHubNetworkStore(':memory:', () => now);
  const state = new GitHubNetworkStore(':memory:', () => now);
  try {
    for (let i = 0; i < 4; i++) {
      const t = search.reserve(`idea-${i}`, 'search');
      search.release(t.owner!);
      now += 4000;
    }
    limited(() => search.reserve('new-id', 'search'));
    for (let hour = 0; hour < 4; hour++) {
      for (let i = 0; i < 30; i++) {
        const t = state.reserve(`${hour}-${i}`, 'core');
        assert.ok(t.owner);
        state.release(t.owner!);
        now += 4000;
      }
      limited(() => state.reserve(`extra-${hour}`, 'core'));
      now += 3600000;
    }
    limited(() => state.reserve('fresh-id', 'core'));
    assert.equal(state.status().usedLastDay, 120);
  } finally {
    search.close();
    state.close();
  }
});

test('every 403 and 429 persists a global cooldown, including headerless secondary limits', async () => {
  for (const status of [401, 403, 429]) {
    const dir = mkdtempSync(join(tmpdir(), 'webmcp-network-')),
      path = join(dir, 'network.sqlite');
    let calls = 0;
    let state = new GitHubNetworkStore(path);
    let provider = new GitHubArchetypeResearcher({
      networkEnabled: true,
      network: state,
      fetch: async () => {
        calls++;
        return new Response('', { status });
      },
    });
    try {
      await provider.search(request);
      assert.equal(calls, 1);
      assert.equal(state.status('idea').ideaRequests, 1); // Failed attempts count too.
      provider.close();
      state = new GitHubNetworkStore(path);
      provider = new GitHubArchetypeResearcher({
        networkEnabled: true,
        network: state,
        fetch: async () => {
          calls++;
          throw new Error('Cooldown bypassed');
        },
      });
      await provider.search({
        ...request,
        discussion: { ...request.discussion, runId: 'new-run' },
      });
      assert.equal(calls, 1);
      assert.ok(state.status().cooldownUntil);
    } finally {
      provider.close();
      cleanupTemp(dir);
    }
  }
});

test('server Retry-After/reset headers and low remaining allowance stop subsequent requests', () => {
  let now = 1_800_000_000_000;
  const state = new GitHubNetworkStore(':memory:', () => now);
  try {
    state.observe(
      new Response('', {
        status: 429,
        headers: { 'retry-after': '7200', 'x-ratelimit-reset': String(now / 1000 + 3600) },
      }),
    );
    assert.equal(Date.parse(state.status().cooldownUntil!), now + 7205000);
    now += 7205001;
    state.observe(
      new Response('{}', {
        headers: { 'x-ratelimit-resource': 'search', 'x-ratelimit-remaining': '1' },
      }),
    );
    limited(() => state.reserve('idea', 'search'));
  } finally {
    state.close();
  }
});

for (const failure of [
  new TypeError('Simulated connection reset after headers'),
  new DOMException('Simulated body read aborted after headers', 'AbortError'),
]) {
  test(`response body ${failure.name} pauses later searches until the shared cooldown expires`, async () => {
    let now = 1_800_000_000_000,
      calls = 0;
    const state = new GitHubNetworkStore(':memory:', () => now);
    const provider = new GitHubArchetypeResearcher({
      networkEnabled: true,
      network: state,
      wait: async (ms) => {
        now += ms;
      },
      fetch: async () => {
        calls++;
        return calls === 1
          ? new Response(
              new ReadableStream({
                start(controller) {
                  controller.error(failure);
                },
              }),
            )
          : Response.json({ items: [] });
      },
    });
    try {
      assert.equal((await provider.search(request)).status, 'failed');
      assert.equal(calls, 1);
      assert.equal(Date.parse(state.status().cooldownUntil!), now + 60000);
      assert.equal(state.status('idea').ideaRequests, 1);

      const differentRun = {
        ...request,
        discussion: { ...request.discussion, runId: 'different-idea' },
      };
      now += 59999;
      const blocked = await provider.search(differentRun);
      assert.equal(blocked.status, 'failed');
      assert.ok(blocked.status === 'failed' && blocked.error.code === 'RATE_LIMITED');
      assert.equal(calls, 1);
      assert.equal(state.status('different-idea').ideaRequests, 0);

      now++;
      assert.equal((await provider.search(differentRun)).status, 'partial');
      assert.equal(calls, 3); // The next manual search may perform its two queries.
    } finally {
      provider.close();
    }
  });
}

test('orchestrator does not replay GitHub jobs after a timeout', async () => {
  let calls = 0;
  const provider = new GitHubArchetypeResearcher({ network: new GitHubNetworkStore(':memory:') });
  provider.search = async (_request, options) => {
    calls++;
    await new Promise((_, reject) =>
      options?.signal?.addEventListener('abort', () => reject(new Error('aborted')), {
        once: true,
      }),
    );
    throw new Error('unreachable');
  };
  const { engine, store, id } = setup(new MockMaterialPortal(), {
    archetypes: provider,
    archetypeTimeoutMs: 10,
  });
  try {
    await engine.drain();
    assert.equal(calls, 1);
    assert.equal(engine.get(id).jobs.find((j) => j.request.purpose === 'archetype')?.attempts, 1);
  } finally {
    store.close();
    provider.close();
  }
});
