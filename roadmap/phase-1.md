# Phase 1: Contract Freeze And Repository Audit

## Goal

Freeze the intended MCP-first browser automation contract for agentyc and produce a repo-grounded audit that distinguishes active product surfaces from transitional or stale ones.

## Why This Phase Exists Now

The repository already contains the newer MCP/browser architecture, but public docs and some older layers still describe a broader product than the code should continue to support. Before deleting modules or rewriting public documentation, the team needs a shared baseline for what is intentionally supported in `agentyc mcp`, what is merely leftover compatibility, and which files are stale enough to remove.

## Repo-Specific Context

- The narrowed product is a pure MCP-first browser automation runtime for coding agents.
- Deterministic extraction without an API key is a core product property and must remain visible in the contract.
- Public docs are stale relative to code, including `README.md`, `docs/overview.md`, `docs/features.md`, `docs/architecture.md`, `docs/api.md`, `docs/configuration.md`, `docs/tech-stack.md`, `pyproject.toml`, `docker/README.md`, and some README files under `agentyc/`.
- The operational source-of-truth modules are `agentyc/mcp/server.py`, `agentyc/mcp/cli.py`, `agentyc/mcp/state.py`, `agentyc/tools/extraction/router.py`, `agentyc/tools/service.py`, `agentyc/config.py`, `agentyc/browser/session.py`, and `agentyc/browser/session_manager.py`.
- Likely deletion targets already exist and should be audited explicitly rather than left implicit.
- The repository already has major oversized active modules that should be treated as concrete modular-refactor targets during the audit: `agentyc/browser/session.py` (~4045 lines), `agentyc/browser/watchdogs/default_action_watchdog.py` (~3633), `agentyc/mcp/server.py` (~2565), `agentyc/tools/service.py` (~2336), `agentyc/browser/watchdogs/downloads_watchdog.py` (~1381), `agentyc/dom/serializer/serializer.py` (~1380), `agentyc/tools/extraction/router.py` (~1329), `agentyc/dom/service.py` (~1268), `agentyc/browser/profile.py` (~1236), and `agentyc/dom/views.py` (~1044).
- Secondary modularity watchlist files include `agentyc/browser/session_manager.py` (~911), `agentyc/browser/watchdogs/dom_watchdog.py` (~867), and `agentyc/browser/watchdogs/har_recording_watchdog.py` (~779).
- The modularity target is repo-wide, not stylistic: active implementation files should generally stay under 700-800 lines, files above that range should be recorded as refactor candidates, files above 1000 lines should be prioritized, and exceptions should require explicit justification.
- For this Python codebase, expected split seams include views/models, services, helpers/utilities, validators, adapters, event wiring, watchdog submodules, parser/formatter modules, fixtures/helpers, and other domain-focused files rather than monolithic service modules.

## In Scope

- Audit the effective MCP tool surface, CLI modes, browser state payloads, extraction behavior, and supported config knobs.
- Mark what is part of the intended product contract versus transitional compatibility or legacy drift.
- Inventory stale public docs, stale package metadata, and stale internal/public README files.
- Produce an explicit candidate deletion list for cloud-, sync-, actor-, and duplicate-controller-era code and tests.
- Identify simplification hotspots in session, config, profile, watchdog, and MCP package surfaces.
- Identify modular split seams in oversized active modules so later refactors can be planned as runtime-hardening work rather than deferred cleanup.

## Out Of Scope

- Large deletions of legacy code.
- Broad doc rewrites beyond audit notes needed to unblock later phases.
- Runtime behavior changes that alter the supported contract.
- Benchmark implementation or shared-browser UX changes.

## Dependencies / Prerequisites

- Access to the current repository state and current public docs.
- Agreement that current code is the starting source of truth, not historical documentation.
- A written place to capture supported versus deprecated behavior for downstream doc and deletion work.

## Key Modules / Files To Touch

- `agentyc/mcp/server.py`
- `agentyc/mcp/cli.py`
- `agentyc/mcp/state.py`
- `agentyc/tools/extraction/router.py`
- `agentyc/tools/service.py`
- `agentyc/config.py`
- `agentyc/browser/session.py`
- `agentyc/browser/session_manager.py`
- `README.md`
- `docs/overview.md`
- `docs/features.md`
- `docs/architecture.md`
- `docs/api.md`
- `docs/configuration.md`
- `docs/tech-stack.md`
- `pyproject.toml`
- `docker/README.md`
- README files under `agentyc/`

## Implementation Workstreams

### Contract inventory

Capture what `agentyc mcp` actually exposes today: tool names, major request/response shapes, CLI entrypoints, state compaction modes, extraction routing behavior, and runtime assumptions around local/shared browser control.

### Public-surface drift audit

Compare the active code paths against public docs and metadata. Mark each mismatch as one of: intentional code that docs must catch up to, stale code that should be removed, or unresolved area requiring a product decision.

### Legacy-path inventory

Identify files and directories that keep the old cloud, sync, actor, or duplicate-controller story alive. Capture downstream tests, docs, and packaging references that would also need deletion.

### Simplification map

Record likely complexity centers for later phases, especially in `agentyc/browser/session.py`, `agentyc/browser/profile.py`, `agentyc/config.py`, `agentyc/mcp/__init__.py`, and `agentyc/browser/watchdogs/crash_watchdog.py`.

### Oversized-file and seam audit

Measure active implementation file sizes, record which modules exceed the 700-800 line target, prioritize files above 1000 lines, and identify concrete split seams such as service extraction, event wiring isolation, watchdog submodules, parser/formatter separation, or views/models division.

## Task Checklist

- [ ] Enumerate the supported MCP server/tool contract from `agentyc/mcp/server.py`.
- [ ] Enumerate the supported CLI contract from `agentyc/mcp/cli.py`.
- [ ] Enumerate state payload modes, ref behavior, and compact-context semantics from `agentyc/mcp/state.py` and `agentyc/tools/service.py`.
- [ ] Enumerate deterministic extraction routes and no-API-key behavior from `agentyc/tools/extraction/router.py`.
- [ ] Enumerate supported configuration knobs and defaults from `agentyc/config.py`.
- [ ] Identify which browser/session behaviors are part of the intended product contract versus accidental compatibility.
- [ ] Audit `README.md`, `docs/overview.md`, `docs/features.md`, `docs/architecture.md`, `docs/api.md`, `docs/configuration.md`, `docs/tech-stack.md`, `pyproject.toml`, `docker/README.md`, and README files under `agentyc/` for stale claims.
- [ ] Produce a deletion candidate list for `agentyc/browser/cloud/**`, `agentyc/sync/**`, `agentyc/actor/**`, `agentyc/mcp/controller.py`, cloud browser tests, and `agentyc/mcp/client.py` unless outbound MCP composition remains strategic.
- [ ] Produce a simplification candidate list for `agentyc/browser/session.py`, `agentyc/browser/profile.py`, `agentyc/config.py`, `agentyc/mcp/__init__.py`, and `agentyc/browser/watchdogs/crash_watchdog.py`.
- [ ] Record active implementation file sizes for the main runtime modules and compare them against the 700-800 line target.
- [ ] Flag all active files above 1000 lines as priority modular-refactor targets and record secondary watchlist files approaching the threshold.
- [ ] Identify concrete split seams for oversized modules, including likely extractions into views/models, services, helpers, validators, adapters, event wiring, watchdog submodules, parser/formatter modules, and fixtures/helpers where relevant.
- [ ] Capture repo-specific modular-refactor candidates for `agentyc/browser/session.py`, `agentyc/browser/watchdogs/default_action_watchdog.py`, `agentyc/mcp/server.py`, `agentyc/tools/service.py`, `agentyc/browser/watchdogs/downloads_watchdog.py`, `agentyc/dom/serializer/serializer.py`, `agentyc/tools/extraction/router.py`, `agentyc/dom/service.py`, `agentyc/browser/profile.py`, and `agentyc/dom/views.py`.
- [ ] Record explicit exception criteria for any oversized file that cannot be split immediately.
- [ ] Record open decisions that later phases must resolve instead of burying them inside code edits.

## Validation / Verification Checklist

- [ ] Every public contract statement is traceable back to one of the source-of-truth modules.
- [ ] Every stale public claim is labeled as doc-fix, code-delete, or decision-needed.
- [ ] The audit explicitly covers extraction, state, CLI, session lifecycle, and configuration.
- [ ] The deletion list includes associated tests, docs, and packaging references, not just code directories.
- [ ] The simplification list is concrete enough to drive implementation phases rather than vague cleanup.
- [ ] The audit includes current file-size data and clear modular split seams for oversized active modules.
- [ ] Priority refactor targets above 1000 lines are distinguished from secondary watchlist files.
- [ ] Any proposed exception to the file-size target has a concrete rationale instead of an implicit waiver.

## Deliverables / Artifacts

- Contract baseline document or tracked audit notes for MCP tools, CLI, state, extraction, config, and shared-browser behavior.
- Public-doc drift inventory covering the known stale files.
- Deletion candidate inventory and retention rationale list where needed.
- Simplification backlog tied to named modules.
- Oversized-file inventory with line counts, priority ranking, and proposed split seams.
- Explicit modularity standards for active implementation files and exception handling.
- Open-decision list for unresolved compatibility or publication questions.

## Risks / Tradeoffs

- Freezing too early may accidentally preserve interfaces that should be removed.
- Freezing too late keeps the repository in a contradictory state and makes docs harder to trust.
- Over-auditing internal details can slow the more valuable public and runtime cleanup work.
- Treating modularity as optional cleanup would leave the hardest-to-maintain runtime paths unbounded and make later hardening work slower and riskier.

## Exit Criteria

- The repository has a shared written statement of what agentyc is and is not.
- Core public and contributor-facing docs can point to the same source-of-truth modules.
- Legacy deletion targets are named explicitly enough to unblock Phase 3.
- Oversized active modules and their split seams are documented well enough to drive Phase 5 refactors.
- Later phases can treat the contract as intentionally scoped rather than inferred from stale material.

## Notes For Docs / Public Communication

- Do not publish broad positioning changes yet; keep this phase focused on internal contract clarity.
- Capture wording that can be reused in Phase 2 so the public rewrite does not drift from the audit.
