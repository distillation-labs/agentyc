# Breakthrough Autoresearch Patterns

This reference combines the research and experiment-loop patterns that matter most for agentyc.

## Research Patterns

- Start from repository evidence before external comparison.
- Separate facts, inferences, and hypotheses.
- Compare mechanisms, not brands.
- Include negative evidence and rejected alternatives.
- End with measurable experiments, not generic advice.

## Experiment Patterns

- Verify the benchmark still runs before changing code.
- Record the baseline and likely noise characteristics.
- Build a ranked backlog before starting the loop.
- Test one main variable at a time.
- Keep only wins that beat the noise floor and preserve guardrails.
- Change angle after repeated failures.

## Agentyc-Specific Surfaces

- `cargo test --workspace`
- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets -- -D warnings`
- browser reliability and DOM extraction benchmarks
- MCP runtime and stdio latency benchmarks

## What To Avoid

- broad rewrites as the first experiment
- optimizing the score by weakening the measuring stick
- treating a plausible single run as proof on noisy metrics
- re-running dead-end ideas without new evidence
- stopping at interesting ideas instead of measurable outcomes
