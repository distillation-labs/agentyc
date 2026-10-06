# Release Gate

Direct binary release runs are blocked unless both the existing cargo gate and the
fail-closed direct-product gate pass. The direct gate is implemented in
`.github/workflows/workflow.yml` as `direct-release-gate`, which
`publish-binaries` depends on. Phase 8 defines a separate planned MCP release
gate; no MCP distribution release is eligible today. Direct binary publication
does not claim MCP compatibility-release evidence.
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

Older MCP/process benchmark records describe a removed direct-CDP MCP surface and are historical only; they are not current host-backed MCP performance evidence. This planning document does not claim that the current workflow enforces a production MCP benchmark gate. The direct-product benchmark reports transport bytes, UTF-8 bytes, serialized payload tokens, deployed model-context tokens, scan/queue/bridge/browser/serialization timings, cache state, actionable-control coverage, stale-ref and unknown rates, event lag, CPU/RSS, queue depth, and human-tab responsiveness. MCP requires a separate Phase 8 benchmark and release artifact; no live MCP benchmark has been run.

Blocking tail metrics require at least 200 valid samples for p95 and 1,000 for
p99, with bootstrap 95% confidence intervals and raw samples. Thirty samples
are smoke-only. Every baseline manifest records commit, build mode, OS/CPU,
Chrome build, extension/host tuple, fixture/data hash, tokenizer, concurrency,
cache state, sample count, and statistical method. A release fails when a
blocking metric exceeds its signed absolute ceiling or regression budget with a
non-overlapping confidence interval. Threshold changes require a dated,
owned decision record.

### Phase 5 performance evidence contract

P5-T8 owns the context/performance evidence artifact at
`artifacts/p5-performance/`. Generate the deterministic offline contract with:

```bash
python3 scripts/run_p5_performance.py \
  --mode offline \
  --smoke \
  --warmups 10 \
  --samples 30 \
  --artifact-dir artifacts/p5-performance
python3 scripts/check_p5_performance.py \
  --mode offline \
  --artifact-dir artifacts/p5-performance
```

The smoke lane is non-blocking and `release_eligible:false`. A blocking run
must use at least 10 warmups and at least 200 valid samples for p95 and 1,000
valid samples for p99 in every declared blocking cell. Its matrix includes cold
and warm temperatures, clean/dirty/resync cache states, full/min/focus/delta
snapshot modes, 1/2/4/8 spaces, nested and OOPIF fixture topology, mutation
bursts, event gaps, reconnects, and active unrelated-user-tab probes. Reports
include the tokenizer contract, warm action/wait latency, separate versus batch
round trips, native-artifact throughput, event lag, stale-reference and unknown
outcome rates, user-tab responsiveness, host/browser RSS, bootstrap 95% CIs,
raw samples, and a baseline manifest.

`--mode offline` is explicit deterministic fixture-model evidence. It may report
modeled context/latency dimensions, but user-tab, RSS, stale-reference, and
unknown-outcome observations remain `not_measured_offline`; it cannot close a
release. `--mode live` accepts only an external package whose provenance is
`production_path`, whose samples are marked `production_observation`, and whose
redaction status is applied. The checker rejects legacy/direct-benchmark,
guessed, byte-estimate, operator-claim, missing-cell, missing-sample, stale, or
unredacted artifacts. Live input must be supplied with `--live-input`; the P5
runner does not launch Chrome or attach to CDP.

The older `scripts/run_direct_benchmark.py` output is a Phase 0/direct-CDP
scaffold and is not P5-T8 evidence. It must not be promoted to a Phase 5 live
claim.

### P1-T7 threshold decision support

P1-T7 freezes provisional Phase 7 thresholds in the strict versioned record
[`artifacts/p1-t7-threshold-decision.json`](../artifacts/p1-t7-threshold-decision.json).
The record has a dated owner signoff (`p1-t7-2026-10-04-v1`, owner Japneet
Kalkat), a complete metric register, explicit provisional limits and regression
budgets, methods, owners, and evidence modes. Its production path is direct
CLI/SDK -> owner-only local IPC -> Rust host/broker -> Chrome Native Messaging
-> enrolled MV3 extension -> the existing user Chrome profile.

The record's exclusion list rejects disposable CDP, host-only/test-host,
acknowledgement-only, byte-estimate, source-inspection, and operator-claim-only
evidence. These sources cannot be promoted to live claims. The sample policy is
10 warmups, at least 200 valid p95 samples, at least 1,000 valid p99 samples,
bootstrap 95% confidence intervals, raw samples, complete sample accounting,
and predeclared exclusions; 30 samples are smoke-only.

Validate the P1-T7 decision contract offline with:

```bash
python3 scripts/check_release_gate.py \
  --phase 1 \
  --decision-record artifacts/p1-t7-threshold-decision.json \
  --mode offline
```

Offline validation passes the phase contract but always reports
`release_eligible=false`; every metric, safety counter, and declared chaos fault
has a null `not_measured_offline` value/status. Live mode additionally requires
production-path provenance, raw samples, bootstrap confidence intervals, valid
counts, central redaction, zero safety counters, and an accounted result for
every declared fault. Missing or unaccounted chaos faults fail closed. Live
claims from disposable CDP, host-only, acknowledgement, or byte-estimate
evidence fail closed. A threshold change requires a reason and a new decision id.

The direct-product production performance suite compares the local protocol, SDK sequential calls, SDK batch calls, full/min/delta snapshots, clean/dirty cache, and event-driven waits against polling. Clean snapshots must perform zero DOM/AX scans; deltas must preserve equivalent actionable coverage or fall back to full/min. MCP performance is a separate Phase 8 evidence item and has no completed live baseline.

The removed legacy MCP benchmark command is not a current validation command. Do not use historical output as current MCP performance or release evidence.

Run the direct benchmark in its explicit disposable-browser lane:

```bash
python3 scripts/run_direct_benchmark.py \
  --mode managed \
  --headless \
  --browser-executable /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --warmups 10 \
  --samples 1000 \
  --fixtures small-form,dense-admin-table,dynamic-feed,nested-frame \
  --cache-states cold,clean,dirty,resync \
  --spaces 1,2,4,8 \
  --artifact-dir artifacts/release-performance
```

This runner measures an owned disposable browser through CDP, not the production host/extension/CLI/SDK path. `serialized_tokens` and `model_context_tokens` are both `ceil(UTF-8 bytes / 4)` with `tokenizer_status: deterministic_byte_estimate_not_model_tokenizer`; they are not deployed-model counts. The batch comparison is three `Runtime.evaluate` requests versus one, reconnect is a CDP websocket reconnect, `host_rss_bytes` is the Python runner's RSS, and human-tab responsiveness is a page evaluation rather than headed human input/focus coexistence. Cache scans and delta payloads are benchmark-local. The 64-cell/64,000-sample generation passes the current artifact checker, not the stronger product requirements above. The `target` lane separately requires explicit loopback port, browser PID, action-target ID, and unrelated human-target ID; it must not be used to claim existing-user-Chrome coexistence.

## Required production test lanes

The release gate requires the test pyramid in
`docs/exec-plans/active/agentyc-browser-task-spaces/research/production-test-strategy.md`.
Direct rollout requires:

- pure unit/property and deterministic component tests;
- process, Native Messaging, and CLI/SDK integration for the direct product;
- headed existing-Chrome workflows with disposable profiles and a local fixture server;
- load/saturation, multi-hour soak/leak, chaos/fault-injection, fuzz, install/update,
  and rollback evidence for the direct product's nightly/pre-release lanes.

MCP compatibility has a separate Phase 8 release gate and is not distribution-ready. The shipped MCP surface is host-backed logical stdio only: offline lists 29 routes; the connected remote catalog declares 30, with 11 returning `capability_unavailable`. No MCP HTTP transport or removed `browser_*` tool surface is shipped. A limited existing-profile run passed MCP stdio, host/Native Messaging connection, and extension fence/rebind; snapshot/action reconciliation and the full workflow gate remain open. Phase 8 requires an explicit disposition for every unavailable route, stdio lifecycle/error/concurrency evidence, and supported-route workflows through the real host socket, Native Messaging bridge, extension, and headed Chrome. Direct rollout does not claim these MCP guarantees.

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

The P1-T7 checker is the earlier threshold-decision contract lane:
`check_release_gate.py --phase 1 --decision-record <record> --mode offline`.
It validates the dated owner/signoff, production path, exclusion list, sample
policy, complete metric register, null `not_measured_offline` values, and
`release_eligible=false` without claiming live behavior. It is separate from
the Phase 7 evidence runner; a threshold change without a new decision id is a
blocker.

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

`run_existing_chrome.py --headed --require-live` now uses bounded public host-backed direct-CLI calls against an already-running extension bridge, not descriptor-supplied live claims. `--cli` (or `AGENTYC_CLI`) selects an existing executable; the default is `target/debug/agentyc`. Optional `--state-dir` must match the operator-approved host binding. The runner never launches, downloads, installs, discovers CDP, or attaches to a browser. `--harness` is retained only as ignored legacy input; a schema-2 descriptor cannot produce live proof.

Preflight requires a ready connected extension bridge, `test_seam:false`, safe direct-path metadata, and the `action` capability. Calls use `agent-a`/`agent-b` principals and record logical space/page, cross-space rejection, takeover/stale-lease rejection, events, and finish/release receipts. CLI `page create` does not create Chrome tabs; those receipts are `host_observed`/`host_observed_not_browser`, not `live_passed`. `--operator-checkpoint` (or `AGENTYC_EXISTING_CHROME_OPERATOR_CHECKPOINT`) enables fixed-token acknowledgments followed by host status/events checks; acknowledgments are never browser observations. Only host restart can add a broker-epoch observation, not a full recovery pass. Current orchestration cannot emit complete ten-scenario live evidence: unavailable preflight is `live_required_unavailable`; partial execution is `live_observation_incomplete` or `operator_checkpoint_required`, exit 1. Safety counters stay null and `release_eligible:false`; independent profile enrollment, browser actions, user-control/restart proof, and measured safety evidence remain required.

The actual product route is the enrolled MV3 extension's `connectNative('com.agentyc.host')` -> Chrome-launched Rust host/broker -> owner-only local socket for agent clients -> extension debugger/tabs APIs. The disposable P0-T2 route instead uses `extension/probes/` and `com.agentyc.p0_probe`. The direct Native Messaging host-smoke script also uses the test host, not the production broker. The separate Rust `agentyc-existing-chrome-probe` uses the real local-socket protocol but only one client and logical pages, and may succeed with returned-space cleanup skipped; its path-bearing output is not the redacted ten-scenario artifact. Operator enrollment, permission/policy review, side-panel opening, takeover/return, disruptive restarts, focus/input checks, and final user-tab checks are separate checkpoints; a human acknowledgment or a host metadata smoke cannot substitute for scenario observations. See [Phase 0 probe/checkpoint audit](../research/phase-0-host-backed-probe.md).

Chrome 136+ default-data-directory remote-debugging restrictions do not authorize copying a user profile or enabling a debugger endpoint as a fallback. DevTools/user cancellation and enterprise blocked-host/screenshot/DLP attach denials must be honored. Flat child sessions from Chrome 125 require implemented routing and non-recursive auto-attach handling; current root-target support does not prove OOPIF execution. The product method allowlist currently denies `Target` commands.

The P0-T2 `--operator-assisted` diagnostic lane requires a human to load the exact staged unpacked directory and acknowledge permission/policy observations after load. Its persisted outcomes are only `recorded`, `none_observed`, or `not_recorded`: acceptance, denial, and policy-blocked inputs are collapsed to `recorded`. It cannot close the automated P0-T2 gate, which requires `operator_assisted:false`, permission status `not_requested`, exact CDP load/uninstall identity, and absence verification. Neither lane proves production distribution or absence of Chrome permission warnings.

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

Current P0-T7 evidence is narrower than this production contract. `run_install_lifecycle.py` defaults to the staged `extension/probes/` fixture, changes manifest versions, and uses experimental browser-target `Extensions.loadUnpacked`/`getExtensions`/`uninstall` in one owned disposable Chrome. Its `LifecycleLedger` is runner-local. `user_tabs_preserved` checks one owned fixture page; `new_mutations: paused` and `kill_switch` fields do not demonstrate broker mutation rejection (verification uses Chrome process liveness). `chrome_process_terminated:false` describes the pre-cleanup check; cleanup then terminates the owned process and removes the temporary profile. Passing the current drill/baseline checker does not prove production enrollment, Web Store updates, a product kill switch, incompatible durable-ledger refusal, or existing-user-tab preservation. Those P0-T7 requirements remain open; thresholds are unchanged.

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
