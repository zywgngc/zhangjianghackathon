import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { benchmarks } from './agents.ts';
import { RunStore } from './store.ts';
import { ResearchOrchestrator } from './orchestrator.ts';
import { MockMaterialPortal } from './portal.ts';

mkdirSync('.demo', { recursive: true });
const store = new RunStore(resolve('.demo/research.sqlite'));
try {
  const engine = new ResearchOrchestrator(store, new MockMaterialPortal(), {
    archetypes: new MockMaterialPortal(),
  });
  const ids = benchmarks.map(
    (b) =>
      engine.start({ requestId: randomUUID(), projectId: `demo-${randomUUID()}`, caseId: b.caseId })
        .id,
  );
  await engine.drain();
  for (const id of ids) {
    const run = engine.get(id);
    const blueprint = run.blueprints.at(-1)!;
    const path = resolve(`.demo/${run.input.caseId}.blueprint.json`);
    writeFileSync(path, JSON.stringify(blueprint, null, 2) + '\n');
    console.log(
      JSON.stringify({
        caseId: run.input.caseId,
        status: run.status,
        mechanics: blueprint.plan.mechanics.length,
        alternatives: blueprint.candidates.length,
        acceptanceChecks: blueprint.acceptance.length,
        gameTestsExecuted: 0,
        mainFollowUps: run.mainUsed,
        materialFollowUps: run.materialUsed,
        output: path,
      }),
    );
  }
} finally {
  store.close();
}
