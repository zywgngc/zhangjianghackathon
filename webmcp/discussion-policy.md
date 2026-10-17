# Bounded follow-up discussion — v0.2

**Contract v0.2, with runtime enforcement in the local reference service.** The original v0.1 design allowed preference changes and repeated research, but did not define a conversation protocol or round limit. This revision adds the requested limit on both boundaries. See [runtime.md](runtime.md) for implemented behavior, tests, and remaining live integration work.

## Two independent budgets

| Boundary | Direction of a follow-up request | Limit per research run |
| --- | --- | --- |
| Main application ↔ our MAS | Main refines the brief, answers our questions, or asks us to reconsider a result | Three follow-ups after the initial brief |
| Our MAS ↔ material portal | MAS refines a search, requests missing evidence/alternatives, or answers portal questions | Three follow-ups after initial searches, shared across the run's archetype and mechanic tasks |

The initial request is round `0`; follow-ups use slots `1`, `2`, and `3`. One request plus its response is one round. Several related questions can be batched into one round. The main and material counters are independent: a main follow-up does not itself consume a material slot, but a resulting material refinement does.

**Scope choice:** the material allowance is run-wide, not three extra rounds for every mechanic. Separate planned mechanics can have initial searches, bounded by the run's total request/cost budget. Repeating or narrowing an existing search intent is a follow-up even if a caller gives it a new thread ID. This prevents a larger mechanic graph from multiplying the discussion budget.

Three is a ceiling, not a target. Stop earlier when requirements and evidence are sufficient. Ordinary status reads, acknowledgements, and result retrieval do not consume a round. A retry with an identical logical payload and the same request ID does not consume an additional round; retries still obey separate attempt/time/cost limits.

## Main application discussion

The main application can send a follow-up without waiting for a question. For example: “Keep the movement system, but make this playable with one finger,” or “Explain why candidate B was rejected and reconsider it with this new constraint.”

When our Planner needs clarification, expose a question batch through the run status with a `clarificationId`, the request ID being answered, project revision, options, defaults, and affected mechanics. Main replies using [MainFollowUpRequest](discussion.ts). The [example](examples/main-follow-up.json) shows a direct refinement rather than an answer to a pending question.

Optional questions always have defaults. Automatic mode applies them without waiting and records their provenance. Guided mode may present a batch with “Use defaults”; submitting the answer batch uses one main follow-up slot. Lack of a reply must not turn an optional decision into an indefinite block. A required clarification, such as incompatible locks, has no fabricated default and may remain unresolved.

Accept a main command atomically: authorize it, validate its expected revision and parent request, check the budget, record the request once, reserve the next main slot, and persist the change. Increment the project revision when effective project inputs change. The reference service treats each follow-up message as new research context and increments the revision; a future explanation-only command could retain it. Invalidate dependent research and resume the same run. Return an acknowledgement with the run ID, accepted request ID, resulting project revision, reserved round, and remaining main follow-ups; see `MainAck` in [model.ts](src/model.ts).

An explicit main follow-up can move a `completed` or `partial` run back to `running` while its follow-up, time, and cost budgets remain. Preserve earlier blueprint versions and publish a new one after review. A clarification response to a pending batch moves `waiting_input` back to `running` under the same checks. Cancelled, fatally failed, or deadline-expired runs require an explicit new-run command; follow-ups do not extend the original deadline or silently replenish any budget.

## Material portal discussion

The existing `MaterialPortalPort.search` method remains the boundary. A follow-up is another search call carrying a complete current search specification plus a `discussion` context. A stateless portal can process that snapshot; a conversational portal can map the IDs to its own session. Native provider chat support is not required.

The portal may return `needs_clarification` with a question batch, a `clarificationId`, and any candidates already found. This result is not a failure and does not mean candidates are verified. The MAS first answers using established project facts or valid defaults. Escalate only unresolved, consequential choices to main, using its separate allowance. Do not forward provider instructions as executable workflow commands.

A response to portal questions includes `followUp.clarificationId` and answers correlated by `questionId`. An unsolicited refinement uses a null clarification ID and an empty answer list. Every follow-up also has a self-contained `message` explaining the requested change; the full request must agree with that message and the accepted project state.

Portal clarification questions have unique IDs. Optional questions have at least two options and a `defaultOptionId` identifying one of them. Required questions have no default and can accept free text. Validate option membership, duplicate IDs, answer provenance, required answers, and parent/question-batch correlation in application code; JSON Schema cannot enforce all these cross-record relationships.

## Correlation, revisions, and durable counters

| Field or record | Rule |
| --- | --- |
| `runId` | Stable across clarification, refinements, retries, and project revisions in this run |
| `threadId` | Identifies the main conversation or a particular portal search thread; changing it never resets the run-wide allowance |
| `requestId` | Unique for changed input; reused only for retries of the same logical request |
| `inReplyToRequestId` | Null for initial searches; for follow-ups, names the accepted parent request in the same boundary/thread/run |
| `round` | Initial `0`, otherwise the next server-reserved slot `1..3` on that boundary; different material threads may therefore have nonconsecutive local rounds |
| `projectRevision` / `expectedProjectRevision` | Preserve the snapshot used for research; reject stale mutations and do not overwrite newer results |
| Run ledger | Persist `mainFollowUpsUsed`, `materialFollowUpsUsed`, request IDs/content hashes, parent links, clarification batches, responses, and outcomes |

Reserve a material slot atomically before dispatch. Concurrent researchers cannot each claim the last slot. A dispatched request retains its slot if the provider fails or times out; an identical retry reuses it. Invalid main commands are rejected before reservation. The server never trusts a caller's round number as proof of remaining budget.

The same request ID with different content is an idempotency conflict. Different content cannot be disguised as a retry. Process duplicate delivery by returning the recorded result or pending operation. Check the parent chain and mark obsolete questions/results when later revisions supersede them.

Requests beyond round three are rejected before work or portal dispatch, with a `FOLLOW_UP_LIMIT_REACHED` application error and the latest result/gaps. This is an orchestrator command error, not a new provider error code. New revisions, page reloads, worker restarts, and agent changes do not reset the counters. Starting a new run requires an explicit main-application command and a new budget; the MAS must not automatically create one to escape the cap.

## Ending the discussion

After the last allowed response, finish with the evidence available. Apply compatible optional defaults and report unresolved mandatory information or source gaps as a partial blueprint. Never relax locks, invent sources, or mark unverified candidates as verified to produce a ready result.

If the portal asks another question after slot three, retain its candidates, record the unanswered question and exhausted budget, and stop calling it. If main still has allowance, it can clarify its own requirements, but that does not replenish the material budget. Reuse existing evidence where valid; otherwise return partial.

A required question with time remaining can wait for an answer within the run deadline. Deadline/cost exhaustion returns partial when useful work exists, or the documented failed outcome when no usable result exists. Closing the page is not cancellation; explicit cancellation still stops the run.

## Example exchange

```mermaid
sequenceDiagram
    participant Main as Main application
    participant MAS as Our research MAS
    participant Portal as Material portal
    Main->>MAS: Initial brief (main round 0)
    MAS->>Portal: Initial archetype/mechanic searches (round 0)
    Portal-->>MAS: Candidates and/or clarification batch
    MAS-->>Main: Research state, options, editable defaults
    Main->>MAS: Refine controls (main follow-up 1)
    MAS->>Portal: Refine affected search (material follow-up 1)
    Portal-->>MAS: Updated candidates + evidence gaps
    MAS->>Portal: Request missing source detail (material follow-up 2)
    Portal-->>MAS: Sources and limitations
    MAS-->>Main: Revised blueprint + remaining budgets
    Note over Main,Portal: Each boundary allows at most 3 follow-ups; stop sooner when sufficient
```

## Contract migration and verification

v0.2 is a breaking proposal update: portal requests require `discussion`; results echo its run/thread/round reference; `needs_clarification` is a new result variant. Existing fixtures are updated to round zero. Do not pass v0.2 payloads to a v0.1-only provider without an explicit adapter mapping and locally persisted discussion state. Do not silently discard a request for clarification.

The [shared TypeScript types](discussion.ts), [portal types](material-portal.ts), [JSON Schema](material-portal.schema.json), and [fixtures](examples/README.md) describe the payloads. The schema's named definitions also include `MainFollowUpRequest`; the implemented start/status/blueprint API is described separately in [runtime.md](runtime.md).

Runtime acceptance must demonstrate: fourth-round rejection on each boundary, independent counters, batched questions and valid defaults, retry idempotency, concurrent last-slot contention, stale-revision handling, restart recovery, partial output at exhaustion, and no counter reset when changing threads. Type/schema checks alone cannot establish these behaviors.
