import test from 'node:test';
import assert from 'node:assert/strict';
import { createResearchServer } from '../src/server.ts';
import { mainCommand, setup } from './helpers.ts';
test('an empty configured token cannot disable authentication', async () => {
  const { engine, store } = setup();
  try {
    await assert.rejects(createResearchServer({ engine, port: 0, token: '' }), /at least 16/);
  } finally {
    store.close();
  }
});
test('HTTP authorization, origin validation, and current-revision blueprint delivery', async () => {
  const { engine, store, id } = setup();
  const server = await createResearchServer({
    engine,
    port: 0,
    worker: false,
    token: 'test-token-at-least-16',
  });
  const headers = {
    Authorization: 'Bearer test-token-at-least-16',
    'Content-Type': 'application/json',
  };
  try {
    assert.equal((await fetch(server.origin + '/health')).status, 200);
    assert.equal((await fetch(server.origin + '/api/cases')).status, 401);
    assert.equal((await fetch(server.origin + '/api/providers')).status, 401);
    assert.deepEqual(await (await fetch(server.origin + '/api/providers', { headers })).json(), {
      archetypes: engine.archetypes.provider,
      materials: engine.portal.provider,
    });
    assert.equal(
      (
        await fetch(server.origin + '/api/cases', {
          headers: { ...headers, Origin: 'https://elsewhere.example' },
        })
      ).status,
      403,
    );
    const page = await fetch(server.origin);
    assert.equal(page.status, 200);
    assert.equal((await fetch(server.origin + '/request-schema.mjs')).status, 200);
    assert.match(page.headers.get('set-cookie')!, /HttpOnly/);
    const cookie = page.headers.get('set-cookie')!.split(';')[0];
    assert.equal(
      (await fetch(server.origin + '/api/cases', { headers: { Cookie: cookie } })).status,
      200,
    );
    assert.equal(
      (await fetch(`${server.origin}/api/runs/${id}/blueprint`, { headers })).status,
      409,
    );
    await engine.drain();
    const bp = await fetch(`${server.origin}/api/runs/${id}/blueprint`, { headers });
    assert.equal(bp.status, 200);
    assert.equal((await bp.json()).readiness, 'partial');
    const cmd = mainCommand(engine, id);
    assert.equal(
      (
        await fetch(`${server.origin}/api/runs/${id}/follow-ups`, {
          method: 'POST',
          headers,
          body: JSON.stringify(cmd),
        })
      ).status,
      202,
    );
    assert.equal(
      (await fetch(`${server.origin}/api/runs/${id}/blueprint`, { headers })).status,
      409,
    );
    assert.equal(
      (
        await fetch(`${server.origin}/api/runs/${id}/cancel`, {
          method: 'POST',
          headers,
          body: '{}',
        })
      ).status,
      200,
    );
    assert.equal(engine.get(id).status, 'cancelled');
    assert.equal(
      (await fetch(server.origin + '/api/runs', { method: 'POST', headers, body: '{"broken":' }))
        .status,
      400,
    );
  } finally {
    await server.close();
    store.close();
  }
});
test('API starts a run promptly and the background worker finishes after the request returns', async () => {
  const { engine, store } = setup();
  const server = await createResearchServer({ engine, port: 0, token: 'demo-token-at-least-16' });
  try {
    const headers = {
      Authorization: 'Bearer demo-token-at-least-16',
      'Content-Type': 'application/json',
    };
    const response = await fetch(server.origin + '/api/runs', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        requestId: 'api-run',
        projectId: 'api-project',
        caseId: 'GAME-TEST-003',
      }),
    });
    assert.equal(response.status, 202);
    const { runId } = await response.json();
    assert.ok(runId);
    const until = Date.now() + 5000;
    while (Date.now() < until && engine.get(runId).status !== 'partial')
      await new Promise((r) => setTimeout(r, 30));
    assert.equal(engine.get(runId).blueprints.at(-1)!.acceptance.length, 83);
  } finally {
    await server.close();
    store.close();
  }
});
