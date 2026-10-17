# WebMCP research architecture

本目录保留 Webmcp 分支的研究 MAS 提案与原协议。`zx` 当前可执行的主流程、联网素材门户和接入状态见 [素材接入说明](../integrations/game-harness/README.md)。

**Status:** proposed design and integration contract, version 0.2. This package defines our MAS and the boundary to the external material portal. It does not implement the portal or a running MAS.

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

Start implementation with a mock adapter using the fixtures, then connect the teammate's real portal behind the same interface. Fixture candidates must never count as researched or verified sources.
