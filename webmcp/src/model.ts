import type { ClarificationQuestion, MainFollowUpRequest } from '../discussion.ts';
import type {
  Engine,
  ImageReference,
  MaterialCandidate,
  MaterialSearchRequest,
  MaterialSearchResult,
} from '../material-portal.ts';

export type CaseId = 'GAME-TEST-001' | 'GAME-TEST-002' | 'GAME-TEST-003';
export interface AcceptanceCheck {
  id: string;
  category: string;
  requirement: string;
  derivedFrom?: string[];
  provisional?: boolean;
  source?: 'system_proposal';
}
export interface Benchmark {
  caseId: CaseId;
  title: string;
  engine: Engine;
  source: { commit: string; path: string; url: string };
  checks: AcceptanceCheck[];
}
export interface StartRequest {
  requestId: string;
  projectId: string;
  /** Legacy caseId-only requests remain explicit benchmark runs. */
  mode?: 'idea' | 'benchmark';
  caseId?: CaseId;
  decisionMode?: 'ask' | 'auto';
  brief?: string;
  knownFeatures?: string[];
  images?: ImageReference[];
  preferences?: {
    implementationStyle?: 'engine_native' | 'portable';
    candidateCount?: number;
    engine?: Engine;
    archetype?: 'runner' | 'survivor' | 'puzzle' | 'platformer';
    difficulty?: 'easy' | 'normal' | 'hard';
    visualStyle?: 'minimal' | 'pixel' | 'illustrated';
  };
  limits?: { maxPortalAttempts?: number; maxRunMs?: number };
}
export interface Decision {
  id: string;
  value: string;
  source: 'system_default' | 'saved_preference' | 'developer_choice' | 'specification';
  locked: boolean;
  rationale: string;
}
export interface Mechanic {
  id: string;
  title: string;
  behavior: string[];
  ownedState: string[];
  inputs: string[];
  outputs: string[];
  dependencies: string[];
  checks: string[];
}
export interface Plan {
  engine: Engine;
  mechanics: Mechanic[];
  crossCuttingChecks: AcceptanceCheck[];
  foundations: { coordinates: string; timing: string; entityOwnership: string };
  archetypeCandidateIds: string[];
  acceptanceDraft?: AcceptanceCheck[];
}
export interface IdeaProposal {
  basis: 'rule_fallback' | 'candidate_hints';
  known: string[];
  imageInterpretation: 'not_performed';
  warnings: string[];
  alternatives: Array<{ id: string; label: string; rationale: string; candidateIds: string[] }>;
}
export interface ProposalSelection {
  requestId: string;
  expectedProjectRevision: number;
  clarificationId: string;
  answers: import('../discussion.ts').DiscussionAnswer[];
}
export interface CandidateRecord {
  candidate: MaterialCandidate;
  requestId: string;
  mechanicId: string | null;
  isMock: boolean;
  evidence: 'discovered';
  projectRevision: number;
}
export interface Blueprint {
  version: number;
  projectRevision: number;
  readiness: 'partial';
  generatedAt: number;
  caseId: CaseId | null;
  source: Benchmark['source'] | null;
  mode?: 'idea' | 'benchmark';
  proposal?: IdeaProposal;
  plan: Plan;
  decisions: Decision[];
  developerNotes: string[];
  assumptions: string[];
  candidates: CandidateRecord[];
  gaps: string[];
  acceptance: Array<AcceptanceCheck & { status: 'not_executed' }>;
  agentMode: 'reference-planner' | 'rule-proposal-planner';
}
export interface PortalJob {
  request: MaterialSearchRequest;
  hash: string;
  state: 'queued' | 'running' | 'completed' | 'failed' | 'stale' | 'cancelled';
  attempts: number;
  availableAt: number;
  leaseOwner: string | null;
  leaseUntil: number;
  result: MaterialSearchResult | null;
  error: string | null;
}
export interface MainRecord {
  command: MainFollowUpRequest;
  hash: string;
  ack: MainAck;
}
export interface MainAck {
  runId: string;
  requestId: string;
  projectRevision: number;
  round: number;
  remaining: number;
}
export interface Run {
  id: string;
  projectId: string;
  revision: number;
  input: StartRequest;
  startHash: string;
  status: 'queued' | 'running' | 'waiting_input' | 'partial' | 'failed' | 'cancelled';
  stage: 'archetypes' | 'decisions' | 'planning' | 'mechanics' | 'review' | 'finished';
  createdAt: number;
  updatedAt: number;
  deadline: number;
  mainUsed: number;
  materialUsed: number;
  portalAttempts: number;
  maxPortalAttempts: number;
  mainThreadId: string;
  lastMainRequestId: string;
  mainRecords: MainRecord[];
  decisions: Decision[];
  proposal?: IdeaProposal;
  initialSelection?: { requestId: string; hash: string };
  decisionBatch: { clarificationId: string; questions: ClarificationQuestion[] };
  plan: Plan | null;
  jobs: PortalJob[];
  blueprints: Blueprint[];
  notes: string[];
  assumptions: string[];
  issues: string[];
  events: Array<{ at: number; type: string; detail: string }>;
}
export class AppError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status = 409) {
    super(message);
    this.name = 'AppError';
    this.code = code;
    this.status = status;
  }
}
