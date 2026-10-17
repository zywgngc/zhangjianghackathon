# Material portal fixtures

These payloads illustrate the proposed contract. All materials, repository URLs, paths, IDs, and provider evidence in responses are synthetic. The reserved `materials.example` domain and `asset://` handles are not live retrieval targets.

All fixtures use v0.2. Initial portal requests/results use discussion round `0`; follow-up slots are reserved from separate run-wide main and material budgets.

- [Archetype request](archetype-request.json): image and description to discover similar game structures.
- [Mechanic request](mechanic-request.json): a waypoint-movement contract to find code or algorithm documentation.
- [Candidate response](mechanic-response.json): two alternative implementations with explicit unknown metadata.
- [Failure response](failure-response.json): a retryable portal outage with no fabricated candidates.
- [Clarification response](portal-clarification-response.json): an alternative response to the initial mechanic request, with an optional question and default.
- [Portal follow-up request](portal-follow-up-request.json): material follow-up slot 1, answering that question and refining the search.
- [Portal follow-up response](portal-follow-up-response.json): correlated result for that follow-up, still synthetic.
- [Main follow-up command](main-follow-up.json): an independent example of main requesting a touch-control refinement, using main slot 1. Its parent initial main command is conceptual; the full main start API is not included.

Validate payloads against [the schema](../material-portal.schema.json), selecting `MaterialSearchRequest`, `MaterialSearchResult`, or `MainFollowUpRequest` under `$defs`. The MAS additionally checks request/result and clarification correlation, default/answer option membership, maximum candidate count, candidate types, project revision, and durable budget reservations. The response fixtures illustrate alternative scenarios, not multiple accepted results for one request.
