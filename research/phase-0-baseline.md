# Phase 0 baseline — P0-T1

**Captured:** 2026-10-02T00:47:49Z UTC baseline start; browser probes completed immediately afterward.
**Scope:** Historical P0-T1 capture. The addendum in section 8 is the current evidence after the host/core implementation slices were added. Historical statements below remain attributed to the original capture and must not be read as current implementation status.

## 1. Build tuple

| Item                      | Observed value                                                          |
| ------------------------- | ----------------------------------------------------------------------- |
| Commit                    | `068ef5e6dc9f5c4372a6f6ab11c3a63ea4b12bb9`                              |
| Commit subject/time       | `chore: update release-gate`; `2026-10-01T21:47:22-03:00`               |
| Workspace/package version | `2.0.0` (`Cargo.toml`, `Cargo.lock`)                                    |
| Rust                      | `rustc 1.98.0 (88d9e12ae 2026-08-18) (Homebrew)`                        |
| Cargo                     | `cargo 1.98.0 (797e8a9bc 2026-08-05) (Homebrew)`                        |
| Rust host                 | `aarch64-apple-darwin`; LLVM `22.1.8`                                   |
| Node/npm                  | Node `v26.10.0`; npm `11.19.1`                                          |
| MCP SDK                   | `rmcp 1.7.0` locked; initialize response reports server version `1.7.0` |
| CDP dependency            | `chromiumoxide_cdp 0.9.1` locked                                        |
| Tokio                     | `1.52.3` locked                                                         |
| Chrome                    | `Google Chrome 154.0.8037.93`                                           |
| Toolchain pin             | No `rust-toolchain.toml` or equivalent pin was present at capture time. |

The initial worktree already contained `M docs/release-gate.md`, untracked `.firecrawl/`, and untracked `docs/exec-plans/`; these were not changed. At the final check, additional untracked paths (`extension/`, `scripts/`, `rust-toolchain.toml`, `tests/direct_benchmark.rs`, `tests/fixtures/`, `tests/harness/`, `tests/probes/`, `tests/replay/`, and `tests/test-manifest.yaml`) were present; they were not created or changed by this task.

Evidence: `artifacts/p0-current/environment.txt`, `locked-package-versions.txt`, `chrome-discovery.txt`.

## 2. Exact tool counts

Runtime `tools/list` probes against the built debug binary returned:

| Profile                     |  Count | Response size measured by probe |
| --------------------------- | -----: | ------------------------------: |
| Default                     | **61** |              16,838 UTF-8 bytes |
| Extended (`mcp --extended`) | **76** |              21,809 UTF-8 bytes |

The default profile contains the 61 non-observability tools. Extended adds 15 observability tools. Static inspection found 76 `#[rmcp::tool]` declarations in `crates/agentyc-mcp/src/lib.rs`. The current source has a stale module comment saying “77-tool” at `crates/agentyc-mcp/src/lib.rs:1`, while the actual advertised counts are 61/76. The README also states 61 default / 76 extended.

The no-subcommand launch probe initialized successfully and reported `agentyc browser automation — 61 tools`.

Evidence: `artifacts/p0-current/tool-counts.json`, `default-launch-probe.json`, `static-tool-inventory.txt`.

## 3. CLI/runtime launch behavior

### CLI frontends

- `agentyc` with no subcommand starts the stdio MCP server (`crates/agentyc/src/main.rs:98-106`). Verified with a JSON-RPC `initialize` request.
- `agentyc mcp` starts stdio MCP; `--extended` sets `AGENTYC_EXTENDED=1` (`main.rs:102-107`).
- `agentyc serve` starts Streamable HTTP at `http://127.0.0.1:8765/mcp` by default (`main.rs:186-203`). No server was left running.
- `agentyc run` opens one `BrowserRuntime`, dispatches one action, then closes it on success or error (`main.rs:138-149`).
- `agentyc repl` opens one runtime for the session and closes it on exit (`main.rs:152-183`).
- `agentyc browser` directly launches Chrome with a remote-debugging port, temporary profile, and optional `--headless`/`--detach` (`main.rs:230-287`).

### Browser launch path

With no `--cdp-url`, `runtime_config` produces a default `BrowserProfile`; `BrowserRuntime::open` calls `BrowserRuntime::launch`, which calls `BrowserSession::launch` and `launch_browser` (`crates/agentyc/src/frontend.rs:166-175`; `crates/agentyc-runtime/src/lib.rs:77-85`; `crates/agentyc-browser/src/session.rs:70-97`; `launcher.rs:238-296`). The launcher:

- discovers Chrome from the macOS candidate list;
- allocates a free local TCP port;
- creates a unique temporary `agentyc-tmp-*` user-data directory;
- adds remote debugging and profile arguments;
- starts Chrome and polls `/json/version` for up to 55 seconds;
- defaults to headed mode unless `AGENTYC_HEADLESS` is truthy.

MCP itself is lazy: `browser_navigate` calls `ensure_browser`, which launches a managed browser when no CDP URL/runtime exists (`crates/agentyc-mcp/src/tools/navigation.rs:20-55,87-95`). `--cdp-url` uses `BrowserRuntime::connect` instead of launching.

### Process-per-command measurement

Command: `target/debug/agentyc run --headless=true close` (three isolated invocations; local managed Chrome only).

- Run 1: exit `0`, `797.22 ms`, output `{ "closed": true }`.
- Run 2: exit `0`, `531.32 ms`, output `{ "closed": true }`.
- Run 3: exit `0`, `555.64 ms`, output `{ "closed": true }`.

The first syntax probe, `target/debug/agentyc run --headless close`, exited `2`: Clap treats `--headless` as `Option<bool>` and requires `--headless=true` before the subcommand. This is current CLI behavior, not a production change.

Evidence: `artifacts/p0-current/cli-help-version.txt`, `cli-run-close-latency.json`, `cli-run-close-invalid-arg.log`, `default-launch-probe.json`.

## 4. Global close behavior

`browser_close_all` delegates to `BrowserRuntime::close_all` (`crates/agentyc-mcp/src/tools/tabs_session.rs:374-391`). The runtime:

1. lists all page targets;
2. sends `Target.closeTarget` for every page target;
3. clears the active page;
4. kills the locally launched browser, if the session owns one;
5. closes the CDP client and clears MCP runtime/browser-scoped state.

`browser_close_session` delegates to the same operation. For an externally attached CDP browser, `launched_browser` is `None`, so the external Chrome process is not killed, but `close_all` still sends `Target.closeTarget` to every page target returned by the attached browser. Therefore the current “global close” operation is global to all visible page targets on the connected endpoint, not limited to a logical agent/session. This source path was not run against the user’s existing browser.

### Isolated managed-browser observation

A local `data:` page and a second `about:blank` page were created. The redacted MCP transcript shows two tabs, raw `tab_id`/`target_id` fields in `browser_list_tabs`, a raw current tab ID in `browser_list_sessions`, and the successful `All sessions closed` response. In a separate isolated run, the managed launch had 1 matching Chrome process before `browser_close_all` and 0 one second afterward. Pre-existing agentyc temporary-profile process groups were observed separately and left untouched.

Evidence: `artifacts/p0-current/browser-raw-id-close-all.json`, `global-close-process-isolation.json`, `close-all-process-check.txt`, `close-all-processes-redacted.txt`.

## 5. Raw-ID output examples, redacted

Current primary/legacy outputs expose browser-generated IDs:

```json
[
  {
    "tab_id": "[REDACTED tab_id]",
    "target_id": "[REDACTED target_id]",
    "title": "P0 Baseline",
    "url": "data:text/html,..."
  }
]
```

Other observed shapes:

```text
New tab created: [REDACTED tab_id]
{"session_id":"default","connected":true,"current_tab_id":"[REDACTED tab_id]"}
```

Source inspection also confirms page-state elements serialize `ref` as `e<backend_node_id>` (`crates/agentyc-mcp/src/tools/state_tools.rs:140-148`), while `TabInfo` serializes both `target_id` and compatibility `tab_id` (`crates/agentyc-browser/src/session.rs:26-45,185-202`). Raw IDs were replaced in permanent artifacts; no cookies, tokens, endpoints, raw PIDs, or metrics identifiers were retained.

## 6. Known false-green test paths

These are current test behaviors, not claims that the implementation is correct:

1. **Protocol test is browser-free.** `cargo test -p agentyc-tests --test mcp_protocol --locked` passed 6/6, but `test_tool_count_is_61`, `test_all_tool_names_present`, `test_server_name_in_tool_descriptions`, and `test_browser_wait` do not require Chrome. `test_browser_list_sessions` only checks for text containing `session_id` or `has_cdp`; it can pass with no browser connected (`tests/mcp_protocol.rs:188-208`).
2. **Allowlist test accepts generic failure.** `test_navigate_blocked_by_allowed_domains` accepts any JSON-RPC/tool error (`tests/mcp_protocol.rs:223-281`). Current `browser_navigate` launches/attaches before checking the allowlist (`navigation.rs:87-95`), so a browser-launch failure can satisfy the test without proving the allowlist path.
3. **Benchmark is MCP-only.** The benchmark measures process startup, `tools/list`, `browser_list_sessions`, and repeated no-op MCP calls. It does not navigate, attach to a page, scan DOM, or prove browser health (`tests/benchmark.rs:113-236`).
4. **Browser-unavailable skips are green.** `Mcp::browser_available()` returns false on any navigation error and browser suites return early with `skipping: no Chrome/Chromium available` (`crates/agentyc-tests/src/lib.rs:226-231`; e.g. `tests/e2e_suite.rs:17-25`, `agent_autonomy.rs:13-20`, `battle_test.rs:10-17`, `headed_stress.rs:14-21`). A required browser capability can therefore be absent while the test process exits successfully.
5. **Heavy suites are ignored by default.** Live-site battle tests, headed stress tests, and generated real-world scenarios are marked `#[ignore]` or generated as ignored tests (`tests/battle_test.rs:1-20`, `headed_stress.rs:1-25`, `real_world.rs:1-17`). Default test commands do not exercise them.
6. **Some helper results are discarded.** `Mcp::wait` does not assert the result, and `Mcp::Drop` invokes `browser_close_all` while discarding its result (`crates/agentyc-tests/src/lib.rs:207-209,276-288`). Cleanup or wait failures can be hidden by otherwise passing tests.

Evidence: `artifacts/p0-current/test-path-inventory.txt` and the cited source paths.

## 7. Validation results

| Command                                                                                 | Result                                                                                                                                                                                                                                           |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `cargo build -p agentyc --locked`                                                       | Passed; debug build finished. Cargo warned that the non-root package profile is ignored.                                                                                                                                                         |
| `cargo metadata --no-deps --format-version 1 --locked`                                  | Passed; same profile warning.                                                                                                                                                                                                                    |
| `cargo test -p agentyc-tests --test mcp_protocol --locked`                              | Passed: 6 passed, 0 failed, 0 ignored, 0.95 s.                                                                                                                                                                                                   |
| `cargo test -p agentyc-tests --test benchmark --locked -- --test-threads=1 --nocapture` | Passed: 1 passed. Cold-start min/median/max `3.3/3.3/13.3 ms`; `tools/list` p50/p95/p99 `0.37/0.70/1.03 ms`; `browser_list_sessions` p50/p95/p99 `0.03/0.04/0.63 ms`; 200-call throughput `28,696 calls/s`; payload `16,840 bytes` for 61 tools. |
| `cargo test -p agentyc-tests --test benchmark --locked -- --test-threads=1`             | Passed: 1 passed, 0 failed, 0 ignored, 0.03 s; benchmark details were hidden because `--nocapture` was not supplied.                                                                                                                             |
| `target/debug/agentyc` + JSON-RPC `initialize`                                          | Passed; default stdio MCP, server version `1.7.0`, 61-tool instruction.                                                                                                                                                                          |
| Default/extended `tools/list` probe                                                     | Passed; 61 / 76 tools.                                                                                                                                                                                                                           |
| Isolated headless local browser probe                                                   | Passed; local `data:`/`about:blank` only; redacted raw-ID output and successful global close.                                                                                                                                                    |

All archived outputs are under `artifacts/p0-current/`. Paths, host name, endpoints, raw browser IDs, process IDs, temporary profile paths, metrics identifiers, cookies, and tokens were redacted or omitted.

## 8. Current addendum — implementation and live-gate status

**Captured:** 2026-10-03 UTC in the current worktree after the Phase 0 live-lane updates. This section supersedes the historical capture above for the current worktree; the evidence artifacts carry their own run nonces and provenance.

The following implementation surfaces exist. The focused test results below are previously recorded evidence, not tests rerun by this documentation audit:

- `crates/agentyc-core`: transport-neutral logical IDs, lifecycle/error/protocol/snapshot/action/event contracts and negative fixtures.
- `crates/agentyc-host`: locked atomic ledger, leases/fencing, action journal, reconciliation, event resume, snapshot cache, refs, waits, actionability, context budget seams, multi-client connection authorities, and the owner-only local IPC server.
- `crates/agentyc-browser/src/profile.rs` and `crates/agentyc-mcp/src/state.rs`: existing profile/session and legacy state surfaces audited for the baseline.
- `extension/`: MV3/native-messaging/debugger/tab/group/side-panel adapter with fake-Chrome tests; its trusted unpacked-development identity is stable and distinct from `extension/probes/`. It is not yet a Web Store or enterprise-managed production distribution.
- Direct CLI and host-backed MCP clients use the owner-only local Unix socket in normal mode; offline mode is the only in-process fake-host seam. Rollout evidence tooling remains explicit and fail-closed.

Previously recorded implementation validation (archived; not rerun in this audit):

- `cargo build -p agentyc --locked`: passed.
- `cargo metadata --no-deps --format-version 1 --locked`: passed.
- `cargo test -p agentyc-tests --test mcp_protocol --locked -- --test-threads=1 --nocapture`: 6 passed.
- `cargo test -p agentyc-tests --test benchmark --locked -- --test-threads=1 --nocapture`: 1 passed; current MCP overhead baseline is recorded in `artifacts/p0-current/`.
- `cargo test --workspace --locked`: passed; required host/core contract suites passed and only the pre-declared 2,379 real-world tests were ignored.
- `node --test extension/tests/*.test.mjs`: 26 passed.
- `python3 -m unittest discover -s tests/harness -p 'test_*.py'`: 58 passed.
- `python3 scripts/check_exec_plan.py docs/exec-plans/active/agentyc-browser-task-spaces`: passed.
- `python3 scripts/check_test_manifest.py tests/test-manifest.yaml`: passed.

Live and protocol evidence:

- `artifacts/p0-extension/report.json` is a live P0-T2 pass on Chrome 154. It used browser-target CDP `Extensions.loadUnpacked`, verified exact extension identity and inventory, exercised debugger command/event, created and cleaned a tab group, completed Chrome-mediated Native Messaging, uninstalled the extension, verified absence, and cleaned the disposable profile.
- `artifacts/p0-native-protocol/host-fault-suite.json` is a direct test-host smoke/fault-suite pass. `run_native_messaging_probe.py` only permits `tests/probes/native_probe`, not the Rust production broker. Framing/origin/replay/version/limit cases are host-only fixture evidence; P0-T2 separately supplies Chrome-mediated test-host Native Messaging proof. Neither proves production existing-profile coexistence.
- `artifacts/p0-capabilities.json` is the complete 76-operation offline capability catalog. Each operation now carries explicit permission/domain/Chrome-version/error metadata fields with `not-observed` status; it is not a live operation matrix.
- `artifacts/p0-performance/` contains the 64-cell/64,000-sample live disposable-CDP generation accepted by the current performance checker. `run_direct_benchmark.py` uses byte/4 token estimates (both token fields), three evaluations versus one for batching, CDP websocket reconnect, Python runner RSS under `host_rss_bytes`, and page-evaluation responsiveness. Cache scans/deltas are runner-local. These are not deployed-model tokens, production host/extension/CLI/SDK timings, or headed human-focus/input coexistence. P0-T6's stronger requirements remain open.
- `artifacts/p0-installation/lifecycle-record.json` plus the drill report are accepted macOS disposable-profile test-extension lifecycle evidence. `run_install_lifecycle.py` defaults to `extension/probes/`, reloads manifest versions, checks one owned fixture page and a runner-local `LifecycleLedger`, and uses process liveness to verify its kill-switch fields. It terminates its owned Chrome during cleanup. This does not prove a production mutation kill switch, durable-ledger rollback, Web Store updates, or user-tab preservation; P0-T7's stronger requirements remain open.
- `artifacts/p0-coexistence/report.json` is the earlier failed-closed descriptor-lane capture; no scenario passed. The current Python entrypoint now runs host-backed direct-CLI observations and ignores `--harness`, but this audit did not execute that mutating lane or replace the artifact. The new route still cannot observe all ten browser scenarios or infer live safety counters from acknowledgments.
- `research/phase-0-path-inventory.md` records existing versus planned Phase 0 surfaces.
- The required test manifest now runs the host fault suite and writes `artifacts/p0-native-protocol/host-fault-suite.json`.

Current Phase 0 status remains **blocked/active** for these evidence gates:

- independently enrolled existing-Chrome two-space coexistence, user-tab/focus safety, and live takeover/restart fencing;
- live Chrome capability observations across the requested version/platform/policy matrix;
- independently observed production Native Messaging bridge/coexistence evidence; the host now owns one broker-backed local Unix socket and keeps Native Messaging framing separate, but the enrolled existing-profile lane has not run;

The current checker reports macOS installation and managed performance sub-gates as passed under its artifact contract. That result does not close P0-T6/P0-T7's stronger done-when requirements or the existing-user-profile coexistence gate. No release threshold was changed by this audit.

Offline, partial headed, and deterministic tests are not substitutes for those gates. No browser download, arbitrary existing debug-endpoint attachment, or user-profile mutation is implied by this baseline.

The official Chrome documentation audit is recorded in `research/phase-0-chrome-docs-audit.md`. Its earlier implementation corrections are historical work, not changes made by this documentation audit. The [host-backed probe/checkpoint audit](phase-0-host-backed-probe.md) distinguishes the Rust local-socket smoke, Python public-CLI orchestration/operator checkpoints, and disposable test-host lane. No live run, registration, browser launch, or user-profile mutation was performed for this audit. Coexistence remains missing.

## 9. Documentation audit validation — 2026-10-03

| Command/check                                                                           | Observed result                                                                                                                                                                                                        |
| --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python3 scripts/check_exec_plan.py docs/exec-plans/active/agentyc-browser-task-spaces` | Passed; nine canonical phases, exactly one active (Phase 0).                                                                                                                                                           |
| `python3 scripts/check_test_manifest.py tests/test-manifest.yaml`                       | Passed; command/artifact structure only, not execution of listed targets.                                                                                                                                              |
| `python3 scripts/check_release_gate.py --phase 0`                                       | Passed; policy and evidence disclosure, not release eligibility.                                                                                                                                                       |
| `python3 scripts/check_extension_permissions.py`                                        | Passed; policy/offline capability catalog, not live Chrome policy coverage.                                                                                                                                            |
| `python3 scripts/check_phase_0_baseline.py research/phase-0-baseline.md`                | Exit 1; `baseline: validated`, `status: blocked`, sole reported blocker `coexistence-live-missing`. Installation, live Chrome, live Native Messaging, and performance sub-gates pass their current artifact contracts. |
| Relative Markdown links in edited Phase 0/release/research files                        | Passed; local link destinations exist.                                                                                                                                                                                 |
| Scoped `git diff --check`                                                               | Passed; no whitespace errors in owned documentation.                                                                                                                                                                   |

P0-T6 and P0-T7 remain unchecked in the plan because the current checker accepts narrower disposable instrumentation than their full done-when requirements. The audit changes no release threshold and claims no new product test, enrollment, live scenario, or Phase 0 completion.
