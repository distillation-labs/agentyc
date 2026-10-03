# Phase 0 host-backed probe and operator checkpoints

**Audited:** 2026-10-03 UTC, including the concurrently added Rust probe and Python host-backed orchestration. The reachable Python entrypoint and `--help` were rechecked after wiring changed.
**Evidence status:** source audit only; no live host/Chrome execution, host registration, extension installation, or user-profile mutation performed by this audit. Phase 0 remains active. A probe exit code or operator acknowledgment cannot close the ten-scenario gate.

## Keep the evidence lanes separate

| Lane                                 | Actual execution                                                                                                                                      | What it does not prove                                                                                                                                 |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Offline fixtures/fake Chrome         | `tests/browser_task_spaces_existing_chrome.rs`, extension fake-API tests, fake local-host tests                                                       | Chrome-mediated execution, enrollment, human coexistence, or production rollout                                                                        |
| Disposable P0-T2                     | `run_chrome_probe.py --headed --require-live --launch-chrome`; test `extension/probes/` -> `com.agentyc.p0_probe`                                     | Production Rust broker, enrolled existing profile, two-agent coexistence, or ordinary extension distribution                                           |
| Direct test-host smoke               | `run_native_messaging_probe.py --require-host-smoke --host-fault-suite`; starts only `tests/probes/native_probe`                                      | Chrome `connectNative()` or the product host/broker                                                                                                    |
| Product host-backed checkpoint probe | `agentyc-existing-chrome-probe` -> `LocalSocketClient` -> running broker; selected lease/fence calls can reach the extension through Native Messaging | Independent enrollment provenance, real browser-page actions, two independent clients, all ten live scenarios, focus/input safety, or restart recovery |
| Python host-backed orchestration     | `run_existing_chrome.py --headed --require-live`; bounded public CLI calls for `agent-a`/`agent-b`, logical receipts, optional operator checkpoints   | Managed browser pages/actions, independent enrollment, complete ten-scenario observations, or measured live safety; `--harness` is ignored             |
| Disposable performance/lifecycle     | CDP benchmark; unpacked test-extension lifecycle with runner-local state                                                                              | Deployed-model tokenizer, CLI/SDK end-to-end timings, product kill switch/ledger rollback, or user-profile preservation                                |

## Actual product route

1. The approved product MV3 extension calls `connectNative('com.agentyc.host')`. This is distinct from the probe extension/host.
2. Chrome checks the registered manifest's exact `allowed_origins`, launches `agentyc-native-host`, and supplies the calling origin argument. The host normalizes the trailing slash, validates the Native Messaging hello, opens the durable `Broker`, completes the handshake, and starts `LocalHostServer` on an owner-only socket.
3. Agent CLI, host-backed MCP, and the Rust checkpoint probe use that socket through `LocalSocketClient`; they do not open another production broker or discover a debugger endpoint. Normal direct CLI failure is not permission to switch to the explicit offline fake-host seam.
4. The host sends authorized logical requests over Native Messaging; the extension implements selected debugger/tabs/group/content operations. On bridge close, the host stops its socket server. Pending dispatched mutations are uncertain, not automatically replayed.

Host state defaults to `~/.agentyc/state` unless its process receives `AGENTYC_STATE_DIR`; socket configuration also supports `AGENTYC_HOST_SOCKET`. Do not assume a shell export changes the environment of already-running GUI Chrome or its future native host. Verify the intended host/socket/state binding before any mutating probe. Exact-origin registration and OS-user socket ownership are trust controls, not cryptographic proof against same-user direct execution or malware. A profile UUID is a binding selector, not authentication.

Source anchors: `crates/agentyc-host/src/bin/agentyc-native-host.rs`, `crates/agentyc-host/src/native_messaging.rs`, `crates/agentyc-host/src/protocol.rs`, `crates/agentyc/src/commands/direct.rs`, `extension/src/native-messaging.mjs`, and `scripts/register_native_host.py`.

## What the current Rust checkpoint probe really checks

The worktree source is `crates/agentyc-host/src/bin/agentyc-existing-chrome-probe.rs`. It accepts `--socket PATH`/`--socket=PATH` or the configured default; it has no headed/operator/restart/enrollment flags. It executes immediately after connecting, with **one client/principal**, and mutates the broker ledger. It does not launch Chrome, use a copied browser CDP endpoint, install an extension, or prompt for user approval. Its no-CDP claim refers to its transport: the product extension may still use CDP methods via `chrome.debugger`.

| Report checkpoint                 | Implemented observation and limit                                                                                                                                                                   |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `connect.local_socket`            | Connects/handshakes and records broker/connection epochs. A fake server can also satisfy this.                                                                                                      |
| `connect.host_extension`          | Reads `host.status`, requires `ready` and advertised `action`. This is not a fresh browser-action challenge or enrollment proof.                                                                    |
| `scenario.two_spaces`             | Creates and claims two logical spaces using one client, not two independent agents.                                                                                                                 |
| `scenario.page_create_list`       | Uses local `page.create` and `page.list` for logical records. It does **not** call `page.create_managed`, create Chrome tabs/groups, navigate, snapshot, or perform a DOM/input action.             |
| `scenario.isolation`              | Confirms page lists exclude the other space's page and rejects epoch-zero `page.create` with `StaleLease`. It does not attempt a cross-space browser mutation under a second principal.             |
| `scenario.lease_takeover`         | Calls local `space.takeover`, requires an acknowledged fence and `agent_owned`. This is an agent lease takeover, not the user clicking the panel/canceling Chrome debugging.                        |
| `scenario.return_control`         | Calls local `space.return`, requires `user_owned`, then confirms unticketed `lease.acquire` fails with `UserControlRequired`. It does not return authority to an agent with a fresh ticketed lease. |
| `scenario.cleanup`                | Finishes/releases the first logical space. It does not prove cleanup of owned Chrome pages or preservation of unrelated tabs.                                                                       |
| `scenario.cleanup_returned_space` | Deliberately **skipped**: current local protocol lacks ticketed reclaim. The second space stays user-owned, yet the report can still set `success:true` and exit 0.                                 |

Lease operations use synthetic `now` values 1–10 and TTL 600, not measured wall-clock latency or epoch recovery. On an earlier failure later checkpoints are skipped; no unconditional cleanup path is demonstrated. Plan for retained logical records, including the returned space, rather than deleting arbitrary ledger entries or retrying the entire mutating probe blindly.

The accompanying local-socket test starts `FakeBridge`/`NullBridge`. Its success demonstrates the smoke's protocol behavior, not live Chrome. The probe output includes a literal `socket_path` and free-form error details, lacks the central release artifact envelope/redaction and ten scenario names, and is **not** an accepted coexistence artifact. Review/redact it before retention; never rename its checkpoints or wrap it in a descriptor to manufacture a live pass.

The current direct CLI `page create` likewise creates only a logical record. The local host protocol separately implements `page.create_managed`, `action.execute`, and `page.close`; these are not exercised by this smoke. Their presence is not a live result.

## Current Python orchestration and its actual prompts

`run_existing_chrome.py` now wires `DirectCli`/`orchestrate_live` into the headed entrypoint. `--cli` or `AGENTYC_CLI` selects an existing executable; otherwise it uses `target/debug/agentyc`. `--state-dir` selects the direct client's state directory, not a Chrome profile. `--cli-timeout` is positive, finite, and capped at 15 seconds. The runner neither builds the binary nor installs/launches/attaches Chrome. It refuses `AGENTYC_FAKE_HOST` and checks ready host status, connected extension bridge, `test_seam:false`, safe direct-path metadata, and `action` capability before creating records. Those metadata checks are not independent enrollment proof.

Calls use separate `agent-a`/`agent-b` principals but are sequential subprocess invocations, not concurrent browser work. They create/claim `research` and `testing`, create/list **logical** `results` and `app` pages, resume events, test cross-space `page.create` rejection, request an agent takeover/fence, and reject a stale lease. A `finally` path attempts finish/release of tracked claimed spaces. It does not create managed Chrome pages, navigate, snapshot, or act on DOM/input. `agent-page-cleanup` is explicitly `host_observed_not_browser`; isolation/fence receipts are `host_observed`, not full live passes. Unclaimed records or failed cleanup can remain; do not retry or remove state blindly.

With `--operator-checkpoint` or `AGENTYC_EXISTING_CHROME_OPERATOR_CHECKPOINT` set to `1`, `true`, or `yes`, and only if logical orchestration has no recorded failures, the runner prompts for these seven names **after the logical cleanup attempt**:

- `user-tab-preservation`
- `focus-stability`
- `return-control-fresh-lease`
- `worker-restart-recovery`
- `host-restart-recovery`
- `chrome-restart-recovery`
- `extension-update-recovery`

Each prompt accepts only `AGENTYC_EXISTING_CHROME_CHECKPOINT_V1 <scenario-name> ACK` from stdin/controlling TTY. `--checkpoint-timeout` defaults to 30 seconds and is capped at 60; missing TTY, timeout, oversized line, or wrong token cannot count as evidence. The runner does **not** perform the named restart, update, return, or human interaction. Acknowledgment is `acknowledgement_only`; subsequent host status/events receipts still cannot observe user-tab state or focus/input. A changed broker epoch at the host-restart checkpoint adds only `host_observed`, not full restart recovery. Prompts are not a pre-mutation approval mechanism and do not authorize disruptive work; the operator must approve that separately.

Current output is `live_required_unavailable` for unavailable/unsafe preflight, or `live_observation_incomplete`/`operator_checkpoint_required` for partial execution. The current orchestration has no complete browser observation path and cannot emit a genuine ten-scenario `live_passed`. Partial safety counters stay null (`not_measured_live_incomplete`), `release_eligible` stays false, and required headed execution exits 1. `--harness` is ignored; `AGENTYC_EXISTING_CHROME_HARNESS` no longer drives execution. Retained descriptor-parsing helpers do not establish live evidence. The runner's central artifact envelope/redaction is distinct from the Rust smoke's raw stdout.

## Operator checkpoints for a real headed capture

These are the full required capture procedures, **not a claim that either smoke implements browser observations**. The Python acknowledgment prompts cover only part of this procedure and occur after logical mutations. Owner: Japneet Kalkat. Stop if a prerequisite/action is unavailable; record the gap rather than setting its scenario to passed.

1. **Enrollment and scope, before mutation.** The operator selects the already-running normal profile, approved product extension build/identity/distribution, matching `com.agentyc.host` registration/binary, intended state/socket, and local fixture scope. Preserve an unrelated operator-owned tab. Record binding/origin/identity checks, Chrome/platform/policy/build tuple, and explicit profile-sharing disclosure. Trusted personal unpacked development is not Web Store/enterprise production distribution. No auto-adoption, user-profile copy, install, or policy change is implicit.
2. **Permissions/policy and visible UI.** The operator reviews actual warnings, grants/denials, and debugger indicators; opens the side panel with a toolbar/user action. Respect enterprise blocked-host/screenshot/DLP denial, incognito rejection, and restricted URLs. Never suppress warnings, synthesize consent, or silently reattach after cancellation. A panel button or `ok:true` forwarding acknowledgment is not a completed host transition.
3. **Probe approval.** Before the Rust smoke, explicitly authorize its ledger mutations and retained user-owned space. Verify it targets the approved host, not a fake socket. If used, build `agentyc-existing-chrome-probe` with `cargo build -p agentyc-host --bin agentyc-existing-chrome-probe --locked` and run the resulting binary with the operator-confirmed `--socket` value. Do not run it as a read-only installation check. It supplies partial smoke evidence only.
4. **Two-agent browser work and human coexistence.** The independent live harness must create actual agent-owned fixture pages via the product path and use two separately authorized clients. Observe navigation/read/action, cross-space mutation rejection, visual groups, and unrelated human-tab activation/input with no background focus theft. `active:false` and a preserved fixture count are insufficient; retain redacted action/state/focus observations.
5. **User takeover/cancellation and return.** The operator performs actual user takeover/stop and debugger/DevTools cancellation checks. Capture the host and extension fence barrier, rejection of queued/new lower-epoch commands, reconciliation of dispatched actions without replay, then explicit ticketed return/reconciliation and a fresh agent lease. Do not call agent `space.takeover` and label it user takeover, or call `space.return` and label it fresh-agent resumption.
6. **Disruptive fault approval.** Obtain a separate checkpoint before each worker termination, host/Native Messaging interruption, browser restart, or extension update in the enrolled profile. Browser restart is a human-authorized scenario, never an automated product launch/kill fallback. Save/recover unrelated user work. Observe broker, connection, worker, browser, document/target, and lease epochs, stale-authority rejection, and no mutation replay; do not infer recovery from a reconnect timer or metadata increment.
7. **Owned cleanup and final preservation.** Close only pages with current individual ownership proof; mixed groups are not cleanup units. Independently confirm the unrelated user tab/state, browser process, and focus survived. Record retained user-owned/lost pages and incomplete cleanup; do not kill the user browser, globally close tabs, or delete a ledger to make the report green.
8. **Evidence review.** Every required scenario below must have its own executed trace/observation and measured zero safety violations. Preserve bounded redacted environment/command/time/source provenance. Human acknowledgments support provenance but cannot replace machine-observed state/actions or measured counters. Run the baseline checker against actual captured artifacts; leave Phase 0 active if evidence or stronger task requirements remain missing. Do not upgrade a partial receipt, null counter, or acknowledgment into `live_passed`.

Current side-panel limitation: `app.mjs` forwards `space.intent_ticket`; the worker validates expiring one-use tickets for destructive actions and sends Native Messaging `kind: request`. The audited host inbound handler accepts responses, events, and inventory, not extension-origin requests. No complete live ticket issuance/side-panel host-dispatch route was established. Do not promise that clicking existing buttons closes the user-control checkpoint; this remains an implementation/live-validation prerequisite, not permission to bypass ticket checks.

## Ten scenarios are still required

`user-tab-preservation`, `two-space-isolation`, `focus-stability`, `takeover-fence`, `return-control-fresh-lease`, `agent-page-cleanup`, `worker-restart-recovery`, `host-restart-recovery`, `chrome-restart-recovery`, and `extension-update-recovery`.

The former descriptor-only entrypoint could accept caller-supplied ten-scenario claims. It is superseded: current headed execution calls the public CLI and ignores descriptors, while incomplete safety counters remain null. Complete independent enrollment/profile provenance, actual managed-browser scenarios, and measured safety observations are still missing; neither host metadata nor the operator token proves them.

Only after operator approval of the existing host/profile and ledger mutations, the current partial probe command is:

```sh
python3 scripts/run_existing_chrome.py --headed --require-live \
  --cli target/debug/agentyc --operator-checkpoint \
  --spaces 2 --agents 2 --artifact-dir artifacts/p0-coexistence
python3 scripts/check_phase_0_baseline.py research/phase-0-baseline.md
```

The binary must already exist and target the approved host; use the actual approved `--state-dir`/socket configuration when it differs from the default. This audit ran only `--help`, not this mutating command, and provides no new live artifact. Current required execution remains non-green; passing fake tests, logical smoke checkpoints, disposable CDP lanes, or operator acknowledgments cannot mark Phase 0 complete.

## Disposable P0-T2 UI fallback is a different checkpoint

`--operator-assisted` is only an explicit diagnostic fallback with `--headed --launch-chrome`. The human opens `chrome://extensions` in the owned disposable window, enables Developer mode, chooses the exact staged directory via Load unpacked, leaves the window open, and answers the post-load permission/policy prompt. The runner does not access extension-UI DOM, private APIs, or the file picker. Missing acknowledgment fails closed.

Persisted outcomes are `none_observed`, `recorded`, or `not_recorded`; `shown_accepted`, `shown_denied`, and `policy_blocked` collapse to `recorded`. Record distinct denials separately if needed; never infer grant from this field. This fallback cannot close the automated P0-T2 checker contract, which requires non-operator CDP load/uninstall/absence evidence and permission status `not_requested`. That status means no manual acknowledgment was requested, not that Chrome has no permission or debugger warning.

Safety sources and freshness limits: [official Chrome audit](phase-0-chrome-docs-audit.md). Task status and checker results: [Phase 0 baseline](phase-0-baseline.md).
