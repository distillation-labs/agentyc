---
phase: 7
name: Hardening, validation, direct rollout, and support
status: pending
owner: Japneet Kalkat
primary_outcome: Reproducible evidence that the direct existing-Chrome task-space product is safe, fast, context-efficient, supportable, reversible, and ready for staged release without waiting for MCP compatibility.
depends_on: phase-6
---

# Phase 7 — Hardening, validation, direct rollout, and support

## Objective

Run the fake, extension, headed existing-Chrome, CLI/SDK, privacy, installation, performance, recovery, and rollback matrix for the direct product. MCP compatibility is reported separately and is not a prerequisite for this release gate.

## Handoff in

- **Inputs:** Phases 0–6 and their artifacts; Phase 0 budgets; host/extension install package; direct CLI/SDK.
- **Must already be true:** no known cross-space/user-tab safety defect; all required fixtures and capability results exist; the legacy MCP stdio/HTTP baseline remains runnable and its behavior is frozen, but no new adapter migration is required for this phase.
- **Do not reopen:** existing Chrome is default; no automatic browser launch/download; host is authoritative; MCP is compatibility; shared-profile limits are documented.

## Confirmed facts

- Existing release checks cover Rust formatting/build/tests and legacy MCP transport but not extension/native-host/profile coexistence. Direct rollout must preserve that legacy baseline rather than silently routing MCP through an unfinished adapter.
- Chrome extension distribution, permissions, platform host registration, and MV3 restart behavior are release surfaces.
- Rollback must not close user tabs or kill user Chrome.

## Working assumptions

- CI can run fake extension/host tests and at least one real headed Chrome lane; platform gaps are recorded with a manual lane.
- Test Chrome profiles are disposable and never contain real user credentials.
- Preview cohorts can install a signed/unpacked extension and native host with explicit consent.

## Unresolved questions and owners

- **U7-1:** final supported platform/Chrome cohort; owner: Japneet Kalkat; close before preview.
- **U7-2:** Web Store/managed distribution readiness; owner: Japneet Kalkat; close before external preview.
- **U7-3:** final default-on timing; owner: Japneet Kalkat; close after preview metrics.

## Scope

### In scope

- Security/adversarial review and fuzz/property testing.
- Host/extension/CLI/SDK contract and integration tests.
- Real headed Chrome coexistence and user-control flows.
- Context/token/latency/RSS/throughput/soak benchmarks.
- Installer/update/uninstall/rollback and ledger migration.
- Redacted metrics, debug bundle, runbook, support handoff.
- Internal, preview, staged rollout and rollback decision.

### Out of scope

- New product capabilities after the validated contract.
- Modern MCP transport.
- Remote multi-tenant service.
- Per-space cookie/storage isolation.
- Third-party-site correctness beyond controlled smoke tests.

## Required test lanes

Every release uses the test pyramid in `research/production-test-strategy.md`:

1. Pure unit/property tests for contracts, state machines, framing, limits, redaction, error mapping, and delta/ref behavior.
2. Deterministic component tests with a fake Chrome bridge, controlled clock, seeded scheduler, and replayable traces.
3. Process integration tests for host IPC, Native Messaging, extension worker lifecycle, CLI/SDK, MCP transports, installers, and ledger migration.
4. Headed existing-Chrome tests on the supported Chrome/platform/policy matrix using disposable profiles and a local fixture server.
5. Nightly/pre-release load, soak, chaos, fuzz, installation, and rollback lanes.

Required lanes fail on missing Chrome, skipped/ignored tests, swallowed tool errors, leaked processes, missing artifacts, or unbounded timeouts. A single diagnostic retry may classify a flake but cannot satisfy a gate.

### Headed Chrome matrix

The release artifact records exact Chrome build, OS/architecture, display backend, profile mode, policy mode, extension ID/build, host build, ledger schema, seed, command, and result. The matrix covers the minimum supported milestone, stable, and beta where supported; macOS arm64/x86_64, Linux x86_64, and Windows x86_64 or an explicit deferred owner; native headed display or Xvfb/Wayland; disposable synthetic and controlled existing-Chrome profiles; debugger/native-host allowed and denied; OOPIF/cross-origin/restricted/incognito/copied-profile states; worker/host/Chrome restart; extension update/uninstall; unpacked and production/managed IDs.

### Existing Chrome and safety

- Install extension/native host in a normal user-approved profile without changing Chrome launch flags.
- Existing user tab remains open, usable, and unclaimed.
- Two agents create separate spaces/pages and work concurrently.
- Visual Chrome tab groups correspond to spaces but do not grant authorization.
- User can open/move/create tabs while agents work; no unexpected focus theft.
- User takeover pauses/fences mutations; dispatched operations reconcile; return-control requires explicit user action and fresh lease.
- Agent page close/release removes only proven claimed pages; user/unmanaged tabs survive.
- Chrome, host, native bridge, and service-worker restarts recover logical state safely.

### Reliability and correctness

- Target/tab replacement, debugger detach, OOPIF/frame churn, navigation/document changes, stale refs, event gaps, and profile mismatch.
- Queue fairness, cancellation, deadlines, backpressure, host lock, reconnect, partial writes, corrupt ledger, incompatible versions.
- Unknown outcomes after click/input/navigation/form/close/storage/evaluate; reconciliation never replays blindly.
- Permission/capability denials, login/payment/destructive/upload/cookie/evaluate confirmation boundaries.

### Context and speed

- Clean snapshot zero DOM/AX scan.
- Full/min/focus/delta/resync/truncation and actual tokenizer metadata.
- Batch SDK versus separate CLI process calls.
- Warm host first action, action/wait, event delivery, extension bridge reconnect.
- Chrome CPU/RSS, host RSS, queue/event/snapshot memory, artifact chunking.
- Human tab responsiveness and focus stability.

### Interface and privacy

- CLI/SDK structured outputs contain no `[id] name`, raw Chrome tab IDs, CDP IDs, cookies, tokens, headers, page bodies, or screenshots unless explicitly requested through a protected artifact path.
- Native Messaging exact origin/version/nonce/sequence/size and platform registration.
- Logs/debug bundles redact secrets and page content by default.

## Measurement protocol

- Fixtures: `small-form`, `dense-admin-table`, `dynamic-feed`, `nested-frame`, delayed/redirecting navigation, cross-origin/OOPIF, dialogs, downloads/uploads, hostile-message, takeover, restart, and user-tab coexistence fixtures.
- Runs: 10 warmups or warmup-until-stable, then at least 200 valid samples per blocking p95 cell and 1,000 per blocking p99 cell; every attempted sample is accounted for as success, timeout, error, invalid measurement, or infrastructure failure. Exclusions require a predeclared rule; timeout/error/missing/discarded samples count against the cell and fail it when the signed budget is exceeded. Report bootstrap 95% confidence intervals, raw samples, failures, queue/event distributions, and environment. Thirty samples are smoke-only.
- Token metrics: transport bytes, UTF-8 bytes, serialized payload tokens, and deployed model-context tokens are separate; record tokenizer name/version/hash, wrapper/encoding, cache state, fixture/data hash, and whether metadata/truncation is included.
- Timing decomposition: client send, IPC, host queue, bridge, Chrome command, browser scan, serialization/tokenization, and response delivery; compare legacy MCP one-shot, persistent MCP, local protocol, SDK sequential, SDK batch, full/min/delta, clean/dirty cache, and event-driven wait against polling.
- Delta target: median ≤35% and p95 ≤60% of equivalent full snapshot only when actionable-control/ref coverage is equivalent; emit full/min when measured delta cost is higher.
- Clean target: zero DOM/AX scan and no element payload when cache is clean.
- Isolation metric: any cross-space mutation, stale-agent mutation after takeover, user/unmanaged close, authorization bypass, secret leak, or silent unknown success is a release blocker.
- Unknown metric: every injected disconnect is classified; no lost mutating response is silently successful or blindly replayed.
- Speed metric: warm metadata p95 ≤50 ms and batch script round trips at least 50% below process-per-command baseline, subject to the Phase 0 signed environment and confidence interval.
- Baseline policy: every blocking metric uses a committed manifest containing commit/build/OS/CPU/Chrome/extension-host tuple/fixture hash/tokenizer/concurrency/cache state/sample/statistical method; threshold changes require a dated owner-approved decision record.

## Tasks

- [ ] P7-T1 — Run security, privacy, and adversarial review.
  - **Files/surfaces:** host auth/IPC/Native Messaging, extension debugger/content/page bridge, evaluate/upload/cookie/download paths, ledger/logging, `docs/security/logging.md`, `fuzz/` targets for `local_frame`, `native_message`, `protocol_envelope`, `artifact_chunk`, and `redaction`.
  - **Done when:** threat model covers origin spoofing, local peer/endpoint races, replay across profile/space/page/generation, malformed/oversized/chunk-flood frames, prompt injection, page message spoofing, raw evaluation, secrets/logging/redaction, permission abuse, user takeover tickets, stale clients, cleanup, package/update tampering, and resource exhaustion; all high-risk paths have fail-closed tests.
  - **Validation:** `cargo fuzz run local_frame`; `cargo fuzz run native_message`; `cargo fuzz run protocol_envelope`; `cargo fuzz run artifact_chunk`; `cargo fuzz run redaction`; adversarial property tests; canary-secret sink scan; zero-bypass checklist `artifacts/p7-security-review.md`. Any auth bypass, cross-space mutation, user-tab close, secret leak, or silent unknown-success is automatic no-go.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T2 — Run full host/core/extension/CLI/SDK direct-product suites and enforce false-green prevention.
  - **Files/surfaces:** core/host/runtime tests, `extension/tests`, `packages/agentyc-browser/test`, direct root integration targets, `crates/agentyc-tests/src/{lib.rs,runner.rs}`, `tests/test-manifest.yaml`.
  - **Done when:** no direct-product compile/lint/test failures remain; every scenario step asserts semantic response status, scope, action receipt, postcondition, and cleanup; browser absence is a failure in required lanes; no ignored/skipped test satisfies coverage; child processes/state directories are cleaned.
  - **Validation:** `python3 scripts/check_test_manifest.py tests/test-manifest.yaml`; `cargo fmt --all -- --check`; `cargo clippy --workspace --all-targets --locked -- -D warnings`; `cargo test --workspace --locked`; `cargo build -p agentyc --locked`; `cargo test -p agentyc-tests --test mcp_protocol --locked`; Phase 0-selected extension/package test commands; archive redacted logs/traces under `artifacts/p7-test-gate/`.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T3 — Run deterministic fake transport, host fault, and state-machine validation.
  - **Files/surfaces:** `agentyc-core`, `agentyc-host`, `agentyc-runtime`, `agentyc-cdp` legacy adapter, `tests/harness/`, `tests/replay/`, test fixtures.
  - **Done when:** controlled clock, seeded ordering, replayable ChromeBridge traces, lease fencing, epoch transitions, ledger recovery, action unknown/reconcile, event lag/resync, cancellation, host lock, and capability errors pass with byte-identical outcomes over 100 repeated runs.
  - **Validation:** `python3 scripts/run_replay_matrix.py --repetitions 100 --manifest tests/test-manifest.yaml --artifact-dir artifacts/p7-faults`; focused package tests plus fault-injection/property/replay suite; archive seed, trace, outcome, byte-comparison report, and redacted replay command under `artifacts/p7-faults/`. Any missing repetition or divergent outcome fails the lane.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T4 — Run real headed existing-Chrome acceptance.
  - **Files/surfaces:** extension e2e, `tests/browser_task_spaces_existing_chrome.rs`, local fixture server, CLI/SDK workflows, `scripts/run_existing_chrome.py`.
  - **Done when:** the ten required Phase 0 scenarios pass across supported Chrome/platforms; user-tab/focus safety and takeover evidence exist; no browser launch/download/CDP URL is used.
  - **Validation:** `python3 scripts/run_existing_chrome.py --headed --spaces 2 --agents 2 --matrix supported --artifact-dir artifacts/p7-existing-chrome`; execute the full manifest scenario set with deterministic seeds, repeat safety scenarios until the signed confidence target is met, and archive screenshots/traces/logs/environment manifests/replay commands.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T5 — Run context/performance/resource benchmarks.
  - **Files/surfaces:** existing direct benchmark and snapshot/action benchmark outputs from Phase 5, host/extension metrics, existing `scripts/run_direct_benchmark.py`, and the direct-product section of existing `docs/release-gate.md`.
  - **Done when:** token/scan/delta with equivalent coverage, first-action, batch round trips, action/wait, event lag, native artifact, CPU/RSS, queue, reconnect, stale-ref/unknown, and human-tab responsiveness gates meet approved thresholds or a signed decision records a change.
  - **Validation:** `python3 scripts/run_direct_benchmark.py --warmups 10 --min-samples-p95 200 --min-samples-p99 1000 --spaces 1,2,4,8 --cache-states cold,clean,dirty,resync --artifact-dir artifacts/p7-performance`; archive JSON/Markdown/raw samples/baseline manifest.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T5a — Run bounded load and saturation tests.
  - **Files/surfaces:** host scheduler/queues, direct benchmark harness, `scripts/run_load_test.py`.
  - **Done when:** approved concurrency levels 1, 2, 4, 8, and maximum are measured with p50/p95/p99, throughput, queue depth, event lag, deadline failures, CPU, RSS, file descriptors, threads, tabs, ledger size, and artifact bandwidth; overload returns typed errors.
  - **Validation:** `python3 scripts/run_load_test.py --spaces 1,2,4,8,max --artifact-dir artifacts/p7-load/`; report saturation point and signed limits.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T5b — Run soak and leak tests.
  - **Files/surfaces:** host/extension lifecycle, `scripts/run_soak_test.py`, metrics/debug bundle sinks.
  - **Done when:** PR smoke, multi-hour nightly, and pre-release soak cycles actions, reconnects, worker/Chrome restarts, takeover/return, retention, and artifacts; RSS, file descriptors, threads, queues, event buffers, tabs, and ledger size have bounded slopes and spaces remain isolated.
  - **Validation:** `python3 scripts/run_soak_test.py --duration 10m --artifact-dir artifacts/p7-soak-smoke/`; nightly/pre-release durations are recorded in the manifest and artifacts.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T5c — Run chaos and fault-injection tests.
  - **Files/surfaces:** host/extension/ledger/bridge fault hooks, `scripts/run_chaos_test.py`.
  - **Done when:** SIGKILL at enqueue/dequeue/dispatch/commit, Native Messaging EOF/partial/exact-limit/oversize/invalid-UTF8 frames, bridge disconnect, worker termination at each boundary, extension reload/update, debugger detach (`target_closed` and `canceled_by_user`), DevTools attach conflict, renderer/page/Chrome restart, sleep/wake, disk-full/read-only, partial writes, corrupt/incompatible ledger, clock jumps, CPU pressure, memory pressure, event-buffer gaps, and late old-generation events validate no mutation replay, correct unknown outcomes, recovery bounds, and page/user-tab preservation.
  - **Validation:** `python3 scripts/run_chaos_test.py --seeds 100 --manifest tests/test-manifest.yaml --artifact-dir artifacts/p7-chaos/`; archive the fault matrix, seeds, traces, outcomes, and replay commands; every injected fault has an accounted result and expected no-replay assertion.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T6 — Validate installation, upgrade, uninstall, ledger migration, and rollback.
  - **Files/surfaces:** installers/manifests, extension package, host metadata, ledger schema, `scripts/run_install_drill.py`, `docs/runbooks/browser-task-spaces.md`.
  - **Done when:** clean install/update/remove, extension ID mismatch, host mismatch, corrupt/incompatible ledger, host crash, mutation kill switch, paused-space resume, old-binary refusal, and the complete supported-version tuple (Chrome, extension, native host, CLI/SDK, ledger schema, and legacy MCP baseline) are deterministic and published; user tabs remain untouched. Stop, rollback, uninstall, and ambiguous rebind retain pages; explicit page cleanup requires fresh proof and confirmation.
  - **Validation:** `python3 scripts/run_install_drill.py --clean-profile --rollback --artifact-dir artifacts/p7-install-rollback`; platform installation drills and archived logs.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T7 — Publish support runbook and observability handoff.
  - **Files:** `docs/runbooks/browser-task-spaces.md`, `docs/configuration.md`, metrics/debug bundle docs.
  - **Done when:** support can diagnose extension not connected, permission denied, bridge loss, debugger detach, stale ref, event lag, unknown action, user-owned state, corrupt ledger, profile mismatch, and rollback without engineering chat; metrics are low-cardinality/redacted.
  - **Validation:** tabletop drill with generated bundles; archive `artifacts/p7-support-drill.md`.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T8 — Run staged preview and make the release decision.
  - **Files/surfaces:** release config, extension/host/CLI/SDK manifests, `scripts/run_preview_drill.py`, plan README, changelog.
  - **Done when:** internal synthetic profile, internal existing-Chrome, and opt-in preview cohorts have named dates/builds/config/metrics/incidents; the direct path has a go/no-go decision; MCP compatibility is recorded as a separate follow-up gate.
  - **Validation:** `python3 scripts/run_preview_drill.py --cohorts synthetic,existing-chrome,opt-in --artifact-dir artifacts/p7-preview`; preview report records rollback readiness, support ownership, and residuals.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T9 — Record follow-ups and close the plan status.
  - **Files:** README, `research/decision-closure.md` if needed, `artifacts/p7-follow-ups.md`.
  - **Done when:** MCP compatibility, modern MCP, isolated-profile mode, remote broker, additional Chrome capabilities/platforms, and SDK distribution each have owner/trigger/destination; direct status is `shipped` or `blocked` with exact reason; no phase is falsely active.
  - **Validation:** fresh-agent completion audit; every artifact path exists.
  - **Owner:** Japneet Kalkat.

- [ ] P7-T10 — Wire direct-product required CI and release dependencies.
  - **Files:** planned `.github/test-policy.yaml`; existing `.github/workflows/test.yaml`, `.github/workflows/workflow.yml`, and `tests/test-manifest.yaml` created by Phase 0; direct-product sections of `docs/release-gate.md`.
  - **Done when:** required PR jobs execute the manifest, pure/component/process suites, redaction checks, and frozen legacy MCP baseline; nightly/pre-release jobs execute headed existing-Chrome, load, soak, chaos, fuzz, install/update, and rollback lanes; release publication depends on the direct-product release gate; missing Chrome, skipped tests, swallowed errors, leaked processes, missing artifacts, and unbounded timeouts fail closed.
  - **Validation:** CI dry-run proves every required manifest entry executed; workflow dependency report shows `publish-binaries` cannot bypass direct release gates; sanitized logs and replay artifacts upload on failure.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [ ] Any user-tab close, cross-space mutation, stale-agent mutation, or silent unknown success blocks release.
- [ ] Existing Chrome path uses no copied CDP URL, default-profile remote debugging, browser download, or automatic launch.
- [ ] Shared-profile limits are visible in UI/docs/errors.
- [ ] Metrics include task-level speed/context, not only MCP overhead.
- [ ] Installation/distribution and rollback are tested.
- [ ] Support can recover without hidden conversation context.
- [ ] Deferred items have owners/triggers, not vague promises.

## Rollout stages

1. **Internal synthetic:** disposable Chrome profile, unsigned/unpacked extension, fake/real host tests; legacy MCP baseline remains green.
2. **Internal existing Chrome:** controlled user profile with explicit permissions and manual support.
3. **Opt-in preview:** signed/managed extension and native host; direct CLI/SDK default; MCP adapter available.
4. **Staged expansion:** increase supported Chrome/platform cohorts only after metrics remain within gates.
5. **Default candidate:** publish direct existing-Chrome workflow; retain MCP/legacy CDP as explicit compatibility.

Rollback pauses new mutations and preserves ledgers/pages; it never uses browser-global close, kills user Chrome, closes a group, or downgrades across incompatible ledger versions. It records the extension/host/CLI/SDK/MCP tuple and has a tested mutation kill switch before preview.

## Handoff out

- **Artifacts:** test/release logs, real-Chrome evidence, benchmark reports, installation/rollback evidence, runbook, preview report, follow-up register.
- **Decisions closed:** direct release readiness, supported matrix/version tuple, support ownership, threshold changes, and rollback readiness.
- **Residuals:** only explicit deferred items with owners/triggers.
- **Next starting condition:** maintenance follows the runbook; new architecture/protocol work opens a new plan.

## Exit gate

This phase is complete only when every task/checklist item is checked, all direct-product safety/privacy/reliability/performance gates pass, the supported-version tuple and installation/rollback/support evidence are archived, direct existing-Chrome release status is explicit, and no active/false phase remains. MCP compatibility is not a prerequisite.
