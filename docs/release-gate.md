# Release Gate

Direct binary release runs are blocked unless both the legacy cargo gate and the
fail-closed direct-product gate pass. The direct gate is implemented in
`.github/workflows/workflow.yml` as `direct-release-gate`, which
`publish-binaries` depends on. MCP compatibility has an independent planned
`mcp-compatibility-gate` with its own versioned report and artifact; MCP
adapter/package publication depends on that gate, but direct binary publication
does not silently claim MCP compatibility-release evidence.
The legacy `release-gate` job remains a compatibility baseline. The planned gate
policy does not enforce the full production matrix through that legacy job by
itself; `direct-release-gate` owns the direct-product policy.

## What Is Gated

The `direct-release-gate` job runs, in order:

1. `cargo fmt --all -- --check` — formatting must be clean.
2. `cargo clippy --workspace --all-targets -- -D warnings` — zero lint warnings.
3. `cargo build --release -p agentyc --locked` — the binary must build.
4. `cargo test --workspace --release --locked` — the full test suite must pass.

A real Chrome may be installed for the legacy headless integration lane, but
that installation is not existing-Chrome evidence. Headed existing-Chrome,
extension/native-host, and managed-distribution lanes are separate required
release or manual gates and must provision a display/profile according to the
supported matrix. A test marked `#[ignore]` or skipped cannot satisfy a required
gate; it belongs only in an explicit optional/manual lane with an owner and
artifact record. The direct gate never launches, downloads, or attaches to a
browser while validating artifacts.

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

## Phase 7 direct evidence contract

The direct gate defaults to live-required mode:

```bash
python3 scripts/run_release_gate.py \
  --require-live \
  --artifact-dir artifacts/p7-release-gate
```

`--mode offline` is a deterministic schema and hook smoke check only. It emits
`status: offline_passed` and `release_eligible: false`; it can never close a
release. A missing source, an offline source in live mode, a skipped/ignored
scenario, a missing metric, or a redaction/envelope mismatch is a blocker. The
workflow uploads the report even when the gate fails so the blocker is
reviewable.

Every source artifact is a JSON envelope with `schema_version`, `phase`,
`kind`, `build_tuple`, UTC timestamp, command provenance, and central
`redaction_status`. The persisted form must be stable after the common
redaction function. Raw browser IDs, debugger URLs, absolute paths, secrets,
page bodies, and unbounded errors are not release evidence. The gate validates
rather than reconstructs live claims.

The benchmark release-gate schema has four required sections: `resource`,
`token`, `context`, and `reliability`. Each metric has `value`, `status`, and an
explicit `ceiling` or `minimum`. Offline metrics use
`status: not_measured_offline` and null values. Live metrics must use
`status: measured`, `evidence_mode: live`, and satisfy their declared limits.
The context section includes zero clean scans, delta ratios, actionable and
equivalent coverage, and truncation accounting. Reliability includes stale
references, unknown outcomes, event/reconnect lag, human-tab responsiveness,
cross-space and stale-agent mutations, blind replays, silent unknown success,
and secret leaks.

The gate generates and validates seeded replay, chaos, and soak artifacts. Each
hook requires at least 100 attempted and valid repetitions, zero missing,
error, or divergent repetitions, and a no-replay assertion. Chaos must account
for every declared fault; soak must report resource slopes and space
isolation. These deterministic hooks are necessary safety checks, not a
substitute for live browser, benchmark, or installation evidence.

### Existing Chrome

`run_existing_chrome.py --headed --harness <descriptor>` accepts only a schema-2
enrollment descriptor. The descriptor must explicitly enroll the profile, host,
and extension; identify an already-running browser; set launch, download, and
CDP use to false; and prove user-tab and focus safety. Raw IDs, paths, and
debugger endpoints are rejected. The runner never attaches, launches, downloads,
or installs anything. Descriptor-only output is non-green. A live result must be
supplied independently with all ten required scenarios marked `live_passed`.

### Installation, update, and rollback

The installation artifact must prove, in one redacted lifecycle record,
`install: installed`, `update: passed`, `uninstall: passed`,
`downgrade: passed`, and `rollback: rolled_back`. It must also prove that new
mutations were paused, pages and user tabs were retained, Chrome was not
terminated, no global close was used, and incompatible ledger state was
refused. `kill_switch.status` must be `armed_and_verified` with `armed: true`
and `verified: true`. The local installation drill only proves its exact test
Native Messaging registration unless a separately captured live lifecycle
record is supplied; it never fabricates the other phases.

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
both `release-gate` and `direct-release-gate` succeed.
