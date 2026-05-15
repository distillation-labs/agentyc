# Phase 8: Collaborative Human+Agent Workspace UX

## Goal

Ship a practical collaborative browser workspace where humans and one or more agents can operate in parallel with clear ownership of tabs and windows.

## Why This Phase Exists Now

The collaborative shared-browser model is strategically important, but it depends on earlier work: the runtime must already be narrow, reliable, measurable, and explicit about target ownership. With those foundations in place, this phase can focus on the UX and state semantics that make parallel human+agent work actually usable instead of merely possible.

## Repo-Specific Context

- Shared-browser collaboration is strategic for agentyc, not an incidental debug mode.
- For parallel agents and subagents, the UI should make agent-owned tabs or windows obvious.
- Stock Chrome/CDP cannot reliably color individual tabs.
- Pure MCP/CDP options are best-effort title prefixing, in-page overlays or ribbons, ownership metadata, separate-window mode, focus behavior, and optional window positioning.
- Extension-based tab groups/colors are optional future work only and must not be a core dependency.
- The most relevant runtime files are `agentyc/browser/session.py`, `agentyc/browser/session_manager.py`, `agentyc/mcp/state.py`, `agentyc/mcp/server.py`, and `agentyc/mcp/cli.py`.

## In Scope

- Add explicit agent-owned and human-owned target semantics.
- Surface ownership metadata through MCP state.
- Implement best-effort title prefixing and lightweight in-page ownership ribbons or overlays where appropriate.
- Support separate-window mode as the strongest isolation option.
- Add focus or optional positioning behavior that helps humans and agents stay oriented.

## Out Of Scope

- Reliable per-tab color control in stock Chrome.
- Mandatory browser extensions for core collaboration behavior.
- Heavy visual UI frameworks or non-browser dashboards unless they are already part of the product.
- Autonomous multi-agent orchestration features unrelated to browser ownership clarity.

## Dependencies / Prerequisites

- Stable session and ownership foundations from Phase 5.
- Efficiency work from Phase 7 complete enough that state can carry ownership metadata without becoming too expensive.
- A clear decision on how ownership identifiers should be named and serialized.

## Key Modules / Files To Touch

- `agentyc/browser/session.py`
- `agentyc/browser/session_manager.py`
- `agentyc/mcp/state.py`
- `agentyc/mcp/server.py`
- `agentyc/mcp/cli.py`
- Public docs for shared-browser behavior and collaboration guidance

## Implementation Workstreams

### Ownership model

Represent human-owned and agent-owned targets explicitly in session management and state serialization. Make this deterministic enough for subagents and parallel work.

### Visible browser affordances

Implement best-effort title prefixing and lightweight in-page ribbons or overlays so humans can see ownership without depending only on state payloads.

### Isolation modes

Support separate-window mode as the clearest collaboration option and pair it with focus/positioning behaviors where useful.

### State and CLI surfacing

Expose ownership and collaboration controls through MCP state and CLI/shared-browser configuration in a way that matches the actual runtime behavior.

## Task Checklist

- [ ] Define an ownership metadata model for human-owned and agent-owned targets.
- [ ] Persist ownership metadata in the session/runtime layer.
- [ ] Expose ownership metadata through MCP state responses.
- [ ] Add best-effort title prefixing for agent-owned tabs where feasible.
- [ ] Add lightweight in-page overlays or ribbons for agent-owned pages.
- [ ] Add or refine separate-window mode for stronger human/agent isolation.
- [ ] Implement focus behavior that helps agents return to owned targets without unnecessarily stealing human surfaces.
- [ ] Evaluate optional window-positioning behavior for clearer parallel use.
- [ ] Ensure parallel agents/subagents can be distinguished consistently.
- [ ] Update CLI and docs to explain collaboration modes and their tradeoffs.

## Validation / Verification Checklist

- [ ] In shared-browser mode, a human can tell which tabs or windows belong to agents.
- [ ] State payloads expose ownership clearly enough for agents to reason about it programmatically.
- [ ] The collaboration model works in stock Chrome without extension dependence.
- [ ] Title prefixing and overlays are best-effort but not dangerously misleading.
- [ ] Separate-window mode provides the clearest isolation option.
- [ ] Parallel agents/subagents do not end up with ambiguous ownership markers.

## Deliverables / Artifacts

- Ownership metadata in runtime and MCP state.
- Browser-visible ownership affordances.
- Separate-window collaboration mode and related CLI/runtime controls.
- Updated docs for human+agent shared-browser operation.

## Risks / Tradeoffs

- Stock Chrome limitations mean some ownership affordances will remain best-effort.
- Overlays and title prefixes can be intrusive if applied too aggressively.
- Rich ownership metadata can increase state size if not designed carefully.

## Exit Criteria

- Shared-browser collaboration is visibly usable for humans and multiple agents.
- Ownership is clear in both runtime state and browser-visible affordances.
- The design works with pure MCP/CDP techniques and does not depend on extensions.

## Notes For Docs / Public Communication

- Public docs should be explicit about Chrome limitations: no reliable per-tab coloring through stock CDP.
- Explain the recommended UX order clearly: separate-window mode first, then overlays/metadata, then title prefixing, with extension-based color/group work only as optional future exploration.
