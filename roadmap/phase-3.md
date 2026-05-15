# Phase 3: Delete Non-Goal Layers And Scope Reduction

## Goal

Remove legacy layers that conflict with the pure MCP browser automation direction and reduce the repository to one clear active product path.

## Why This Phase Exists Now

Once the contract is frozen and public docs are corrected, the remaining ambiguity is structural. As long as cloud, sync, actor, or duplicate-controller layers remain in-tree without strong justification, contributors and users will continue to infer unsupported product directions. This phase converts the narrowed roadmap into actual repository shape.

## Repo-Specific Context

- Likely removal targets include `agentyc/browser/cloud/**`, `agentyc/sync/**`, `agentyc/actor/**`, `agentyc/mcp/controller.py`, cloud browser tests, and likely `agentyc/mcp/client.py` unless outbound MCP composition remains strategic.
- Simplification targets include `agentyc/browser/session.py`, `agentyc/browser/profile.py`, `agentyc/config.py`, `agentyc/mcp/__init__.py`, and related imports.
- Docs/public files are already stale, so code deletion must happen together with cleanup of imports, tests, docs, and packaging references.

## In Scope

- Delete clearly out-of-scope modules and tests.
- Remove imports, exports, package references, and docs references to deleted paths.
- Reduce compatibility shims and dual-path branching that only exists to keep deleted layers alive.
- Decide whether `agentyc/mcp/client.py` is retained or removed.

## Out Of Scope

- Benchmark work.
- Shared-browser UX polish beyond preserving current behavior during deletion.
- Large new abstractions intended to replace deleted code one-for-one.
- Keeping legacy layers indefinitely under soft deprecation unless there is a concrete product need.

## Dependencies / Prerequisites

- Phase 1 deletion list and retention rationale.
- Phase 2 public docs updated so deletion does not surprise readers with contradictory top-level messaging.
- Confidence that deleted code is not part of the intended MCP-first runtime path.

## Key Modules / Files To Touch

- `agentyc/browser/cloud/**`
- `agentyc/sync/**`
- `agentyc/actor/**`
- `agentyc/mcp/controller.py`
- `agentyc/mcp/client.py` if not retained
- Cloud-browser tests and associated fixtures
- `agentyc/browser/session.py`
- `agentyc/browser/profile.py`
- `agentyc/config.py`
- `agentyc/mcp/__init__.py`
- Any packaging, docs, or CI files that still reference deleted paths

## Implementation Workstreams

### Legacy subtree deletion

Delete directories and modules that no longer belong to the product, together with their tests and docs references.

### Import and packaging cleanup

Remove transitive imports, package exports, entrypoints, optional dependencies, and CI jobs that assume deleted layers exist.

### Runtime-path reduction

Collapse branching in the surviving browser/session/config code where the only purpose was to support removed layers.

### Retained-exception justification

If any legacy-looking file survives, record the specific strategic reason it remains so it does not linger as accidental scope drift.

## Task Checklist

- [ ] Delete `agentyc/browser/cloud/**` if Phase 1 confirms it is outside scope.
- [ ] Delete `agentyc/sync/**` if Phase 1 confirms it is outside scope.
- [ ] Delete `agentyc/actor/**` if Phase 1 confirms it is outside scope.
- [ ] Delete `agentyc/mcp/controller.py` if it is superseded by the active server path.
- [ ] Delete cloud-browser tests and fixtures that validate non-goal behavior.
- [ ] Decide whether `agentyc/mcp/client.py` remains strategic; delete it if not.
- [ ] Remove imports, exports, and entrypoints that reference deleted paths.
- [ ] Remove stale optional dependencies or packaging metadata tied to deleted layers.
- [ ] Remove stale docs and README references that mention deleted layers.
- [ ] Simplify runtime/config branching that only existed to support removed code paths.
- [ ] Record migration notes or release notes for any intentionally removed compatibility path.

## Validation / Verification Checklist

- [ ] The repository root and package layout no longer present cloud, sync, actor, or duplicate-controller layers as active architecture.
- [ ] CI and tests no longer reference deleted non-goal paths.
- [ ] Import errors caused by deletions are resolved across the surviving package.
- [ ] Public docs and package metadata no longer mention deleted layers except in explicit migration or release notes.
- [ ] Any retained exception has written justification.

## Deliverables / Artifacts

- A reduced repository that reflects the MCP-first browser runtime directly.
- Deleted code, test, doc, and packaging references for non-goal layers.
- Simplified import and runtime structure for subsequent hardening work.
- Release-note material describing intentional removals.

## Risks / Tradeoffs

- Hidden dependencies can surface only after deletion.
- Some legacy code may still contain useful implementation ideas, but keeping it in-tree without active purpose carries long-term confusion cost.
- Deletion reduces ambiguity but can also expose unsupported downstream consumers more sharply.

## Exit Criteria

- The default repository shape clearly matches the narrowed product story.
- No deleted layer is still required by tests, packaging, or core runtime imports.
- Contributors can understand the active architecture without first learning the retired one.

## Notes For Docs / Public Communication

- Public release notes should describe removed layers plainly and tie the removals to the pure MCP browser automation direction.
- Internal contributor notes should be updated in the same pass so deleted paths are not accidentally reintroduced.
