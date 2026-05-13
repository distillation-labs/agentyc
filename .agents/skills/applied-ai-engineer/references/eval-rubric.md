# Applied AI Engineer Eval Rubric

## The skill passes when it:

- triggers for productionization, harness, eval, observability, rollout, reliability, or routing work
- does not trigger for pure research or trivial code edits
- defines outcome, metric, constraints, baseline, facts, inferences, and hypotheses
- proposes a concrete harness or evaluation method before trusting a change
- includes observability, guardrails, and rollback thinking
- prefers enforceable repository artifacts over prompt-only guidance
- knows when prompt tuning is not enough and data or system design must change
- can talk about evaluation methods in a grounded way: code-based, model-graded, or human-graded

## The skill fails when it:

- ships changes without evals or benchmarks
- optimizes a single metric while ignoring regressions
- turns into a pure literature-review skill
- recommends big rewrites before smaller benchmarked slices
- omits rollout, rollback, or failure detection
- treats model routing, context efficiency, and observability as afterthoughts
- lacks a clear rubric for subjective or model-graded evals
