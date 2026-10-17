import test from 'node:test';
import assert from 'node:assert/strict';
import { GitHubArchetypeResearcher, githubQueries } from '../src/github-archetypes.ts';
import {
  UnconfiguredMaterialPortal,
  MockMaterialPortal,
  FunctionPortalAdapter,
} from '../src/portal.ts';
import { validateResult } from '../src/validation.ts';
import { setup, mainCommand, hasCode } from './helpers.ts';
import type { MaterialSearchRequest } from '../material-portal.ts';

const commit = 'a'.repeat(40),
  tree = 'b'.repeat(40);
const request: MaterialSearchRequest = {
  contractVersion: '0.2',
  requestId: 'search-1',
  projectRevision: 1,
  purpose: 'archetype',
  materialKinds: ['game_repository'],
  limit: 3,
  description: '猫咪生存，archetype=survivor [system_default]',
  images: [],
  discussion: {
    runId: 'run-1',
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
const json = (body: unknown) => new Response(JSON.stringify(body));
function fixture() {
  const calls: string[] = [];
  const files = [
    { path: 'README.md', content: 'A survivor game with Phaser and enemy waves.' },
    { path: 'LICENSE', content: 'MIT License: example fixture only.' },
    { path: 'package.json', content: JSON.stringify({ dependencies: { phaser: '^3.90.0' } }) },
    {
      path: 'src/Player.ts',
      content: 'class Player {}\nfunction levelUp() {}\nconst damage = 2;\nconst velocity = 1;',
    },
    { path: 'src/Enemies.ts', content: 'function spawnEnemy() {}' },
    { path: 'src/main.ts', content: 'function startGame() {}' },
  ].map((f, i) => ({
    ...f,
    sha: String(i + 1).repeat(40),
    size: Buffer.byteLength(f.content),
    type: 'blob',
    mode: '100644',
  }));
  const fetcher: typeof fetch = async (url, init) => {
    const u = new URL(String(url));
    assert.equal(u.origin, 'https://api.github.com');
    assert.equal(init?.redirect, 'error');
    const p = u.pathname;
    calls.push(p);
    if (p === '/search/repositories')
      return json({
        items: [
          { full_name: 'games/no-license', default_branch: 'main', license: null },
          {
            full_name: 'games/Survivor-AI',
            default_branch: 'main',
            description: 'LLM agents play a social elimination game',
            license: { spdx_id: 'MIT' },
          },
          {
            full_name: 'games/survivor-localisation',
            default_branch: 'main',
            license: { spdx_id: 'MIT' },
          },
          {
            full_name: 'games/private',
            default_branch: 'main',
            private: true,
            license: { spdx_id: 'MIT' },
          },
          ...['one', 'two', 'three', 'four', 'five'].map((n) => ({
            full_name: `games/${n}`,
            default_branch: 'main',
            description: `Survivor game ${n}`,
            language: 'TypeScript',
            license: { spdx_id: 'MIT' },
          })),
        ],
      });
    if (p.endsWith('/git/ref/heads/main')) return json({ object: { type: 'commit', sha: commit } });
    if (p.endsWith(`/git/commits/${commit}`)) return json({ sha: commit, tree: { sha: tree } });
    if (p.endsWith(`/git/trees/${'c'.repeat(40)}`))
      return json({
        truncated: false,
        tree: files
          .filter((f) => f.path.startsWith('src/'))
          .map((f) => ({ ...f, path: f.path.slice(4) })),
      });
    if (p.endsWith(`/git/trees/${tree}`))
      return json({
        truncated: false,
        tree: [
          ...files.filter((f) => !f.path.includes('/')),
          { path: 'src', sha: 'c'.repeat(40), type: 'tree', mode: '040000' },
          { path: 'assets', sha: 'd'.repeat(40), type: 'tree', mode: '040000' },
          { path: 'src/game-link.ts', sha: 'e'.repeat(40), size: 50, type: 'blob', mode: '120000' },
          { path: 'vendor/player.ts', sha: 'e'.repeat(40), size: 20, type: 'blob', mode: '100644' },
          {
            path: 'game-large.ts',
            sha: 'e'.repeat(40),
            size: 100000,
            type: 'blob',
            mode: '100644',
          },
        ],
      });
    const f = files.find((f) => p.endsWith(`/git/blobs/${f.sha}`));
    if (f)
      return json({
        sha: f.sha,
        encoding: 'base64',
        content: Buffer.from(f.content).toString('base64'),
      });
    throw new Error(`Unexpected API path: ${p}`);
  };
  return { calls, fetcher, files };
}

test('real adapter pins multiple repositories and records only files actually read', async () => {
  const { fetcher, calls, files } = fixture();
  const provider = new GitHubArchetypeResearcher({ fetch: fetcher });
  const result = await provider.search(request);
  validateResult(request, result);
  assert.equal(result.isMock, false);
  assert.equal(result.status, 'partial');
  assert.ok('candidates' in result);
  assert.equal(result.candidates.length, 3);
  assert.equal(calls.length, 32); // two searches + three repositories × ten bounded metadata/file reads
  for (const c of result.candidates) {
    assert.equal(c.source.revision, commit);
    assert.deepEqual(new Set(c.source.paths), new Set(files.map((f) => f.path)));
    assert.ok(c.evidence.some((e) => e.sourceUri.includes(`/blob/${commit}/src/Player.ts#L3`)));
    assert.ok(c.source.symbols.includes('Player'));
    assert.ok(c.coverageHints.includes('刷怪 / 生存'));
    assert.match(c.summary, /phaser@\^3.90.0/);
    assert.equal(c.license?.scope, 'unknown');
    assert.ok(c.limitations.some((l) => l.includes('未运行游戏')));
  }
  assert.ok(!calls.some((p) => /no-license|private|four|five|localisation|Survivor-AI/.test(p)));
  const cached = await provider.search({ ...request, requestId: 'retry-id' });
  assert.equal(calls.length, 32);
  assert.equal(cached.requestId, 'retry-id');
});

test('rate limits, authorization failures, network errors and empty search never produce fixtures', async () => {
  for (const [status, code] of [
    [429, 'RATE_LIMITED'],
    [403, 'RATE_LIMITED'],
    [401, 'AUTH_REQUIRED'],
  ] as const) {
    let calls = 0;
    const provider = new GitHubArchetypeResearcher({
      fetch: async () => {
        calls++;
        return new Response('ignored', { status, headers: { 'x-ratelimit-remaining': '0' } });
      },
    });
    const result = await provider.search(request);
    assert.equal(calls, 1);
    assert.equal(result.isMock, false);
    assert.equal(result.status, 'failed');
    if (result.status === 'failed') {
      assert.equal(result.error.code, code);
      assert.equal(result.error.retryable, false);
    }
  }
  const unavailable = await new GitHubArchetypeResearcher({
    fetch: async () => {
      throw new Error('network');
    },
  }).search(request);
  assert.equal(unavailable.status, 'failed');
  const empty = await new GitHubArchetypeResearcher({
    fetch: async () => json({ items: [] }),
  }).search(request);
  assert.ok('candidates' in empty);
  assert.deepEqual(empty.candidates, []);
  assert.ok(empty.limitations.some((l) => l.includes('0/3')));
});

test('partial inspection reports missing files; rate limits preserve already completed repositories', async () => {
  const f = fixture();
  const provider = new GitHubArchetypeResearcher({
    fetch: async (url, init) => {
      const p = new URL(String(url)).pathname;
      if (p.includes('/games/two/')) return new Response('', { status: 429 });
      if (p.endsWith(`/git/blobs/${'4'.repeat(40)}`)) return new Response('', { status: 404 });
      return f.fetcher(url, init);
    },
  });
  const result = await provider.search(request);
  validateResult(request, result);
  assert.ok('candidates' in result);
  assert.equal(result.candidates.length, 1);
  assert.ok(!result.candidates[0].source.paths.includes('src/Player.ts'));
  assert.ok(result.candidates[0].limitations.some((l) => l.includes('Player.ts')));
  assert.ok(result.limitations.some((l) => l.includes('额度')));
});

test('bounds responses, rejects mechanic requests, and propagates explicit cancellation', async () => {
  const provider = new GitHubArchetypeResearcher({
    fetch: async () => new Response(' '.repeat(2_000_001)),
  });
  const large = await provider.search(request);
  assert.equal(large.status, 'failed');
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(provider.search(request, { signal: controller.signal }), {
    name: 'AbortError',
  });
  const mechanic = {
    ...request,
    purpose: 'mechanic',
    mechanicId: 'movement',
    requiredBehavior: ['move'],
    materialKinds: ['source_code'],
  } as MaterialSearchRequest;
  const rejected = await provider.search(mechanic);
  assert.equal(rejected.status, 'failed');
  if (rejected.status === 'failed') assert.equal(rejected.error.code, 'UNSUPPORTED_INPUT');
  assert.match(
    githubQueries({
      ...request,
      description: 'archetype=platformer',
      constraints: { ...request.constraints, engine: 'phaser' },
    })[0],
    /platformer game phaser/,
  );
});

test('archetypes belong to WebMCP and never go through the teammate material port', async () => {
  const f = fixture();
  const materials: MaterialSearchRequest[] = [];
  const missing = new UnconfiguredMaterialPortal();
  const portal = new FunctionPortalAdapter({
    provider: missing.provider,
    idempotentRequests: true,
    search: async (r) => {
      materials.push(r);
      return missing.search(r);
    },
  });
  const { engine, store, id } = setup(portal, {
    archetypes: new GitHubArchetypeResearcher({ fetch: f.fetcher }),
  });
  try {
    await engine.drain();
    let run = engine.get(id);
    assert.ok(materials.length > 0 && materials.every((r) => r.purpose === 'mechanic'));
    assert.equal(run.materialUsed, 0);
    assert.ok(run.blueprints[0].candidates.every((c) => !c.isMock && c.mechanicId === null));
    assert.ok(run.blueprints[0].gaps.some((g) => g.includes('尚未配置')));
    assert.throws(
      () =>
        engine.materialFollowUp(id, {
          requestId: 'bad',
          parentRequestId: run.jobs[0].request.requestId,
          expectedProjectRevision: run.revision,
          message: 'new search',
        }),
      hasCode('INVALID_INPUT'),
    );
    engine.mainFollowUp(mainCommand(engine, id));
    await engine.drain();
    run = engine.get(id);
    assert.equal(run.archetypeUsed, 1);
    assert.equal(run.mainUsed, 1);
    assert.equal(run.materialUsed, 3); // only the three allowed mechanic follow-ups
  } finally {
    store.close();
  }
});
