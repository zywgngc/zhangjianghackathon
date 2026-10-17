# WebMCP handoff: current state and integration responsibilities

**Snapshot: October 16, 2026. Branch: `Webmcp`. Contract proposal: v0.2.**

This branch delivers an architecture proposal, diagrams, a typed material-search interface, a JSON Schema, and synthetic examples. **There is no executable research MAS, live portal adapter, browser tool registration, or generated game in this package yet.** The next milestone is to agree on the portal interface and build one complete research workflow.

**Latest design change:** main may send up to three follow-ups to our MAS, and the MAS may send up to three follow-ups to the material portal, using independent run-wide counters. Initial requests do not count. The [discussion protocol](discussion-policy.md) defines clarification, defaults, correlation, and stopping behavior; its enforcement still needs implementation.

`Webmcp` was created from `prompting` with the project owner's approval because `main` did not exist at branch creation. “Main application” below means the upstream game-generator product, not a Git branch. This document is a status snapshot; it must be updated when implementation or integration lands.

## 1. What this component is supposed to deliver

Given a game description, optional reference images, selected architecture, and developer preferences, our component should produce a **versioned implementation blueprint** for the downstream game builder.

The research order matters:

1. Find multiple similar game archetypes and identify their shared structure and meaningful differences.
2. Turn consequential differences into developer decisions, each with a visible default. A developer can answer zero optional questions.
3. Partition the chosen design into functional mechanics and define their contracts.
4. Find and inspect multiple source or algorithm candidates for each mechanic.
5. Recommend a compatible combination, preserve alternatives, and explain missing evidence or remaining work.

A mechanic means movement, pathfinding, targeting, combat, wave scheduling, economy, or another functional subsystem. Pictures, textures, and other presentation assets are not the unit of this partitioning. Engine and simulation foundations are shared constraints across mechanics.

The output is a plan backed by traceable sources and explicit validation evidence. Generating, integrating, and testing the complete playable game belongs to the downstream Builder/QA stages in the overall design. Their inclusion in a diagram does not establish that those services are implemented or available in this repository.

## 2. What exists today

| Area | Delivered on this branch | Still required |
| --- | --- | --- |
| Architecture | Workflow, ownership, agent responsibilities, and two diagram exports | Executable orchestration and structured agent output schemas |
| Developer choices | Default precedence, provenance, locks, and revision rules documented | Decision storage, preference controls, and update handling |
| Material portal boundary | `MaterialPortalPort`, shared discussion types, JSON Schema, eight fixtures | Agreement with the portal owner; mock and live client adapters |
| Follow-up discussion | Main follow-up request type, portal clarification/result types, and separate three-round budget rules | Durable counter reservation, command handling, question resolution, and exhaustion behavior |
| External MCP material portal | Boundary and expected behavior documented | Teammate-owned implementation; its availability has not been verified here |
| Browser WebMCP | Proposed application tool list and adapter boundary | Tool registration, feature detection, authenticated command integration |
| Source research and review | Evidence levels and composition rules documented | Real searches, source inspection, pinned revisions, and verification workers |
| Durable execution | Proposed revision, retry, cancellation, and recovery behavior | Persistent project/run records, queue, checkpointing, and worker recovery |
| Builder handoff | Blueprint contents and readiness principles documented | Blueprint schema, producer, and downstream consumer agreement |

The `.ts` file contains declarations only. The JSON examples contain fictional candidates and placeholder image references. Opening the HTML displays architecture diagrams; it does not run the workflow. There is no install/start command for this component yet.

## 3. Ownership and the three interfaces

| Boundary | Producer / caller | Consumer | Responsibility |
| --- | --- | --- | --- |
| Main application → our research service | Game-generator UI/API, including browser WebMCP tools | Our project service and orchestrator | Start research, change preferences, resolve decisions, read status, retrieve blueprint |
| Our research service → external material portal | Our `MaterialPortalPort` client adapter | Teammate's MCP service | Search from descriptions/images/constraints and return candidate materials |
| Our research service → game builder | Our reviewed blueprint | Downstream Builder/QA | Implement and verify the chosen mechanics using the supplied contracts and evidence |

Our team owns research logic, project/research state handling, defaulted decisions, mechanic contracts, evidence review, and both client-facing and portal-facing adapters. The portal teammate owns discovery/retrieval infrastructure and material delivery. The builder team owns the playable-game implementation. These are responsibility boundaries, not a requirement to deploy a separate microservice for every role.

**Browser WebMCP and the external MCP portal are separate.** Browser tools expose our studio commands. The portal provides candidate materials. A portal response neither answers a developer decision nor authorizes a final implementation choice.

### Main application input and output — proposed, partially typed

The upstream boundary needs the following information. These are logical fields for the first API design. The [shared discussion types](discussion.ts) now define `MainFollowUpRequest`; the complete start/status/blueprint API remains to be designed. `material-portal.ts` models the downstream material-search boundary.

| Input group | Required meaning |
| --- | --- |
| Project context | Project ID, expected project revision, authenticated caller context, and mutation idempotency key |
| Brief and references | Game description and zero or more resolvable image references; images are optional |
| Selected architecture | Engine, version when known, target devices/input modes, language, and hard runtime constraints |
| Developer preferences | Saved choices, explicitly locked constraints, and automatic or guided decision mode |
| Execution policy | Bounds on requests, candidates, concurrency, verification time, and total run cost/time |

Our API should return a durable `runId` promptly. Subsequent reads return stage, run status, accepted project revision, effective defaults, decisions, mechanic/candidate summaries, evidence gaps, and a blueprint reference when available. A browser reload should recover the same run through the project service.

Run status also exposes remaining main/material follow-up allowances and any pending question batch. Main can refine a result or answer questions using one of its three slots. Material clarifications first use known facts or valid defaults; unresolved consequential choices may be surfaced to main. A question batch and its answer form one exchange, rather than one round per question.

Keep project authorization server-side. The portal request's `projectRevision` is provenance, not permission to access a project. A project edit creates a new revision; results from older revisions remain historical until explicitly checked for reuse.

### External portal input and output — contract artifacts included

`MaterialPortalPort.search(request, { signal })` accepts `purpose: "archetype"` or `"mechanic"`, a description, image references, architecture constraints, requested material types, and a result limit. Mechanic requests also specify `mechanicId` and `requiredBehavior`.

The response identifies the provider and request, reports retrieval status, and supplies candidates with source references, known revisions/files/symbols, evidence, license information when available, and limitations. The adapter maps this application interface onto the teammate's actual MCP tools. We have not assumed or implemented their endpoint, tool names, or transport.

Read [portal-io.md](portal-io.md) for semantics, [material-portal.ts](material-portal.ts) for types, and [the fixtures](examples/README.md) for concrete payloads. Any incompatibility with the real portal should be resolved through an explicit contract revision or adapter mapping, not a silent change in meaning.

## 4. Requirements that are settled versus proposals to confirm

| Settled product requirement | Implementation choice still open |
| --- | --- |
| Research archetypes before deciding which developer questions matter | Archetype similarity scoring and ranking |
| Offer multiple alternatives where useful; explain shortfalls | Initial budget of three archetypes and up to three inspected candidates per mechanic |
| Every optional design question has an editable default | Exact UI layout, question prioritization, and saved-preference storage |
| Respect explicit choices and locks | Persistent state schema and conflict-resolution API |
| Partition at mechanic/core-algorithm level | First mechanic library and output schemas |
| Preserve the external portal as a teammate-owned boundary | MCP tools, transport/authentication, image resolution, and service limits |
| Fit the selected web-game architecture | First engine/version and later engine adapter coverage |
| Preserve evidence, alternatives, and gaps | Scoring weights and explicit blueprint readiness policy |
| Allow at most three follow-ups on each boundary, excluding initial requests | Durable ledger implementation and actual provider session mapping; this proposal uses run-wide counters |

The broader plan routes interface-heavy games to HTML/React, 2D games to Phaser, and 3D games to Babylon.js. Those routes describe intended support. This branch does not implement any engine adapter. A Phaser tower-defense workflow is the proposed first integration example, not a restriction on the eventual product.

## 5. Worked example: a developer answers zero optional questions

**Illustration only: this walkthrough has not been executed.** Brief: “Make a simple 2D tower-defense game for desktop and mobile browsers,” with an optional reference image and Phaser selected.

| Step | Expected work | Persisted output |
| --- | --- | --- |
| 1. Archetype research | Compare fixed-path defense, maze-building defense, and lane defense, supported by retrieved examples | Candidate archetypes, source evidence, shared mechanics, and differences |
| 2. Decisions | Offer fixed path versus player-built maze, finite versus endless waves, difficulty, and style only where the brief leaves them open | Options, recommended defaults, rationale, effective values, provenance, affected mechanics |
| 3. Apply defaults | In automatic mode use fixed paths, finite waves, normal difficulty, automatic targeting, and simple high-contrast visuals | Every inferred value marked `system_default`; saved preferences and explicit choices take precedence |
| 4. Partition | Define movement, waves, targeting, combat, economy, and progression; establish shared timing, coordinates, and entity state | Six mechanic contracts plus dependencies and acceptance checks |
| 5. Search per mechanic | Request multiple waypoint-movement approaches, then do the corresponding research for other mechanics | Candidate sets with source references and reported limitations |
| 6. Review and compose | Inspect implementations and reject incompatible timing/state/dependency assumptions; verify required behavior | Recommended combination, alternatives, adaptation tasks, and evidence |
| 7. Handoff | Export the blueprint at the accepted project revision | Explicit readiness, selected mechanics, source provenance, checks, and unresolved gaps |

For movement, compare direct waypoint traversal with cumulative path-distance lookup. Require elapsed-time movement, support crossing several waypoints in one update, and a goal event emitted once per enemy. These requirements are concrete enough to search for implementations and design a focused verification harness.

The [mechanic request fixture](examples/mechanic-request.json) expresses that search. Its [response fixture](examples/mechanic-response.json) supplies two synthetic alternatives to exercise the shape of the interface. Neither is an actual repository recommendation or evidence of a successful search.

If the developer later chooses maze-building, movement and placement/path-validation requirements change. Increment the project revision, invalidate dependent selections, and preserve unrelated work only after checking its dependencies. Do not rebuild everything or silently retain incompatible candidates.

Zero optional answers must be a supported path. Missing authentication, contradictory hard constraints, or unavailable services are operational problems; defaults cannot manufacture access or erase those conflicts.

## 6. What “state” means at each level

Portal results and discussion envelopes are represented in the included contract. Full run, decision, candidate-review, and blueprint state models remain proposed semantics and still need schemas.

| Level | Proposed state / value | Meaning |
| --- | --- | --- |
| Run stage | Intake → archetypes → decisions → mechanic contracts → source research → review → blueprint | Which part of the workflow is active; gaps can send work back to earlier stages |
| Run lifecycle | `queued`, `running`, `waiting_input`, `completed`, `partial`, `failed`, `cancelled` | Scheduling and outcome. `waiting_input` is for a necessary clarification such as conflicting locks; optional preferences use defaults |
| Decision | Effective option plus `system_default`, `saved_preference`, or `developer_choice` | What applies and why; a default must never be represented as a human answer |
| Portal retrieval | `completed`, `partial`, `failed`, `needs_clarification` | Retrieval outcome or a question batch; `completed` can return zero candidates |
| Discussion budget | Initial round `0`; follow-up slots `1..3` on each boundary | Separate durable main/material counters shared across the run; retries and revision changes do not reset them |
| Candidate evidence | `discovered`, `inspected`, `runnable`, `behavior_verified` | What our review actually established about a candidate |
| Blueprint | `draft`, `partial`, `ready` | Whether mandatory mechanics, compatibility, provenance, and the configured evidence policy are satisfied |

A successful portal search is not evidence that code runs, and runnable code is not evidence that it satisfies the mechanic. For algorithm documentation, inspection can be meaningful without a runnable artifact; behavior verification belongs to an implementation or harness derived from it. Do not fabricate execution evidence for a document.

A run becomes `completed` only when its blueprint meets the configured readiness policy. A bounded run that produces useful work but leaves required gaps becomes `partial`; a fatal error with no usable result becomes `failed`. Preserve intermediate results in either case. `ready` describes readiness for the builder, not a finished playable game.

The readiness policy must be explicit before production use: required evidence per mechanic, acceptable adaptation work, source-reuse requirements, and treatment of original-implementation tasks. Missing permissions for code reuse, unresolved required behavior, incompatible selections, and synthetic fixtures cannot be hidden by an aggregate score. An original-implementation proposal stays visibly distinct from verified reusable code.

### State ownership and recovery

The orchestrator validates agent proposals and is the sole writer accepting research-state transitions. Store the project revision, run/task IDs, decisions, dependency graph, candidate/evidence records, and blueprint revisions durably. Agents operate against a revision snapshot.

Retries of identical portal input retain the request ID. Changed input receives a new ID. Persist provider job IDs when the portal offers asynchronous jobs, so restart recovery can resume rather than duplicate work. A late response cannot overwrite newer accepted state. Explicit cancellation stops accepting new work and propagates to workers; closing the browser is not cancellation.

Reserve follow-up slots atomically before work is accepted/dispatched, retain parent/request/question correlation, and reject a fourth follow-up. Counter exhaustion returns available evidence with explicit gaps; it does not trigger an automatic new run. See [discussion-policy.md](discussion-policy.md) for the complete rules.

## 7. What we need from the portal teammate

The immediate request is **interface review and integration information**, not implementation of our planner, decisions, or game builder.

| Item to confirm | Why we need it |
| --- | --- |
| Actual MCP tool names and request/result schemas | Map the proposed `search` call to the service's supported operations |
| Transport, endpoint, and authentication setup | Connect from our backend with an appropriate service identity |
| Image input format, resolution, expiry, and size/type limits | Determine whether URI or opaque asset references can be consumed |
| Search capabilities | Confirm whether the service can discover whole-game references, mechanic code, and algorithm documentation |
| Source access and provenance | Obtain exact files/revisions where possible; distinguish summaries from inspectable source |
| Limits and result behavior | Set budgets, pagination, timeouts, partial-result handling, and rate-limit backoff |
| Async jobs, retries, and cancellation | Decide how to recover operations and avoid accidental duplication |
| v0.2 discussion mapping | Confirm full-snapshot follow-ups, `needs_clarification`, question/default semantics, and any native session support |
| A real sample request/result and service availability | Exercise the adapter against actual provider behavior |

Unknown metadata is acceptable if reported explicitly. If source-file retrieval requires a separate tool, document it; the current application port describes discovery and does not yet define a source-download operation. If image search is unavailable, report that limitation instead of treating the reference as processed. Authentication secrets belong in the agreed backend configuration, not in these documentation examples.

## 8. Build order and completion criteria

| Milestone | Owner | Completion evidence |
| --- | --- | --- |
| A. Agree on the interfaces | Our team + portal owner; builder owner for blueprint consumption | Recorded mapping to actual portal tools and a reviewed draft of upstream and blueprint schemas |
| B. Build the local workflow with fixtures | Our team | Durable run, role outputs, decision/default records, mechanic graph, and mock adapter; all outputs visibly marked synthetic |
| C. Connect real retrieval and review | Our team + portal owner | Real archetype and mechanic queries; inspectable sources; adapter failure/retry behavior; revision-aware result acceptance |
| D. Demonstrate the research slice | Our team | One engine and archetype family, four to six mechanics, zero optional answers, multiple alternatives where available, and an honest readiness result |
| E. Integrate the builder | Our team + builder owner | Builder consumes a versioned blueprint and reports integration/test outcomes against its mechanic checks |

Milestone B can proceed while portal details are being finalized. It validates our workflow, not the portal or the quality of discovered code. Milestone D requires real source evidence and a declared readiness policy; candidate shortfalls must be visible rather than filled with fixtures.

For the first live slice, acceptance should cover automatic defaults, one preference change and dependency invalidation, a portal error or timeout, cancellation/recovery behavior, incompatible candidate rejection, and a blueprint whose claimed evidence can be traced to sources and checks. Also demonstrate three-round limits on both boundaries, idempotent retries, concurrent last-slot contention, and partial output at exhaustion. Broad genre coverage and all three engine routes follow this slice.

## 9. Verification of the current package

The original package received diagram inspection, local-link checks, JSON parsing and targeted payload/constraint checks, and Git whitespace checks. Its branch upload was verified. For v0.2, both TypeScript contract files passed a strict TypeScript 5.9.3 check. Ajv 8.20.0 with formats validated all eight fixtures against the draft 2020-12 schema, rejected 19 deliberately invalid payloads, and accepted three additional boundary cases. Example request/result and clarification correlation were also checked.

Those checks validate contract artifacts and example exchanges. They do not establish durable counter enforcement, concurrent slot reservation, retry/recovery behavior, a live portal connection, or an end-to-end research run. No browser WebMCP integration, real-source discovery, candidate behavior verification, or game build has been demonstrated by this package.

## 10. Reading order

1. This handoff for scope, current state, and responsibilities.
2. [Architecture](architecture.md) for workflow and agent behavior; [standalone HTML](architecture.html) for the diagrams.
3. [Portal I/O proposal](portal-io.md), then [types](material-portal.ts), [schema](material-portal.schema.json), and [examples](examples/README.md) for integration review.
4. [Discussion protocol](discussion-policy.md) for the v0.2 follow-up limits and clarification exchange.

Keep the README status and this snapshot synchronized with actual implementation. When a milestone lands, link its code and verification evidence instead of changing “planned” to “done” based only on a diagram or proposal.
