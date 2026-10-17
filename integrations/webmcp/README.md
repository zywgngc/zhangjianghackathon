# Material portal v0.2 integration

Implementation follows the `Webmcp` proposal at commit
`437981e281281994c1f2377ab69b3c7871943b2e`. The original TypeScript declarations
are kept beside this file; the exact JSON Schema and provenance manifest are
packaged in `asset_agent/specs/`. No original contract fields were renamed.

## Call from the main agent

The selected integration is a Python module. Install `.[research]` in the host
environment, then pass the complete v0.2 JSON object:

```python
from asset_agent import ResearchMaterialPortal

with ResearchMaterialPortal(
    catalog_dir="catalogs/references",
    cache_path=".asset-agent/material-research.sqlite3",
) as portal:
    result = portal.search_sync(request)
    # In an async workflow: result = await portal.search(request)
```

`MaterialPortal` also dispatches v0.2 requests to this implementation and retains
legacy art requests separately. Its corresponding options are
`reference_catalog_dir`, `research_cache_path`, and `research_timeout_seconds`.
Explicit `ArtMaterialPortal` and `AssetAgent` calls remain available for art-file
search, resizing, and delivery.

For a process boundary, the same operation accepts one JSON payload on stdin:

```text
python -m asset_agent research - --catalog catalogs/references
```

Stdout contains one JSON result. Expected provider failures remain structured
`status: "failed"` values, rather than being replaced by fixtures. The CLI is a
local process adapter, not an MCP server. Remote MCP tool names, endpoint,
service authentication and source-download operations are not deployed here.
The host team can register this application operation behind its agreed MCP
transport without changing the request/result contract.

## Provider and result boundary

The included `LocalReferenceProvider` reads `materials.json`. Its search is
offline and metadata-based; the separate bootstrap explicitly downloads small,
immutable upstream source files. The included catalog has 13 real candidates
and 24 pinned source/license files. Cached files are for inspection and are
never executed by the portal.

An injected synchronous provider implements:

```python
def search(request, *, cancel_event):
    return {
        "candidates": [...],       # exact v0.2 MaterialCandidate values
        "limitations": [...],
        "partial": False,
        "isMock": False,
    }
```

It can instead return a complete, correlated `MaterialSearchResult`, including
`needs_clarification` or `failed`. Bare clarification results use
`status: "needs_clarification"`, `clarificationId`, and `questions`. Provider
errors can raise `asset_agent.research_portal.ProviderError` with one of the
six official codes, retryability, and an optional delay. Providers must not put
credentials into user-facing messages.

The adapter validates candidates and discussion correlation. It does not infer
reuse rights, select a final implementation, execute retrieved instructions, or
claim runtime/behavior verification. Source-code licenses do not establish art,
audio, trademark, or other asset rights. Unknown metadata remains unknown.

## Images, discussion, and state

Images must use portal-accessible URIs or separately agreed opaque references;
local filesystem image paths are rejected. The local provider currently has no
image resolver or visual retrieval model. Requests containing images produce a
partial response explaining that only text was searched. A configured provider
may explicitly declare `supports_images = True` when it actually processes them;
bare adapters can return `imagesProcessed: True` as adapter metadata.

Initial round is zero and the schema accepts follow-up slots 1–3. This provider
does not reserve the MAS's run-wide material/main follow-up allowances, maintain
project decisions, or accept stale application state on the MAS's behalf.
The upstream durable orchestrator remains responsible for those checks.

When a parent exchange is cached locally, its run/thread/parent/question/answer
relationships and nondecreasing project revision are checked. Default mode also
permits stateless full-snapshot follow-ups, with a limitation explaining the
upstream correlation responsibility. `require_parent=True` requires a cached
parent instead.

SQLite records request content hashes, pending operations, cancellations, and
validated results. A duplicate request ID with changed content returns
`INVALID_INPUT`. Identical concurrent requests share one operation, including
across instances using the same database and provider name. Completed results
preserve their original retrieval timestamp across restarts.

A response timeout retains the pending operation; retrying identical input
polls it without starting another provider call. Terminal explicitly retryable
provider errors can be retried by the caller; there is no automatic retry loop.
If the process dies with a pending remote operation, this implementation does
not automatically replay it. `get_request_status()` exposes the stored state;
provider-specific job reconciliation must be agreed before using paid remote
jobs. The included local provider has no external jobs.

For explicit synchronous cancellation pass
`options={"signal": threading.Event()}`. Async task cancellation also propagates
a signal. Providers must cooperate with `cancel_event.is_set()`; arbitrary
blocking third-party code cannot be forcibly stopped safely in a Python thread.
Cancelled tasks do not accept late results. `close(wait=False)` can stop waiting
for workers; it does not claim that external jobs were cancelled.

## Reproduce verification

```text
python tools/bootstrap_reference_catalog.py --offline
python tools/evaluate_research_portal.py
python -m unittest discover -s tests
```

Use the environment where `.[research]` is installed. The evaluation writes a
new directory under `output/research-live-*` containing all nine requests,
responses, and a report. It records candidate counts and pinned revisions,
including zero-source gaps. It does not mark any candidate behavior verified.

The original `examples/webmcp-v02` response fixtures are synthetic and remain
`isMock: true`. Using a fixture provider requires explicit `allow_mock=True`;
production mode rejects both fresh mock results and cached mock results.
