import { parentPort, workerData } from 'node:worker_threads';
import { RunStore } from '../src/store.ts';
import { ResearchOrchestrator } from '../src/orchestrator.ts';
import { MockMaterialPortal } from '../src/portal.ts';
const store = new RunStore(workerData.path);
const barrier = new Int32Array(workerData.barrier);
Atomics.add(barrier, 0, 1);
Atomics.notify(barrier, 0);
while (Atomics.load(barrier, 0) < 2) Atomics.wait(barrier, 0, 1, 2000);
try {
  const engine = new ResearchOrchestrator(store, new MockMaterialPortal(), {
    archetypes: new MockMaterialPortal(),
  });
  const ack = engine.mainFollowUp(workerData.command);
  store.close();
  parentPort!.postMessage({ ok: true, ack });
} catch (error) {
  store.close();
  parentPort!.postMessage({ ok: false, code: (error as { code: string }).code });
}
