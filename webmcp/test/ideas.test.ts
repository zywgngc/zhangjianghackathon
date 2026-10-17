import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { RunStore } from '../src/store.ts';
import { ResearchOrchestrator } from '../src/orchestrator.ts';
import { MockMaterialPortal, FunctionPortalAdapter } from '../src/portal.ts';
import { mainCommand, hasCode } from './helpers.ts';
import type { ProposalSelection, StartRequest } from '../src/model.ts';

function setup(extra: Partial<StartRequest> = {}) {
  const store = new RunStore(':memory:');
  const engine = new ResearchOrchestrator(store, new MockMaterialPortal(), {
    archetypes: new MockMaterialPortal(),
  });
  const run = engine.start({
    requestId: randomUUID(),
    projectId: randomUUID(),
    brief: '猫咪生存小游戏',
    ...extra,
  });
  return { engine, store, id: run.id };
}
function selection(
  engine: ResearchOrchestrator,
  id: string,
  answers: ProposalSelection['answers'] = [],
): ProposalSelection {
  const r = engine.get(id);
  return {
    requestId: randomUUID(),
    expectedProjectRevision: r.revision,
    clarificationId: r.decisionBatch.clarificationId,
    answers,
  };
}
const choice = (id: string, value: string) => ({
  questionId: id,
  optionId: value,
  text: null,
  source: 'developer_choice' as const,
});

test('a sparse idea searches archetypes first, waits for optional choices, and imports no benchmark requirements', async () => {
  const { engine, store, id } = setup({ knownFeatures: ['猫咪主角'] });
  try {
    await engine.drain();
    let run = engine.get(id);
    assert.equal(run.stage, 'decisions');
    assert.equal(run.status, 'waiting_input');
    assert.equal(run.jobs.length, 1);
    assert.equal(run.jobs[0].request.purpose, 'archetype');
    assert.equal(run.jobs[0].request.constraints.engine, null);
    assert.equal(run.plan, null);
    assert.equal(run.blueprints.length, 0);
    assert.ok(run.proposal?.known.includes('猫咪主角'));
    assert.ok(
      run.decisionBatch.questions.every(
        (q) => !q.required && q.options.some((o) => o.optionId === q.defaultOptionId),
      ),
    );
    assert.equal(run.decisions.find((d) => d.id === 'archetype')?.value, 'survivor');
    const cmd = selection(engine, id);
    const ack = engine.selectProposal(id, cmd);
    assert.deepEqual(engine.selectProposal(id, cmd), ack);
    assert.throws(
      () => engine.selectProposal(id, { ...cmd, answers: [choice('difficulty', 'hard')] }),
      hasCode('IDEMPOTENCY_CONFLICT'),
    );
    await engine.drain();
    run = engine.get(id);
    const bp = run.blueprints.at(-1)!;
    assert.equal(run.mainUsed, 0);
    assert.equal(run.materialUsed, 0);
    assert.equal(bp.caseId, null);
    assert.equal(bp.source, null);
    assert.equal(bp.mode, 'idea');
    assert.ok(bp.acceptance.length > 0 && bp.acceptance.length < 54);
    assert.ok(
      bp.acceptance.every(
        (c) =>
          c.id.startsWith('IDEA/') &&
          c.provisional &&
          c.source === 'system_proposal' &&
          c.status === 'not_executed',
      ),
    );
    assert.ok(bp.decisions.every((d) => d.source === 'system_default' && !d.locked));
    assert.ok(
      bp.plan.mechanics.every((m) =>
        m.checks.every((id) => bp.acceptance.some((c) => c.id === id)),
      ),
    );
  } finally {
    store.close();
  }
});

test('changed choices affect mechanics and acceptance; omitted choices stay defaults', async () => {
  const { engine, store, id } = setup();
  try {
    await engine.drain();
    engine.selectProposal(
      id,
      selection(engine, id, [
        choice('attackMode', 'manual'),
        choice('difficulty', 'easy'),
        choice('progression', 'persistent'),
      ]),
    );
    await engine.drain();
    const bp = engine.get(id).blueprints.at(-1)!;
    assert.ok(bp.plan.mechanics.some((m) => m.id === 'save-progress'));
    assert.ok(bp.acceptance.some((c) => c.requirement.includes('主动触发攻击')));
    assert.ok(bp.acceptance.some((c) => c.requirement.includes('5 点生命')));
    assert.equal(bp.decisions.find((d) => d.id === 'difficulty')?.source, 'developer_choice');
    assert.equal(bp.decisions.find((d) => d.id === 'visualStyle')?.source, 'system_default');
    const mechanics = engine.get(id).jobs.filter((j) => j.request.purpose === 'mechanic');
    assert.ok(
      mechanics.some((j) => j.request.description.includes('difficulty=easy [developer_choice]')),
    );
  } finally {
    store.close();
  }
});

test('image-only auto mode retains references without pretending to interpret them', async () => {
  const images = [
    {
      uri: 'asset:cat-reference',
      mimeType: 'image/png' as const,
      description: '参考图片（未提供文字描述）',
    },
  ];
  const { engine, store, id } = setup({ brief: '', images, decisionMode: 'auto' });
  try {
    await engine.drain();
    const run = engine.get(id);
    assert.equal(run.status, 'partial');
    assert.equal(run.mainUsed, 0);
    assert.equal(run.proposal?.imageInterpretation, 'not_performed');
    assert.equal(run.proposal?.basis, 'rule_fallback');
    assert.ok(run.proposal?.alternatives.every((a) => !a.candidateIds.length));
    assert.ok(run.jobs.every((j) => JSON.stringify(j.request.images) === JSON.stringify(images)));
    assert.ok(run.blueprints.at(-1)!.gaps.some((g) => g.includes('未识别图片')));
  } finally {
    store.close();
  }
});

test('initial archetype change researches the selected scope without consuming follow-up slots', async () => {
  const { engine, store, id } = setup();
  try {
    await engine.drain();
    const cmd = selection(engine, id, [choice('archetype', 'platformer')]);
    engine.selectProposal(id, cmd);
    engine.selectProposal(id, cmd);
    assert.equal(engine.get(id).jobs.filter((j) => j.request.purpose === 'archetype').length, 2);
    await engine.drain();
    const run = engine.get(id);
    assert.equal(run.jobs[0].state, 'stale');
    assert.match(run.jobs[1].request.description, /archetype=platformer/);
    assert.equal(run.archetypeUsed, 0);
    assert.equal(run.mainUsed, 0);
    assert.equal(run.materialUsed, 0);
    assert.ok(
      run.blueprints[0].candidates.every((c) => c.requestId !== run.jobs[0].request.requestId),
    );
    assert.ok(run.plan?.mechanics.some((m) => m.id === 'platform-motion'));
  } finally {
    store.close();
  }
});

test('later archetype changes rebuild the plan even when all material follow-up slots are spent', async () => {
  const { engine, store, id } = setup({ decisionMode: 'auto' });
  try {
    await engine.drain();
    const before = structuredClone(engine.get(id).blueprints[0]);
    store.mutate(id, (r) => {
      r.materialUsed = 3;
    });
    const cmd = mainCommand(engine, id);
    cmd.discussion.followUp = {
      message: '改为平台跳跃',
      clarificationId: engine.get(id).decisionBatch.clarificationId,
      answers: [choice('archetype', 'platformer')],
    };
    engine.mainFollowUp(cmd);
    await engine.drain();
    const run = engine.get(id),
      bp = run.blueprints.at(-1)!;
    assert.equal(run.mainUsed, 1);
    assert.equal(run.materialUsed, 3);
    assert.equal(run.revision, 2);
    assert.ok(bp.plan.mechanics.some((m) => m.id === 'platform-motion'));
    assert.ok(!bp.plan.mechanics.some((m) => m.id === 'combat'));
    assert.ok(!run.decisions.some((d) => d.id === 'attackMode'));
    assert.equal(run.decisions.find((d) => d.id === 'jumpRule')?.source, 'system_default');
    assert.ok(bp.candidates.some((c) => c.mechanicId === null));
    assert.ok(bp.candidates.every((c) => c.projectRevision === run.revision));
    assert.ok(!bp.candidates.some((c) => c.mechanicId === 'input'));
    assert.ok(bp.gaps.some((g) => g.includes('exhausted')));
    assert.deepEqual(run.blueprints[0], before);
  } finally {
    store.close();
  }
});

test('invalid inputs and stale proposal replies cannot consume initial selection or budgets', async () => {
  const { engine, store, id } = setup();
  try {
    for (const invalid of [
      { brief: '  ', images: [], knownFeatures: [] },
      { mode: 'idea', caseId: 'GAME-TEST-001' },
      { mode: 'benchmark', caseId: undefined },
    ])
      assert.throws(
        () =>
          engine.start({
            requestId: randomUUID(),
            projectId: randomUUID(),
            ...invalid,
          } as StartRequest),
        hasCode('INVALID_INPUT'),
      );
    await engine.drain();
    const cmd = selection(engine, id);
    assert.throws(
      () => engine.selectProposal(id, { ...cmd, expectedProjectRevision: 2 }),
      hasCode('STALE_REVISION'),
    );
    assert.throws(
      () => engine.selectProposal(id, { ...cmd, answers: [choice('difficulty', 'impossible')] }),
      hasCode('INVALID_INPUT'),
    );
    assert.throws(
      () =>
        engine.selectProposal(id, {
          ...cmd,
          answers: [{ ...choice('difficulty', 'hard'), source: 'system_default' }],
        }),
      hasCode('INVALID_INPUT'),
    );
    assert.equal(engine.get(id).initialSelection, undefined);
    assert.equal(engine.get(id).mainUsed, 0);
  } finally {
    store.close();
  }
});

test('real candidate hints can propose an archetype but cannot turn it into a confirmed requirement', async () => {
  const store = new RunStore(':memory:');
  const mock = new MockMaterialPortal();
  const portal = new FunctionPortalAdapter({
    provider: 'candidate-test',
    search: async (r) => {
      const result = await mock.search(r);
      result.isMock = false;
      if ('candidates' in result)
        result.candidates.forEach((c) => {
          c.title = 'Puzzle merge sample';
        });
      return result;
    },
  });
  try {
    const engine = new ResearchOrchestrator(store, portal, { archetypes: portal });
    const run = engine.start({
      requestId: 'candidate',
      projectId: 'candidate',
      brief: '一个简单小游戏',
    });
    await engine.drain();
    const r = engine.get(run.id);
    assert.equal(r.proposal?.basis, 'candidate_hints');
    assert.equal(r.decisions.find((d) => d.id === 'archetype')?.value, 'puzzle');
    assert.equal(r.decisions.find((d) => d.id === 'archetype')?.source, 'system_default');
    assert.equal(r.plan, null);
  } finally {
    store.close();
  }
});

test('an unanswered initial proposal expires without being implicitly confirmed', async () => {
  const store = new RunStore(':memory:');
  let now = 1000;
  const engine = new ResearchOrchestrator(store, new MockMaterialPortal(), {
    now: () => now,
    archetypes: new MockMaterialPortal(),
  });
  try {
    const run = engine.start({
      requestId: 'expire',
      projectId: 'expire',
      brief: '生存游戏',
      limits: { maxRunMs: 1000 },
    });
    await engine.drain();
    now = 3000;
    await engine.tick();
    assert.equal(engine.get(run.id).status, 'failed');
    assert.equal(engine.get(run.id).blueprints.length, 0);
  } finally {
    store.close();
  }
});

test('provider retry exhaustion still offers the initial proposal instead of silently confirming it', async () => {
  const store = new RunStore(':memory:');
  const portal = new FunctionPortalAdapter({
    provider: 'offline',
    idempotentRequests: true,
    search: async () => {
      throw new Error('Unavailable');
    },
  });
  try {
    const engine = new ResearchOrchestrator(store, portal, { archetypes: portal });
    const run = engine.start({
      requestId: 'offline',
      projectId: 'offline',
      brief: '小游戏',
      limits: { maxPortalAttempts: 1 },
    });
    await engine.drain();
    const state = engine.get(run.id);
    assert.equal(state.stage, 'decisions');
    assert.equal(state.status, 'waiting_input');
    assert.equal(state.blueprints.length, 0);
    assert.ok(state.issues.some((i) => i.includes('exhausted')));
  } finally {
    store.close();
  }
});
