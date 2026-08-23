# Phase library

The planning workflow uses pre-plan gates followed by only the execution phases needed for the work. Do not create a fixed ceremony stack for every request.

## Pre-plan gates

These gates happen before implementation phases for Feature and Initiative work. They are recorded in `research/` rather than treated as coding phases.

### Gate A — Decision exploration

**Purpose:** Surface structurally different candidate approaches before the first plausible answer becomes the architecture.

**Required outputs:** decision cards, isolated frame branches, wide candidate set, clusters, scores, traps, shortlisted candidates, and reduced-mode disclosure if isolation was unavailable.

**Exit gate:** every meaningful open decision has either a verified recommendation path or an explicit unresolved question with owner and impact.

### Gate B — Evidence saturation

**Purpose:** Verify candidate mechanisms and constraints with repository evidence, official documentation, primary sources, implementations, operational evidence, and relevant comparable products.

**Required outputs:** source map, source ledger, findings, conflicts, candidate verification queues, saturation record, and access/budget limits.

**Exit gate:** applicable source classes are covered, high-value sources were read beyond snippets, counterevidence was checked, load-bearing claims are mapped to evidence or unresolved questions, and the two-pass stopping rule is recorded.

### Gate C — Decision closure

**Purpose:** Turn exploration and research into a bounded recommendation that protects the user from carrying every alternative forward.

**Required outputs:** recommendation, decisive reasons, accepted trade-offs, rejected alternatives, traps, confidence, revisit triggers, and stop rationale.

**Exit gate:** each decision has one current recommendation, no hidden blocking uncertainty, and a clear rule for when it may be reopened.

## Discovery

**Use when:** the problem is not fully understood. Always use for Feature and Initiative work.

**Outputs:** evidence-backed problem statement, personas and pain points, current workflow, scope boundaries, pre-mortem, comparable products when relevant, open questions, and decision links.

**Checklist:**

- [ ] Problem is framed with evidence rather than assumption.
- [ ] User, operator, and affected non-user perspectives are covered.
- [ ] Current workflow includes loading, empty, error, retry, and recovery states.
- [ ] Scope is explicit: build now, out of scope, deferred.
- [ ] Pre-mortem risks have mitigations or accepted rationales.
- [ ] Discovery does not reopen a closed decision without new evidence.

**Exit gate:** architecture can begin without redoing discovery or guessing the user-visible outcome.

## Architecture

**Use when:** meaningful technical decisions exist. Always use for Feature and Initiative work.

**Outputs:** component relationships, file/module layout, data model and indexes, API contracts, state machines, server/client ownership, Convex/Next.js ownership where applicable, provider abstractions, tenancy/auth boundaries, trade-offs, risks, testing, rollout constraints, and file-size plan.

**Checklist:**

- [ ] Every meaningful decision has rationale, evidence, alternatives, and accepted trade-offs.
- [ ] Server/client ownership is explicit and justified by user-visible behavior.
- [ ] State transitions and error paths are defined.
- [ ] Multi-tenancy and authorization are enforced at every boundary.
- [ ] External providers are behind an internal contract and adapter.
- [ ] File-size and modularity plan is explicit.
- [ ] Scale, reliability, security, observability, testability, and cost are addressed where material.
- [ ] Discarded alternatives and traps are recorded.

**Exit gate:** a coding agent can implement without re-deciding architecture.

## Contracts & Schema

**Use when:** introducing a provider, public API, complex data model, cross-service contract, or serialization surface.

**Outputs:** exact types/interfaces, success/error request and response schemas, validation rules, provider adapter contracts, compatibility and serialization behavior.

**Exit gate:** all contract fields, constraints, error paths, and ownership boundaries are explicit.

## Core Implementation

**Use when:** backend, service, data, or business logic is needed.

**Outputs:** dependency-ordered work packages, exact files/symbols, success criteria, migrations, auth/tenancy enforcement, logging, and tests.

**Exit gate:** core behavior is implemented and targeted typecheck/lint/unit checks pass.

## UI Implementation

**Use when:** user-facing screens, forms, navigation, or interaction are needed.

**Outputs:** route/component list, data dependencies, responsive states, validation/submission flow, loading/empty/error/success behavior, accessible feedback, and tests.

**Exit gate:** the user-visible flow is navigable end to end and every planned state is handled.

## Integration

**Use when:** external API, webhook, OAuth, provider SDK, sync, rate limits, or third-party setup is required.

**Outputs:** adapter/client, auth, retries/backoff, timeout/cancellation, error mapping, webhook verification, quotas, and provider test evidence.

**Exit gate:** integration works behind an abstraction and failure behavior is explicit.

## Hardening

**Use:** after implementation and before validation.

**Outputs:** edge-case handling, permission/security review, performance checks, observability, alerts, runbook, support notes, and deferred work with owners.

**Exit gate:** core behavior is stable, failure modes are handled, and operations can diagnose it.

## Validation

**Use:** before rollout for any user-reaching Feature or Initiative.

**Outputs:** exact commands and outputs, browser/mobile/provider/staging evidence as needed, regression results, acceptance review, and unresolved residuals.

**Exit gate:** acceptance criteria pass, no blocking regressions remain, and evidence is recorded.

## Rollout

**Use:** when the change reaches users or changes operational behavior.

**Outputs:** release lane, cohort, promotion criteria, rollback trigger/action, support handoff, dashboards/alerts, ownership, and follow-ups.

**Exit gate:** release is complete or ready with an executable rollback and named post-launch owner.

## Every execution phase must contain

1. Objective.
2. Self-contained context.
3. Handoff in: inputs, preconditions, and decisions not to reopen.
4. Confirmed facts, working assumptions, unresolved questions, and blockers.
5. Scope and affected surfaces.
6. Concrete checkable tasks with files, done conditions, validation, and owner.
7. Quality checklist.
8. Decisions recorded.
9. Exact validation evidence.
10. Handoff out: artifacts, closed decisions, residuals, and next starting condition.
11. Exit gate that blocks advancement until the phase is 100% complete.
