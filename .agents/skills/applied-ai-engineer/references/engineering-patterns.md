# Applied AI Engineering Patterns

This reference captures repeated patterns behind strong applied AI teams and production agent
systems.

## OpenAI

- Use evals as a first-class engineering artifact.
- Keep the repository as the system of record.
- Use short maps plus enforceable code, not giant instruction dumps.
- Make architectural choices legible and testable.
- Prefer baselines, comparisons, and regression control over intuition.

## Anthropic

- Establish success criteria before prompt tuning.
- Use the smallest high-signal context that still solves the task.
- Use progressive disclosure rather than flooding context.
- Make long-running work resumable with explicit artifacts.
- Prefer task-specific evaluations over generic “looks good” judgments.

## Google DeepMind

- Define a clean baseline before changing the system.
- Use ablations to identify what actually matters.
- Separate signal from confounders.
- Treat measurement quality as part of the research itself.

## Cursor

- Keep the loop close to the local codebase: retrieve, inspect, edit, verify.
- Reduce friction between research, implementation, and validation.
- Prefer precise context targeting over broad prompt stuffing.

## Windsurf

- Treat agent work as plan-plus-execution, not only chat.
- Keep visible intermediate state so long tasks can recover without losing intent.
- Make environment status and tool usage legible enough for iterative autonomous work.

## Mistral

- Efficient model usage depends on routing, compact context, and clean decomposition.
- Smaller passes with strong structure can beat one large opaque pass.
- System quality comes from orchestration and interfaces, not only raw model size.

## DeepSeek

- Long-horizon systems benefit from checkpointing and resumable trajectories.
- Stable prompt structure improves reuse and efficiency.
- Preserve useful state for tool-calling loops without replaying everything.

## Devin And Cognition

- Prefer realistic task environments over abstract unit-only evaluation.
- Use autonomous evaluator flows when deterministic checks are insufficient.
- Store external notes and environment state so long tasks can resume cleanly.

## NVIDIA

- Measure the entire pipeline: chunking, retrieval, reranking, shaping, latency, memory.
- The best architecture on paper is not the best architecture until it wins on the target corpus.

## What This Means For Agentyc

- Browser automation quality is measured by action reliability, not vibe. Use real browser tests with `pytest-httpserver` and representative page structures.
- New MCP tools should land with CI tests, deterministic extraction benchmarks, and a clear fallback story.
- Watchdog correctness, CDP session lifecycle, and network interception behavior should each have dedicated test coverage.
- Implementation patterns from other teams should be translated into repo-local harnesses,
  concrete browser tests, and efficient task routing rather than copied superficially.
