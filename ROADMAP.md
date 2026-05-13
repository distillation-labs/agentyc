## Finish-Tonight MCP Hardening Plan (Completed)

### Outcome and metric

**User-visible outcome**

Ship a browser automation MCP that coding agents can trust for real work: fast startup, compact state, deterministic extraction where possible, explicit failures when not, and materially better ergonomics than Playwright MCP / agent-browser for coding-agent loops.

**Primary release metric**

The runtime is "ready for the main MCP push" only if all of these hold at once:

- `./scripts/lint.sh` passes
- `./scripts/test.sh` passes
- `uv run python scripts/benchmark_mcp_runtime.py` passes
- the benchmark pack preserves:
  - `avg_auto_recall == 1.0`
  - `avg_deterministic_recall == 1.0`
  - `avg_structured_recall == 1.0`
- zero-LLM deterministic coverage expands beyond the current 5 direct-extraction cases
- cold import / hot-path latency do not regress while reliability coverage expands

**Secondary guardrails**

- no autonomous agent loop
- no hidden fallback automation
- no silent retries that hide broken browser behavior
- no reduction in explicit MCP tool contracts
- no degradation of compact-state behavior or stable refs

### Constraints

- The product stays MCP-first and deterministic.
- Browser control remains direct and CDP-first.
- New behavior must land with tests, benchmark coverage, and rollback-safe routing.
- Use local fixtures and deterministic assertions where possible; do not depend on flaky live web pages.
- Favor smaller enforceable slices over speculative rewrites.
- Preserve explicit failure modes and clear metadata for agents.

### Current status

All six phases below are complete. The runtime now has a harsher reliability gate, broader zero-LLM extraction coverage, explicit action failures, route metadata for extraction results, faster MCP import/session startup, a validated context-efficiency follow-up, a completed hotspot autoresearch breakthrough loop, a broader real-world hardening pack for rich text, ARIA comboboxes, and iframe workflows, and a green repo-wide ship gate.

### Current baseline

The repository is already in the right product shape:

- MCP-only refactor is complete.
- Autonomous agent, standalone CLI, sandbox, and skills surfaces are removed from the product path.
- `browser_get_state` supports `mode=auto|full|min|focus`, stable refs like `e123`, `effective_mode`, optional repeated-label `context`, and `since_hash`.
- `browser_extract_content` already supports deterministic direct routes for:
  - links
  - tables
  - list / checklist items
  - form fields
  - key-value panels / status summaries
  - search-result / link-collection queries
- `browser_extract_content` now also supports `output_schema` over MCP and can return validated structured JSON for compatible low-ambiguity table/list/form/key-value/link-collection queries without an LLM round-trip.
- `browser_extract_content` appends `<extraction_metadata>` with route, `llm_used`, deterministic/structured flags, and partial markers.
- free-text summary/action extraction now uses a size-gated compact action-summary context on large pages, keeps full markdown on small pages, and reports `context_mode` in extraction metadata.
- MCP actions now use explicit error codes such as `target_disabled`, `stale_ref`, `navigation_timeout`, and `postcondition_failed`, plus mild live-ref recovery for DOM drift.
- `browser_get_state` now suppresses wrapper labels around visible text-entry controls so coding agents see the actual input/select/textarea target instead of a misleading duplicate ref.
- the latest hotspot loop also kept seven DOM/runtime wins:
  - a DOM-ready navigation fallback that removes lifecycle-timeout tail spikes on already-loaded local pages
  - a DOM tree session-id hoist that resolves the CDP session once per tree build instead of once per node
  - removal of an unused ready-state probe before DOM snapshot capture
  - a staged AX path that avoids the multi-frame AX walk on pages with no iframes
  - a cached no-iframe hint with stale-hint correction for repeated state calls
  - removal of a dead iframe scroll probe that never fed DOM serialization
  - a short-lived JS listener probe cache for repeated calls on the same target

**Latest validated baseline**

- `./scripts/lint.sh` passes
- `./scripts/test.sh` passes with **300 passed**
- `uv run python scripts/benchmark_mcp_runtime.py` passes with:
  - `fixture_count = 20`
  - `auto_compacted_pages = ['dense-catalog', 'long-docs']`
  - `avg_auto_payload_reduction_pct = 8.0`
  - `avg_min_payload_reduction_pct = 8.1`
  - `avg_auto_recall = 1.0`
  - `avg_min_recall = 1.0`
  - `deterministic_case_count = 7`
  - `avg_deterministic_recall = 1.0`
  - `zero_llm_deterministic_cases = 7`
  - `structured_case_count = 5`
  - `avg_structured_recall = 1.0`
  - `action_case_count = 12`
  - `avg_action_success = 1.0`
  - `failing_action_cases = []`
  - `llm_prompt_chars_total = 10,413`
  - `llm_prompt_tokens_total = 2,600`
  - latest validation run:
    - `import_ms = 226.6`
    - `session_init_ms = 962.1`
  - latest 3-run medians:
    - `import_ms = 214.3`
    - `session_init_ms = 964.6`
  - latest action-latency loop medians:
    - `confirm-dialog action ms = 1772.0 -> 1671.7`
    - `debounced-autocomplete action ms = 1138.0 -> 1128.2`
    - `shadow-dom-workspace action ms = 836.0 -> 837.0`
  - latest clean wall baseline from the prior hotspot loop remains:
    - `wall_s = 10.27`
  - latest hotspot latency breakthrough:
    - `dense-catalog auto-state ms = 35.7 -> 31.7`
    - `long-docs auto-state ms = 20.0 -> 17.1`
  - latest real-world hardening breakthrough:
    - rich-text `contenteditable` editing is now benchmarked and green
    - ARIA combobox expansion and option selection are now benchmarked and green
    - same-origin iframe form workflows are now benchmarked and green through naive text-based ref lookup
  - latest action reliability latency breakthrough:
    - bounded dialog-blocked mouse release and post-click refocus waits now prevent long confirm-popup stalls while preserving action reliability guardrails at `1.0`

### Facts

- The architecture is now aligned with the intended product.
- The repo already has benchmark and regression infrastructure for state shaping and extraction routing.
- The biggest wins so far came from:
  - removing product-scope clutter
  - compacting state for large pages
  - deterministic extraction on low-ambiguity page structures
  - lazy-loading MCP surfaces and extraction-only runtime dependencies off the session-start hot path
  - size-gated compact action-summary context plus compressed extraction prompt scaffolding, which cut aggregate LLM prompt cost by about two-thirds on the current benchmark without losing benchmark recall or action success
  - a navigation DOM-ready fallback that removed intermittent full-suite timeout tails and stabilized the wall median around 10.30s
  - a compounded DOM hot-path cleanup that reduced dense-catalog auto-state latency by about 11% and long-docs by about 15% without hurting suite-level guardrails
  - selective suppression of wrapper labels for visible text-entry controls, which removes misleading duplicate refs and lets coding agents hit the real input in iframe/form workflows without extra tag disambiguation
  - real-world ops fixture expansion that now enforces shadow-DOM forms, debounced autocomplete selection, and confirm-dialog decisions in the benchmark and CI gates
  - bounded click-path waits around dialog-blocked mouse release and post-click refocus, which reduced confirm-dialog action latency by ~6% in repeated benchmark medians with no guardrail regressions
- The benchmark pack now includes harsh dynamic UI scenarios:
  - delayed enablement
  - modal / overlay interference
  - repeated labels
  - DOM drift
  - tab switching
  - accessibility-heavy controls
  - rich-text editing via `contenteditable`
  - ARIA combobox / listbox selection
  - same-origin iframe form interaction
  - shadow DOM form interaction
  - debounced autocomplete option selection
  - confirm-dialog decision flow
- The current remaining gaps are not release blockers; they are future breadth improvements:
  - wider site and layout diversity in the fixture corpus
  - more deterministic routes only where exact scoring can stay reliable
  - deeper optional postconditions where the latency tradeoff is worth it
  - if full-suite wall time becomes the primary target again, the next leverage point is action-path latency rather than further DOM-state micro-optimizations, because several benchmark scenarios intentionally include fixed sleeps to model delayed UI behavior

### Inferences

- The core hardening work is complete; the next gains come from widening corpus coverage, not changing the product architecture.
- The fastest path to future improvements remains benchmark-first: add a fixture, prove recall or reliability, then widen behavior.
- Trust now comes primarily from explicit contracts and eval coverage, not from adding more hidden intelligence.

### Hypotheses

- Additional deterministic surfaces are only worth shipping when they can preserve exact benchmarked recall.
- Stronger optional postconditions may help on a subset of risky actions, but should stay gated by latency budget.
- Broader fixture diversity is now the highest-leverage next investment.

## Implementation plan

### Phase 1 - Lock the release gate and harsh eval pack

**Goal**

Upgrade the current benchmark suite from "good curated pack" to "release gate for real agent pain."

**Checklist**

- [x] Add a harsh reliability fixture pack for common coding-agent pain:
  - delayed enablement / loading state transitions
  - modal / overlay interference
  - tab changes and navigation postconditions
  - repeated labels / ambiguous controls
  - DOM drift after state capture
  - accessibility-heavy composite controls
- [x] Extend benchmark reporting so reliability scenarios produce structural pass/fail results, not only payload and recall summaries.
- [x] Split benchmark output into:
  - compact-state metrics
  - deterministic extraction metrics
  - action reliability metrics
- [x] Make the benchmark pack the non-negotiable gate for future routing / latency changes.

**Exit criteria**

- New harsh fixtures run locally and deterministically.
- We have a clear before/after baseline for action success and regression detection.

### Phase 2 - Expand deterministic zero-LLM extraction

**Goal**

Increase the number of page structures that coding agents can query without an LLM round-trip.

**Checklist**

- [x] Add deterministic extraction support for low-ambiguity structures beyond tables/lists/forms:
  - key-value panels / settings summaries
  - card grids / result cards via deterministic link collections
  - search result lists
  - nav/menu collections and pagination controls where structure is obvious
- [x] Add schema-aware projection for the new low-ambiguity structures where it is safe.
- [x] Keep routing conservative: if ambiguity is high, fall back instead of pretending to know.
- [x] Add benchmark fixtures and regression tests for each new deterministic surface.
- [x] Raise the zero-LLM deterministic case count above the current 5-case baseline.

**Exit criteria**

- New direct-query routes are benchmarked.
- Structured deterministic output remains validated before returning.
- No regression in existing extraction recall metrics.

### Phase 3 - Harden interaction reliability

**Goal**

Make click/type/navigation behavior predictable enough that coding agents stop needing workarounds.

**Checklist**

- [x] Add explicit preflight checks for click/type targets:
  - target exists
  - target is attached
  - target is visible enough
  - target is enabled / actionable
- [x] Add explicit postcondition checks where appropriate:
  - click caused expected DOM / tab / navigation change
  - type updated the intended field
  - tab switch actually selected the requested target
- [x] Improve ref robustness against mild DOM drift without introducing hidden agent-like recovery.
- [x] Normalize error taxonomy so failures are obvious:
  - stale ref
  - blocked by overlay
  - target disabled
  - navigation timeout
  - postcondition failed
- [x] Add targeted regressions for the new failure modes.

**Exit criteria**

- Interaction failures become explicit and classifiable.
- Reliability fixtures show fewer false-success outcomes.

### Phase 4 - Tighten latency and context cost

**Goal**

Reduce perceived slowness without making the surface more magical or opaque.

**Checklist**

- [x] Profile cold import and session-start hot paths again.
- [x] Trim or defer imports that are not required on the main MCP request path.
- [x] Reduce redundant serialization / shaping work in state and extraction flows.
- [x] Re-check screenshot, HTML, and extract paths for avoidable duplicated work.
- [x] Benchmark before/after and keep the benchmark pack as the gate.

**Exit criteria**

- Import / hot-path numbers improve or at minimum do not regress.
- No benchmark recall or reliability regression in exchange for speed.

### Phase 5 - Add observability and agent-facing contracts

**Goal**

Make the MCP legible enough that agents can debug failures instead of hallucinating around them.

**Checklist**

- [x] Expose or standardize metadata that matters to agents:
  - route used
  - llm used / not used
  - effective state mode
  - partial / truncated flags
  - failure code / reason
- [x] Ensure deterministic and fallback paths return stable response shapes.
- [x] Improve README / MCP usage guidance for compact-state and structured extraction workflows.
- [x] Turn recurring taste into repo artifacts:
  - tests
  - benchmark assertions
  - docs
  - explicit workflow rules

**Exit criteria**

- Agents get enough signal to recover cleanly from failures.
- Human debugging is faster because the runtime tells the truth about what path it took.

### Phase 6 - Final ship gate

**Goal**

Close the night with a repo state that is benchmarked, documented, and safe to keep building on.

**Checklist**

- [x] Run full validation:
  - `./scripts/lint.sh`
  - `./scripts/test.sh`
  - `uv run python scripts/benchmark_mcp_runtime.py`
- [x] Re-check package / entrypoint sanity if any startup path changed materially.
- [x] Sync docs, plan, and benchmark baselines.
- [x] Capture final remaining gaps honestly so the next push starts from facts, not vibes.

**Exit criteria**

- Full validation is green.
- The repo has a current benchmark-backed story for what is done and what still is not.

## Harness and eval plan

**Baseline commands**

- `./scripts/lint.sh`
- `./scripts/test.sh`
- `uv run python scripts/benchmark_mcp_runtime.py`

**New evaluation layers to add**

1. **Reliability fixtures**
   - deterministic local fixtures that simulate dynamic UI behavior and interaction traps
   - scored by exact structural outcomes, not prose

2. **Deterministic extraction expansion**
   - per-surface fixture + expected structured subset / exact field checks
   - no LLM required for success on low-ambiguity cases

3. **Latency comparisons**
   - import time
   - state capture latency
   - extraction latency
   - session-start / first tool call latency if startup code changes

4. **Fallback correctness**
   - explicit tests that ambiguous or incompatible schemas fall back cleanly instead of fabricating output

**Gate policy**

- No phase is considered complete without updated tests or benchmark cases.
- If a speed improvement hurts recall or reliability, the change does not ship as-is.
- If a deterministic route cannot be scored reliably, it is not ready to be trusted.

## Observability and guardrails

- Preserve explicit routing boundaries: deterministic first only where safe, then fallback.
- Avoid hidden "smart recovery" loops that make failures harder to reason about.
- Prefer machine-readable metadata over log-only explanations.
- Keep stable refs and compact-state invariants under regression test coverage.
- For any new deterministic extractor, require:
  - fixture coverage
  - regression tests
  - benchmark scoring
  - explicit fallback story

## Rollout and rollback

**Rollout strategy**

- Work phase by phase.
- Keep the current behavior as the baseline and only widen routing after the new path is benchmarked.
- Validate each slice before stacking the next one.

**Rollback strategy**

- If a new deterministic route lowers recall or produces wrong structure, route it back to fallback and keep the benchmark case.
- If reliability hardening causes latency spikes, keep the checks that catch false success and remove only the expensive parts.
- If import slimming causes packaging or runtime issues, prefer explicit targeted lazy imports over broad refactors.

## Open questions and tradeoffs

- How aggressive should ref recovery be before it becomes hidden agent behavior?
- Which postconditions are cheap enough to enable by default versus only on risky actions?
- How far should deterministic extraction go before ambiguity makes the route a liability?
- Is the next biggest trust win better error contracts or better action repair logic? My current read is: error contracts first, repair logic second.

## Remaining non-blockers

- Expand the fixture corpus across more real-world app layouts before widening deterministic routing further.
- Keep stronger postconditions selective so the latency budget stays honest.
- Add new deterministic routes only when they can be scored exactly and keep recall at 1.0.

## Finish order

1. Phase 1: release gate and harsh eval pack
2. Phase 2: deterministic surface expansion
3. Phase 3: interaction reliability hardening
4. Phase 4: latency and context tightening
5. Phase 5: observability and agent-facing contracts
6. Phase 6: final ship gate
