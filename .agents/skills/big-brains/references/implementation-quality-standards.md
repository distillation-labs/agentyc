# Implementation quality standards

Apply these constraints to implementation-ready plans. Scale them to the actual risk; do not add abstractions without a reason.

## Modularity and file size

- Keep new or touched files at or below 400 lines whenever practical.
- Before a file crosses the limit, identify the extraction target: validator, adapter, state machine, repository, UI state component, or test helper.
- Do not hide a large file behind generated code or move complexity into an unowned utility.
- Each module should have one primary responsibility and a small, explicit contract.

## Contracts

- Define request, response, error, event, persisted-data, and provider types before implementation when the surface is non-trivial.
- Validate external input at the boundary and use explicit error states.
- Keep serialization/deserialization rules and compatibility behavior documented.
- Use an internal provider interface before making direct third-party calls; adapters own provider-specific behavior.

## Boundaries

Every plan must state:

- Server-owned behavior and why the user-visible result requires it.
- Client-only behavior and why it is safe there.
- Query, mutation, action, scheduled action, or HTTP action ownership for Convex flows.
- Server Action versus route handler choice for Next.js mutation paths.
- Organization/tenant scoping at every data boundary.
- Auth, permission, webhook verification, and secret-handling boundaries.

## Correctness and failure handling

- Define loading, empty, success, partial, retryable-error, terminal-error, and permission-denied states where relevant.
- Make idempotency, retries, timeouts, cancellation, duplicate delivery, ordering, and backpressure explicit.
- Never silently relax a security, data-integrity, or compliance requirement to make a path succeed.
- Record which failures are handled locally and which are escalated.

## Observability and logging

- Use the repository's structured logging convention, not `console.*` diagnostics.
- Log meaningful lifecycle events with safe, low-cardinality context.
- Never log secrets, tokens, full sensitive payloads, or unbounded user text.
- Define metrics for success, failure, latency, fallback, retry, queue/backlog, and resource behavior when material.

## Testing

Plans must cover:

- Happy path and representative edge cases.
- Failure modes and recovery behavior.
- Permission and cross-tenant isolation.
- Contract and serialization compatibility.
- Performance or resource guardrails when the decision depends on them.
- Deterministic tests first; live provider/browser checks only when necessary and explicitly requested or required for proof.

## Decision integrity

- Every meaningful architecture choice has rationale, evidence, alternatives, and accepted trade-offs.
- Do not introduce a provider, abstraction, cache, queue, migration, or background job merely because it is familiar.
- If a decision cannot yet be verified, keep it in working assumptions or unresolved questions; do not bury it in implementation tasks.
