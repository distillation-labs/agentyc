# Agentyc Roadmap

Agentyc is being narrowed to a pure MCP-first browser automation runtime for coding agents. The mission of this roadmap is to align the repository, runtime, and public contract around deterministic, fast, reliable, token-efficient browser automation over MCP, with structured extraction that does not require an API key for core workflows, while removing cloud-, sync-, and agent-era layers that no longer match the product.

## North Star And Success Metrics

North star: `agentyc mcp` exposes a stable, inspectable browser automation contract that a coding agent can use to drive Chrome deterministically, retrieve useful state with bounded context cost, extract structured data without LLM dependence for common cases, and share a live browser workspace with a human when collaboration is needed.

Top-level success metrics:
- Public docs and package metadata match the code paths that actually ship.
- Core product docs point to `agentyc/mcp/server.py`, `agentyc/mcp/cli.py`, `agentyc/mcp/state.py`, `agentyc/tools/extraction/router.py`, `agentyc/tools/service.py`, `agentyc/config.py`, `agentyc/browser/session.py`, and `agentyc/browser/session_manager.py` as the operational source of truth.
- Deterministic extraction covers common link/list/table/form/key-value workflows without requiring API credentials.
- Browser actions and state retrieval stay bounded, explicit, and inspectable instead of hanging silently.
- `CrashWatchdog` is safely attached to existing and future page targets without duplicating unrelated monitoring responsibilities.
- Legacy cloud, actor, sync, and duplicate-controller layers no longer define the repository story.
- Shared-browser workflows clearly expose human-owned and agent-owned surfaces, including parallel-agent scenarios.
- Active implementation modules follow explicit modularity guardrails: shared logic is extracted, domain seams are named, most active files stay under roughly 700-800 lines, files above 1000 lines have either been split or carry explicit justification plus a tracked refactor plan.

## Roadmap Principles

- Keep the product narrow: pure MCP-first browser automation for coding agents.
- Treat current code, not stale docs, as the authoritative starting point.
- Prefer one active runtime path over dual local/cloud or old/new architectures.
- Preserve deterministic extraction and no-API-key workflows as a first-class product advantage.
- Remove stale public claims in the same program as code cleanup.
- Keep browser behavior inspectable through state, HTML, screenshots, and explicit errors.
- Treat modular refactors as runtime hardening work, not cosmetic cleanup: extract shared logic, avoid duplication, and split large modules into reusable domain-focused files such as views/models, services, helpers, validators, adapters, event wiring, watchdog submodules, parser/formatter modules, and test fixtures/helpers.
- Keep active implementation files generally below 700-800 lines. Treat files above that range as refactor candidates, files above 1000 lines as priority refactor targets, and require explicit written justification for exceptions.
- Use pure MCP/CDP collaboration primitives first; extension-based tab groups or colors remain optional future work only.
- Make every release claim evidence-backed through docs sync, cleanup proof, and benchmarks.

## Phase Order

| Phase | Purpose |
| --- | --- |
| [Phase 1](./phase-1.md) | Freeze the intended MCP/browser contract and audit the repository against current reality. |
| [Phase 2](./phase-2.md) | Rewrite public docs and packaging metadata so the repo tells the same story as the code. |
| [Phase 3](./phase-3.md) | Delete non-goal layers and remove scope-confusing legacy paths. |
| [Phase 4](./phase-4.md) | Safely attach `CrashWatchdog` to the live browser session lifecycle. |
| [Phase 5](./phase-5.md) | Harden the runtime and simplify the surviving architecture around the narrowed product. |
| [Phase 6](./phase-6.md) | Build benchmark and eval proof for reliability, determinism, and bounded runtime behavior. |
| [Phase 7](./phase-7.md) | Reduce token and context cost without degrading actionability or deterministic extraction. |
| [Phase 8](./phase-8.md) | Ship collaborative human+agent shared-browser UX with clear ownership semantics. |
| [Phase 9](./phase-9.md) | Publish the cleaned product with release gates, cleanup proof, and aligned public artifacts. |

## Milestone / Release Gate Summary

| Gate | Meaning |
| --- | --- |
| Contract Baseline Frozen | Supported MCP tools, CLI entrypoints, state payloads, extraction behavior, and config knobs are audited and intentionally scoped. |
| Public Contract Synced | `README.md`, `docs/`, `pyproject.toml`, `docker/README.md`, and stale internal/public READMEs no longer describe cloud- or sync-era product shapes. |
| Non-Goal Deletion Landed | `agentyc/browser/cloud/**`, `agentyc/sync/**`, `agentyc/actor/**`, `agentyc/mcp/controller.py`, cloud-browser tests, and other justified removals are deleted or explicitly retained with written rationale. |
| Watchdog And Runtime Hardened | `CrashWatchdog` is wired safely, reconnect/teardown paths are bounded, and remaining runtime branches reflect current scope. |
| Modularity Guardrails Met | Core runtime modules are split along domain seams, oversized files are reduced or justified, and modularity is treated as part of maintainability and runtime quality. |
| Benchmarks And Efficiency Proof Ready | Deterministic extraction, latency, reliability, compact-state behavior, and context-cost claims are measurable and reproducible. |
| Shared-Browser UX Ready | Human-owned and agent-owned tabs/windows are visible, inspectable, and workable in stock Chrome without extension dependence. |
| Publication Gate Passed | Public docs, package surface, cleanup evidence, and runtime behavior all align with the MCP-first browser automation story. |

## Phase Index

- [Phase 1: Contract Freeze And Repository Audit](./phase-1.md)
  Summary: lock the real contract before cleanup so later deletions and rewrites are intentional rather than reactive.
- [Phase 2: Docs Sync And Public Contract Rewrite](./phase-2.md)
  Summary: update public-facing material to match the narrowed product and current module layout.
- [Phase 3: Delete Non-Goal Layers And Scope Reduction](./phase-3.md)
  Summary: remove cloud-, actor-, sync-, and duplicate-controller-era code that conflicts with the product direction.
- [Phase 4: CrashWatchdog Safe Integration](./phase-4.md)
  Summary: connect crash monitoring to the actual session lifecycle with careful target coverage and cleanup semantics.
- [Phase 5: Runtime Hardening And Architecture Simplification](./phase-5.md)
  Summary: reduce configuration and session complexity so the active runtime is smaller, clearer, and easier to verify.
- [Phase 6: Benchmark And Eval Harness](./phase-6.md)
  Summary: establish repeatable evidence for deterministic extraction, reliability, latency, and failure behavior.
- [Phase 7: Token And Context Efficiency](./phase-7.md)
  Summary: make MCP state and extraction outputs cheaper for coding agents while preserving useful signal.
- [Phase 8: Collaborative Human+Agent Workspace UX](./phase-8.md)
  Summary: make shared-browser ownership obvious for humans, agents, and subagents using pure MCP/CDP-friendly techniques.
- [Phase 9: Publication, Cleanup Proof, And Release Gate](./phase-9.md)
  Summary: package the narrowed product with evidence that docs, code, tests, and repository cleanup now agree.
