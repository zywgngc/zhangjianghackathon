import { readFileSync } from 'node:fs';
import type { MaterialCandidate, MaterialSearchRequest } from '../material-portal.ts';
import type {
  Benchmark,
  Blueprint,
  CaseId,
  Decision,
  Mechanic,
  Plan,
  Run,
  StartRequest,
} from './model.ts';
import { AppError } from './model.ts';
import { ideaDecisions, ideaPlan, isIdea, propose, selectedEngine } from './ideas.ts';

export const benchmarks: Benchmark[] = ['001', '002', '003'].map((id) =>
  JSON.parse(readFileSync(new URL(`../benchmarks/GAME-TEST-${id}.json`, import.meta.url), 'utf8')),
);
export function benchmark(id: CaseId): Benchmark {
  const item = benchmarks.find((b) => b.caseId === id);
  if (!item) throw new AppError('INVALID_INPUT', 'Unsupported reference case.', 400);
  return item;
}
export function decisions(input: StartRequest): Decision[] {
  if (isIdea(input)) return ideaDecisions(input, propose(input));
  const spec = benchmark(input.caseId!);
  if (input.preferences?.engine && input.preferences.engine !== spec.engine)
    throw new AppError('LOCK_CONFLICT', `The specification requires ${spec.engine}.`, 400);
  return [
    {
      id: 'engine',
      value: spec.engine,
      source: 'specification',
      locked: true,
      rationale: 'Required by the pinned game specification.',
    },
    {
      id: 'gameplay',
      value: input.caseId!,
      source: 'specification',
      locked: true,
      rationale: 'Preserve specified controls, numeric rules, theme, and acceptance checks.',
    },
    {
      id: 'implementationStyle',
      value: input.preferences?.implementationStyle ?? 'engine_native',
      source: input.preferences?.implementationStyle ? 'saved_preference' : 'system_default',
      locked: false,
      rationale:
        'Prefer implementations with low integration cost; portable algorithms remain alternatives.',
    },
    {
      id: 'candidateCount',
      value: String(input.preferences?.candidateCount ?? 3),
      source: input.preferences?.candidateCount ? 'saved_preference' : 'system_default',
      locked: false,
      rationale: 'Request several alternatives; the result count is not guaranteed.',
    },
  ];
}
const range = (start: number, end: number): number[] =>
  Array.from({ length: end - start + 1 }, (_, i) => start + i);
type Definition = [
  id: string,
  title: string,
  behavior: string[],
  tests: number[],
  dependencies: string[],
];
const definitions: Record<CaseId, Definition[]> = {
  'GAME-TEST-001': [
    [
      'squad-movement',
      'Squad movement and formation',
      [
        'Advance at 8 world units/second; horizontal speed 6; clamp squad center X to [-5.5,5.5].',
        'Drag release stops horizontal input. Keep movement frame-rate independent.',
      ],
      [2, 3, 4, 12, 13, 43],
      [],
    ],
    [
      'arithmetic-gates',
      'Single-use arithmetic gates',
      [
        'Apply addition, subtraction, multiplication, or floor division to the whole squad.',
        'Clamp soldier count to [0,200]; reject division by zero. One gate per group, activated once by squad center.',
      ],
      [6, 7, 8, 9, 10, 11, 47, 50],
      ['squad-movement'],
    ],
    [
      'automatic-shooting',
      'Targeting, projectiles, and damage',
      [
        'Each living soldier attacks twice/second; base bullet damage 1; bullet speed 30 and lifetime at most 3 seconds.',
        'Damage requires actual hits. Retarget valid enemies ahead; pool projectiles without reducing simulated firepower.',
      ],
      [5, 14, 15, 16, 17, 19, 44, 48, 49],
      ['squad-movement'],
    ],
    [
      'hazard-collision',
      'Hazard and enemy collisions',
      [
        'Enemy contact removes one soldier and the enemy once.',
        'Barricade, wire, and mine losses are 2,3,5 respectively; each obstacle triggers once.',
      ],
      [18, 51],
      ['squad-movement'],
    ],
    [
      'boss-progression',
      'Boss and level state machine',
      [
        'Boss HP zero wins; reaching the end with living boss loses; zero soldiers loses.',
        'Three levels, unlock next on victory, retry resets temporary state.',
      ],
      [...range(20, 26), 52],
      ['automatic-shooting', 'hazard-collision'],
    ],
    [
      'persistent-economy',
      'Rewards and permanent upgrades',
      [
        'Keep gold and upgrade configuration separate from run state.',
        'Apply specified costs/rewards exactly; persist permanent upgrades and preserve them across retry.',
      ],
      [27, 28, 29, 30, 53],
      ['boss-progression'],
    ],
  ],
  'GAME-TEST-002': [
    [
      'movement-spawning',
      'Movement, pursuit, and spawning',
      [
        'Virtual joystick and keyboard free movement; enemies approach from all directions.',
        'Use configured spawn schedules and pool up to 200 normal enemies.',
      ],
      [2, 3, 6, 7, 54, 75],
      [],
    ],
    [
      'skill-runtime',
      'Automatic skill execution',
      [
        'Implement 12 active skills with five levels, independent cooldowns and effect handlers; no visual-only attacks.',
        'Use additive level multipliers 1.2/1.4, cap cooldown reduction at 40%; six active and six passive slots maximum.',
      ],
      [4, 5, 14, 15, 16, 17, 23, 24, 25, 26, 27, 85],
      ['movement-spawning'],
    ],
    [
      'status-effects',
      'Status effects and resistance',
      [
        'Burn and poison refresh duration rather than stacking indefinitely.',
        'Bosses resist freeze as a 30% slow and are immune to stun/knockback.',
      ],
      [28, 29, 30],
      ['skill-runtime'],
    ],
    [
      'experience-upgrades',
      'Experience and upgrade sampling',
      [
        'XP_next(L)=ceil(4+1.5*L+0.12*L^2). Carry excess XP and process all level-ups sequentially.',
        'Offer up to three unique valid choices; exclude maxed or unavailable skills and use repeatable rewards when exhausted.',
        'Free selections at 5 and 15 seconds; guarantee a new skill option at 15 seconds if only one skill is owned.',
      ],
      [8, 9, 10, 11, 12, 13, 50, 51, 83, 84, 88],
      ['skill-runtime'],
    ],
    [
      'skill-evolution',
      'Evolution dependency graph',
      [
        'Six evolution recipes; enforce prerequisites and preserve passive levels.',
        'Ice-fire evolution consumes two base skills and occupies one slot. Valid progression must permit an ultimate within 180 seconds.',
      ],
      [18, 19, 20, 21, 22, 52, 53, 86],
      ['experience-upgrades'],
    ],
    [
      'combat-bosses',
      'Damage, drops, and boss encounters',
      [
        'Player contact invulnerability is 0.6 seconds; healing cannot exceed maximum HP.',
        'Victory requires boss defeat, not timer survival; boss attacks have warnings and scheduled spawns.',
      ],
      [...range(31, 49)],
      ['status-effects', 'movement-spawning'],
    ],
    [
      'persistent-progression',
      'Economy and permanent progression',
      [
        'Apply configured gold drops/rewards and all six permanent upgrades.',
        'Persist permanent upgrades; reset temporary skills and passives on new runs.',
      ],
      [...range(55, 60)],
      ['combat-bosses'],
    ],
  ],
  'GAME-TEST-003': [
    [
      'click-navigation',
      'Selection and navigation',
      [
        'Tap enemy, weapon, or ground to navigate through the open field; do not use tower floors or joystick control.',
        'Avoid walls, handle retargeting and unreachable targets, and stop within interaction range without teleporting.',
      ],
      [4, 6, 7, 11, 14, 15, ...range(26, 31), 71, 78, 80, 81],
      [],
    ],
    [
      'power-comparison',
      'Strict deterministic power rules',
      [
        'Only P>E permits combat; equality or a stronger enemy must not trigger movement, combat, power loss, or death.',
        'Use one authoritative positive integer power value; no HP, critical hits, or random combat outcomes.',
      ],
      [5, 16, 17, 18, 20, 21, 22, 23, 34, 37, 75, 83],
      ['click-navigation'],
    ],
    [
      'interaction-lifecycle',
      'One-time combat and collection',
      [
        'Resolve attacks only in range after facing/animation; add enemy power once and deactivate defeated targets.',
        'Weapons add configured bonuses once and become unselectable; keep UI and simulation power consistent.',
      ],
      [8, 9, 10, 12, 13, 19, 24, 25, 32, 33, 35, 44, 76, 77, 79],
      ['power-comparison'],
    ],
    [
      'level-solvability',
      'Deterministic level progression',
      [
        'Three levels start at power 10,20,30, with boss powers 150,500,1500.',
        'Retain all specified enemy/weapon values, require reachable objects and at least one strict-inequality winning route.',
      ],
      [...range(38, 48), 82],
      ['interaction-lifecycle'],
    ],
    [
      'boss-unlocks',
      'Boss victory and level unlocking',
      [
        'Boss defeat triggers victory and next-level unlock; invalid boss selection triggers neither victory nor defeat.',
        'Completed levels remain replayable; restart resets temporary powers but preserves unlocks.',
      ],
      [36, 49, 50, 51],
      ['level-solvability'],
    ],
    [
      'persistent-state',
      'Saved progression and settings',
      [
        'Persist unlocked/completed levels and audio settings.',
        'Keep temporary target/path/combat state separate from permanent progression.',
      ],
      [52, 53, 54, 55],
      ['boss-unlocks'],
    ],
  ],
};

export interface PlannerPort {
  plan(run: Run, archetypes: MaterialCandidate[]): Plan;
}
/** Deterministic reference-case planner, deliberately not an LLM or a general specification parser. */
export class ReferencePlanner implements PlannerPort {
  plan(run: Run, archetypes: MaterialCandidate[]): Plan {
    if (isIdea(run.input)) return ideaPlan(run, archetypes);
    const spec = benchmark(run.input.caseId!);
    const mechanics: Mechanic[] = definitions[spec.caseId].map(
      ([id, title, behavior, tests, dependencies]) => ({
        id,
        title,
        behavior,
        dependencies,
        checks: tests.map((n) => `${spec.caseId}/T${String(n).padStart(2, '0')}`),
        ownedState: [`${id} state`],
        inputs: [
          'accepted project configuration',
          'simulation delta',
          ...dependencies.map((id) => `${id} events`),
        ],
        outputs: [`${id} events`, 'observable state for acceptance checks'],
      }),
    );
    const mapped = new Set(mechanics.flatMap((m) => m.checks));
    return {
      engine: spec.engine,
      mechanics,
      crossCuttingChecks: spec.checks.filter((c) => !mapped.has(c.id)),
      foundations: {
        coordinates:
          spec.engine === 'babylon'
            ? 'X/Z ground plane, Y height; use specified world units.'
            : '2D scene pixels; keep screen transforms separate from world input.',
        timing: 'Pause-aware elapsed simulation time; no independent browser timers for gameplay.',
        entityOwnership:
          'One authoritative host state; mechanic modules communicate through declared events.',
      },
      archetypeCandidateIds: archetypes.map((c) => c.candidateId),
    };
  }
}
export class Researcher {
  request(run: Run, mechanic?: Mechanic): MaterialSearchRequest {
    const spec = run.input.caseId ? benchmark(run.input.caseId) : null;
    const engine = selectedEngine(run);
    const base = {
      contractVersion: '0.2' as const,
      requestId: '',
      projectRevision: run.revision,
      discussion: {
        runId: run.id,
        threadId: mechanic ? `mechanic:${mechanic.id}` : 'archetypes',
        round: 0 as const,
        inReplyToRequestId: null,
        followUp: null,
      },
      description: [
        spec?.title ??
          'Sparse game idea: propose missing details, do not treat defaults as user requirements.',
        run.input.brief ?? '',
        ...(run.input.knownFeatures ?? []).map((s) => `Developer-provided characteristic: ${s}`),
        mechanic
          ? mechanic.title
          : spec
            ? 'Find similar game archetypes without changing the fixed specification.'
            : 'Compare multiple related game archetypes and describe differences that require developer choices.',
        `Engine: ${engine}. Effective choices (with provenance): ${run.decisions.map((d) => `${d.id}=${d.value} [${d.source}]`).join('; ')}.`,
        ...(!spec
          ? [
              'Free text characteristics remain constraints to inspect; report conflicts rather than silently overriding them.',
            ]
          : []),
        ...run.notes,
      ]
        .filter(Boolean)
        .join('\n'),
      images: run.input.images ?? [],
      constraints: {
        engine:
          !mechanic &&
          !spec &&
          run.decisions.find((d) => d.id === 'engine')?.source === 'system_default'
            ? null
            : engine,
        engineVersion: null,
        platform: 'web' as const,
        language: 'typescript' as const,
        inputModes: (run.decisions.find((d) => d.id === 'controls')?.value === 'touch'
          ? ['touch']
          : run.decisions.find((d) => d.id === 'controls')?.value === 'keyboard'
            ? ['pointer', 'keyboard']
            : ['pointer', 'touch', 'keyboard']) as Array<'pointer' | 'touch' | 'keyboard'>,
      },
      limit: Number(run.decisions.find((d) => d.id === 'candidateCount')?.value ?? 3),
    };
    return mechanic
      ? {
          ...base,
          purpose: 'mechanic',
          mechanicId: mechanic.id,
          requiredBehavior: mechanic.behavior,
          materialKinds: ['source_code', 'algorithm_documentation'],
        }
      : { ...base, purpose: 'archetype', materialKinds: ['game_repository'] };
  }
}
export class EvidenceReviewer {
  review(run: Run, plan: Plan, now: number): Blueprint {
    const candidates: Blueprint['candidates'] = [];
    const seen = new Set<string>();
    const gaps = [...run.issues];
    for (const job of run.jobs.filter(
      (j) => j.request.projectRevision === run.revision && j.state === 'completed',
    )) {
      if (!job.result || !('candidates' in job.result)) continue;
      const mechanicId = job.request.purpose === 'mechanic' ? job.request.mechanicId : null;
      for (const candidate of job.result.candidates) {
        const key = `${mechanicId}:${candidate.source.uri}:${candidate.source.revision ?? 'unknown'}`;
        if (seen.has(key)) continue;
        seen.add(key);
        candidates.push({
          candidate,
          requestId: job.request.requestId,
          mechanicId,
          isMock: job.result.isMock,
          evidence: 'discovered',
          projectRevision: run.revision,
        });
      }
      gaps.push(...job.result.limitations);
    }
    for (const mechanic of plan.mechanics) {
      const found = candidates.filter((c) => c.mechanicId === mechanic.id);
      if (!found.length) gaps.push(`${mechanic.id}: no current candidate evidence.`);
      else
        gaps.push(
          `${mechanic.id}: source inspection, license/engine compatibility, and behavior verification are pending.`,
        );
    }
    if (candidates.some((c) => c.isMock))
      gaps.push('Synthetic material is excluded from production readiness.');
    gaps.push(
      'No compatible implementation combination has been verified. Engine dependency versions still need pinning.',
    );
    const spec = run.input.caseId ? benchmark(run.input.caseId) : null;
    if (run.proposal) gaps.push(...run.proposal.warnings);
    return {
      version: run.blueprints.length + 1,
      projectRevision: run.revision,
      readiness: 'partial',
      generatedAt: now,
      caseId: spec?.caseId ?? null,
      source: spec?.source ?? null,
      mode: spec ? 'benchmark' : 'idea',
      ...(run.proposal ? { proposal: structuredClone(run.proposal) } : {}),
      plan,
      decisions: structuredClone(run.decisions),
      developerNotes: [...run.notes],
      assumptions: [
        ...run.assumptions,
        ...(!spec
          ? run.decisions
              .filter((d) => d.source === 'system_default')
              .map((d) => `${d.id}=${d.value} (system_default; editable proposal)`)
          : []),
      ],
      candidates,
      gaps: [...new Set(gaps)],
      acceptance: (spec?.checks ?? plan.acceptanceDraft ?? []).map((c) => ({
        ...c,
        status: 'not_executed',
      })),
      agentMode: spec ? 'reference-planner' : 'rule-proposal-planner',
    };
  }
}
