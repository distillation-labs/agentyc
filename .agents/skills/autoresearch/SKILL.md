---
name: autoresearch
description: >
  Use for autonomous, metric-driven experiment loops that modify code, rerun benchmarks after
  each change, keep only real wins, and continue until a breakthrough target is reached or the
  human stops the run. Trigger when the user asks to run autoresearch, benchmark and improve a
  metric, iterate autonomously on performance or quality, keep trying until a target is met, or
  run controlled experiments without pausing after each step. Do not use for knowledge questions,
  targeted bug fixes, code review, or direct implementation work that does not require a repeated
  experiment loop.
when_to_use: >
  Especially useful for retrieval quality, token efficiency, indexing speed, workflow automation,
  and any task where Contextro already has a benchmark or eval harness that can be rerun after
  each experiment.
metadata:
  author: contextro
  version: "4.1.0"
  category: workflow-automation
  tags: [research, optimization, benchmarking, experimentation, autonomous]
license: Proprietary
---

# Autoresearch

Run a disciplined experiment loop. Measure first, change one thing at a time, and keep only
results that beat the noise floor and preserve guardrails.

## Use This Skill To Produce

- a baseline grounded in the repo's real benchmark outputs
- a concrete breakthrough target
- a ranked hypothesis backlog
- one-variable experiments with commit-level attribution
- keep/discard decisions based on measured results
- a durable lab notebook with results and insights

## Contextro Defaults

Primary benchmark surfaces:

- `python scripts/benchmark_retrieval_quality.py --path src --query-limit 20`
- `python scripts/benchmark_chunk_profiles.py --path src --query-limit 20`
- `python scripts/benchmark_token_efficiency.py`
- `python scripts/benchmark_disclosure.py`
- `python scripts/benchmark_embeddings.py`
- `python scripts/bench_final.py`

Historical result sources:

- `scripts/results.tsv`
- `scripts/results_indexing_speed.tsv`
- `scripts/token_benchmark_results.json`
- `scripts/benchmark_results.json`

Read-only by default:

- benchmark scripts
- tests
- existing result logs

Primary modifiable surface:

- `src/contextro_mcp/`

## Method

### 1. Establish The Real Baseline First

Before changing code:

1. Read the existing result files.
2. Read the relevant benchmark script and target source files.
3. Verify the benchmark runs cleanly in the current environment.
4. Record the baseline and noise characteristics.

Do not start experiments until the baseline is reproducible.

### 2. Define The Metric And Breakthrough Target

Name:

- the primary metric
- the direction of improvement
- secondary guardrails
- the breakthrough target

If the user does not set a target, choose one that is genuinely ambitious, not a tiny local win.

### 3. Build A Hypothesis Backlog

Generate several candidate ideas before starting the loop.

Source hypotheses from:

- failure cases
- profiling or bottleneck evidence
- current repo architecture
- prior experiments in the logs
- outside research only when it transfers cleanly

Rank by expected impact, implementation cost, and reversibility.

### 4. Run One-Variable Experiments

Each experiment should test one main idea.

Prefer modular edits that isolate the hypothesis cleanly. Do not start with a broad restructure
unless earlier measurements show the existing shape is itself the bottleneck.

Required sequence:

1. pick the next hypothesis
2. implement the smallest change that tests it
3. commit the change
4. run the benchmark
5. compare against baseline and guardrails
6. keep or discard
7. log the result and the insight

Do not bundle multiple variables into one experiment unless you are intentionally running a later
compound experiment.

### 5. Use Noise Discipline

Do not treat a single plausible run as truth.

- For noisy metrics, rerun and compare the median against the noise floor.
- For deterministic metrics, a single reproducible improvement may be enough.
- Below-noise deltas are not wins.

### 6. Keep Only Real Wins

Keep a change only when:

- the primary metric improves enough to matter
- tests and lint stay green
- memory, latency, or other guardrails remain acceptable
- benchmark integrity is preserved

Discard or revert when:

- the metric regresses
- the gain is within noise
- tests fail and cannot be fixed quickly
- the benchmark or eval harness becomes invalid

### 7. Reassess Regularly

Every few experiments, step back and ask:

- what have we learned
- which direction is actually working
- whether we are optimizing the right bottleneck
- whether a different angle has higher leverage

If several experiments fail in a row, change angle rather than thrashing.

### 8. Compound Only After Isolated Wins

After several strong individual wins, combine them deliberately.

Call out whether the interaction is:

- additive
- superadditive
- conflicting

## Decision Rules

- Keep improvements that beat the noise floor and preserve guardrails.
- Keep simplifications when they do not hurt the primary metric.
- Revert regressions immediately.
- Revert crashes after limited recovery attempts.
- Do not stop after a local win if the breakthrough target is still unmet.
- Stop when the breakthrough target is met or the user interrupts.

## Safety Rules

- Never modify benchmark or eval scripts to improve the score.
- Never modify test fixtures to make the result look better.
- Never delete tests to keep an experiment.
- Always keep a clean revert path.
- Do not install new dependencies without approval.
- Preserve benchmark comparability across the whole run.

## Output Format

Return work in this order:

1. `Metric and breakthrough target`
2. `Baseline and guardrails`
3. `Hypothesis backlog`
4. `Current experiment`
5. `Measured result`
6. `Keep or discard decision`
7. `Insight logged`
8. `Next experiment`

## Anti-Patterns

- starting experiments before the baseline is verified
- changing multiple variables at once by default
- using a giant cleanup rewrite as the first experiment
- accepting sub-noise-floor deltas as wins
- stopping to ask permission after every experiment
- re-running already failed ideas without new evidence
- optimizing the score by changing the measuring stick
- leaving the branch broken after a failed run

## Handoff Rule

- use `breakthrough-researcher` when the solution space is still unclear
- use `applied-ai-engineer` when the best next step is hardening, rollout, or observability

## References

- `references/experiment-patterns.md`
- `references/eval-rubric.md`
