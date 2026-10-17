import { randomUUID } from 'node:crypto';
import type {
  ClarificationQuestion,
  DiscussionAnswer,
  MainFollowUpRequest,
} from '../discussion.ts';
import type { MaterialSearchRequest, MaterialSearchResult } from '../material-portal.ts';
import { decisions, EvidenceReviewer, ReferencePlanner, Researcher } from './agents.ts';
import { ideaDecisions, isIdea, propose, refreshIdeaQuestions, selectedEngine } from './ideas.ts';
import type { PlannerPort } from './agents.ts';
import { AppError } from './model.ts';
import type {
  MainAck,
  Mechanic,
  PortalJob,
  Run,
  StartRequest,
  ProposalSelection,
} from './model.ts';
import type { PortalAdapter } from './portal.ts';
import type { ArchetypeResearchPort } from './archetypes.ts';
import { GitHubArchetypeResearcher } from './github-archetypes.ts';
import { RunStore } from './store.ts';
import {
  hash,
  validateAnswers,
  validateContract,
  validateResult,
  validateStart,
  validateSelection,
} from './validation.ts';

const scope = (r: MaterialSearchRequest): string =>
  r.purpose === 'archetype' ? 'archetypes' : `mechanic:${r.mechanicId}`;
const latest = (run: Run, key: string): PortalJob | undefined =>
  run.jobs.filter((j) => scope(j.request) === key).at(-1);
const boundary = (request: MaterialSearchRequest): 'archetype' | 'material' =>
  request.purpose === 'archetype' ? 'archetype' : 'material';
const questions = (): ClarificationQuestion[] => [
  {
    questionId: 'implementationStyle',
    prompt: 'Which implementation style should research prefer?',
    required: false,
    defaultOptionId: 'engine_native',
    options: [
      { optionId: 'engine_native', label: 'Engine-native integration' },
      { optionId: 'portable', label: 'Portable algorithms' },
    ],
  },
  {
    questionId: 'candidateCount',
    prompt: 'How many alternatives should each search request?',
    required: false,
    defaultOptionId: '3',
    options: [
      { optionId: '1', label: 'One' },
      { optionId: '2', label: 'Two' },
      { optionId: '3', label: 'Three' },
    ],
  },
];
export interface MaterialFollowUp {
  requestId: string;
  parentRequestId: string;
  expectedProjectRevision: number;
  message: string;
  clarificationId?: string | null;
  answers?: DiscussionAnswer[];
}
export class ResearchOrchestrator {
  readonly store: RunStore;
  readonly portal: PortalAdapter;
  readonly archetypes: ArchetypeResearchPort;
  private planner: PlannerPort;
  private researcher = new Researcher();
  private reviewer = new EvidenceReviewer();
  private now: () => number;
  private timeoutMs: number;
  private archetypeTimeoutMs: number;
  private maxAttempts: number;
  private controllers = new Map<string, { runId: string; controller: AbortController }>();
  constructor(
    store: RunStore,
    portal: PortalAdapter,
    options: {
      now?: () => number;
      timeoutMs?: number;
      maxAttempts?: number;
      planner?: PlannerPort;
      archetypes?: ArchetypeResearchPort;
      archetypeTimeoutMs?: number;
    } = {},
  ) {
    this.store = store;
    this.portal = portal;
    this.archetypes = options.archetypes ?? new GitHubArchetypeResearcher();
    this.now = options.now ?? Date.now;
    this.timeoutMs = options.timeoutMs ?? 10000;
    this.archetypeTimeoutMs = options.archetypeTimeoutMs ?? options.timeoutMs ?? 60000;
    this.maxAttempts = options.maxAttempts ?? 3;
    this.planner = options.planner ?? new ReferencePlanner();
  }
  private event(run: Run, type: string, detail: string): void {
    run.updatedAt = this.now();
    run.events.push({ at: run.updatedAt, type, detail });
  }
  private active(run: Run): void {
    if (['cancelled', 'failed'].includes(run.status))
      throw new AppError('RUN_CLOSED', 'This run cannot accept more work.');
    if (this.now() >= run.deadline)
      throw new AppError('DEADLINE_EXCEEDED', 'The run deadline has expired.');
  }
  private revision(run: Run, expected: number): void {
    if (run.revision !== expected)
      throw new AppError('STALE_REVISION', `Expected current revision ${run.revision}.`);
  }
  private used(run: Run, boundary: 'main' | 'material' | 'archetype'): number {
    return boundary === 'main'
      ? run.mainUsed
      : boundary === 'material'
        ? run.materialUsed
        : (run.archetypeUsed ?? 0);
  }
  private provider(request: MaterialSearchRequest): ArchetypeResearchPort | PortalAdapter {
    return request.purpose === 'archetype' ? this.archetypes : this.portal;
  }
  private canRetry(request: MaterialSearchRequest): boolean {
    const provider = this.provider(request);
    return (
      provider.idempotentRequests &&
      (!('automaticRetries' in provider) || provider.automaticRetries !== false)
    );
  }
  private jobTimeout(request: MaterialSearchRequest): number {
    return request.purpose === 'archetype' ? this.archetypeTimeoutMs : this.timeoutMs;
  }
  private limit(run: Run, boundary: 'main' | 'material' | 'archetype'): void {
    if (this.used(run, boundary) >= 3)
      throw new AppError(
        'FOLLOW_UP_LIMIT_REACHED',
        `${boundary} follow-up allowance is exhausted.`,
      );
  }
  private checkLockedAnswers(run: Run, answers: DiscussionAnswer[]): void {
    for (const answer of answers) {
      const lock = run.decisions.find((d) => d.locked && d.id === answer.questionId);
      if (lock && (answer.optionId ?? answer.text) !== lock.value)
        throw new AppError('LOCK_CONFLICT', `Clarification cannot change locked ${lock.id}.`);
    }
  }
  start(input: StartRequest): Run {
    validateStart(input);
    const resolved = decisions(input);
    const now = this.now();
    const id = randomUUID();
    const run: Run = {
      id,
      projectId: input.projectId,
      revision: 1,
      input: structuredClone(input),
      startHash: hash(input),
      status: 'queued',
      stage: 'archetypes',
      createdAt: now,
      updatedAt: now,
      deadline: now + (input.limits?.maxRunMs ?? (isIdea(input) ? 3600000 : 120000)),
      mainUsed: 0,
      materialUsed: 0,
      archetypeUsed: 0,
      portalAttempts: 0,
      maxPortalAttempts: input.limits?.maxPortalAttempts ?? 32,
      mainThreadId: `main:${id}`,
      lastMainRequestId: input.requestId,
      mainRecords: [],
      decisions: resolved,
      decisionBatch: { clarificationId: `decisions:${id}:1`, questions: questions() },
      plan: null,
      jobs: [],
      blueprints: [],
      notes: [],
      assumptions: [],
      issues: [],
      events: [
        {
          at: now,
          type: 'created',
          detail: isIdea(input)
            ? 'Sparse idea recorded; missing details are editable proposals, not confirmed requirements.'
            : 'Reference-case run created; no game tests have been executed.',
        },
      ],
    };
    if (isIdea(input)) {
      run.proposal = propose(input);
      refreshIdeaQuestions(run);
    }
    const created = this.store.create(run);
    // Re-entering after a crash between create and enqueue also repairs this initial step.
    if (!created.jobs.length && created.status === 'queued')
      this.store.mutate(created.id, (r) => {
        r.decisionBatch.clarificationId = `decisions:${r.id}:${r.revision}`;
        this.queueScope(r);
      });
    return this.store.get(created.id);
  }
  get(id: string): Run {
    return this.store.get(id);
  }
  /** Initial choice batch is round zero: omit answers to retain their recorded default provenance. */
  selectProposal(id: string, input: ProposalSelection): { runId: string; projectRevision: number } {
    validateSelection(input);
    return this.store.mutate(id, (run) => {
      const digest = hash(input);
      if (run.initialSelection?.requestId === input.requestId) {
        if (run.initialSelection.hash !== digest)
          throw new AppError('IDEMPOTENCY_CONFLICT', 'Selection ID has different content.');
        return { runId: id, projectRevision: input.expectedProjectRevision };
      }
      this.active(run);
      this.revision(run, input.expectedProjectRevision);
      if (!isIdea(run.input) || run.stage !== 'decisions' || run.initialSelection)
        throw new AppError(
          'NOT_READY',
          'Initial choices are not awaiting resolution; use a follow-up for later revisions.',
        );
      if (input.clarificationId !== run.decisionBatch.clarificationId)
        throw new AppError('CLARIFICATION_CONFLICT', 'Unknown proposal batch.');
      if (
        [
          run.input.requestId,
          ...run.jobs.map((j) => j.request.requestId),
          ...run.mainRecords.map((r) => r.command.requestId),
        ].includes(input.requestId)
      )
        throw new AppError('IDEMPOTENCY_CONFLICT', 'Request ID belongs to another command.');
      const before = this.researcher.request(run);
      this.applyChoices(run, input.answers);
      run.initialSelection = { requestId: input.requestId, hash: digest };
      this.event(
        run,
        'proposal_selected',
        'Initial choices resolved; omitted answers retain defaults. No follow-up slots consumed.',
      );
      const result = latest(run, 'archetypes')?.result ?? null;
      const after = this.researcher.request(run);
      const searchChanged =
        input.answers.some((a) =>
          ['archetype', 'engine', 'candidateCount'].includes(a.questionId),
        ) &&
        (before.description !== after.description || before.limit !== after.limit);
      if (searchChanged) {
        // Exactly one refinement of the initial proposal, guarded by initialSelection above.
        // Keep the exploratory evidence as history; it cannot stand in for the selected scope.
        for (const job of run.jobs) if (job.request.purpose === 'archetype') job.state = 'stale';
        after.requestId = randomUUID();
        after.discussion.threadId = `archetypes:selected:${run.id}`;
        run.stage = 'archetypes';
        this.reserve(run, after, true);
      } else this.planAfterArchetypes(run, result);
      this.finalize(run);
      return { runId: id, projectRevision: run.revision };
    });
  }
  private applyChoices(run: Run, answers: DiscussionAnswer[]): void {
    validateAnswers(run.decisionBatch.questions, answers);
    for (const answer of answers) {
      if (answer.source === 'research_context')
        throw new AppError(
          'INVALID_INPUT',
          'Main preference changes need developer, saved preference, or default provenance.',
          400,
        );
      const decision = run.decisions.find((d) => d.id === answer.questionId)!;
      if (decision.locked)
        throw new AppError('LOCK_CONFLICT', 'Cannot change a locked specification.');
      decision.value = answer.optionId!;
      decision.source = answer.source;
    }
    refreshIdeaQuestions(run);
  }
  private reserve(run: Run, request: MaterialSearchRequest, initialRefinement = false): PortalJob {
    const digest = hash(request);
    const old = run.jobs.find((j) => j.request.requestId === request.requestId);
    if (old) {
      if (old.hash !== digest)
        throw new AppError('IDEMPOTENCY_CONFLICT', 'Portal request ID has different content.');
      return old;
    }
    if (request.discussion.round > 0) this.limit(run, boundary(request));
    validateContract('MaterialSearchRequest', request);
    if (
      run.mainRecords.some((r) => r.command.requestId === request.requestId) ||
      request.requestId === run.initialSelection?.requestId ||
      request.requestId === run.input.requestId
    )
      throw new AppError('IDEMPOTENCY_CONFLICT', 'Request ID belongs to another command.');
    this.active(run);
    this.revision(run, request.projectRevision);
    if (
      request.discussion.runId !== run.id ||
      (request.constraints.engine !== selectedEngine(run) &&
        !(
          isIdea(run.input) &&
          request.purpose === 'archetype' &&
          request.constraints.engine === null
        ))
    )
      throw new AppError(
        'LOCK_CONFLICT',
        'Run or engine does not match the accepted specification.',
      );
    const key = scope(request);
    if (
      request.purpose === 'mechanic' &&
      !run.plan?.mechanics.some((m) => m.id === request.mechanicId)
    )
      throw new AppError('INVALID_INPUT', 'Unknown mechanic scope.', 400);
    const parent = latest(run, key);
    if (request.discussion.round === 0) {
      if (parent && !initialRefinement)
        throw new AppError(
          'FOLLOW_UP_REQUIRED',
          'This scope already has an initial search; changing threads does not reset its budget.',
        );
    } else {
      this.limit(run, boundary(request));
      if (request.discussion.round !== this.used(run, boundary(request)) + 1)
        throw new AppError(
          'ROUND_CONFLICT',
          'The next material slot must be reserved by the server.',
        );
      if (
        !parent ||
        parent.request.requestId !== request.discussion.inReplyToRequestId ||
        parent.request.discussion.threadId !== request.discussion.threadId
      )
        throw new AppError(
          'PARENT_CONFLICT',
          'Follow-up must refer to the latest request in this search thread.',
        );
      const follow = request.discussion.followUp;
      if (follow.clarificationId !== null) {
        if (
          parent.result?.status !== 'needs_clarification' ||
          parent.result.clarificationId !== follow.clarificationId
        )
          throw new AppError('CLARIFICATION_CONFLICT', 'Question batch does not match the parent.');
        validateAnswers(parent.result.questions, follow.answers);
        this.checkLockedAnswers(run, follow.answers);
      } else if (
        parent.result?.status === 'needs_clarification' &&
        parent.result.questions.some((q) => q.required)
      )
        throw new AppError(
          'CLARIFICATION_REQUIRED',
          'Required portal questions must be answered explicitly.',
        );
      if (request.purpose === 'archetype') run.archetypeUsed = (run.archetypeUsed ?? 0) + 1;
      else run.materialUsed++;
    }
    const job: PortalJob = {
      request: structuredClone(request),
      hash: digest,
      state: 'queued',
      attempts: 0,
      availableAt: this.now(),
      leaseOwner: null,
      leaseUntil: 0,
      result: null,
      error: null,
    };
    run.jobs.push(job);
    run.status = 'running';
    this.event(
      run,
      'research_queued',
      `${key}, ${boundary(request)} round ${request.discussion.round}`,
    );
    return job;
  }
  private queueScope(run: Run, mechanic?: Mechanic): void {
    const request = this.researcher.request(run, mechanic);
    request.requestId = randomUUID();
    const parent = latest(run, scope(request));
    if (parent) {
      request.discussion = {
        runId: run.id,
        threadId: parent.request.discussion.threadId,
        round: (this.used(run, boundary(request)) + 1) as 1 | 2 | 3,
        inReplyToRequestId: parent.request.requestId,
        followUp: {
          message:
            'Re-evaluate this scope using the current accepted project revision and preferences.',
          clarificationId: null,
          answers: [],
        },
      };
    }
    try {
      this.reserve(run, request);
    } catch (error) {
      if (
        error instanceof AppError &&
        ['FOLLOW_UP_LIMIT_REACHED', 'CLARIFICATION_REQUIRED'].includes(error.code)
      )
        run.issues.push(`${scope(request)}: ${error.message}`);
      else throw error;
    }
  }
  materialFollowUp(id: string, input: MaterialFollowUp): PortalJob {
    return this.store.mutate(id, (run) => {
      if (
        !input ||
        typeof input.requestId !== 'string' ||
        !input.requestId ||
        typeof input.parentRequestId !== 'string' ||
        typeof input.message !== 'string' ||
        !input.message.trim() ||
        input.message.length > 8000 ||
        !Number.isInteger(input.expectedProjectRevision)
      )
        throw new AppError('INVALID_INPUT', 'Invalid material follow-up command.', 400);
      const previous = run.jobs.find((j) => j.request.requestId === input.requestId);
      const parent = run.jobs.find((j) => j.request.requestId === input.parentRequestId);
      if (!parent) throw new AppError('PARENT_CONFLICT', 'Unknown material parent request.');
      if (parent.request.purpose === 'archetype')
        throw new AppError(
          'INVALID_INPUT',
          'Archetype research belongs to WebMCP; use a main follow-up to refine repository research.',
          400,
        );
      const request: MaterialSearchRequest = structuredClone(parent.request);
      request.requestId = input.requestId;
      request.projectRevision = input.expectedProjectRevision;
      const content = {
        message: input.message,
        clarificationId: input.clarificationId ?? null,
        answers: input.answers ?? [],
      };
      request.description = `${parent.request.description}\nFollow-up: ${input.message}`;
      request.discussion = {
        runId: run.id,
        threadId: parent.request.discussion.threadId,
        round:
          (previous?.request.discussion.round as 1 | 2 | 3) ??
          ((run.materialUsed + 1) as 1 | 2 | 3),
        inReplyToRequestId: input.parentRequestId,
        followUp: content as never,
      };
      if (!previous) {
        this.active(run);
        this.limit(run, 'material');
        this.revision(run, input.expectedProjectRevision);
      }
      const result = this.reserve(run, request);
      return structuredClone(result);
    });
  }
  mainFollowUp(command: MainFollowUpRequest): MainAck {
    // Return a domain budget error for a fourth round, before schema's numeric boundary error.
    const id = command?.discussion?.runId;
    if (typeof id !== 'string') throw new AppError('INVALID_INPUT', 'Missing run ID.', 400);
    return this.store.mutate(id, (run) => {
      const digest = hash(command);
      const old = run.mainRecords.find((r) => r.command.requestId === command.requestId);
      if (old) {
        if (old.hash !== digest)
          throw new AppError('IDEMPOTENCY_CONFLICT', 'Main request ID has different content.');
        return old.ack;
      }
      this.active(run);
      this.limit(run, 'main');
      validateContract('MainFollowUpRequest', command);
      if (
        command.requestId === run.input.requestId ||
        command.requestId === run.initialSelection?.requestId ||
        run.jobs.some((j) => j.request.requestId === command.requestId)
      )
        throw new AppError('IDEMPOTENCY_CONFLICT', 'Request ID belongs to another command.');
      this.revision(run, command.expectedProjectRevision);
      if (
        command.projectId !== run.projectId ||
        command.discussion.threadId !== run.mainThreadId ||
        command.discussion.inReplyToRequestId !== run.lastMainRequestId
      )
        throw new AppError(
          'PARENT_CONFLICT',
          'Main thread or parent does not match current state.',
        );
      if (command.discussion.round !== run.mainUsed + 1)
        throw new AppError('ROUND_CONFLICT', 'Unexpected main round.');
      const content = command.discussion.followUp;
      if (isIdea(run.input) && run.stage === 'decisions' && !run.initialSelection)
        throw new AppError(
          'NOT_READY',
          'Resolve the initial proposal before submitting follow-ups.',
        );
      if (content.message.length > 8000)
        throw new AppError('INVALID_INPUT', 'Follow-up message is too long.', 400);
      let portalParent: PortalJob | undefined;
      if (content.clarificationId) {
        if (content.clarificationId === run.decisionBatch.clarificationId) {
          this.applyChoices(run, content.answers);
        } else {
          portalParent = run.jobs.find(
            (j) =>
              j.request.projectRevision === run.revision &&
              j.result?.status === 'needs_clarification' &&
              j.result.clarificationId === content.clarificationId &&
              latest(run, scope(j.request)) === j,
          );
          if (!portalParent || portalParent.result?.status !== 'needs_clarification')
            throw new AppError('CLARIFICATION_CONFLICT', 'Unknown or obsolete question batch.');
          validateAnswers(portalParent.result.questions, content.answers);
          this.checkLockedAnswers(run, content.answers);
        }
      }
      run.mainUsed++;
      run.revision++;
      run.lastMainRequestId = command.requestId;
      run.notes.push(content.message);
      // Conservative first slice: changed research context invalidates old selections, not source history.
      for (const job of run.jobs)
        if (
          job.request.projectRevision !== run.revision &&
          ['queued', 'running'].includes(job.state)
        )
          job.state = 'stale';
      run.issues = [];
      run.status = 'running';
      run.stage = 'archetypes';
      run.plan = null;
      run.decisionBatch = {
        clarificationId: `decisions:${run.id}:${run.revision}`,
        questions: questions(),
      };
      refreshIdeaQuestions(run);
      if (portalParent) {
        const request = structuredClone(portalParent.request);
        request.requestId = randomUUID();
        request.projectRevision = run.revision;
        request.description += `\nClarification: ${content.message}`;
        request.discussion = {
          runId: run.id,
          threadId: portalParent.request.discussion.threadId,
          round: (this.used(run, boundary(request)) + 1) as 1 | 2 | 3,
          inReplyToRequestId: portalParent.request.requestId,
          followUp: structuredClone(content),
        };
        if (this.used(run, boundary(request)) < 3) this.reserve(run, request);
        else
          run.issues.push(
            'Material follow-up budget exhausted; answered questions could not be sent.',
          );
        if (portalParent.request.purpose !== 'archetype') this.queueScope(run);
      } else this.queueScope(run);
      const ack = {
        runId: run.id,
        requestId: command.requestId,
        projectRevision: run.revision,
        round: run.mainUsed,
        remaining: 3 - run.mainUsed,
      };
      run.mainRecords.push({ command: structuredClone(command), hash: digest, ack });
      this.event(
        run,
        'main_follow_up',
        `Accepted main round ${run.mainUsed}; revision ${run.revision}.`,
      );
      this.finalize(run);
      return ack;
    });
  }
  cancel(id: string): Run {
    this.store.mutate(id, (run) => {
      if (run.status === 'cancelled') return;
      run.status = 'cancelled';
      for (const job of run.jobs)
        if (['queued', 'running'].includes(job.state)) job.state = 'cancelled';
      this.event(run, 'cancelled', 'Explicit cancellation.');
    });
    for (const { runId, controller } of this.controllers.values())
      if (runId === id) controller.abort();
    return this.store.get(id);
  }
  private planAfterArchetypes(run: Run, result: MaterialSearchResult | null): void {
    if (
      isIdea(run.input) &&
      !run.initialSelection &&
      (!run.mainUsed || (run.input.decisionMode ?? 'ask') === 'ask')
    ) {
      const candidates =
        result && 'candidates' in result && !result.isMock ? result.candidates : [];
      run.proposal = propose(run.input, candidates);
      // Do this once before presenting the batch; subsequent retries retain developer choices.
      if (run.stage !== 'decisions') run.decisions = ideaDecisions(run.input, run.proposal);
      refreshIdeaQuestions(run);
      if ((run.input.decisionMode ?? 'ask') === 'ask') {
        run.stage = 'decisions';
        run.status = 'waiting_input';
        this.event(
          run,
          'proposal_ready',
          'Choose any optional details or accept all defaults; round zero.',
        );
        return;
      }
    }
    run.stage = 'planning';
    run.plan = this.planner.plan(run, result && 'candidates' in result ? result.candidates : []);
    if (run.plan.engine !== selectedEngine(run))
      throw new AppError('INVALID_PLAN', 'Planner changed a locked engine.', 500);
    run.stage = 'mechanics';
    this.event(
      run,
      'planned',
      `${run.plan.mechanics.length} mechanic contracts; ${isIdea(run.input) ? 'acceptance draft derived from effective choices' : 'fixed specification retained'}.`,
    );
    for (const mechanic of run.plan.mechanics) this.queueScope(run, mechanic);
  }
  private complete(run: Run, job: PortalJob): void {
    const result = job.result;
    if (result?.status === 'needs_clarification') {
      const answers: DiscussionAnswer[] = [];
      for (const q of result.questions) {
        const fact = run.decisions.find((d) => d.id === q.questionId);
        if (
          fact &&
          (q.options.some((o) => o.optionId === fact.value) || (q.required && !q.options.length))
        ) {
          answers.push({
            questionId: q.questionId,
            source: 'research_context',
            ...(q.options.length
              ? { optionId: fact.value, text: null }
              : { optionId: null, text: fact.value }),
          });
          continue;
        }
        if (fact?.locked) {
          run.issues.push(`${scope(job.request)}: portal options conflict with locked ${fact.id}.`);
          return;
        }
        if (q.required) {
          this.event(run, 'needs_clarification', result.clarificationId);
          return;
        }
        answers.push({
          questionId: q.questionId,
          optionId: q.defaultOptionId,
          text: null,
          source: 'system_default',
        });
      }
      if (this.used(run, boundary(job.request)) >= 3) {
        run.issues.push(
          `${scope(job.request)}: ${boundary(job.request)} discussion exhausted with unresolved questions.`,
        );
        return;
      }
      const next = structuredClone(job.request);
      next.requestId = randomUUID();
      next.discussion = {
        runId: run.id,
        threadId: job.request.discussion.threadId,
        round: (this.used(run, boundary(job.request)) + 1) as 1 | 2 | 3,
        inReplyToRequestId: job.request.requestId,
        followUp: {
          message: 'Apply the provided optional defaults; retain all locked project requirements.',
          clarificationId: result.clarificationId,
          answers: answers as [DiscussionAnswer, ...DiscussionAnswer[]],
        },
      };
      run.assumptions.push(
        ...answers.map(
          (a) => `${result.clarificationId}/${a.questionId}=${a.optionId ?? a.text} (${a.source})`,
        ),
      );
      this.reserve(run, next);
      return;
    }
    if (job.state === 'failed')
      run.issues.push(
        `${scope(job.request)}: ${job.error ?? (result?.status === 'failed' ? result.error.message : 'Portal request failed.')}`,
      );
    if (job.request.purpose === 'archetype') this.planAfterArchetypes(run, result);
  }
  private finalize(run: Run): void {
    if (run.status === 'cancelled') return;
    if (run.stage === 'decisions' && !run.initialSelection && this.now() < run.deadline) return;
    if (
      run.jobs.some(
        (j) =>
          j.request.projectRevision === run.revision && ['queued', 'running'].includes(j.state),
      )
    )
      return;
    const pendingJobs = run.jobs.filter(
      (j) =>
        j.request.projectRevision === run.revision &&
        latest(run, scope(j.request)) === j &&
        j.result?.status === 'needs_clarification' &&
        j.result.questions.some((q) => q.required),
    );
    const pending = pendingJobs.length > 0;
    if (
      pendingJobs.some((j) => this.used(run, boundary(j.request)) < 3) &&
      run.mainUsed < 3 &&
      this.now() < run.deadline
    ) {
      run.status = 'waiting_input';
      return;
    }
    if (pending)
      run.issues.push(
        'Required clarification remains unresolved; a discussion budget or deadline is exhausted.',
      );
    if (
      isIdea(run.input) &&
      !run.initialSelection &&
      (run.input.decisionMode ?? 'ask') === 'ask' &&
      this.now() < run.deadline
    ) {
      this.planAfterArchetypes(run, latest(run, 'archetypes')?.result ?? null);
      return;
    }
    if (!run.plan) run.plan = this.planner.plan(run, []);
    run.stage = 'review';
    const blueprint = this.reviewer.review(run, run.plan, this.now());
    const previous = run.blueprints.at(-1);
    if (
      !previous ||
      previous.projectRevision !== run.revision ||
      hash({ ...previous, version: 0, generatedAt: 0 }) !==
        hash({ ...blueprint, version: 0, generatedAt: 0 })
    )
      run.blueprints.push(blueprint);
    run.status = 'partial';
    run.stage = 'finished';
    this.event(
      run,
      'reviewed',
      'Partial blueprint: source behavior and combination verification remain pending.',
    );
  }
  private expire(run: Run): void {
    if (this.now() < run.deadline) return;
    if (run.stage === 'decisions' && !run.initialSelection) {
      run.status = 'failed';
      run.issues.push(
        'Proposal selection deadline exceeded; no choices were implicitly confirmed. Start a new run.',
      );
      this.event(run, 'expired', 'Proposal selection timed out.');
      return;
    }
    for (const job of run.jobs)
      if (['queued', 'running'].includes(job.state)) {
        job.state = 'failed';
        job.error = 'Run deadline exceeded.';
        job.leaseOwner = null;
      }
    if (!run.issues.includes('Run deadline exceeded.')) run.issues.push('Run deadline exceeded.');
    this.finalize(run);
  }
  private claim(id: string): PortalJob | null {
    return this.store.mutate(id, (run) => {
      if (['cancelled', 'failed'].includes(run.status)) return null;
      if (
        run.status === 'partial' &&
        !run.jobs.some((j) => ['queued', 'running'].includes(j.state))
      )
        return null;
      this.expire(run);
      if (this.now() >= run.deadline) return null;
      if (!run.jobs.length && run.status === 'queued') this.queueScope(run);
      for (const job of run.jobs.filter(
        (j) => j.state === 'running' && j.leaseUntil <= this.now(),
      )) {
        job.leaseOwner = null;
        if (this.canRetry(job.request) && job.attempts < this.maxAttempts) {
          job.state = 'queued';
          job.availableAt = this.now();
          this.event(run, 'recovered', job.request.requestId);
        } else {
          job.state = 'failed';
          job.error =
            'Dispatch outcome unknown after interruption; automatic replay is not safe or retry limit reached.';
          this.complete(run, job);
        }
      }
      const job = run.jobs.find(
        (j) =>
          j.state === 'queued' &&
          j.request.projectRevision === run.revision &&
          j.availableAt <= this.now(),
      );
      if (!job) {
        if (run.status !== 'partial') this.finalize(run);
        return null;
      }
      if (run.portalAttempts >= run.maxPortalAttempts) {
        for (const j of run.jobs)
          if (j.state === 'queued') {
            j.state = 'failed';
            j.error = 'Portal attempt budget exhausted.';
          }
        run.issues.push('Portal attempt budget exhausted.');
        this.finalize(run);
        return null;
      }
      job.state = 'running';
      job.attempts++;
      run.portalAttempts++;
      job.leaseOwner = randomUUID();
      job.leaseUntil = this.now() + this.jobTimeout(job.request) + 1000;
      run.status = 'running';
      this.event(run, 'portal_dispatched', `${job.request.requestId}, attempt ${job.attempts}`);
      return structuredClone(job);
    });
  }
  async tick(): Promise<boolean> {
    let claimed: PortalJob | null = null;
    for (const run of this.store.list()) {
      if (['cancelled', 'failed'].includes(run.status)) continue;
      try {
        claimed = this.claim(run.id);
      } catch (error) {
        if (error instanceof AppError && error.code === 'RUN_SUPERSEDED') continue;
        throw error;
      }
      if (claimed) break;
    }
    if (!claimed) return false;
    const job = claimed;
    const controller = new AbortController();
    this.controllers.set(job.leaseOwner!, { runId: job.request.discussion.runId, controller });
    let timer: ReturnType<typeof setTimeout> | undefined;
    let result: MaterialSearchResult | null = null;
    let failure: string | null = null;
    try {
      const remaining = this.store.get(job.request.discussion.runId).deadline - this.now();
      const raw = await Promise.race([
        this.provider(job.request).search(structuredClone(job.request), {
          signal: controller.signal,
        }),
        new Promise<never>((_, reject) => {
          timer = setTimeout(
            () => {
              reject(new AppError('TIMEOUT', 'Portal call timed out.', 504));
              controller.abort();
            },
            Math.max(1, Math.min(this.jobTimeout(job.request), remaining)),
          );
          controller.signal.addEventListener(
            'abort',
            () => reject(new AppError('ABORTED', 'Portal call aborted.', 499)),
            { once: true },
          );
        }),
      ]);
      validateResult(job.request, raw);
      result = raw;
    } catch (error) {
      failure = (error as Error).message;
      // Malformed provider payloads must not be retried or mistaken for ordinary search results.
      if (error instanceof AppError && error.code === 'INVALID_PORTAL_RESULT') result = null;
      else
        result = {
          contractVersion: '0.2',
          requestId: job.request.requestId,
          discussion: {
            runId: job.request.discussion.runId,
            threadId: job.request.discussion.threadId,
            round: job.request.discussion.round,
          },
          provider: this.provider(job.request).provider,
          retrievedAt: new Date(this.now()).toISOString(),
          isMock: false,
          status: 'failed',
          error: {
            code: 'TIMEOUT',
            message: failure,
            retryable: this.canRetry(job.request),
            retryAfterMs: 100,
          },
        };
    } finally {
      if (timer) clearTimeout(timer);
      this.controllers.delete(job.leaseOwner!);
    }
    try {
      this.store.mutate(job.request.discussion.runId, (run) => {
        const current = run.jobs.find((j) => j.request.requestId === job.request.requestId)!;
        if (run.status === 'cancelled' || current.leaseOwner !== job.leaseOwner) return;
        current.leaseOwner = null;
        current.result = result;
        current.error = failure;
        if (current.state === 'stale' || run.revision !== job.request.projectRevision) {
          current.state = 'stale';
          this.event(run, 'stale_result', job.request.requestId);
          return;
        }
        if (this.now() >= run.deadline) {
          current.state = 'failed';
          current.error = 'Run deadline exceeded.';
          this.expire(run);
          return;
        }
        if (
          result?.status === 'failed' &&
          result.error.retryable &&
          this.canRetry(job.request) &&
          current.attempts < this.maxAttempts
        ) {
          current.state = 'queued';
          current.availableAt =
            this.now() +
            Math.max(10, Math.min(result.error.retryAfterMs ?? 100 * 2 ** current.attempts, 30000));
          return;
        }
        current.state = result && result.status !== 'failed' ? 'completed' : 'failed';
        this.complete(run, current);
        this.finalize(run);
      });
    } catch (error) {
      if (!(error instanceof AppError && error.code === 'RUN_SUPERSEDED')) throw error;
    }
    return true;
  }
  async drain(maxWaitMs = 10000): Promise<void> {
    const deadline = Date.now() + maxWaitMs;
    while (Date.now() < deadline) {
      const worked = await this.tick();
      if (!worked) {
        if (
          !this.store
            .list()
            .some((r) => r.jobs.some((j) => ['queued', 'running'].includes(j.state)))
        )
          return;
        await new Promise((r) => setTimeout(r, 10));
      }
    }
    throw new AppError('DRAIN_TIMEOUT', 'Worker did not become idle within the drain deadline.');
  }
}
