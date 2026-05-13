# Autoresearch Experiment Patterns

Autoresearch exists to run an autonomous but disciplined benchmark loop against real repository
surfaces.

## Core Loop

1. Read prior results.
2. Verify the benchmark still runs.
3. Set a breakthrough target.
4. Generate and rank hypotheses.
5. Run one-variable experiments.
6. Keep only measured wins.
7. Log the result and the insight.
8. Reassess after clusters of failures or wins.

## Contextro-Specific Surfaces

- Retrieval quality: `python scripts/benchmark_retrieval_quality.py --path src --query-limit 20`
- Chunking: `python scripts/benchmark_chunk_profiles.py --path src --query-limit 20`
- Token efficiency: `python scripts/benchmark_token_efficiency.py`
- Disclosure and compression: `python scripts/benchmark_disclosure.py`
- Embeddings: `python scripts/benchmark_embeddings.py`
- Full benchmark: `python scripts/bench_final.py`

## Keep Or Discard Rules

- Keep only if the delta is real and guardrails hold.
- Revert regressions immediately.
- Treat failing tests, broken lint, or invalid benchmark outputs as blockers.
- Do not treat benchmark-script edits as valid optimization work.

## Battle-Test Expectations

- Read existing result history before proposing the first experiment.
- Reuse winning directions before jumping to unrelated ideas.
- Change angle after repeated failures.
- Stop only on breakthrough target, explicit user interruption, or a true external blocker.
