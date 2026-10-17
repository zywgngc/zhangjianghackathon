# Running the WebMCP research service

The first executable slice is available on `Webmcp`. It has a durable local orchestrator, separate Researcher/Planner/Reviewer components, a mock material portal, an HTTP API, a small research studio, and a feature-detected browser WebMCP adapter.

**This is a reference-case research service.** The included Planner is deterministic and supports the three pinned benchmark specifications. It is not an LLM, a general game-specification parser, or a playable-game generator. The default portal returns synthetic alternatives. Source inspection and compatible-combination verification remain unimplemented, so exported blueprints explicitly remain `partial` and every game acceptance check remains `not_executed`.

## Quick start

For the integrated Python harness and research workspace, use [the combined startup guide](../docs/webmcp-integration.md). The standalone commands below remain supported.

Use Node.js 24.10 or newer within the Node 24 release line and pnpm. From the repository root:

```sh
cd webmcp
pnpm install --frozen-lockfile
pnpm check
pnpm test
pnpm demo
pnpm start
```

Open [the local research studio](http://127.0.0.1:4174). Select a case and start with defaults. The worker researches archetypes first, partitions the fixed specification into mechanics, requests candidate alternatives, and exports a partial blueprint. Refreshing or closing the page does not cancel the run. Reopening the studio lists persisted runs.

`pnpm demo` runs all three cases without a browser and writes `.demo/GAME-TEST-00N.blueprint.json`. Runtime state lives in `.data/research.sqlite`; `.data`, `.demo`, dependencies, and environment files are ignored by Git.

| Case | Mechanics | Synthetic alternatives, including archetypes | Preserved game checks |
| --- | ---: | ---: | ---: |
| Number Gate Shooter | 6 | 21 | 54 |
| Wuxia Survivors | 7 | 24 | 88 |
| Power Hunter | 6 | 21 | 83 |

The 225 check descriptions were extracted from the `tests` branch at commit `81fc5366aa417754ccdba07d5d3dc08daa8f6a02`. Each benchmark records the original path and immutable source URL. Windows-safe filenames are used locally; the source branch is unchanged. Power Hunter uses the body specification's title `战力吞噬者`; its header's alternate name `百炼英雄` is still an upstream inconsistency.

## What runs today

- SQLite transactions reserve main and material follow-up slots atomically, including across independent processes using the same local database. The two limits remain three per run.
- Request hashes and parent links enforce idempotency and correlation. Stale revisions cannot overwrite current accepted research. Historical blueprints remain available in run state.
- The worker persists queued requests, attempt counts, deadlines, and dispatch leases. Automatic replay after uncertain delivery is allowed only when an adapter explicitly guarantees request-ID idempotency.
- Optional portal questions use established project facts where available, then valid defaults. Required unanswered questions pause for main. Counter exhaustion, missing evidence, and failed searches remain explicit gaps.
- Cancellation sends an abort signal and fences late results. A provider that ignores abort cannot keep a local task waiting beyond its timeout.
- The Reviewer treats all returned candidates as discovered data. Provider summaries, `isMock: false`, revision strings, or confidence claims cannot promote them to verified implementations.
- The studio and browser tools use the same HTTP handlers and server-side budget validation. Unavailable native WebMCP support leaves the ordinary studio/API usable.

The deterministic planner preserves specified engines and mechanics. Its editable choices are implementation style and candidate count. Free-text follow-ups are retained as research context and included in portal queries; they are **not automatically translated into arbitrary new gameplay or code changes**. Full contextual preference interpretation and arbitrary game families require a model-backed Planner adapter and further validation.

Revision handling is conservative: a main follow-up invalidates current research acceptance and requests new evidence within the remaining material allowance. Source history is retained, but automatic reuse of unaffected mechanic results is a future optimization. This can produce a partial revised blueprint when three material follow-ups cannot refresh all mechanics.

## Main application integration

The development server binds only to `127.0.0.1`. Opening its page establishes a local HttpOnly, SameSite cookie session. Programmatic callers can set `WEBMCP_TOKEN` in the server environment and use `Authorization: Bearer <token>`. Configured tokens must contain at least 16 characters. This is a single-user local development service, not production login or multi-tenant authorization.

Optional environment settings: `PORT` (default `4174`), `WEBMCP_DB` (SQLite path), and `WEBMCP_TOKEN`. Keep credentials out of source files and request examples.

| Method / path | Input or result |
| --- | --- |
| `GET /health` | Public process/provider health |
| `GET /api/cases` | Three benchmark profiles and their acceptance checks |
| `GET /api/contract` | v0.2 discussion/material JSON Schema |
| `GET /api/runs` | Persisted run summaries |
| `POST /api/runs` | `StartRequest`; returns HTTP 202 with run ID and project revision |
| `GET /api/runs/:id` | Durable state, decisions, questions, jobs, counters, and blueprint history |
| `POST /api/runs/:id/follow-ups` | Existing `MainFollowUpRequest`; returns a durable acknowledgement |
| `POST /api/runs/:id/material-follow-ups` | `MaterialFollowUp`; the server assigns the next material round |
| `GET /api/runs/:id/blueprint` | Latest reviewed blueprint **for the current revision**, or `NOT_READY` |
| `POST /api/runs/:id/cancel` | Empty JSON object; explicitly cancels the run |

See [model.ts](src/model.ts) for `StartRequest`, `Run`, and `MainAck`, [discussion.ts](discussion.ts) for the main follow-up command, and [orchestrator.ts](src/orchestrator.ts) for the material follow-up input. API errors have `{ "error": { "code": "...", "message": "..." } }`.

Example initial request:

```json
{
  "requestId": "start-demo-001",
  "projectId": "project-demo-001",
  "caseId": "GAME-TEST-001",
  "preferences": { "implementationStyle": "portable", "candidateCount": 3 },
  "limits": { "maxPortalAttempts": 32, "maxRunMs": 120000 }
}
```

Use the returned run state to populate follow-up revision, parent request ID, main thread ID, and next main round. Reply to `decisionBatch.clarificationId` for implementation-style/candidate-count choices. Required portal questions appear on the latest relevant job result; reply to that result's clarification ID through the main command. The complete state is available through both the API and the studio's expandable state panel.

The browser adapter registers `get_research_context`, `start_research`, `get_research_state`, `follow_up_research`, `get_implementation_blueprint`, and `cancel_research` when `document.modelContext.registerTool` is available. Registration lifetime uses an abort signal; unregistering tools never cancels durable backend research. See [the current WebMCP draft](https://webmachinelearning.github.io/webmcp/) for the evolving browser API. Automated adapter tests use a simulated model context. A manual check in the Codex in-app browser registered all six tools, started Number Gate Shooter through `start_research`, and retrieved its partial blueprint through `get_implementation_blueprint` (six mechanics, 21 synthetic alternatives). A second run used image references and portable/two-candidate preferences for Power Hunter; `get_research_state` confirmed the references reached every portal request and the blueprint contained 14 synthetic alternatives. No image was fetched or interpreted. Other browser/agent combinations remain unverified.

## Connecting the teammate's material portal

Keep the external service teammate-owned. Replace `MockMaterialPortal` through `FunctionPortalAdapter`, passing a function that maps our request to their real MCP tool and maps its response back to v0.2:

```ts
import { FunctionPortalAdapter } from './src/portal.ts';
import { ResearchOrchestrator } from './src/orchestrator.ts';

const portal = new FunctionPortalAdapter({
  provider: 'team-material-portal',
  idempotentRequests: false, // Enable only after the provider guarantees safe request-ID replay.
  search: async (request, { signal } = {}) => {
    return await teammateClient.searchMaterials(request, { signal });
  }
});
const engine = new ResearchOrchestrator(store, portal);
```

`teammateClient` above is an integration placeholder, not an included SDK or endpoint. Instantiate `createResearchServer({ engine })` in your own entry point when using that adapter; the shipped `pnpm start` entry point deliberately uses fixtures. Images are passed as references; the mock never interprets their contents. The actual MCP transport, authentication, image resolution, asynchronous provider-job recovery, and source-file download interface still need agreement with the portal owner.

The service uses [Node's SQLite API](https://nodejs.org/docs/latest-v24.x/api/sqlite.html) for a local single-host store. Moving to Postgres/distributed workers, production authorization, model-backed agents, sandboxed source verification, and the Builder/QA integration remains separate work.

## Verification

`pnpm check` type-checks the contracts, runtime, and tests. `pnpm test` covers payload semantics, defaults/locks, both follow-up budgets, idempotency, stale results, clarification, cancellation, timeout, recovery, actual cross-worker contention, API authentication/origin checks, and the browser registration adapter. API tests need permission to open loopback sockets.

`pnpm demo` demonstrates the complete fixture workflow for the three cases with zero optional answers. This validates research orchestration and traceability, not the 225 game acceptance outcomes or real-source quality. Browser interoperability evidence is limited to the manual check described above.
