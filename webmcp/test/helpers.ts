import { randomUUID } from 'node:crypto';
import { rmSync } from 'node:fs';
import { basename, dirname, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import type { MainFollowUpRequest } from '../discussion.ts';
import type { MaterialSearchRequest, MaterialSearchResult } from '../material-portal.ts';
import { ResearchOrchestrator } from '../src/orchestrator.ts';
import { MockMaterialPortal } from '../src/portal.ts';
import type { PortalAdapter } from '../src/portal.ts';
import { RunStore } from '../src/store.ts';
export function setup(
  portal: PortalAdapter = new MockMaterialPortal(),
  options: ConstructorParameters<typeof ResearchOrchestrator>[2] = {},
  path = ':memory:',
) {
  const store = new RunStore(path);
  const engine = new ResearchOrchestrator(store, portal, { archetypes: portal, ...options });
  const run = engine.start({
    requestId: randomUUID(),
    projectId: randomUUID(),
    caseId: 'GAME-TEST-001',
  });
  return { store, engine, id: run.id };
}
export function mainCommand(
  engine: ResearchOrchestrator,
  id: string,
  requestId = randomUUID(),
): MainFollowUpRequest {
  const run = engine.get(id);
  return {
    contractVersion: '0.2',
    requestId,
    projectId: run.projectId,
    expectedProjectRevision: run.revision,
    discussion: {
      runId: id,
      threadId: run.mainThreadId,
      round: (run.mainUsed + 1) as 1 | 2 | 3,
      inReplyToRequestId: run.lastMainRequestId,
      followUp: {
        message: 'Compare the available implementations more carefully.',
        clarificationId: null,
        answers: [],
      },
    },
  };
}
export function failed(request: MaterialSearchRequest): MaterialSearchResult {
  return {
    contractVersion: '0.2',
    requestId: request.requestId,
    discussion: {
      runId: request.discussion.runId,
      threadId: request.discussion.threadId,
      round: request.discussion.round,
    },
    provider: 'test',
    retrievedAt: new Date().toISOString(),
    isMock: true,
    status: 'failed',
    error: {
      code: 'UNAVAILABLE',
      message: 'Temporary fixture outage.',
      retryable: true,
      retryAfterMs: 10,
    },
  };
}
export const hasCode = (code: string) => (error: unknown) =>
  Boolean(error && typeof error === 'object' && 'code' in error && error.code === code);
export function cleanupTemp(path: string): void {
  const target = resolve(path);
  if (dirname(target) !== resolve(tmpdir()) || !basename(target).startsWith('webmcp-'))
    throw new Error('Refusing unexpected cleanup target.');
  rmSync(target, { recursive: true, force: true });
}
