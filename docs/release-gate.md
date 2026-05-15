# Release Gate

Agentyc publish runs are blocked unless the release gate and modularity guard both pass.

## What Is Gated

- `scripts/benchmark_mcp_runtime.py --release-gate` evaluates the dogfood benchmark output against explicit thresholds.
- `scripts/check_release_guards.py` enforces a package file-size guard so very large Python modules fail before publish.
- The file-size guard also records a watchlist for active modules above the preferred modularity target.
- `--open-issue` remains opt-in. The benchmark gate can fail without opening GitHub issues.

## Benchmark Gate

The benchmark report includes a `release_gate` section when `--release-gate` is enabled.

Example:

```bash
uv run python scripts/benchmark_mcp_runtime.py \
  --preset dogfood \
  --release-gate \
  --fail-on-regression \
  --max-import-ms 2500 \
  --max-session-init-ms 12000 \
  --min-avg-auto-payload-reduction-pct 8.0 \
  --min-avg-auto-recall 0.99 \
  --min-avg-min-recall 0.99 \
  --min-avg-deterministic-recall 0.99 \
  --min-avg-structured-recall 0.99 \
  --min-avg-action-success 1.0 \
  --min-collaboration-required-check-pass-rate 1.0
```

The payload-reduction threshold should be treated as a regression floor, not an aspirational Phase 7 target. The current dogfood benchmark baseline in this repository reports `avg_auto_payload_reduction_pct=8.3`, so the publish gate uses `8.0` until a higher measured floor is demonstrated and checked in.

The gate fails when either of these conditions is true:

- any fixture regression is detected
- any configured threshold is unmet

Thresholds are evaluated from the generated benchmark summary and from top-level timings such as `import_ms` and `session_init_ms`.

## Dogfood Wrapper

`scripts/dogfood.sh` forwards environment-driven gate settings to the benchmark script.

Supported environment variables:

- `DOGFOOD_RELEASE_GATE=1`
- `DOGFOOD_FAIL_ON_REGRESSION=1`
- `DOGFOOD_MAX_IMPORT_MS`
- `DOGFOOD_MAX_SESSION_INIT_MS`
- `DOGFOOD_MIN_AVG_AUTO_PAYLOAD_REDUCTION_PCT`
- `DOGFOOD_MIN_AVG_AUTO_RECALL`
- `DOGFOOD_MIN_AVG_MIN_RECALL`
- `DOGFOOD_MIN_AVG_DETERMINISTIC_RECALL`
- `DOGFOOD_MIN_AVG_STRUCTURED_RECALL`
- `DOGFOOD_MIN_AVG_ACTION_SUCCESS`
- `DOGFOOD_MIN_COLLABORATION_REQUIRED_CHECK_PASS_RATE`
- `DOGFOOD_ARTIFACT_DIR`
- `DOGFOOD_OPEN_ISSUES=1` and the existing issue-related variables when issue creation is explicitly desired

Example:

```bash
DOGFOOD_RELEASE_GATE=1 \
DOGFOOD_FAIL_ON_REGRESSION=1 \
DOGFOOD_MIN_AVG_ACTION_SUCCESS=1.0 \
./scripts/dogfood.sh
```

## Modularity And File-Size Guard

The release workflow also runs:

```bash
uv run python scripts/check_release_guards.py --artifact-file .release-guard.json
```

Current behavior:

- scans `agentyc/**/*.py`
- records a watchlist for files above `800` lines
- fails if any single Python file exceeds `1000` lines

This matches the roadmap policy more closely: most active files should stay under roughly `700-800` lines, while files above `1000` lines are treated as release blockers.

The generated JSON artifact includes:

- `watch_lines`
- `max_lines`
- `watchlist_count`
- `watchlist`
- `violations`

This lets CI distinguish between modules that need continued refactor pressure and modules that are too large to ship.

## Checked-In Evidence

- `roadmap/phase-6-shared-browser-baseline.json` captures the shared-browser benchmark baseline, including `avg_auto_payload_reduction_pct=8.3` and `collaboration_required_check_pass_rate=1.0`.
- `roadmap/phase-9-modularity-guard.json` captures the current watchlist and confirms there are no Python files above the `1000`-line hard limit.
- `roadmap/phase-9-release-gate/report.json` can be regenerated with the command above to capture the current release-gate benchmark output.

## CI Publish Flow

`.github/workflows/workflow.yml` now has a dedicated `release-gates` job. `publish` depends on that job, so PyPI publishing is blocked until both checks pass.
