/** Proposed application contract v0.1. No portal implementation or MCP SDK dependency. */
export type Engine = "html" | "phaser" | "babylon";
export type MaterialKind =
  | "game_repository"
  | "source_code"
  | "algorithm_documentation"
  | "reference_media";

export interface ImageReference {
  /** Portal-accessible URI or an agreed opaque asset reference; never a local filesystem path. */
  uri: string;
  mimeType: "image/png" | "image/jpeg" | "image/webp";
  description: string;
}

export interface SearchConstraints {
  /** null means no engine constraint has been selected yet. */
  engine: Engine | null;
  engineVersion: string | null;
  platform: "web";
  language: "typescript" | "javascript" | "any";
  inputModes: Array<"keyboard" | "pointer" | "touch">;
}

interface SearchRequestBase {
  contractVersion: "0.1";
  requestId: string;
  /** Non-negative project revision; used for provenance, not authorization. */
  projectRevision: number;
  description: string;
  images: ImageReference[];
  constraints: SearchConstraints;
  materialKinds: MaterialKind[];
  /** Integer from 1 to 10; an upper bound, not a required result count. */
  limit: number;
}

export type MaterialSearchRequest = SearchRequestBase & (
  | { purpose: "archetype"; mechanicId?: never; requiredBehavior?: never }
  | { purpose: "mechanic"; mechanicId: string; requiredBehavior: string[] }
);

export interface MaterialSource {
  uri: string;
  repositoryUrl: string | null;
  /** Commit SHA or immutable object revision when known; resolve mutable tags before verification. */
  revision: string | null;
  paths: string[];
  symbols: string[];
}

export interface LicenseEvidence {
  identifier: string | null;
  sourceUri: string;
  scope: "source_code" | "assets" | "mixed" | "unknown";
}

export interface MaterialCandidate {
  candidateId: string;
  kind: MaterialKind;
  title: string;
  summary: string;
  source: MaterialSource;
  /** Provider claims to investigate, not verified mechanic coverage. */
  coverageHints: string[];
  license: LicenseEvidence | null;
  evidence: Array<{ description: string; sourceUri: string }>;
  limitations: string[];
}

interface ResultMetadata {
  contractVersion: "0.1";
  requestId: string;
  provider: string;
  retrievedAt: string;
  /** Fixtures must be true and cannot satisfy production evidence requirements. */
  isMock: boolean;
}

export type PortalErrorCode =
  | "UNAVAILABLE"
  | "TIMEOUT"
  | "RATE_LIMITED"
  | "INVALID_INPUT"
  | "UNSUPPORTED_INPUT"
  | "AUTH_REQUIRED";

export type MaterialSearchResult = ResultMetadata & (
  | {
      status: "completed" | "partial";
      candidates: MaterialCandidate[];
      limitations: string[];
    }
  | {
      status: "failed";
      error: {
        code: PortalErrorCode;
        message: string;
        retryable: boolean;
        retryAfterMs: number | null;
      };
    }
);

export interface MaterialPortalPort {
  /**
   * Resolve expected provider failures as status="failed".
   * An explicit abort may reject with an AbortError; the orchestrator records cancellation.
   */
  search(
    request: MaterialSearchRequest,
    options?: { signal?: AbortSignal },
  ): Promise<MaterialSearchResult>;
}
