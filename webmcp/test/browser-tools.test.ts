import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { Ajv2020 } from 'ajv/dist/2020.js';
import { registerResearchTools, researchTools } from '../public/webmcp.mjs';
import { setup } from './helpers.ts';
const contract = JSON.parse(
  readFileSync(new URL('../material-portal.schema.json', import.meta.url), 'utf8'),
);
test('browser start accepts image references and preferences and preserves them at the portal boundary', async () => {
  const { engine, store } = setup();
  try {
    const start = researchTools(
      async (_path: string, _method: string, input: any) => engine.start(input),
      contract,
    ).find((tool: any) => tool.name === 'start_research')!;
    const ajv = new Ajv2020({ strict: true });
    createRequire(import.meta.url)('ajv-formats')(ajv);
    const validate = ajv.compile(start.inputSchema);
    const input = {
      requestId: 'browser-images',
      projectId: 'browser-images-project',
      caseId: 'GAME-TEST-001',
      images: [
        {
          uri: 'https://references.example/game.png',
          mimeType: 'image/png',
          description: 'Developer reference',
        },
      ],
      preferences: { implementationStyle: 'portable', candidateCount: 2 },
      limits: { maxPortalAttempts: 12, maxRunMs: 120000 },
    };
    assert.equal(validate(input), true, ajv.errorsText(validate.errors));
    assert.equal(validate({ ...input, preferences: { candidateCount: 4 } }), false);
    assert.equal(
      validate({ ...input, images: [{ ...input.images[0], mimeType: 'text/plain' }] }),
      false,
    );
    const result = await start.execute(input);
    const run = JSON.parse(result.content[0].text);
    assert.deepEqual(run.jobs[0].request.images, input.images);
    assert.equal(run.jobs[0].request.limit, 2);
    assert.equal(run.decisions.find((d: any) => d.id === 'implementationStyle').value, 'portable');
  } finally {
    store.close();
  }
});
test('browser adapter feature detection leaves the ordinary API usable', async () => {
  const registration = await registerResearchTools({}, () => {}, contract);
  assert.equal(registration.supported, false);
  registration.dispose();
});
test('browser tools use the same API and abort unregisters tools without cancelling runs', async () => {
  const registered: any[] = [];
  const calls: any[] = [];
  const document = {
    modelContext: {
      registerTool: async (tool: any, options: any) => registered.push({ tool, options }),
    },
  };
  const api = async (...args: any[]) => {
    calls.push(args);
    return { ok: true };
  };
  const r = await registerResearchTools(document, api, contract);
  assert.equal(r.supported, true);
  assert.equal(registered.length, 7);
  const state = registered.find((x) => x.tool.name === 'get_research_state');
  await state.tool.execute({ runId: 'run-1' });
  assert.deepEqual(calls[0], ['/api/runs/run-1']);
  r.dispose();
  assert.ok(registered.every((x) => x.options.signal.aborted));
  assert.equal(calls.length, 1);
});
test('failed registration cleans up already registered tools', async () => {
  let signal: AbortSignal | undefined;
  let n = 0;
  const document = {
    modelContext: {
      registerTool: async (_tool: any, options: any) => {
        signal = options.signal;
        if (++n === 2) throw new Error('Unsupported tool');
      },
    },
  };
  await assert.rejects(
    registerResearchTools(document, () => {}, contract),
    /Unsupported/,
  );
  assert.equal(signal?.aborted, true);
});

test('browser schemas accept sparse ideas and initial choices, retaining server validation', async () => {
  const calls: any[] = [];
  const tools = researchTools(async (...args: any[]) => {
    calls.push(args);
    return {};
  }, contract);
  const ajv = new Ajv2020({ strict: true });
  createRequire(import.meta.url)('ajv-formats')(ajv);
  const start = tools.find((t: any) => t.name === 'start_research')!;
  const validateStart = ajv.compile(start.inputSchema);
  assert.ok(
    validateStart({
      requestId: 'idea',
      projectId: 'idea',
      brief: 'cat game',
      decisionMode: 'auto',
    }),
  );
  assert.equal(validateStart({ requestId: 'empty', projectId: 'empty' }), false);
  const selection = tools.find((t: any) => t.name === 'resolve_research_proposal')!;
  const validateSelection = ajv.compile(selection.inputSchema);
  const input = {
    runId: 'idea',
    selection: {
      requestId: 'pick',
      expectedProjectRevision: 1,
      clarificationId: 'batch',
      answers: [],
    },
  };
  assert.ok(validateSelection(input), ajv.errorsText(validateSelection.errors));
  await selection.execute(input);
  assert.deepEqual(calls[0], ['/api/runs/idea/decisions', 'POST', input.selection]);
});
