import type { MaterialSearchRequest, MaterialSearchResult } from '../material-portal.ts';

/** Owned by WebMCP. Reuses the result envelope, but does not call the external material portal. */
export interface ArchetypeResearchPort {
  readonly provider: string;
  readonly idempotentRequests: boolean;
  search(
    request: MaterialSearchRequest,
    options?: { signal?: AbortSignal },
  ): Promise<MaterialSearchResult>;
}
