# WebMCP research architecture

**Status:** runnable sparse-idea research with WebMCP-owned **live GitHub archetype search and bounded static source inspection**. The durable orchestrator, deterministic proposal/planning roles, HTTP API, Chinese studio, and browser adapter are implemented. The teammate-owned material portal remains an independent, unconfigured port. Model/vision planning, behavior verification, and game generation remain pending. See [repository research and ownership](github-research.md).

**Run it:** see [runtime.md](runtime.md) for setup, API routes, demonstration results, and the portal integration hook. From this directory: `pnpm install --frozen-lockfile`, `pnpm check`, `pnpm test`, then `pnpm start`.

**Harness integration:** [Combined startup and adapter contract](../docs/webmcp-integration.md). The FastAPI workbench now hosts this studio at `/research`, forwards authenticated commands to this runtime, and gates Builder admission on the pinned blueprint's readiness, evidence, and engine compatibility.

**Start here: [Recipient handoff — current state, ownership, worked example, and next steps](handoff.md).** It explains what has been delivered, what still needs implementation, and what information we need from the portal owner.

**Follow-up discussion:** [Up to three rounds on each boundary](discussion-policy.md), with separate run-wide budgets for main → MAS and MAS → material portal. v0.2 adds conversation correlation and a `needs_clarification` response; the initial request is round zero.

The MAS turns a game description, reference images, selected architecture, and developer preferences into a source-backed implementation blueprint. It discovers several game archetypes, applies editable defaults, partitions the game into mechanics, and evaluates multiple source candidates for each mechanic.

## Research workflow

![Research workflow](diagrams/research-workflow.svg)

## External material portal

![Material portal boundary](diagrams/material-portal.svg)

The external MCP portal is owned by another teammate. Our `MaterialPortalPort` is the application interface; its adapter translates requests into their MCP tool calls. The browser WebMCP adapter exposes our studio commands to browser agents. These are separate integration boundaries.

## Documents and exports

| File | Purpose |
| --- | --- |
| [runtime.md](runtime.md) | Setup, current implementation scope, HTTP API, and live portal adapter integration |
| [src](src) | Durable orchestrator/store, role components, portal adapters, server, and demo |
| [test](test) | Executable contract, runtime, concurrency, API, and browser-adapter tests |
| [benchmarks](benchmarks) | 225 acceptance requirements extracted from the pinned `tests` branch |
| [public](public) | Local studio and feature-detected browser WebMCP registration |
| [handoff.md](handoff.md) | Recipient overview, implementation status, main-application I/O, state semantics, and integration milestones |
| [discussion-policy.md](discussion-policy.md) | Three-round limits, defaults, refinements, clarification, exhaustion, and v0.2 migration |
| [discussion.ts](discussion.ts) | Shared discussion types and the main-application follow-up request |
| [architecture.md](architecture.md) | Roles, decisions, mechanic partitioning, candidate review, and Builder handoff |
| [portal-io.md](portal-io.md) | Proposed request/response contract, ownership, failure behavior, and open integration details |
| [material-portal.ts](material-portal.ts) | Dependency-free TypeScript port and data types |
| [material-portal.schema.json](material-portal.schema.json) | JSON Schema for portal requests/results and main follow-up commands |
| [examples](examples/README.md) | Initial searches, clarification, follow-ups, candidate results, and failure fixtures |
| [architecture.html](architecture.html) | Standalone diagrams and contract summary; download and open locally to print |
| [diagrams](diagrams) | SVG exports and editable DOT sources |

`pnpm start` uses live GitHub repository research and explicitly reports missing material integration. `pnpm demo` and `WEBMCP_RESEARCH_MODE=fixtures` use synthetic data for offline regression. Neither source discovery nor static reading certifies behavior; current blueprints remain explicitly partial.
