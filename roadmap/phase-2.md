# Phase 2: Docs Sync And Public Contract Rewrite

## Goal

Rewrite public docs and package-facing metadata so agentyc is described consistently as a pure MCP-first browser automation runtime for coding agents, grounded in the current code.

## Why This Phase Exists Now

The repo already has a narrowed direction, but the public contract remains stale. Shipping deletions or runtime changes without first correcting docs would continue to mislead users about scope, supported entrypoints, and extraction behavior. This phase turns the Phase 1 contract freeze into a public-facing, technically accurate narrative.

## Repo-Specific Context

- Known stale public files include `README.md`, `docs/overview.md`, `docs/features.md`, `docs/architecture.md`, `docs/api.md`, `docs/configuration.md`, `docs/tech-stack.md`, `pyproject.toml`, `docker/README.md`, and some README files under `agentyc/`.
- Public docs need to reflect the active source-of-truth modules instead of historical architectures.
- Deterministic extraction without an API key should be documented as the default core path where applicable.
- Public language must stop implying cloud/agent/sync-era product layers are still first-class.

## In Scope

- Rewrite top-level product positioning around MCP-first browser automation.
- Update architecture, API, configuration, and feature docs to match current code paths.
- Update package metadata and Docker docs to match current install and runtime expectations.
- Remove or rewrite stale README files under `agentyc/` that still advertise non-goal layers.
- Clarify shared-browser workflow language without over-promising Phase 8 UX work.

## Out Of Scope

- Deleting large code subtrees.
- Adding brand-new runtime features solely for documentation symmetry.
- Publishing benchmark claims that have not yet been measured.
- Documenting optional future extension-based collaboration mechanisms as core behavior.

## Dependencies / Prerequisites

- Phase 1 contract baseline completed.
- Clear mapping from public claims to source-of-truth modules.
- Decision on whether any legacy behavior remains supported temporarily and how it should be described.

## Key Modules / Files To Touch

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
- Any release-facing doc stubs already present in the repository

## Implementation Workstreams

### Product narrative rewrite

Replace broad or outdated product framing with a narrow description of agentyc as an MCP-first browser runtime for coding agents, centered on local/shared Chrome control and deterministic extraction.

### Contract documentation sync

Document CLI entrypoints, state payload behavior, extraction routing, and configuration using the real module surfaces as authority. Remove unsupported examples, stale commands, and old architecture diagrams or descriptions.

### Packaging and install surface cleanup

Update package metadata, install descriptions, and container documentation so distribution artifacts tell the same story as the repo landing pages.

### Internal/public README cleanup

Remove or rewrite README files under `agentyc/` that expose old cloud or sync-era mental models to contributors or users.

## Task Checklist

- [ ] Rewrite `README.md` to define agentyc as pure MCP browser automation for coding agents.
- [ ] Update `docs/overview.md` to reflect current scope, runtime shape, and non-goals.
- [ ] Update `docs/features.md` to emphasize deterministic browser control, extraction, and inspectability.
- [ ] Update `docs/architecture.md` to point to the real server, state, tool-service, and browser-session modules.
- [ ] Update `docs/api.md` so documented MCP/state behavior matches current tool and state surfaces.
- [ ] Update `docs/configuration.md` to match `agentyc/config.py` and remove dead knobs.
- [ ] Update `docs/tech-stack.md` to describe the current browser/CDP/MCP stack rather than removed layers.
- [ ] Update `pyproject.toml` metadata fields that still imply outdated scope.
- [ ] Update `docker/README.md` to match the current runtime and container expectations.
- [ ] Audit and rewrite or delete stale README files under `agentyc/` that remain public or contributor-visible.
- [ ] Remove claims that suggest API keys are required for core extraction when deterministic routes exist.
- [ ] Remove claims that suggest cloud browser, sync workflows, or agent-platform layers are active first-class product surfaces.

## Validation / Verification Checklist

- [ ] Each updated doc can be traced to one or more source-of-truth modules.
- [ ] CLI examples match `agentyc/mcp/cli.py`.
- [ ] API and state descriptions match `agentyc/mcp/server.py` and `agentyc/mcp/state.py`.
- [ ] Extraction descriptions match `agentyc/tools/extraction/router.py` and `agentyc/tools/service.py`.
- [ ] Configuration docs match `agentyc/config.py`.
- [ ] No updated public file still presents cloud/agent/sync-era behavior as the default story.
- [ ] No updated public file claims benchmarked properties that have not yet been proven in later phases.

## Deliverables / Artifacts

- Updated public documentation set aligned with the narrowed product.
- Updated package and container-facing metadata.
- Cleaned internal/public README surface under `agentyc/`.
- A public contract that users can trust while deeper code deletion and runtime simplification continue.

## Risks / Tradeoffs

- Docs can get ahead of code if legacy paths still exist temporarily.
- Narrowing the public story may force clearer release-note handling for removed or deprecated behavior.
- Removing stale docs aggressively may expose gaps where the active code still lacks a crisp explanation.

## Exit Criteria

- Public docs describe only currently intended product surfaces.
- Install, CLI, API, and config examples are consistent with the current code.
- The repo landing experience no longer requires a reader to understand two competing product stories.

## Notes For Docs / Public Communication

- This phase is itself the public communication phase; changes should read as implementation-aligned technical documentation, not marketing copy.
- Call out removals or behavior narrowing plainly where users may notice the difference.
