# Execution-plan and phase-handoff standard

This is the durable format for Feature and Initiative work. A plan is a live execution artifact, not a static essay. Agents update it in the same session as the work it describes.

## Folder structure

Use:

```text
docs/exec-plans/active/<feature>/
  README.md
  research/
    source-map.md
    source-ledger.md
    decision-exploration.md
    decision-closure.md
    findings-1.md
    open-questions.md              # only when needed
  plans/
    phase-0-discovery.md
    phase-1-architecture.md
    phase-2-contracts.md           # when contract surface is non-trivial
    phase-3-core-implementation.md
    phase-4-ui-implementation.md  # when there is a user-facing surface
    phase-5-integration.md         # when external services are involved
    phase-6-hardening.md
    phase-7-validation.md
    phase-8-rollout.md             # when the change reaches users
```

Select only the phases the work needs, renumbering them consecutively. Do not create empty phase files merely to match this example. For a Small plan, use a single decision/task document instead of this folder.

## README: front-loaded decision brief

`README.md` must be readable in under two minutes and must link to the archive:

```markdown
# [Feature]

- **Owner:** [named person]
- **Primary outcome:** [one measurable outcome]
- **Status:** planning / active phase N / blocked / ready for rollout / shipped
- **Release posture:** direct / internal / flag / private preview / staged

## Decision brief

**Recommendation:** [one choice]

**Why now:** [problem and evidence]

**Why this choice:** [two or three decisive reasons]

**Main trade-offs:** [accepted costs]

**Risks to watch:** [top two or three]

**Stop rationale:** [why research and alternatives are sufficient]

## Scope

### Build now
- [complete GA-ready capability]

### Out of scope or deferred
- [item] — [reason and revisit condition]

## Success and operations

- **Success metrics:** [specific measurements]
- **Validation:** [commands/evidence]
- **Rollback:** [reversible action]
- **Operational owner:** [named person]

## Archive

- [Decision exploration](research/decision-exploration.md)
- [Decision closure](research/decision-closure.md)
- [Source map](research/source-map.md)
- [Source ledger](research/source-ledger.md)
- [Phase files](plans/)
```

The main brief must not dump every candidate or every source. The archive preserves completeness without making the user hold the entire search in working memory.

## Required phase-file shape

Every phase file must contain all sections below. A phase with an unchecked required item is not complete.

```markdown
---
phase: 0
name: Discovery
status: pending | active | blocked | complete
owner: [named person]
primary_outcome: [one result]
depends_on: [phase IDs or none]
---

# Phase [N] — [name]

## Objective
[One paragraph: what this phase accomplishes and why it exists.]

## Context
[Self-contained context. Link to the decision brief, closure, source IDs, current code, and user-visible problem. A fresh agent must not need the original chat.]

## Handoff in
- **Inputs:** [files, decisions, source IDs, prerequisites]
- **Must already be true:** [preconditions]
- **Do not reopen:** [closed decisions unless new evidence appears]

## Confirmed facts
- [fact with repository location or source ID]

## Working assumptions
- [assumption, verification method, owner]

## Unresolved questions and blockers
- [question/blocker, impact, owner, next action]

## Scope
### In scope
- [items]
### Out of scope
- [items]

## Affected surfaces
- **Files/modules:** [exact paths, symbols, or discovery commands]
- **Contracts/data:** [exact surfaces]
- **Server/client ownership:** [who owns each meaningful flow and why it is user-visible]

## Tasks
- [ ] P0-T1 — [imperative atomic task]
  - **Files/surfaces:** [paths or symbols]
  - **Input:** [what it uses]
  - **Done when:** [observable acceptance condition]
  - **Validation:** `[exact command or evidence]`
  - **Owner:** [person/agent]

## Quality checklist
- [ ] [phase-specific quality gate]

## Decisions recorded this phase
- **D-N:** [decision, rationale, evidence, alternatives, trade-off]

## Validation evidence
- **Command/action:** `[exact invocation]`
- **Result:** [actual output, artifact, or link]
- **Date/environment:** [version, commit, runtime]

## Handoff out
- **Artifacts produced:** [files, code, tests, research, screenshots]
- **Decisions closed:** [IDs]
- **Validation passed:** [commands/evidence]
- **Known residuals:** [non-blocking items and owners]
- **Next phase starting condition:** [exact condition]

## Exit gate
This phase may advance only when:

- Every required task and checklist item is checked.
- No unresolved blocker remains, or each blocker has been explicitly moved to a named follow-up with an owner and impact.
- Validation evidence is recorded.
- Handoff out is complete enough for a fresh agent to start the next phase without clarification.
```

## Task-writing rules

A task is implementation-ready only when it has:

- One imperative verb and one observable result.
- Exact file path, module, symbol, endpoint, schema, or a command that discovers it.
- Inputs and dependencies.
- A completion condition.
- A validation command or evidence method.
- An owner when work is distributed.

Reject vague tasks such as:

- “Handle edge cases.”
- “Update the backend.”
- “Research the API.”
- “Make it production-ready.”

Rewrite them as bounded tasks, for example:

```text
- [ ] P3-T4 — Add idempotency-key validation to `convex/orders.ts:createOrder`.
  - Files/surfaces: `convex/orders.ts`, `convex/orders.test.ts`.
  - Done when: duplicate keys return the original order ID and never create a second row.
  - Validation: `pnpm vitest convex/orders.test.ts --run`.
```

## Phase discipline

- Only one phase may be `active` at a time.
- The agent marks a phase active before starting and complete immediately after its gate passes.
- The plan file is updated in the same work session as code, docs, or research changes.
- If work is blocked, keep the phase active/blocked and record the blocker; do not advance for convenience.
- If an item belongs in a later phase, move it explicitly and link the destination task.
- “Mostly done” is not complete: required checkboxes must be 100% checked.
- A later phase cannot quietly redo a closed decision. Reopen only with new evidence, a changed constraint, or an explicit owner decision.

## Coding-agent readiness audit

Before closing a phase, ask:

1. Could a fresh agent identify the exact files and symbols without asking?
2. Could it tell what not to change?
3. Are server/client, data, auth, tenancy, provider, and error boundaries explicit?
4. Does every task have a test or evidence path?
5. Are the prior phase's decisions and source links present?
6. Are blockers, residuals, and next starting conditions explicit?

If any answer is no, the phase stays open.

## Plan-level completion audit

A plan is implementation-ready only when:

- The decision brief has one owner, one primary outcome, scope, non-goals, release posture, and stop rationale.
- Decision-critical alternatives were explored, verified, and either selected, rejected, or marked unresolved.
- The source ledger covers the declared source map and records saturation or an explicit ceiling.
- Architecture decisions have rationale, evidence, alternatives, trade-offs, risks, and test strategy.
- Every phase is self-contained and has tasks, validation, blockers, handoff in/out, and an exit gate.
- Success metrics, observability, rollout, rollback, support ownership, and deferred work are explicit.
- A fresh coding agent could execute each phase without hidden conversation context.
