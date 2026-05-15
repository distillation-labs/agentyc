# Phase 6: Benchmark And Eval Harness

## Goal

Build a repeatable benchmark and eval harness that proves agentyc's claims about deterministic extraction, reliability, bounded runtime behavior, and inspectable failure modes.

## Why This Phase Exists Now

By this point the active architecture should be narrow enough that benchmarks measure the product that will actually ship, not a moving target. Without this phase, claims about speed, reliability, and no-API-key extraction remain qualitative. This phase creates the evidence needed for publication and for later optimization work.

## Repo-Specific Context

- The most important behavior to measure lives in `agentyc/mcp/server.py`, `agentyc/mcp/state.py`, `agentyc/tools/extraction/router.py`, and `agentyc/tools/service.py`.
- Deterministic extraction without an API key is a top-level product promise and needs explicit measurement.
- Browser actions should fail explicitly rather than hang, so reliability metrics should include timeout, retry, and failure-mode coverage, not just happy-path latency.
- Shared-browser flows should be represented in the eval set where they materially affect state or target ownership behavior.

## In Scope

- Build repeatable benchmarks and evals for navigation, actions, state retrieval, and deterministic extraction.
- Measure latency, reliability, timeout behavior, compact-state effectiveness, and output usefulness.
- Track extraction accuracy on common high-value structures such as links, lists, tables, forms, and key-value content.
- Establish regression thresholds for publication and future optimization phases.

## Out Of Scope

- Broad performance tuning unrelated to measured bottlenecks.
- Marketing benchmark comparisons against unrelated products unless rigorously justified.
- Extension-based collaboration UX validation.
- Rewriting large runtime subsystems merely to make benchmark numbers look better.

## Dependencies / Prerequisites

- Phases 3 through 5 complete enough that the measured runtime is stable.
- A representative corpus of pages or fixtures that matches coding-agent browsing tasks.
- Agreement on what constitutes success for extraction accuracy, latency, and failure behavior.

## Key Modules / Files To Touch

- `agentyc/mcp/server.py`
- `agentyc/mcp/state.py`
- `agentyc/tools/extraction/router.py`
- `agentyc/tools/service.py`
- Test/eval directories aligned with current CI layout
- Public proof sections in existing docs once results are ready

## Implementation Workstreams

### Eval corpus definition

Create a representative task set covering page navigation, focused action-taking, deterministic extraction, and shared-browser state reasoning.

### Harness implementation

Build runners and assertions that capture latency, reliability, timeout/failure modes, and output quality in a repeatable way.

### Baseline generation

Record initial numbers for extraction success, runtime bounds, state payload size, and retry/failure behavior.

### Regression gating

Turn the most critical behaviors into gates that can catch contract or reliability regressions before release.

## Task Checklist

- [ ] Define benchmark tasks that reflect real coding-agent browser workflows.
- [ ] Include deterministic extraction cases for links, lists, tables, forms, and key-value structures.
- [ ] Include action and navigation cases that exercise timeouts and explicit failure modes.
- [ ] Include state retrieval cases across compact modes where relevant.
- [ ] Build a repeatable harness that captures latency, success/failure, retries, and output shape.
- [ ] Record baseline results for deterministic extraction without API keys.
- [ ] Record baseline results for action reliability and bounded runtime behavior.
- [ ] Add regression thresholds or comparison logic for critical metrics.
- [ ] Document how to rerun the harness before release.
- [ ] Identify weak spots in the corpus that need expansion to avoid false confidence.

## Validation / Verification Checklist

- [ ] The eval set includes both happy-path and failure-path scenarios.
- [ ] Results are reproducible enough to compare runs meaningfully.
- [ ] Deterministic extraction claims are backed by measured cases rather than anecdotes.
- [ ] Runtime metrics include timeout/failure behavior, not just average latency.
- [ ] The harness can detect regressions in output shape or state semantics that matter to agents.

## Deliverables / Artifacts

- Benchmark and eval harness.
- Baseline metrics for extraction, latency, reliability, and state behavior.
- Regression gates tied to high-value product claims.
- Rerun instructions for contributor and release workflows.

## Risks / Tradeoffs

- A weak corpus can overstate confidence.
- Highly dynamic external pages can introduce noise unless fixtures or controlled targets are used wisely.
- Too many metrics can obscure the few release-critical ones.

## Exit Criteria

- Core product claims around determinism, reliability, and bounded behavior are measurable.
- Release work can reference repeatable evidence instead of qualitative assertions.
- Later optimization work has a stable baseline to improve against.

## Notes For Docs / Public Communication

- Public docs should publish only metrics with stable methodology and enough context to avoid overclaiming.
- Internal docs should preserve raw methodology and known limitations, not just summary numbers.
