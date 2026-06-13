# Release Gate

Release runs are blocked unless the cargo-based release gate passes. The gate is
implemented in `.github/workflows/workflow.yml` as the `release-gate` job, which
the `publish-binaries` job depends on.

## What Is Gated

The `release-gate` job runs, in order:

1. `cargo fmt --all -- --check` — formatting must be clean.
2. `cargo clippy --workspace --all-targets -- -D warnings` — zero lint warnings.
3. `cargo build --release -p agentyc --locked` — the binary must build.
4. `cargo test --workspace --release --locked` — the full test suite must pass.

A real Chrome is installed (via `browser-actions/setup-chrome`) so the
browser-automation integration tests run against an actual browser. Tests marked
`#[ignore]` (live network or a visible display) are not part of the gate and are
run manually with `cargo test -- --ignored`.

## Performance Regression Gate

`tests/benchmark.rs` is a normal integration test that doubles as a performance
gate. It measures cold-start, `tools/list` latency, per-call MCP overhead, and
sustained throughput over stdio, then asserts regression ceilings:

| Metric | Assertion |
|--------|-----------|
| Cold-start median | `< 500 ms` |
| MCP overhead p50 | `< 10 ms` |
| Sustained throughput | `> 100 calls/sec` |

Run it directly to print the full report:

```bash
AGENTYC_HEADLESS=1 cargo test -p agentyc-tests --test benchmark -- --nocapture
```

Because it is part of `cargo test --workspace`, a performance regression fails
the release gate automatically.

## Soak / Stress Coverage

`tests/e2e_suite.rs` ports the original soak suite. Loop counts default low for
CI but scale with `AGENTYC_TEST_SCALE`:

```bash
# Reproduce a heavy (~10k operation) soak run locally.
AGENTYC_HEADLESS=1 AGENTYC_TEST_SCALE=25 \
  cargo test -p agentyc-tests --test e2e_suite -- --nocapture
```

## Publish Flow

`publish-binaries` builds release binaries for the supported targets
(`x86_64`/`aarch64` macOS, `x86_64` Linux, `x86_64` Windows), packages them as
`.tar.gz` / `.zip`, and attaches them to the GitHub release. It only runs after
`release-gate` succeeds.
