# Phase 5: Runtime Hardening And Architecture Simplification

## Goal

Make the surviving runtime smaller, more reliable, and easier to reason about by simplifying session, profile, config, and MCP package surfaces around the active MCP/browser path.

## Why This Phase Exists Now

After legacy layers are removed and `CrashWatchdog` is attached, the repository is ready for targeted simplification without guessing which branches still matter. This phase turns the narrower architecture into a cleaner runtime: fewer hidden compatibility branches, clearer session ownership, and more explicit lifecycle behavior.

## Repo-Specific Context

- Main simplification targets include `agentyc/browser/session.py`, `agentyc/browser/profile.py`, `agentyc/config.py`, `agentyc/mcp/__init__.py`, and adjacent code in `agentyc/browser/session_manager.py`, `agentyc/mcp/state.py`, and `agentyc/tools/service.py`.
- The product is local/shared Chrome control over MCP, not a dual local/cloud runtime.
- Shared-browser operation is strategic, so simplification should preserve inspectability and ownership semantics rather than flatten them away.
- Explicit failure modes are preferred over hidden fallback behavior.
- Modularity is part of runtime hardening in this phase, not deferred style cleanup. Oversized files hide lifecycle coupling, make watchdog behavior harder to reason about, and increase regression risk during future feature work.
- Active implementation files should generally stay under 700-800 lines. Files above that threshold should be treated as refactor candidates, files above 1000 lines are priority targets, and exceptions require explicit written justification plus a tracked follow-up plan.
- Current priority refactor targets include `agentyc/browser/session.py` (~4045 lines), `agentyc/browser/watchdogs/default_action_watchdog.py` (~3633), `agentyc/mcp/server.py` (~2565), `agentyc/tools/service.py` (~2336), `agentyc/browser/watchdogs/downloads_watchdog.py` (~1381), `agentyc/dom/serializer/serializer.py` (~1380), `agentyc/tools/extraction/router.py` (~1329), `agentyc/dom/service.py` (~1268), `agentyc/browser/profile.py` (~1236), and `agentyc/dom/views.py` (~1044).
- Secondary watchlist modules are `agentyc/browser/session_manager.py` (~911), `agentyc/browser/watchdogs/dom_watchdog.py` (~867), and `agentyc/browser/watchdogs/har_recording_watchdog.py` (~779).
- Expected modular split patterns in this repo include views/models separation, service modules, validators, adapters, helpers/utilities, event wiring extraction, watchdog submodules, parser/formatter modules, and test fixture/helper modules.

## In Scope

- Simplify constructors, configs, and branching in the browser/session/profile/config path.
- Remove obsolete kwargs, config aliases, or fallback branches that only exist for retired product layers.
- Tighten startup, reconnect, and shutdown semantics.
- Clarify tab/session ownership metadata that downstream MCP state needs.
- Reduce package-export and import-surface ambiguity.
- Execute concrete modular refactors in oversized core files so the active runtime has enforceable, reusable domain boundaries.

## Out Of Scope

- Benchmark harness creation.
- Public publication packaging.
- Shared-browser visual UX features beyond the metadata/runtime support needed for later phases.
- Reintroducing compatibility code for deleted layers.

## Dependencies / Prerequisites

- Phase 3 deletions complete.
- Phase 4 watchdog integration complete or sufficiently stable.
- Identified simplification map from Phase 1.

## Key Modules / Files To Touch

- `agentyc/browser/session.py`
- `agentyc/browser/profile.py`
- `agentyc/browser/session_manager.py`
- `agentyc/config.py`
- `agentyc/mcp/__init__.py`
- `agentyc/mcp/state.py`
- `agentyc/tools/service.py`
- Tests covering startup, reconnect, teardown, and state behavior

## Implementation Workstreams

### Session lifecycle cleanup

Reduce hidden branches in startup, attach, reconnect, and shutdown so the main runtime path is explicit and bounded.

This work should split `agentyc/browser/session.py` along real domain seams instead of keeping lifecycle logic, target/session coordination, state assembly, event wiring, and helper code in one monolith.

### Config/profile surface reduction

Remove dead parameters, legacy aliases, and code paths that only exist to support deleted modes. Make the remaining configuration match actual runtime needs.

This includes pulling profile setup, validation, and adapter-style behavior into smaller modules where the current files blur concerns.

### Ownership and state clarity

Ensure session and target ownership metadata is structured clearly enough for later collaborative UX work and current state serialization.

When state or ownership logic is mixed into broad service files, split serializers, views, formatters, or metadata helpers into narrower modules.

### Package-surface cleanup

Trim ambiguous exports from `agentyc/mcp/__init__.py` and related modules so consumers are guided toward the intended entrypoints.

### Watchdog modularization

Break large watchdog implementations into reusable submodules for event wiring, policy/decision logic, parsing/formatting, and shared helpers so each watchdog is easier to test and reason about.

### Tooling and DOM service decomposition

Reduce monolithic service modules in tools and DOM paths by extracting routers, validators, parser/formatter logic, structured views/models, and reusable helpers that mirror the actual domain boundaries.

## Task Checklist

- [ ] Remove obsolete session constructor branches tied to deleted cloud/sync/actor layers.
- [ ] Simplify browser profile setup to match the surviving local/shared browser model.
- [ ] Remove dead or misleading config knobs and aliases from `agentyc/config.py`.
- [ ] Update config loading and defaults to make the active runtime path explicit.
- [ ] Clarify `BrowserSession` startup, reconnect, and shutdown control flow.
- [ ] Clarify `SessionManager` ownership and target/session coordination behavior.
- [ ] Ensure MCP state serialization exposes the metadata needed by the active runtime story.
- [ ] Remove ambiguous or stale exports from `agentyc/mcp/__init__.py`.
- [ ] Tighten timeout and cleanup behavior in `agentyc/tools/service.py` where runtime hardening depends on it.
- [ ] Split `agentyc/browser/session.py` into smaller domain-focused modules so lifecycle, event wiring, helpers, and state/ownership concerns are not concentrated in a single 4000+ line file.
- [ ] Split `agentyc/browser/watchdogs/default_action_watchdog.py` and `agentyc/browser/watchdogs/downloads_watchdog.py` into watchdog submodules with clearer boundaries between event subscriptions, runtime policy, parsing, and formatting.
- [ ] Refactor `agentyc/mcp/server.py` to separate transport/server setup, tool registration, request handling, and shared helpers.
- [ ] Refactor `agentyc/tools/service.py` and `agentyc/tools/extraction/router.py` to extract reusable services, validators, adapters, and parser/formatter modules instead of expanding the central service/router files.
- [ ] Refactor `agentyc/dom/service.py`, `agentyc/dom/views.py`, and `agentyc/dom/serializer/serializer.py` so DOM models/views, serialization, and helper logic are not bundled into oversized files.
- [ ] Refactor `agentyc/browser/profile.py` to isolate profile models, setup helpers, and validation/adapter logic.
- [ ] Review `agentyc/browser/session_manager.py`, `agentyc/browser/watchdogs/dom_watchdog.py`, and `agentyc/browser/watchdogs/har_recording_watchdog.py` against the 700-800 line guardrail and split them if they continue to grow.
- [ ] Remove obvious duplication that survives only because related logic currently lives in different oversized files.
- [ ] Add or update tests around extracted modules so modularization preserves behavior rather than relying on manual inspection.
- [ ] Record explicit justifications and follow-up plans for any active core file that remains above the target range after this phase.
- [ ] Update tests to cover the simplified runtime surface and explicit failure modes.

## Validation / Verification Checklist

- [ ] The runtime can be explained primarily through one local/shared browser path.
- [ ] Startup, reconnect, and teardown are bounded and testable.
- [ ] Removed config knobs are absent from docs and tests.
- [ ] Fewer branches exist solely for compatibility with retired architecture.
- [ ] State serialization still provides enough context for agent operation and later collaborative UX work.
- [ ] Failures are more explicit, not more opaque.
- [ ] Active core files are generally within the 700-800 line target, or are documented exceptions with explicit rationale and open refactor follow-up.
- [ ] No priority refactor target remains above 1000 lines without written justification approved as part of the phase output.
- [ ] Shared logic has moved into reusable domain modules instead of being copied across session, watchdog, DOM, and tool-service files.
- [ ] The new module boundaries make tests, ownership semantics, and runtime responsibilities easier to explain with less cross-file ambiguity.

## Deliverables / Artifacts

- Simplified runtime and configuration surface.
- Reduced import/export ambiguity.
- Updated tests for session lifecycle and failure behavior.
- Clearer module boundaries for later benchmarking and UX work.
- Modular refactor changelist for oversized files, including extracted modules and retained exceptions.
- File-size compliance report for active runtime modules, with line counts and justifications where needed.
- Named split seams and follow-up backlog for any deferred refactor work.

## Risks / Tradeoffs

- Removing undocumented compatibility can still surprise downstream users.
- Simplification often reveals previously hidden edge cases that now fail explicitly.
- Overcompressing ownership semantics could make later collaborative UX work harder.
- Large-file refactors can temporarily increase integration risk if extracted boundaries are guessed instead of grounded in real domain seams.
- Chasing numeric line-count targets without preserving inspectability and lifecycle clarity would be counterproductive; modularity is valuable only when it clarifies responsibility and reuse.

## Exit Criteria

- The surviving runtime is materially easier to explain and verify than before.
- Session lifecycle behavior is explicit, bounded, and covered by tests.
- Legacy compatibility code no longer dominates the core session/config path.
- The main oversized modules have been split materially enough that file-size guardrails are credible and enforceable.
- Any remaining oversized active file has explicit written justification plus an open refactor plan rather than silent acceptance.

## Notes For Docs / Public Communication

- Update contributor-facing architecture docs as this phase lands so internal explanations do not lag behind the code.
- External docs should emphasize clearer behavior, reduced scope, and stronger maintainability, while contributor docs should record the modular boundaries that now define the runtime.
