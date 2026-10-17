# Material portal fixtures

These payloads illustrate the proposed contract. All materials, repository URLs, paths, IDs, and provider evidence in responses are synthetic. The reserved `materials.example` domain and `asset://` handles are not live retrieval targets.

- [Archetype request](archetype-request.json): image and description to discover similar game structures.
- [Mechanic request](mechanic-request.json): a waypoint-movement contract to find code or algorithm documentation.
- [Candidate response](mechanic-response.json): two alternative implementations with explicit unknown metadata.
- [Failure response](failure-response.json): a retryable portal outage with no fabricated candidates.

Validate payloads against [the schema](../material-portal.schema.json), selecting the request or result definition. The MAS additionally checks request/result correlation, maximum candidate count, and candidate types against the original request.
