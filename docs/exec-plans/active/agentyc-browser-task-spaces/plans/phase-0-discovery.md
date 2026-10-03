---
phase: 0
name: Existing-Chrome feasibility and baseline
status: active
owner: Japneet Kalkat
primary_outcome: A real-Chrome feasibility report, capability matrix, end-to-end baseline, and approved budgets for the extension/native host, host broker, context, and automation paths.
depends_on: none
---

# Phase 0 — Existing-Chrome feasibility and baseline

## Objective

Prove the target product boundary before activating later phases or rollout: a normal user-approved Chrome profile, an installed MV3 extension, a Native Messaging host, one host broker, two independent agent clients, and no copied CDP URL or automatic browser launch. Convert all remaining Chrome, permissions, profile, protocol, token, and performance assumptions into signed parameters.

## Handoff in

- **Inputs:** revised README; `research/decision-supersession.md`; S-018–S-025; current crates and tests; read-only ego-lite reference.
- **Must already be true:** later-phase activation and production rollout remain gated. Core/host/extension/direct-client slices already exist; their presence and deterministic tests are not live proof. This Phase 0 audit does not authorize further production migration.
- **Do not reopen:** existing Chrome is the default; MCP is adapter-only; no automatic browser launch/download; task spaces/pages are canonical; raw IDs and `[id] name` are forbidden in primary output.

## Confirmed facts

- The historical/explicit legacy CLI path launches a temporary profile when no `--cdp-url` is supplied: `crates/agentyc/src/main.rs::run_action`, `crates/agentyc-runtime/src/lib.rs::BrowserRuntime::open`, `crates/agentyc-browser/src/launcher.rs`.
- The legacy browser session assumes one `active_page`: `crates/agentyc-browser/src/session.rs`.
- The historical/explicit legacy MCP path owns browser/server state and lazily opens a browser: `crates/agentyc-mcp/src/tools/mod.rs`, `tools/navigation.rs`, `lib.rs`.
- Normal direct CLI and host-backed MCP clients use the owner-only local socket; only the explicit offline seam constructs a fake broker. The Chrome-launched `agentyc-native-host` owns the durable broker and a separate Native Messaging stdio bridge. See [the host-backed probe audit](../../../../../research/phase-0-host-backed-probe.md).
- Chrome 136+ ignores remote-debugging port/pipe switches against the default data directory unless a non-standard `--user-data-dir` is supplied (S-018). The product's existing-profile lane independently forbids copied CDP endpoints, profile copies, launch, and download; this is not a claim that Chrome forbids every opt-in debugging mechanism.
- `chrome.debugger` requires a sensitive permission, has an allowlisted CDP domain set, and supports flat related sessions from Chrome 125 (S-019).
- Native Messaging has exact-origin registration and bounded messages; it is not itself automation (S-020).
- MV3 workers restart and cannot be the authoritative lease/ledger owner (S-021).

## Working assumptions to verify

- Chrome 125+ is the minimum full-support floor for flat related-target sessions; older versions are unsupported or restricted to a documented partial mode.
- A stable Web Store/managed extension ID is the production distribution identity; a pinned `key` gives the unpacked-development build a stable separate ID. Unpacked development is for trusted personal development and is not production distribution evidence.
- A profile-scoped extension instance UUID is only a binding selector, never authentication. First enrollment and every rebind require explicit user confirmation; mismatch, copied profile, reinstall, storage reset, or extension-ID change enters `rebind_required` and fences prior authority.
- The current native executable owns the broker and serves one owner-only local socket; reconnect/multi-profile behavior still needs live proof that it does not create conflicting brokers. Direct same-user execution of the native binary is not cryptographically distinguishable from Chrome-launched execution.
- Chrome exposes selected debugger domains including `Target`, but the current product method allowlist only covers selected `Accessibility`, `DOM`, `DOMSnapshot`, `Input`, `Network`, `Page`, `Runtime`, `IO`, and `Log` methods and denies `Target` commands. Root-target support does not establish child-session/OOPIF execution. Flat sessions require explicit routing; auto-attach is not recursive. DevTools/user cancellation and enterprise attach denials must fail closed, not trigger a CDP fallback.
- A side panel is available for the supported Chrome floor and can expose pause/takeover/finish without stealing focus unless the user opens it.
- A deployed-model tokenizer adapter is still required; the current managed runner uses a byte/4 estimate, not model-context measurements. Bytes/chars must be reported separately.

## Unresolved questions and blockers

- **U0-1 — Chrome matrix:** exact stable/beta versions, platforms, and enterprise policies; owner: Japneet Kalkat; resolve with real headed runs.
- **U0-2 — Extension identity/distribution:** the internal unpacked-development identity is frozen and host registration derives it from `extension/manifest.json`; official Web Store versus enterprise-managed distribution remains deferred with owner Japneet Kalkat before production packaging. See `research/phase-0-chrome-docs-audit.md`.
- **U0-3 — Permission policy:** minimum versus optional permissions for debugger, host access, cookies, downloads, uploads, and side panel; owner: Japneet Kalkat; resolve with capability review.
- **U0-4 — Profile scope/binding:** behavior with multiple Chrome profiles/incognito windows, copied profiles, reinstall/storage reset, and explicit rebind confirmation; owner: Japneet Kalkat; resolve with binding probe and unsupported cases.
- **U0-5 — Budgets:** queue, event, snapshot, native-message, memory, and latency limits; owner: Japneet Kalkat; resolve from measurements.

None of these blocks writing contracts, but Phase 1 cannot claim a complete Chrome support matrix until U0-1–U0-4 are recorded.

### Current execution blocker

P0-T2 has recorded Chrome 154 test-extension/test-host evidence in an owned disposable profile. Its public browser-target `Extensions.loadUnpacked`/`getExtensions`/`uninstall` flow is experimental and version-observed; fixture/control-page CDP instrumentation is also used. It is not a production-profile fallback or a real broker coexistence test. The product Native Messaging bridge, exact-origin registration, owner-only socket, direct CLI, and host-backed MCP slices exist, but no independently captured existing-user-profile coexistence evidence is available. The Rust `agentyc-existing-chrome-probe` now exercises the local-socket protocol, but only logical pages with one client and skipped returned-space cleanup. `run_existing_chrome.py` now runs bounded public direct-CLI calls for two principals and offers operator checkpoints, but creates only logical pages and cannot observe all ten browser scenarios. Descriptors are ignored; acknowledgments and host-only observations do not close the live gate. P0-T6/P0-T7 artifacts pass current checkers but leave the stronger product/tokenizer/rollback requirements open. Owner: Japneet Kalkat. Impact: Phase 0 stays active and Phase 1 cannot be activated. Release posture: blocked. Next action: verify approved product extension/host/profile enrollment and capture real host-backed scenarios with [operator checkpoints](../../../../../research/phase-0-host-backed-probe.md); do not launch, debug, or mutate arbitrary Chrome.

## Scope

### In scope

- Test-only extension/host probe and local fixture pages.
- Native Messaging framing/handshake probe, extension-origin validation, payload-size behavior, reconnect, and host lock behavior.
- Real Chrome debugger attach/event/frame/OOPIF capability tests.
- Two-space/two-agent/user-tab coexistence vertical slice with no production default rollout.
- Baselines for current direct CLI, proposed persistent host/SDK, snapshot scans/tokens, action latency, Chrome CPU/RSS, and extension/host recovery.
- A production-grade test manifest, deterministic clock/scheduler/fake-Chrome harness, replayable fault traces, realistic MCP workflow corpus, and headed-Chrome matrix.
- Capability matrix for all current default/extended MCP operations mapped to extension-supported, partial, legacy-only, or unsupported.
- Distribution/installer feasibility for macOS first, with Linux/Windows evidence or explicit later owner.

### Out of scope

- Production task-space implementation.
- Automatic browser launch/download or profile import.
- Third-party web automation as correctness proof.
- Changing default CLI/MCP behavior.
- Choosing visual branding beyond functional side-panel states.

## Installation evidence split (checker evidence versus product proof)

Installation evidence has two separate targets and statuses:

- **Installation preflight:** a read-only registration/origin/host prerequisite check. It is required before the live Native Messaging probe. `--check-install` only checks prerequisites; it does not prove installation, update, uninstall, downgrade, rollback, or user-tab preservation.
- **Explicit installation drill:** `run_install_lifecycle.py` records staged test-extension load/reload/uninstall in an owned disposable Chrome; `run_install_drill.py` validates that lifecycle record separately from registration. This is P0-T7 evidence, not the P0-T3 preflight. The runner-local ledger, one owned fixture page, and process-liveness kill-switch fields do not prove production ledger rollback, mutation fencing, or existing-user-tab preservation.

The test manifest names preflight and drill targets separately. No combined live-probe target may treat `--check-install` as installation evidence. Archived macOS disposable lifecycle/drill artifacts pass the current checker; that result is not proof of the full P0-T7 done-when contract.

## Affected surfaces

- **Files/tests:** add test-only probes under `extension/probes/` and `tests/probes/`; use the existing `crates/agentyc-tests` target, standalone probes, or explicitly scoped probes of the now-existing `agentyc-host` path; add local fixtures under `tests/fixtures/browser-task-spaces/` and `tests/fixtures/mcp/`; add `tests/test-manifest.yaml`, `tests/harness/`, `tests/replay/`, and `tests/fixtures/browser-task-spaces/`; write `research/phase-0-baseline.md` and artifacts under `artifacts/`.
- **Contracts/data:** baseline schema for Chrome version, extension ID/mode, permissions, host handshake, profile instance, latency, round trips, tokens, scans, RSS, event loss, and safety outcomes.
- **Ownership:** disposable probes own only their temporary test data. The product local-socket smoke mutates the selected broker ledger and requires an explicitly approved host/profile/state binding; it must not touch unrelated user tabs. This documentation audit changes no code or runtime state.

## Required feasibility scenarios

1. User has a running, normal Chrome profile with one unrelated user tab.
2. Extension connects to the host without a CDP URL or browser restart.
3. Agent A creates space `research`, page `results`; agent B creates space `testing`, page `app`.
4. Both agents navigate/read/act concurrently; each sees only its logical space.
5. Extension creates matching Chrome tab groups; user can inspect them in the tab strip/side panel.
6. User continues using the unrelated tab and its focus is not stolen by background actions.
7. User takes over `research`; queued/new actions are rejected; dispatched action is reconciled.
8. User returns control; agent resumes the same space/page handles after a fresh lease.
9. User closes or moves an agent page; host marks it lost/unknown without closing other tabs.
10. Host, native bridge, service worker, and Chrome restart paths recover or fail explicitly.
11. No primary output contains `[id] name`, raw Chrome IDs, or raw CDP IDs.

## Tasks

- [x] P0-T0 — Bootstrap the plan's validation, deterministic harness, and artifact layout.
  - **Files/surfaces:** workspace `rust-toolchain.toml` or equivalent pinned toolchain file; `.gitignore`; `tests/test-manifest.yaml`; `tests/harness/`; `tests/replay/`; `tests/fixtures/browser-task-spaces/`; `tests/fixtures/mcp/`; `tests/probes/`; `artifacts/.gitkeep`; `scripts/check_exec_plan.py`; `scripts/check_test_manifest.py`; `crates/agentyc-tests/Cargo.toml` only for test targets that already have their required dependencies; `docs/exec-plans/active/agentyc-browser-task-spaces/plans/PLAN_INDEX.md`.
  - **Done when:** planned versus existing paths are explicit, artifacts are ignored/redacted by policy, Rust and Node/npm/Chrome test floors are pinned or their ceilings are recorded, every Phase 0 command has a runnable target or named prerequisite, the manifest names installation preflight and installation drill as separate targets with separate statuses and dependencies, and the manifest rejects missing commands, silent skips, swallowed results, unbounded timeouts, and missing artifact declarations.
  - **Validation:** `cargo metadata --no-deps --format-version 1 --locked`; `cargo build -p agentyc --locked`; `cargo test -p agentyc-tests --test mcp_protocol --locked`; `npm --version`; `node --version`; `python3 scripts/check_exec_plan.py docs/exec-plans/active/agentyc-browser-task-spaces`; `python3 scripts/check_test_manifest.py tests/test-manifest.yaml`; `git check-ignore artifacts/p0-current/secret.log`.
  - **Owner:** Japneet Kalkat.

- [x] P0-T1 — Record the current baseline and exact legacy surfaces.
  - **Files/surfaces:** `Cargo.toml`, current `README.md`, `crates/agentyc/src/{main.rs,frontend.rs}`, `crates/agentyc-runtime/src/lib.rs`, `crates/agentyc-browser/src/{session.rs,launcher.rs,profile.rs}`, `crates/agentyc-mcp/src/{lib.rs,state.rs,tools/}`; output `research/phase-0-baseline.md`.
  - **Done when:** commit, versions, current test results, default/extended tool counts, process-per-command latency, browser launch behavior, global close behavior, raw-ID outputs, and known false-green paths are recorded.
  - **Validation:** `cargo build -p agentyc --locked`; `cargo metadata --no-deps --format-version 1 --locked`; `cargo test -p agentyc-tests --test mcp_protocol --locked`; `cargo test -p agentyc-tests --test benchmark --locked -- --test-threads=1`; archive logs under `artifacts/p0-current/`.
  - **Owner:** Japneet Kalkat.

- [x] P0-T2 — Build a test-only extension/native-host vertical slice.
  - **Files/surfaces:** `extension/probes/manifest.json`, service worker, debugger/tabs/native messaging probe; `tests/probes/native_probe`; `scripts/run_chrome_probe.py`; platform manifest fixtures; no production host crate changes.
  - **Done when:** the probe connects to a real Chrome tab, sends one allowed debugger command, receives an event, creates a tab group, and sends a validated envelope through Native Messaging to a local test host.
  - **Validation:** `python3 scripts/run_chrome_probe.py --headed --require-live --launch-chrome`; branded Chrome must use the trusted browser-target CDP `Extensions.loadUnpacked` flow because Chrome rejects `--load-extension`. The runner must verify the returned extension ID and `Extensions.getExtensions` identity/path/version/enabled inventory, then uninstall the exact extension and verify absence. The live probe must launch only a short-lived system-temporary profile, remove it after the run, never attach to an arbitrary existing debug endpoint, never call Chrome private extension APIs, and never use file-picker APIs. Capture extension/runner tree hashes, extension version, Chrome version, CDP load/unload evidence, handshake transcript without secrets, and screenshots in `artifacts/p0-extension/`. The operator-assisted UI lane remains an explicit diagnostic fallback and cannot close the automated release gate.
  - **Owner:** Japneet Kalkat.

- [x] P0-T3 — Measure Native Messaging framing, origin, reconnect, and limits.
  - **Files/surfaces:** test host protocol harness, `extension/probes`, `tests/probes/native_messaging.rs`, `scripts/run_native_messaging_probe.py`, or an explicitly registered `agentyc-tests` target.
  - **Done when:** fragmented/truncated/invalid UTF-8/invalid JSON/wrong-origin/replayed/oversized/unsupported-version messages fail closed; clean EOF and host crash are distinguishable; reconnect does not create a second broker.
  - **Validation:** run `python3 scripts/run_native_messaging_probe.py --require-host-smoke --host-fault-suite --artifact artifacts/p0-native-protocol/host-fault-suite.json`; the accepted executable is `tests/probes/native_probe`, not the Rust broker. Classify the direct test-host result as `host_smoke_passed`, never as Chrome-mediated or production-broker evidence. P0-T2 supplies the separate Chrome-mediated Native Messaging handshake. `--check-install` is only a read-only prerequisite and is not an installation drill. The artifact records wrong-origin, replay, version, UTF-8, JSON, truncation, oversized-frame, and wrong-phase rejection plus bounded frame/chunk/artifact/assembly/in-flight-byte limits. Reconnect remains a deterministic single-broker host test; live restart/reconnect evidence remains a P0-T5 requirement.
  - **Owner:** Japneet Kalkat.

- [x] P0-T4 — Build the Chrome capability matrix.
  - **Files/surfaces:** probe commands, new `scripts/run_capability_matrix.py`, and `research/phase-0-baseline.md`; map current operations from `README.md` and `crates/agentyc-mcp/src/lib.rs`.
  - **Done when:** every navigation, state, interaction, wait, frame, storage/cookie, download/upload, screenshot/PDF, observability, and evaluate operation is marked `supported`, `partial`, `unsupported`, or `legacy-only`, with explicit per-operation permission/domain/Chrome-version/error metadata. Unknown metadata must be represented as `not-observed`, never inferred from the catalog.
  - **Validation:** `python3 scripts/run_capability_matrix.py --mode offline --matrix artifacts/p0-capabilities.json`; the 76-operation catalog is complete and every operation has explicit permission/domain/Chrome-version/error metadata fields. Current Chrome 154 P0-T2 evidence is recorded separately; stable/beta, Linux/Windows, DevTools attach conflict, OOPIF, cross-origin frame, restricted URL, incognito, and enterprise-policy observations remain named `not-observed` residuals rather than inferred claims.
  - **Owner:** Japneet Kalkat.

- [ ] P0-T5 — Measure existing-profile coexistence, lifecycle epochs, and user-control safety.
  - **Files/surfaces:** approved existing user profile, product `extension/` and `com.agentyc.host`/broker/local-socket path, local fixtures, the existing `tests/browser_task_spaces_existing_chrome.rs` fixture smoke, Rust `agentyc-existing-chrome-probe` logical checkpoint smoke, and `scripts/run_existing_chrome.py` host-backed CLI orchestration. Complete browser-page/user-control/restart observations and independent enrollment capture remain missing prerequisites.
  - **Done when:** the required feasibility scenarios pass with zero user-tab closes, zero focus theft outside explicit user actions, zero cross-space successful mutations, correct pause/takeover/return semantics, and explicit profile-sharing warnings; browser-session, worker-instance, connection, and broker epochs are observed separately; multiple Chrome profiles, incognito windows, extension reinstall/update, copied-profile identity mismatch, and stale-ledger attachment are either supported with proof or rejected before mutation authority. Takeover also proves the extension-side fence barrier drains/rejects lower-epoch commands queued in the host, Native Messaging pipe, worker, debugger, and content-script bridge.
  - **Validation:** only after approving the already-running enrolled host/profile and ledger mutations, run `python3 scripts/run_existing_chrome.py --headed --require-live --cli target/debug/agentyc --operator-checkpoint --spaces 2 --agents 2 --artifact-dir artifacts/p0-coexistence`. The executable must already exist; no browser is launched or attached. `--harness` is legacy/ignored. Current public-CLI coverage is logical only: unavailable preflight, incomplete observations, or acknowledgment-only checkpoints exit 1; none closes the ten-scenario gate. Follow [the checkpoint audit](../../../../../research/phase-0-host-backed-probe.md) for actual versus required enrollment, human input/focus, user takeover/return, debugger cancellation, restarts/update, and owned cleanup. Seeded fault traces, screenshots, measured safety counters, and ≥200 valid latency samples for gating p95 remain requirements, not supplied by the current smoke.
  - **Owner:** Japneet Kalkat.

- [ ] P0-T6 — Measure context, latency, and resource baselines.
  - **Files/surfaces:** `tests/benchmark.rs`; new registered `tests/direct_benchmark.rs`, `tests/browser_task_spaces_existing_chrome.rs`; `scripts/run_direct_benchmark.py`; test tokenizer adapter; `docs/release-gate.md`.
  - **Done when:** report includes time to first useful action, warm metadata/action/wait p50/p95/p99, SDK batch versus separate CLI calls, transport/UTF-8/serialized/model-context token counts with tokenizer metadata, clean-snapshot DOM scans, delta/full actionable-control coverage, Chrome CPU/RSS, host RSS, event lag, reconnect time, stale-ref/unknown rates, and human-tab responsiveness. It contains a committed baseline manifest with environment, fixture hash, cache state, concurrency, sample count, and confidence intervals.
  - **Validation:** `python3 scripts/run_direct_benchmark.py --mode managed --headless --browser-executable /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --warmups 10 --samples 1000 --fixtures small-form,dense-admin-table,dynamic-feed,nested-frame --cache-states cold,clean,dirty,resync --spaces 1,2,4,8 --artifact-dir artifacts/p0-performance`. The archived live 64-cell/64,000-sample generation passes the current performance checker. It uses disposable CDP instrumentation: byte/4 token estimates, three evaluations versus one for batching, CDP reconnect, Python runner RSS, and page-evaluation responsiveness. Cache/delta measurements are runner-local. It does not satisfy the production tokenizer, host/extension/CLI/SDK end-to-end, or headed human-coexistence requirements above, so P0-T6 remains open. Thirty samples are smoke-only and cannot gate p95/p99.
  - **Owner:** Japneet Kalkat.

- [ ] P0-T7 — Run the explicit installation drill, separate from installation preflight.
  - **Files/surfaces:** existing `install/native-messaging/`, extension package metadata, `scripts/register_native_host.py`, `scripts/run_install_lifecycle.py`, `scripts/run_install_drill.py`, and `docs/installation.md`; no default rollout changes. The task must publish macOS/Linux/Windows registration, signing, update, uninstall, and downgrade assumptions even when a platform is deferred.
  - **Done when:** macOS user-level registration works; Linux/Windows support is either tested or explicitly deferred with an owner; stable/unpacked extension IDs and host manifests are documented; install, update, uninstall, downgrade, and rollback leave user Chrome and tabs unchanged.
  - **Validation:** `python3 scripts/run_install_lifecycle.py --run --artifact-dir artifacts/p0-installation`, followed by `python3 scripts/run_install_drill.py --drill --required --extension-id hlnmcimoechnbccahemchokemgceaffp --clean-profile --lifecycle-record artifacts/p0-installation/lifecycle-record.json --artifact-dir artifacts/p0-installation`. The archived macOS disposable record/drill passes the current installation checker. The default fixture is `extension/probes/`; version changes are unpacked reloads, preservation checks one owned fixture, and rollback/kill-switch fields use a runner-local ledger and process liveness. Production registration/enrollment, broker mutation rejection and durable-ledger rollback, and existing-user-tab preservation still need observations, so P0-T7 remains open. Linux/Windows remain explicitly deferred with owner Japneet Kalkat.
  - **Owner:** Japneet Kalkat.

- [ ] P0-T8 — Freeze the baseline addendum and phase gates.
  - **Files/surfaces:** `research/phase-0-baseline.md`, this phase, README, `research/decision-closure.md` only if evidence falsifies a decision.
  - **Done when:** supported Chrome versions, permissions, extension distribution, profile-binding/rebinding rules, profile guarantees, exact protocol/message budgets, queue/lease/snapshot/action latency limits, capability matrix, and residual risks are signed; every unresolved item has an owner/destination.
  - **Validation:** fresh-agent review confirms Phase 1 can proceed without inventing the browser boundary.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [ ] No test requires a copied CDP URL or automatically launches/downloads a browser for the target path.
- [ ] Chrome official distribution constraints are recorded: unpacked is development-only, Web Store is the ordinary-user path, and macOS self-hosting is enterprise-managed.
- [ ] Every Phase 0 validation names existing prerequisites; the existing host-backed path is distinguished from the test-only probe and missing live executor.
- [ ] Profile binding is treated as enrollment/reconciliation, not authentication; incognito and copied-profile behavior is explicit.
- [ ] Extension-side takeover fencing and no-replay behavior are tested.
- [ ] Tests distinguish existing Chrome from temporary managed test Chrome.
- [ ] No test prints cookies, tokens, page bodies, or raw browser IDs into permanent artifacts.
- [ ] User-tab safety and focus coexistence are tested, not inferred from “no close” counts.
- [ ] Real tokenizer counts and end-to-end timings are reported separately from transport bytes.
- [ ] Deterministic tests use controlled time, seeded scheduling, replayable traces, isolated state, and no false-green browser-unavailable path.
- [ ] Headed Chrome, extension worker, host, debugger, permission, and user-coexistence failures have executable probes.
- [ ] Every unsupported capability has a typed result and documented fallback.

## Handoff out

- **Artifacts:** baseline, Chrome capability matrix, Native Messaging protocol proof, coexistence traces, performance/resource report, installation report, approved budgets.
- **Closed decisions:** Chrome floor, permission/distribution mode, profile guarantee wording, initial limits, supported vertical-slice operations.
- **Residuals:** platform/version gaps are carried with owners to Phase 3/8; no hidden blocker remains.
- **Next starting condition:** Phase 1 can freeze core/host/extension ownership and state machines from measured facts.

## Exit gate

Advance only when every task/checklist item is checked, all named commands run against registered targets, the two-space existing-Chrome vertical slice passes with zero safety violations, every capability/permission/profile assumption is recorded, and a fresh agent can implement Phase 1 without inventing the browser boundary. A failed platform probe may be carried only as a named bounded-support record with owner, impact, and release posture.
