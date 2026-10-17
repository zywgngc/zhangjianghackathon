# WebMCP research architecture

**Status:** proposed design and integration contract, version 0.1. This package defines our MAS and the boundary to the external material portal. It does not implement the portal or a running MAS.

**Start here: [Recipient handoff — current state, ownership, worked example, and next steps](handoff.md).** It explains what has been delivered, what still needs implementation, and what information we need from the portal owner.

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
| [architecture.md](architecture.md) | Roles, decisions, mechanic partitioning, candidate review, and Builder handoff |
| [portal-io.md](portal-io.md) | Proposed request/response contract, ownership, failure behavior, and open integration details |
| [material-portal.ts](material-portal.ts) | Dependency-free TypeScript port and data types |
| [material-portal.schema.json](material-portal.schema.json) | JSON Schema for request and result payloads |
| [examples](examples/README.md) | Archetype and mechanic requests, candidate response, and failure response |
| [architecture.html](architecture.html) | Standalone diagrams and contract summary; download and open locally to print |
| [diagrams](diagrams) | SVG exports and editable DOT sources |

Start implementation with a mock adapter using the fixtures, then connect the teammate's real portal behind the same interface. Fixture candidates must never count as researched or verified sources.
