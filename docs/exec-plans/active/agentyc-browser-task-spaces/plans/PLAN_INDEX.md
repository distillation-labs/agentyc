# Execution plan registry

This file is the machine-readable-by-convention registry for the plan. Only the nine canonical phase files below are executable. Files under `plans/superseded/` are historical redirects and must not be treated as phases.

## Phase order and activation

| Phase | Canonical file                      | Depends on | Status  | Entry condition                                                                                                     |
| ----: | ----------------------------------- | ---------- | ------- | ------------------------------------------------------------------------------------------------------------------- |
|     0 | `phase-0-discovery.md`              | none       | active  | planning audit complete; no production implementation started                                                       |
|     1 | `phase-1-architecture.md`           | 0          | pending | Phase 0 exit gate checked and baseline artifacts exist                                                              |
|     2 | `phase-2-contracts.md`              | 1          | pending | Phase 1 exit gate checked; architecture/security artifacts exist                                                    |
|     3 | `phase-3-core-implementation.md`    | 2          | pending | Phase 2 schemas/fixtures pass                                                                                       |
|     4 | `phase-4-extension.md`              | 3          | pending | Phase 3 host/bridge/ledger gate passes and U3-1 is closed                                                           |
|     5 | `phase-5-context-and-automation.md` | 4          | pending | Phase 4 real-Chrome bridge gate passes                                                                              |
|     6 | `phase-6-direct-cli-sdk.md`         | 5          | pending | Phase 5 context/action gate passes                                                                                  |
|     7 | `phase-7-direct-rollout.md`         | 6          | pending | Phase 6 direct interface gate passes                                                                                |
|     8 | `phase-8-mcp-compatibility.md`      | 7          | pending | Direct rollout is explicitly released or blocked with a documented reason; existing MCP remains separately runnable |

Allowed status transitions are `pending -> active -> complete` or `pending -> active -> blocked`. Exactly one phase may be `active`. A phase is marked `complete` only after its exit gate and required artifacts pass; a blocked phase names an owner, impact, and next action. The README status and this registry must be updated in the same change.

## Execution rules

- Do not execute superseded files.
- Do not create a phase-local crate or test dependency before the phase that creates it; Phase 0 uses `agentyc-tests`/standalone probes and may not depend on `agentyc-host`.
- Every task names whether a path is existing or planned/new. Planned files are created by the task that first owns them. Later sequential phases may modify an existing shared artifact only when the task says so and limits its owned section: Phase 1 owns architecture checkers, Phase 5 owns direct context benchmarks, Phase 7 owns direct release/CI sections, and Phase 8 owns MCP adapter/release sections.
- The canonical domain term is `space`; `space_id` is the canonical field. `group_id` is only a deprecated compatibility alias or a visual-group hint.
- MCP is an adapter over the same host broker. Its compatibility tests and server must remain runnable throughout Phases 0–7, but MCP compatibility is not a prerequisite for direct rollout.
- The direct product gate must not change the legacy MCP implementation silently. Any compatibility change is isolated to Phase 8 or explicitly recorded as a migration prerequisite.

## Required phase artifact convention

Generated logs, traces, screenshots, token reports, and debug bundles go under `artifacts/<phase>-<purpose>/`, are redacted, and are not source-controlled unless a task explicitly says so. Each artifact records schema version, build tuple, environment, timestamp, command, result, and redaction status. `.gitignore` must exclude local artifacts and secrets before Phase 0 creates them.
