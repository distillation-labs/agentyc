# Phase 9: Publication, Cleanup Proof, And Release Gate

## Goal

Ship the narrowed product with proof that docs, code, packaging, cleanup, and measured behavior all align around the MCP-first browser automation story.

## Why This Phase Exists Now

The earlier phases produce a cleaner repository and stronger runtime, but release quality depends on demonstrating alignment rather than assuming it. This final phase turns the accumulated work into publication evidence: updated public files, proof that stale layers are gone, benchmark references, and a strict release gate that prevents backsliding into contradictory messaging.

## Repo-Specific Context

- Public files still need a final consistency pass across `README.md`, `docs/overview.md`, `docs/features.md`, `docs/architecture.md`, `docs/api.md`, `docs/configuration.md`, `docs/tech-stack.md`, `pyproject.toml`, `docker/README.md`, and stale internal/public README files under `agentyc/`.
- The narrowed product story is pure MCP-first browser automation for coding agents.
- Publication must prove that deleted cloud/agent/sync-era layers are gone from docs, packaging, and CI, not just absent from the source tree.
- Benchmark and eval output from earlier phases should be referenced carefully and factually.
- Publication also needs to prove that modular runtime hardening landed: active core files should be within the target size range or carry explicit justification with an open refactor plan.

## In Scope

- Final consistency pass across public docs and package-facing metadata.
- Cleanup proof that deleted paths are absent from the packaged and documented product surface.
- Release gate checklist covering contract, docs, runtime reliability, efficiency, and collaborative UX.
- Publication-ready release notes or changelog material that describe the narrowed product and removals.
- Final modularity gate covering file-size guardrails, oversized-file exceptions, and evidence that modular refactors were treated as product quality work rather than optional cleanup.

## Out Of Scope

- New feature work unrelated to release alignment.
- Reopening major architecture decisions already settled by earlier phases.
- Stretch-goal collaboration features that are not required for the release gate.
- Claims about benchmark superiority that exceed the measured evidence.

## Dependencies / Prerequisites

- Prior phases complete or sufficiently stable.
- Benchmark and eval artifacts from Phase 6 and Phase 7.
- Shared-browser collaboration work from Phase 8.
- Updated public docs from Phase 2, with subsequent cleanup changes folded in.

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
- Release notes or changelog locations used by the repository
- CI or packaging files involved in release validation

## Implementation Workstreams

### Final doc and metadata pass

Run a final consistency check so top-level docs, package metadata, Docker docs, and contributor-visible README files all tell the same story.

### Cleanup proof

Verify that deleted layers are absent from package manifests, CI, docs, and contributor guidance, not just removed from source directories.

### Release evidence assembly

Collect benchmark references, runtime-hardening evidence, and shared-browser UX notes into release-facing artifacts.

### Gate enforcement

Create an explicit pass/fail checklist that must be satisfied before publication.

This gate should include modularity/file-size compliance for active implementation files, with special attention to core runtime modules that previously exceeded the target range.

## Task Checklist

- [ ] Run a final consistency audit across all public docs and metadata files.
- [ ] Confirm package metadata and Docker docs match the released runtime story.
- [ ] Confirm deleted cloud/sync/actor/controller-era paths are absent from docs, CI, and packaging.
- [ ] Confirm benchmark and eval references are factual and traceable to real artifacts.
- [ ] Confirm deterministic no-API-key extraction is described accurately and within measured limits.
- [ ] Confirm shared-browser collaboration docs match shipped ownership affordances and known Chrome limitations.
- [ ] Confirm active core implementation files are generally within the 700-800 line target, with files above that range reviewed explicitly.
- [ ] Confirm no active priority refactor target remains above 1000 lines without written justification and an open tracked refactor plan.
- [ ] Confirm modular refactor outcomes are reflected in contributor-facing architecture docs or internal release evidence where file-size exceptions remain.
- [ ] Prepare release notes or changelog text describing the narrowed scope and cleanup work.
- [ ] Prepare an explicit release gate checklist covering contract, docs, deletion proof, runtime reliability, efficiency, and collaboration UX.
- [ ] Include modularity/file-size compliance as a pass/fail release-gate section rather than a best-effort note.
- [ ] Block publication if any release gate item fails or relies on unverified claims.

## Validation / Verification Checklist

- [ ] A new reader of the public repo understands agentyc as MCP-first browser automation for coding agents.
- [ ] No public file still leads readers toward removed cloud, sync, or agent-platform product stories.
- [ ] Release notes describe removals and scope narrowing clearly.
- [ ] Benchmark references match reproducible evidence.
- [ ] Shared-browser documentation matches the shipped UX and limitations.
- [ ] The release gate can fail the release if contradictions remain.
- [ ] The release gate can also fail the release if oversized active files lack explicit justification or a real refactor plan.

## Deliverables / Artifacts

- Final synchronized public documentation set.
- Release notes or changelog entry for the narrowed product release.
- Cleanup proof checklist and evidence bundle.
- Final release gate checklist with pass/fail status.
- Modularity compliance report covering active core files, exceptions, and linked refactor follow-up where applicable.

## Risks / Tradeoffs

- Publication pressure can encourage softening or skipping gate checks.
- Incomplete cleanup proof can let stale messaging survive in less-visible files.
- Overly polished release text can hide unresolved runtime issues if evidence discipline slips.
- A soft modularity gate would allow oversized core files to remain normalized, undermining long-term maintainability and future hardening work.

## Exit Criteria

- Public docs, package metadata, and release notes all match the cleaned codebase.
- Cleanup proof exists for deleted non-goal layers.
- Benchmarks, runtime behavior, and collaboration UX claims are backed by verifiable artifacts.
- The release gate is strict enough to catch contradictions before publication.
- Active core files are either within the target size range or explicitly justified with a tracked refactor plan that is visible in the release evidence.

## Notes For Docs / Public Communication

- This phase should produce the final outward-facing explanation of the product and should avoid introducing any unmeasured claims.
- Keep the message technical and factual: narrowed scope, explicit browser runtime, deterministic extraction, shared-browser collaboration, evidence-backed reliability, and maintainability guardrails that were enforced before release.
