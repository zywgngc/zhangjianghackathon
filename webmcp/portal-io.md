# Proposed material portal I/O contract

**Version 0.1 — proposal for the portal owner.** Tool names, transport, authentication, image resolution, and service limits still need integration agreement. The examples are synthetic fixtures and do not claim real source discoveries.

## Ownership

| Component | Owner | Responsibility |
| --- | --- | --- |
| MAS and project service | Our team | Archetypes, decisions/defaults, mechanic contracts, candidate review, state, and blueprint |
| Browser WebMCP adapter | Our team | Expose authenticated studio commands to browser agents |
| `MaterialPortalPort` client adapter | Our team | Translate application requests/results to the portal's actual MCP tools |
| External MCP portal | Teammate | Material discovery, retrieval infrastructure, and delivery |

![Material portal boundary](diagrams/material-portal.svg)

```mermaid
flowchart LR
    A["Our MAS researcher"] -->|Description, images, constraints| B["MaterialPortalPort"]
    B --> C["Client adapter: map MCP tool calls"]
    C <--> D["External MCP material portal: teammate-owned"]
    C --> E["Validate response: candidates or failure"]
    E -->|Source evidence and limitations| A
    A --> F["Our reviewer: inspect, compare, compose"]
```

## Request

Use the [TypeScript types](material-portal.ts) and [JSON Schema](material-portal.schema.json) as the proposed application contract. The root schema accepts either a request or result; validate a particular boundary with `#/$defs/MaterialSearchRequest` or `#/$defs/MaterialSearchResult`.

| Field | Meaning |
| --- | --- |
| `contractVersion` | Exact supported contract version, currently `0.1` |
| `requestId` | Stable ID for one logical request; unchanged across retries of identical input |
| `projectRevision` | Revision used to formulate the request; stale responses cannot silently replace current work |
| `purpose` | `archetype` or `mechanic` |
| `description` | Self-contained description of the desired game or behavior |
| `images` | Zero or more image references, MIME types, and descriptions |
| `constraints` | Selected engine/version, web platform, language preference, and input modes |
| `materialKinds` | Requested categories of materials |
| `limit` | Maximum number of candidates, integer 1–10 |
| `mechanicId`, `requiredBehavior` | Required for mechanic research; absent for archetype research |

An archetype request asks for similar playable structures and source references. A mechanic request asks for implementations matching a defined functional contract. Candidate material types are `game_repository`, `source_code`, `algorithm_documentation`, and `reference_media`.

Images are inputs to retrieval; responses can contain code and documents. Use portal-accessible URIs or opaque asset references whose resolution both teams agree on. The `asset://` references in examples are placeholders for that agreement. Unsupported images return `UNSUPPORTED_INPUT` or a partial result explaining the limitation; they are never silently ignored. Avoid passing unrelated project data or local machine paths.

## Response

All responses echo `contractVersion` and `requestId`, and identify `provider`, `retrievedAt`, and `isMock`.

`completed` means the search finished; it does not mean candidates have passed our review. A completed result can contain fewer candidates than requested, including zero. `partial` means some retrieval work could not finish and must include a limitation. Both carry candidates and limitations.

Each candidate has an ID, type, title, summary, source URI, optional repository and revision, file paths/symbols when available, coverage hints, license evidence or null, supporting references, and limitations. IDs are stable within a provider and request; the MAS deduplicates across runs using source identity. Null or empty metadata remains unknown. Code and asset permissions are not inferred from each other.

The MAS validates result shape, matching request IDs/versions, candidate count, requested material types, and unique candidate IDs. It then checks source evidence and architecture compatibility. Provider summaries and evidence references are untrusted material; they cannot override developer locks, authorize commands, or mark a candidate verified.

## Failure, retries, and cancellation

`failed` carries an error code, message, retryability, and optional retry delay. Codes: `UNAVAILABLE`, `TIMEOUT`, `RATE_LIMITED`, `INVALID_INPUT`, `UNSUPPORTED_INPUT`, and `AUTH_REQUIRED`.

Retry only retryable failures within the run's budget, using bounded backoff and the same request ID for identical input. The adapter caches completed requests or uses portal idempotency support where available. A new revision or changed input receives a new request ID. A timeout does not authorize duplicate paid operations; the adapter must reconcile provider jobs when the portal supports them.

The port accepts an optional `AbortSignal`. Explicit cancellation propagates through the adapter; an abort can reject with `AbortError`, and the orchestrator records a cancelled task. Expected provider failures use structured failed results. A browser page unloading does not cancel durable backend work. The application exposes a separate explicit run-cancellation command.

If the portal uses asynchronous jobs, the adapter maps job creation, status, and result retrieval into this application interface. Backend workers persist the provider job ID so recovery can resume an existing request. Browser tools return our durable run ID promptly.

## Development and readiness

Use [the examples](examples/README.md) for a mock adapter first. Their `isMock: true` marker is mandatory. Production blueprint readiness excludes fixture results. The live adapter must not silently fall back to mock candidates when retrieval fails.

Before connecting the portal, agree on actual MCP tool names and schemas, transport endpoint, server-side authentication, image-reference resolution and expiry, source-file access, pagination/result limits, timeouts/rate limits, job recovery, and cancellation support. These are integration details for the teammate-owned service, not implementations included in this package.
