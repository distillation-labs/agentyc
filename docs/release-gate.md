# Release Gate

Direct binary release runs are blocked unless the direct-product release gate
passes. The planned gate is implemented in `.github/workflows/workflow.yml` as
`direct-release-gate`, which `publish-binaries` depends on. MCP compatibility has
an independent planned `mcp-compatibility-gate` with its own versioned report and
artifact; MCP adapter/package publication depends on that gate, but direct
binary publication does not silently claim MCP compatibility-release evidence.
Until the Phase 7-T10 and Phase 8-T9 workflow tasks land, the current
`release-gate` job remains the legacy cargo gate and does not enforce the full
production matrix.

## What Is Gated

The planned `direct-release-gate` job runs, in order:

1. `cargo fmt --all -- --check` — formatting must be clean.
2. `cargo clippy --workspace --all-targets -- -D warnings` — zero lint warnings.
3. `cargo build --release -p agentyc --locked` — the binary must build.
4. `cargo test --workspace --release --locked` — the full test suite must pass.

A real Chrome is installed (via `browser-actions/setup-chrome`) for the
headless integration lane. Headed existing-Chrome, extension/native-host, and
managed-distribution lanes are separate required release or manual gates and
must provision a display/profile according to the supported matrix. A test
marked `#[ignore]` or skipped cannot satisfy a required gate; it belongs only in
an explicit optional/manual lane with an owner and artifact record.

## Performance and context regression gate

`tests/benchmark.rs` remains a historical legacy-MCP transport baseline. It
must not be treated as the direct browser-task completion benchmark. Until
Phase 7-T10 rewires CI, the existing workflow still executes its legacy
assertions; this planning document does not claim that the current workflow
already enforces the new gates. Direct and host-backed MCP benchmarks report transport bytes, UTF-8 bytes, serialized
payload tokens, deployed model-context tokens, scan/queue/bridge/browser/
serialization timings, cache state, actionable-control coverage, stale-ref and
unknown rates, event lag, CPU/RSS, queue depth, and human-tab responsiveness.

Blocking tail metrics require at least 200 valid samples for p95 and 1,000 for
p99, with bootstrap 95% confidence intervals and raw samples. Thirty samples
are smoke-only. Every baseline manifest records commit, build mode, OS/CPU,
Chrome build, extension/host tuple, fixture/data hash, tokenizer, concurrency,
cache state, sample count, and statistical method. A release fails when a
blocking metric exceeds its signed absolute ceiling or regression budget with a
non-overlapping confidence interval. Threshold changes require a dated,
owned decision record.

The performance suite compares legacy MCP one-shot, persistent MCP, the local
protocol, SDK sequential calls, SDK batch calls, full/min/delta snapshots,
clean/dirty cache, and event-driven waits against polling. Clean snapshots must
perform zero DOM/AX scans; deltas must preserve equivalent actionable coverage
or fall back to full/min.

Run the legacy baseline directly:

```bash
AGENTYC_HEADLESS=1 cargo test -p agentyc-tests --test benchmark -- --nocapture
```

Run the direct/host-backed benchmark after Phase 0 creates it:

```bash
python3 scripts/run_direct_benchmark.py --warmups 10 --min-samples-p95 200 --min-samples-p99 1000 --artifact-dir artifacts/release-performance
```

## Required production test lanes

The release gate requires the test pyramid in
`docs/exec-plans/active/agentyc-browser-task-spaces/research/production-test-strategy.md`.
Direct rollout requires:

- pure unit/property and deterministic component tests;
- process, Native Messaging, CLI/SDK, and the frozen legacy MCP stdio/HTTP baseline;
- headed existing-Chrome workflows with disposable profiles and a local fixture server;
- load/saturation, multi-hour soak/leak, chaos/fault-injection, fuzz, install/update,
  and rollback evidence for the direct product's nightly/pre-release lanes.

MCP compatibility has a separate Phase 8 release gate. It additionally requires
wire lifecycle, HTTP/SSE/session, exact tool/schema manifests, stable error
mapping, concurrency/fairness, event replay/backpressure, cancellation,
reconnect/unknown outcomes, and every supported tool through the real
host/extension/headed-Chrome path. Direct rollout does not silently claim those
Phase 8 guarantees.

Every required lane fails on missing Chrome, skipped/ignored tests, swallowed
tool errors, leaked child processes, missing redacted artifacts, or unbounded
timeouts. Any authorization bypass, cross-space mutation, user-tab close,
stale-agent mutation after takeover, secret leak, blind replay, or silent
unknown-success is an automatic no-go for the applicable release.

## Soak / Stress Coverage

`tests/e2e_suite.rs` is a legacy stress baseline. It does not replace the
required load, soak, and chaos lanes. Loop counts default low for CI but scale
with `AGENTYC_TEST_SCALE`:

```bash
# Reproduce a heavy (~10k operation) legacy soak run locally.
AGENTYC_HEADLESS=1 AGENTYC_TEST_SCALE=25 \
  cargo test -p agentyc-tests --test e2e_suite -- --nocapture
```

The production lanes additionally require bounded load/saturation, multi-hour
leak/soak, and seeded fault-injection with replayable traces. Each reports
throughput, p50/p95/p99, queue/event lag, deadlines, CPU/RSS, file descriptors,
threads, tabs, ledger/artifact growth, reconnects, unknown outcomes, and
cross-space/user-tab safety.

## Publish Flow

`publish-binaries` builds release binaries for the supported targets
(`x86_64`/`aarch64` macOS, `x86_64` Linux, `x86_64` Windows), packages them as
`.tar.gz` / `.zip`, and attaches them to the GitHub release. It only runs after
`release-gate` succeeds.
