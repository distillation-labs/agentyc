---
name: breakthrough-autoresearch
description: >
  Researches hard improvement problems and runs disciplined autonomous experiment loops. Use when
  the user asks to research deeply, find the highest-ROI ideas, compare mechanisms from other
  systems, run an autoresearch loop, benchmark and improve a metric, keep trying until a target is
  met, or ruthlessly experiment until a bottleneck is disproven. Do not use for trivial bug fixes,
  docs-only work, or production hardening after the winning direction is already chosen.
when_to_use: >
  Especially useful for self-improving-agent workflows in agentyc: root-cause investigation,
  breakthrough hunting, ranked hypothesis backlogs, and repeated benchmark-driven experiments over
  browser reliability, retrieval quality, routing, observability, or long-horizon agent behavior.
metadata:
  version: "1.0.0"
  category: research-and-optimization
  tags: [research, autoresearch, experimentation, benchmarking, hypotheses, ablation, optimization, self-improvement]
license: Proprietary
---

# Breakthrough Autoresearch

Turn fuzzy improvement goals into falsifiable experiments, then run the loop ruthlessly until the
target is met or the bottleneck is disproven.

## Use Cases

- "Research why this agent workflow regressed and rank the highest-ROI fixes."
- "Benchmark and improve this metric until we hit the target."
- "Keep trying different approaches until we either break through or learn the bottleneck is elsewhere."

## What This Skill Owns

- repository-grounded root-cause investigation
- fact, inference, and hypothesis separation
- mechanism-level comparison against outside systems or papers
- ranked hypothesis backlogs
- one-variable experiment loops with keep/discard decisions
- persistence until the metric target is met, disproven, or blocked

## Boundaries

- Use this skill when the work is still exploratory or optimization-heavy.
- Do not use it for simple bug fixes, docs work, or final production hardening after the winning direction is known.
- Once a promising direction is chosen, compose with `applied-ai-engineer` to add harnesses, observability, rollout, and rollback.

## Agentyc Defaults

Canonical benchmark surfaces:

- `AGENTYC_HEADLESS=1 cargo test -p agentyc-tests --test benchmark -- --nocapture`
  — cold-start, tools/list latency, per-call MCP overhead, and sustained throughput with regression ceilings
- `AGENTYC_HEADLESS=1 AGENTYC_TEST_SCALE=25 cargo test -p agentyc-tests --test e2e_suite`
  — scaled end-to-end soak over the deterministic tool surface

Guardrail surfaces:

- `cargo test --workspace` — unit + integration tests (browser tests need Chrome)
- `cargo fmt --all -- --check` — formatting
- `cargo clippy --workspace --all-targets -- -D warnings` — lints

When the question is "browser excellence," name the exact metric you are moving: task completion,
false-positive completion rate, average or p95 tool latency, token/context footprint, recall,
extraction quality, interruption recovery, or long-horizon reliability.

## Method

### 1. Define The Exact Question

State:

- the user-visible outcome
- the primary metric
- the guardrails
- the breakthrough target
- the current baseline or the plan to obtain it

If the question is too broad, narrow it before touching code.

### Required Experiment Card

Before editing, write down:

- benchmark surface and exact command
- current baseline and noise floor
- breakthrough target and keep/discard threshold
- guardrails that must stay green
- one main variable under test
- held-out tasks or failure clusters
- rollback or revert path

### 2. Ground In Repository Reality

Before bringing in outside ideas:

- inspect the current architecture and likely bottlenecks
- find the relevant benchmark or eval surface
- confirm the benchmark still runs in the current environment
- record the baseline and noise characteristics

Do not start experiments until the baseline is reproducible.

### 3. Separate Facts, Inferences, And Hypotheses

- Facts: directly supported by repo evidence or source material.
- Inferences: conclusions supported by multiple facts.
- Hypotheses: proposed changes that still need to be tested.

Never present a hypothesis as established truth.

### 4. Build A Ranked Hypothesis Backlog

Generate several candidate directions before starting the loop.

Rank by:

- expected impact
- reversibility
- implementation cost
- discriminating power of the next experiment

Include rejected or deprioritized alternatives when they are plausible competitors.

### 5. Run One-Variable Experiments

Required sequence:

1. pick the next hypothesis
2. implement the smallest change that tests it
3. run the benchmark or eval
4. compare against baseline and guardrails
5. keep or discard
6. log the result and the insight

Do not bundle multiple main variables into one experiment unless you are intentionally testing an interaction.

### 6. Use Noise Discipline

- For noisy metrics, rerun and compare the median against the noise floor.
- Below-noise deltas are not wins.
- Deterministic improvements still need guardrail checks.

### 7. Reassess Direction Regularly

After a cluster of wins or failures, step back and ask:

- what did we learn
- which direction is actually working
- whether the bottleneck moved
- whether the next highest-ROI test changed

If several experiments fail in a row, change angle instead of thrashing.

### 8. Stop For A Reason

Valid stop conditions:

- the breakthrough target is met
- the current bottleneck is disproven
- an external blocker is real and named
- the user interrupts the run

## Research And Comparison Rules

- Start from repository reality, not vibes.
- Translate outside systems into mechanisms, not brand-name cargo cults.
- Prefer negative evidence over hype.
- End with measurable experiments, not generic advice.

## Safety Rules

- Never modify benchmark or eval scripts to improve the score.
- Never weaken tests or fixtures to make an experiment look better.
- Always keep a clean revert path.
- Do not treat a single promising run as enough on noisy tasks.
- Never hide regressions behind bigger prompts, extra retries, larger models, or broader context
  windows without measuring the cost.
- Never count a partial step like a click, request, or DOM mutation as success if the user-visible
  task is still incomplete.
- Never ship a single-site or one-benchmark hack as a general win without proving transfer on
  held-out tasks.

## Examples

Example 1: Root-cause investigation plus experiment loop
User says: "Research why browser action success fell and keep trying fixes until we get back above 95%."
Actions:
- establish the baseline and failure clusters
- rank the most plausible hypotheses
- run one-variable experiments in priority order
- keep only changes that beat the noise floor and preserve guardrails
Result: a ranked explanation plus a durable sequence of validated improvements

Example 2: Outside-mechanism transfer
User says: "Compare how leading agent systems handle long-horizon reliability and test what transfers here."
Actions:
- identify mechanisms, not marketing claims
- map each mechanism onto agentyc constraints
- choose the cheapest discriminating experiments first
Result: adopt, adapt, or avoid guidance tied to measurable tests

## Troubleshooting

- If the benchmark is unstable, stop and repair the measurement surface before continuing.
- If every idea is still speculative, produce a clearer fact base before coding.
- If repeated experiments fail, reassess the bottleneck rather than repeating the same angle.
- If a direction starts looking shippable, hand off to `applied-ai-engineer` for hardening.

## Output Format

Return results in this order:

1. `Research question and target`
2. `Benchmark surface`
3. `Current baseline`
4. `Facts`
5. `Inferences`
6. `Hypotheses`
7. `Ranked backlog`
8. `Experiment card`
9. `Current experiment`
10. `Measured result`
11. `Keep or discard decision`
12. `Next experiment or stop reason`
13. `Recommendation`

## Composition Rule

- use `applied-ai-engineer` once the winning direction needs productionization, observability, and rollout safety
- encode a discovered failure mode as a deterministic `cargo test` case under `tests/`
- use `dev-contextro-mcp` when the bottleneck starts with codebase discovery, impact tracing, or retrieval-budget work
- use `agentyc-browser-automation` when the experiment needs end-to-end browser-task evidence rather than subsystem analysis
- use `llm-provider-engineer` when the hypothesis is about model routing, token accounting, or structured output stability

## References

- `references/research-and-experiment-patterns.md`
- `references/eval-rubric.md`
- `evals/cases.yaml`
