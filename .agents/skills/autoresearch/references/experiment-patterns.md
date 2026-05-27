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

## Agentyc-Specific Surfaces

- Full test suite: `./scripts/test.sh`
- CI tests with real browser: `uv run pytest -vxs tests/ci`
- Linting and formatting: `./scripts/lint.sh`
- Type checking: `uv run pyright`
- Code quality: `uv run ruff check --fix`
- Browser tests under `tests/ci/browser/` validate real CDP behavior
- MCP tool tests under `tests/ci/` validate stdio integration

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
