# Applied AI Research Notes

Useful traits for real applied AI engineering work:

- baseline-first thinking
- eval-first shipping discipline
- routing and context efficiency
- observability and recovery for long-running work
- iterative refinement with rollback
- realistic benchmark design
- data quality and label discipline
- prompt, tool, and system co-design
- enforcing behavior through repo artifacts

The strongest systems usually combine:

- short, high-signal instructions
- representative evals
- deterministic checks where possible
- model-graded checks where necessary
- explicit rollout/rollback criteria

## Research Discipline

- define the exact research question before collecting ideas
- ground recommendations in repository evidence before outside comparison
- label findings as facts, inferences, or hypotheses
- include rejected or deprioritized alternatives
- translate outside systems into mechanisms, not brand-name cargo cults

## Experiment Discipline

- verify the benchmark or eval still runs before changing code
- record the current baseline and expected noise characteristics
- rank a hypothesis backlog before starting the loop
- change one variable at a time by default
- keep only changes that beat the noise floor and preserve guardrails
- stop when the target is met, the bottleneck is disproven, or an external blocker is real
