import type {
  MaterialPortalPort,
  MaterialSearchRequest,
  MaterialSearchResult,
} from '../material-portal.ts';

export interface PortalAdapter extends MaterialPortalPort {
  readonly provider: string;
  /** Only true when identical request IDs are safe to replay after an uncertain outcome. */
  readonly idempotentRequests: boolean;
}
/** Production placeholder for the teammate-owned service, never synthetic search results. */
export class UnconfiguredMaterialPortal implements PortalAdapter {
  readonly provider = 'material-portal-not-configured';
  readonly idempotentRequests = true;
  async search(request: MaterialSearchRequest): Promise<MaterialSearchResult> {
    return {
      contractVersion: '0.2',
      requestId: request.requestId,
      discussion: {
        runId: request.discussion.runId,
        threadId: request.discussion.threadId,
        round: request.discussion.round,
      },
      provider: this.provider,
      retrievedAt: new Date().toISOString(),
      isMock: false,
      status: 'failed',
      error: {
        code: 'UNAVAILABLE',
        message: '素材门户尚未配置；请由门户负责人接入 MaterialPortalPort。未生成模拟候选。',
        retryable: false,
        retryAfterMs: null,
      },
    };
  }
}
export class FunctionPortalAdapter implements PortalAdapter {
  readonly provider: string;
  readonly idempotentRequests: boolean;
  private call: MaterialPortalPort['search'];
  constructor(options: {
    provider: string;
    idempotentRequests?: boolean;
    search: MaterialPortalPort['search'];
  }) {
    this.provider = options.provider;
    this.idempotentRequests = options.idempotentRequests ?? false;
    this.call = options.search;
  }
  search(
    request: MaterialSearchRequest,
    options?: { signal?: AbortSignal },
  ): Promise<MaterialSearchResult> {
    return this.call(request, options);
  }
}
/** Fixture provider only. It does not search the internet, inspect images, or claim verified sources. */
export class MockMaterialPortal implements PortalAdapter {
  readonly provider = 'synthetic-reference-portal';
  readonly idempotentRequests = true;
  async search(
    request: MaterialSearchRequest,
    options?: { signal?: AbortSignal },
  ): Promise<MaterialSearchResult> {
    options?.signal?.throwIfAborted();
    const topic = request.purpose === 'mechanic' ? request.mechanicId : 'archetype';
    return {
      contractVersion: '0.2',
      requestId: request.requestId,
      discussion: {
        runId: request.discussion.runId,
        threadId: request.discussion.threadId,
        round: request.discussion.round,
      },
      provider: this.provider,
      retrievedAt: new Date().toISOString(),
      isMock: true,
      status: 'completed',
      candidates: Array.from({ length: request.limit }, (_, i) => ({
        candidateId: `fixture:${topic}:${i + 1}`,
        kind: request.materialKinds[i % request.materialKinds.length],
        title: `Synthetic ${topic} alternative ${i + 1}`,
        summary:
          'Fixture for orchestration and comparison. This is not a real implementation recommendation.',
        source: {
          uri: `https://materials.example/${topic}/${i + 1}`,
          repositoryUrl: null,
          revision: null,
          paths: [],
          symbols: [],
        },
        coverageHints: request.purpose === 'mechanic' ? request.requiredBehavior : [],
        license: null,
        evidence: [],
        limitations: ['Synthetic source; behavior, license, and compatibility are unverified.'],
      })),
      limitations: [
        'Mock retrieval only; no actual source discovery or image interpretation was performed.',
      ],
    };
  }
}
