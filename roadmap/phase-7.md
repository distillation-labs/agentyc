# Phase 7: Token And Context Efficiency

## Goal

Reduce the token and context cost of using agentyc from coding agents while preserving deterministic usefulness, inspectability, and action success.

## Why This Phase Exists Now

Efficiency work is most valuable once the runtime contract is stable and measurable. Earlier phases establish what must remain true; this phase improves how cheaply that value is delivered. It also directly supports the product goal of fast, reliable, token-efficient browser automation over MCP.

## Repo-Specific Context

- Primary efficiency levers live in `agentyc/mcp/state.py`, `agentyc/tools/extraction/router.py`, `agentyc/tools/service.py`, and `agentyc/mcp/server.py`.
- Deterministic extraction without an API key is strategically important and should be preferred whenever it can answer the request well.
- Current state compaction modes and compact-context behavior are already part of the contract and should be improved with measured discipline, not intuition.
- Efficiency gains should support coding-agent workflows, not reduce state so aggressively that the agent loses actionability.

## In Scope

- Tune state payload shape and compaction behavior.
- Improve extraction routing so deterministic paths are used more often when appropriate.
- Reduce unnecessary verbosity in action and state responses.
- Measure token/context improvements against the Phase 6 harness.
- Clarify the intended use of compact modes and deterministic extraction in docs.

## Out Of Scope

- Removing inspectability-critical state purely to lower payload size.
- Replacing deterministic extraction with opaque summarization.
- Unmeasured tuning driven only by intuition.
- Shared-browser visual UX work beyond the metadata needed in state responses.

## Dependencies / Prerequisites

- Phase 6 benchmark/eval harness and baseline metrics.
- Stable state and tool behavior from Phases 4 and 5.
- Clear product acceptance criteria for what state and extraction outputs must remain actionable.

## Key Modules / Files To Touch

- `agentyc/mcp/state.py`
- `agentyc/tools/extraction/router.py`
- `agentyc/tools/service.py`
- `agentyc/mcp/server.py`
- Public docs describing state and extraction behavior

## Implementation Workstreams

### State compaction tuning

Refine `auto`, `min`, `focus`, or related state modes so they preserve useful control context while dropping avoidable bulk.

### Deterministic-routing expansion

Increase the fraction of extraction requests that can be satisfied through deterministic, no-API-key routes for common structures and intents.

### Response-shape discipline

Trim redundant fields and over-verbose output in tool/service responses where the agent does not benefit from repetition.

### Evidence-driven iteration

Use Phase 6 baselines to keep only improvements that reduce context cost without materially hurting task completion or inspectability.

## Task Checklist

- [ ] Measure current token/context cost by state mode and extraction path.
- [ ] Identify the highest-volume payload contributors in MCP state and extraction outputs.
- [ ] Tune compact-state behavior while preserving the fields needed for common browser actions.
- [ ] Expand deterministic extraction routing where common requests still fall through unnecessarily.
- [ ] Remove redundant or low-value response fields from service/state outputs where safe.
- [ ] Re-run benchmarks after each material efficiency change.
- [ ] Compare efficiency gains against actionability and extraction usefulness, not payload size alone.
- [ ] Update docs to explain when to use compact modes and how deterministic extraction reduces context cost.
- [ ] Record any intentionally retained verbosity that protects inspectability or recovery.

## Validation / Verification Checklist

- [ ] Payload-size reductions are measured, not assumed.
- [ ] Core browser actions still succeed with the slimmer state outputs.
- [ ] Deterministic extraction coverage improves or remains stable for the targeted request classes.
- [ ] Compact modes do not hide information required for debugging or recovery in common workflows.
- [ ] Public docs accurately describe the tradeoffs of each relevant mode.

## Deliverables / Artifacts

- Leaner state and extraction outputs.
- Comparative benchmark results showing context-cost improvements.
- Updated docs for compact modes, extraction defaults, and efficiency behavior.
- A retained-verbosity rationale list for fields that remain intentionally expensive.

## Risks / Tradeoffs

- Over-compaction can reduce action success or make failures harder to debug.
- Query-intent routing can misclassify ambiguous extraction requests.
- Efficiency wins on one corpus may not generalize unless the benchmark set remains representative.

## Exit Criteria

- Common coding-agent workflows use materially less context than the baseline.
- Deterministic extraction remains the default no-API-key path for the targeted cases.
- Efficiency gains do not materially degrade actionability or inspectability.

## Notes For Docs / Public Communication

- Public messaging should frame efficiency as measured reduction in context cost, not as vague “smarter summarization.”
- Explain clearly that deterministic extraction is both an accuracy and token-efficiency strategy.
