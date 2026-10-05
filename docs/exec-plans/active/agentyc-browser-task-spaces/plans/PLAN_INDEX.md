# Execution plan registry

This file is the machine-readable-by-convention registry for the plan. Only the nine canonical phase files below are executable. Files under `plans/superseded/` are historical redirects and must not be treated as phases.

## Phase order and activation

| Phase | Canonical file                      | Depends on | Status   | Entry condition                                                                                                    |
| ----: | ----------------------------------- | ---------- | -------- | ------------------------------------------------------------------------------------------------------------------ |
|     0 | `phase-0-discovery.md`              | none       | complete | bounded Phase 0 checker and headed existing-Chrome evidence passed; later-phase residuals are explicit             |
|     1 | `phase-1-architecture.md`           | 0          | complete | Phase 1 exit gate passed; architecture/security/contracts are frozen and Phase 2 may implement exact schemas       |
|     2 | `phase-2-contracts.md`              | 1          | complete | Phase 2 schemas/fixtures/checkers pass; deterministic contract handoff is complete                                 |
|     3 | `phase-3-core-implementation.md`    | 2          | complete | Phase 3 host/bridge/ledger gate passes and U3-1 is closed                                                          |
|     4 | `phase-4-extension.md`              | 3          | active   | Phase 3 host/bridge/ledger gate passes and U3-1 is closed                                                          |
|     5 | `phase-5-context-and-automation.md` | 4          | pending  | Phase 4 real-Chrome bridge gate passes                                                                             |
|     6 | `phase-6-direct-cli-sdk.md`         | 5          | pending  | Phase 5 context/action gate passes                                                                                 |
|     7 | `phase-7-hardening.md`              | 6          | pending  | Phase 6 direct interface gate passes                                                                               |
|     8 | `phase-8-mcp-compatibility.md`      | 7          | pending  | Direct launch validation is complete or blocked with a documented reason; existing MCP remains separately runnable |

Allowed status transitions are `pending -> active -> complete` or `pending -> active -> blocked`. Exactly one phase may be `active`. A phase is marked `complete` only after its exit gate and required artifacts pass; a blocked phase names an owner, impact, and next action. The README status and this registry must be updated in the same change.

## Execution rules

- Do not execute superseded files.
- Do not infer phase activation from files being present. Phase 1, Phase 2, and Phase 3 are complete only because their checked tasks, quality gates, and evidence records pass; Phase 4 is the sole active phase. Core/host/extension/direct-client slices remain subject to their owning phase gates. Phase 0 may audit and probe the existing host-backed path; deterministic host tests or disposable probes do not close its live existing-profile gate. Future dependencies must name existing versus planned prerequisites.
- Every task names whether a path is existing or planned/new. Planned files are created by the task that first owns them. Later sequential phases may modify an existing shared artifact only when the task says so and limits its owned section: Phase 1 owns architecture checkers, Phase 5 owns direct context benchmarks, Phase 7 owns direct release/CI sections, and Phase 8 owns MCP adapter/release sections.
- The canonical domain term is `space`; `space_id` is the canonical field. `group_id` is only a deprecated compatibility alias or a visual-group hint.
- MCP is a host-backed logical adapter over the same host broker and currently runs over stdio only. The offline server exposes 29 routes; the connected remote catalog declares 30, with 12 returning `capability_unavailable`. MCP compatibility is not a prerequisite for direct launch, but it is not distribution-ready until Phase 8's live and release gates pass.
- Direct-product validation is not MCP release evidence. Any MCP surface or support-status change must be recorded in Phase 8; do not imply the removed direct-CDP MCP server, `browser_*` tools, or HTTP transport remain available.

## Required phase artifact convention

Generated logs, traces, screenshots, token reports, and debug bundles go under `artifacts/<phase>-<purpose>/`, are redacted, and are not source-controlled unless a task explicitly says so. Each artifact records schema version, build tuple, environment, timestamp, command, result, and redaction status. `.gitignore` must exclude local artifacts and secrets before Phase 0 creates them.
