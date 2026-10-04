---
phase: 1
name: Core architecture, security, and invariants
status: complete
owner: Japneet Kalkat
primary_outcome: A transport-neutral architecture and executable state/ownership/security model for the host broker, Chrome extension, direct clients, and MCP adapter.
depends_on: phase-0
---

# Phase 1 — Core architecture, security, and invariants

## Objective

Turn the Phase 0 evidence into normative component boundaries, state machines, capability policies, persistence rules, and threat-model invariants. A coding agent must be able to implement the core without re-deciding whether MCP, CDP, a temporary profile, a tab label, or a Chrome tab group is authoritative.

## Handoff in

- **Inputs:** Phase 0 baseline/capability matrix; D-09–D-17; S-018–S-025; current crate map.
- **Must already be true:** real-Chrome feasibility either passes or has explicit bounded support gaps and owners.
- **Do not reopen:** extension/native host is the default browser boundary; host broker is canonical; local CLI/SDK is primary; MCP is adapter-only; profile sharing is disclosed; no `[id] name`.

## Confirmed facts

- `agentyc-core`, `agentyc-host`, the MV3 extension, the direct CLI, and the thin Node SDK now exist and passed their Phase 0 deterministic gates.
- The legacy `agentyc-runtime`/`agentyc-browser` CDP/process path remains explicit compatibility/test-only; the existing-Chrome product path is the extension/native host/local socket.
- `agentyc-mcp` remains a compatibility adapter over the host broker; it is not the canonical state owner.
- MV3 service workers can terminate; host persistence is authoritative, while same-session exact-tab recovery is bounded and browser-session changes require host rebind (S-021).
- Chrome debugger target/session/frame identity remains distinct from durable product identity; root-target support is proven, while flat related-target/OOPIF routing is a Phase 1 capability decision (S-019).

## Working assumptions

- Core uses `serde`/`thiserror` but has no Chrome, Tokio, MCP, or filesystem dependency.
- Host runs one broker per enrolled profile binding and one local endpoint; a host lock prevents duplicate authority. A presented profile UUID selects a binding but does not authenticate it.
- Profile binding states are `unbound`, `bound`, `rebind_required`, and `revoked`; first enrollment, mismatch, copied-profile detection, reinstall/storage reset, and extension-ID change require explicit side-panel/installer confirmation and fence the previous binding.
- The extension stores only profile/connection metadata and bounded UI state; leases/ledger/actions live in the host.
- Chrome tab groups are a presentation mapping and may be missing/changed without invalidating the logical space.
- Local OS IPC is the default authentication boundary; Unix peer credentials/restrictive socket directories or Windows named-pipe security descriptors are required, endpoint files are protected against symlink/path replacement, client-supplied principals are never authentication, and remote TCP is deferred and disabled by default.

## Unresolved questions and owners

- **U1-1:** exact extension distribution and platform installer layout from Phase 0; owner: Japneet Kalkat; impact: Phase 3/8.
- **U1-2:** exact capability fallback for unsupported debugger domains; owner: Japneet Kalkat; impact: Phase 2/5.
- **U1-3:** whether a future isolated profile mode will be added; owner: Japneet Kalkat; impact: separate plan, not a blocker for shared-profile mode.

## Scope

### In scope

- Component/data-flow diagram and ownership matrix.
- Domain IDs and records.
- Space/page/user/lease/action/ref/event state machines.
- Extension-host-agent trust boundaries.
- Capability/policy matrix and profile guarantees.
- Crash/restart/reconciliation semantics.
- File/module and dependency graph.
- Threat model for prompt injection, stale clients, Native Messaging spoofing, raw evaluation, file upload, cookies, and cleanup.

### Out of scope

- Concrete implementation code.
- Final UI styling.
- Modern MCP wire migration.
- A second browser/profile isolation product.

## Normative architecture

```text
packages/agentyc-browser or agentyc JSON CLI
  -> local protocol client
  -> agentyc-host HostServer
       -> BrowserBroker
           -> Space/Page Ledger
           -> Lease/Fencing Manager
           -> Per-space Scheduler
           -> Snapshot/Ref Cache
           -> Event/Wait Router
           -> ChromeBridge
       -> NativeMessagingBridge
  -> extension service worker
       -> chrome.debugger
       -> chrome.tabs / tabGroups / scripting / sidePanel
       -> content scripts/page bridge

agentyc-mcp compatibility adapter
  -> local protocol client / in-process broker test backend
```

### Ownership matrix

| Concern              | Canonical owner                                       | Extension role                                    | CLI/SDK role                    | MCP role                                        |
| -------------------- | ----------------------------------------------------- | ------------------------------------------------- | ------------------------------- | ----------------------------------------------- |
| space/page IDs       | `agentyc-core` + host ledger                          | report live tab hints                             | send logical IDs                | translate legacy fields                         |
| leases/epochs        | host broker                                           | enforce bridge admission                          | present receipts                | map sessions to principals                      |
| Chrome tabs/debugger | extension adapter                                     | create/attach/send/observe                        | never call Chrome APIs directly | never call Chrome APIs directly                 |
| snapshots/refs       | host runtime/core                                     | supply DOM/AX/events                              | consume envelopes               | serialize legacy state                          |
| user control         | host transition authority + side-panel intent tickets | render/confirm user actions; never self-authorize | request/observe transitions     | return typed errors; never claim user authority |
| persistence          | host ledger/journal                                   | persist only profile instance/UI metadata         | reconnect                       | no independent copy                             |
| cleanup              | host authorization + extension execution              | remove proven claimed tabs                        | request release                 | scope legacy close                              |
| policy/evaluate      | host policy                                           | execute approved capability                       | request with capability         | adapter rejects unsafe bypass                   |

## State machines

### Task-space lifecycle

```text
created
  -> agent_owned(principal, epoch)
  -> handoff_requested
  -> draining
  -> agent_owned(new principal, epoch+1)
  -> paused
  -> user_owned
  -> recovering
  -> finished
  -> released

agent_owned -> orphaned on host/client/extension/browser loss
orphaned -> recovering only after explicit claim and reconciliation
user_owned -> agent_owned only after explicit user return and fresh lease
finished -> released only for explicit broker-owned cleanup
```

Guards and effects:

| Transition                           | Guard                                               | Effect                                                          | Duplicate/error                              |
| ------------------------------------ | --------------------------------------------------- | --------------------------------------------------------------- | -------------------------------------------- |
| create → agent_owned                 | ledger commit and lease grant                       | create logical space and optional agent page                    | idempotency returns same space               |
| agent_owned → paused                 | owner lease                                         | reject new mutations, cancel queued work, keep reads per policy | repeat is idempotent                         |
| agent_owned → user_owned             | explicit user takeover                              | fence epoch, stop new dispatch, retain pages                    | stale agent receives `user_control_required` |
| handoff_requested → agent_owned(new) | drain complete and recipient accepts                | increment epoch and ledger                                      | old epoch denied                             |
| loss → orphaned                      | host/bridge/browser unavailable                     | in-flight post-dispatch actions become unknown                  | no replay                                    |
| orphaned → recovering                | explicit claimant and matching profile/broker proof | reconcile live tabs, no side-effect replay                      | ambiguity remains orphaned                   |
| finished → released                  | explicit retention/cleanup policy                   | close only proven claimed pages                                 | user/unmanaged page preserved                |

### Page lifecycle

```text
planned -> creating -> managed
managed -> target_lost -> rebinding -> managed
managed -> user_owned -> managed only after explicit return
managed -> closing -> closed -> retired
unknown/unmanaged -> adoptable -> managed only by explicit claim/confirmation
```

A page record contains durable `page_id`, label, canonical `space_id`, ownership, lifecycle, browser-profile binding, tab-generation hint, navigation/document/frame generations, last known URL/title, and retention. Chrome `tabId` and debugger session IDs are not authoritative.

Page binding states are `unbound`, `bound`, `lost`, `ambiguous`, `rebind_required`, `user_owned`, and `closed`. After browser restart, extension reinstall, profile mismatch, copied-profile detection, or ledger mismatch, generations are invalidated; URL/title/visual-group matching alone never rebinds or closes a page. Ambiguous matches remain unmanaged until explicit user-approved rebind and a fresh lease.

### Action lifecycle

```text
queued -> rejected
queued -> running
running -> succeeded
running -> failed(retryable|terminal)
running -> cancelled
running -> unknown
unknown -> reconciled(succeeded|failed|requires_confirmation)
```

Every mutation validates principal/space/lease at enqueue, dequeue, and immediately before extension dispatch. The host then sends an extension fence barrier on takeover: it increments the epoch, stops old-epoch issuance, requires the extension to reject lower-epoch commands at execution time and acknowledge its queues are drained/rejected, and classifies dispatched-but-unacknowledged mutations as `unknown`. Lost responses after dispatch are `unknown`; click, input, navigation, upload, storage, cookie, evaluate, and close are never blindly replayed.

Takeover is a durable state machine: `agent_owned -> fence_pending -> fence_dispatched -> fence_acknowledged -> user_owned`. If the epoch increment or fence acknowledgement is lost, the space remains `fence_pending`/paused; no new agent or user mutation is admitted until reconciliation proves the extension's highest accepted lease epoch and lower-epoch commands are rejected. Fence commands are idempotent and carry a fence ID, broker/connection epochs, lease epoch, and request identity.

### Runtime epochs

The protocol distinguishes `broker_epoch`, `connection_epoch`, `browser_session_epoch`, and `worker_instance_epoch`. A broker restart changes the broker epoch; every Native Messaging connection gets a new connection epoch; a real Chrome/profile restart changes the browser-session epoch; every MV3 service-worker start changes only the worker-instance epoch. Worker restart rehydrates from host state and preserves the browser-session epoch. Browser-session changes invalidate target/session/frame/document bindings and require reconciliation; tab ID, URL, title, label, or tab-group membership never proves rebinding. Old epochs are rejected before mutation dispatch.

### Extension/host connection lifecycle

```text
disconnected -> handshaking -> connected
connected -> draining -> disconnected
connected -> bridge_lost -> reconnecting -> connected
handshaking -> rejected on origin/version/profile/nonce/schema failure
```

The host may accept an extension connection only after exact extension ID, profile instance, protocol version, and nonce/sequence validation. The extension must not infer authority from a successful Chrome API call; the host lease is required for each mutation.

## Security invariants

1. A local socket/named pipe verifies OS peer identity and restrictive endpoint permissions; client-supplied principal names are metadata, not authentication. The threat model trusts same-OS-user local processes and does not claim protection from same-user malware.
2. Native host validates Chrome's caller origin from Native Messaging transport metadata, exact extension ID, message length, JSON schema, protocol version, nonce/sequence, broker instance epoch, and capability. The origin is never accepted from an extension JSON field.
3. Extension messages are untrusted input; content scripts cannot directly call Native Messaging; page `postMessage` requires a unique channel nonce and schema validation. Host-to-extension commands carry broker instance epoch and lease epoch; stale commands are rejected at execution time.
4. Page content, labels, URLs, network bodies, cookies, and screenshots are data, not instructions; prompt-injection defenses are documented in the client skill.
5. `browser_evaluate`, cookies, storage writes, downloads, and uploads are mutation-capable and require policy/lease checks.
6. No command can target a raw tab/target ID from the public protocol.
7. No close/release path acts on a page without a broker ownership proof, a fresh live-tab/generation proof, and the required single-use user-intent ticket. Host stop, crash, update, uninstall, and ambiguous rebind retain pages and mark them paused/orphaned/release-eligible; they never perform cleanup implicitly.
8. User takeover fences old epochs before returning control; a stale client cannot renew or dispatch.
9. Ledgers contain no secrets or full page contents; action journal entries are minimal and redacted.
10. Host/browser/extension version mismatch fails before mutation authority is granted.

## Profile guarantee matrix

| Data/behavior                 | Existing-Chrome task spaces                 | Product rule                           |
| ----------------------------- | ------------------------------------------- | -------------------------------------- |
| Cookies/login                 | shared profile state                        | disclose; no per-space isolation claim |
| local/session storage         | shared by origin/profile as Chrome dictates | do not use as a secret boundary        |
| installed extensions          | shared                                      | capability and prompt risks documented |
| history/bookmarks/permissions | shared                                      | user-owned; agent APIs are scoped      |
| downloads/files               | profile/filesystem capability               | explicit path/policy and confirmation  |
| user tabs                     | unmanaged by default                        | no auto-adopt/close                    |
| agent pages                   | claimed by space after creation             | scoped cleanup only                    |
| Chrome tab group              | visual mapping only                         | never authorization                    |

## Module/dependency map

```text
agentyc-core
  <- agentyc-host, agentyc-runtime, agentyc-mcp, packages/agentyc-browser, agentyc CLI
agentyc-host
  <- agentyc-runtime, agentyc-browser legacy adapter, agentyc-dom/tools, local protocol
extension/
  <- Native Messaging host contract; no Rust dependency at runtime
packages/agentyc-browser / CLI
  <- local protocol + core schemas
agentyc-mcp
  <- SDK/host client + core; no direct CDP authority
agentyc-browser
  <- agentyc-cdp; legacy/test only during migration
```

## Tasks

- [x] P1-T1 — Publish the component and ownership record.
  - **Files:** this phase, `docs/architecture.md`, new `docs/architecture-existing-chrome.md`, `plans/PLAN_INDEX.md`.
  - **Done when:** every state/flow has one owner; `space`/`space_id` is canonical; MCP, CLI, SDK, extension, and host dependencies are explicit; no raw Chrome state escapes the host/extension adapter; the phase registry has one executable file per phase.
  - **Validation:** `python3 scripts/check_exec_plan.py docs/exec-plans/active/agentyc-browser-task-spaces` (new deterministic checker); `rg -n "CdpClient|BrowserRuntime|active_page" crates/agentyc-mcp crates/agentyc/src` with only legacy allowlisted paths; architecture artifact `artifacts/p1-architecture-review.md`.
  - **Owner:** Japneet Kalkat.

- [x] P1-T2 — Freeze domain IDs, generations, and public presentation rules.
  - **Files:** planned `crates/agentyc-core/src/{ids.rs,records.rs,states.rs}`; output contract examples; `scripts/check_core_contracts.py`.
  - **Done when:** space/page/frame/document/navigation/snapshot/ref/action/event identities and no-raw-ID rules are normative; the checker is deterministic and owned by this task.
  - **Validation:** `python3 scripts/check_core_contracts.py --negative-identity-fixtures`; schema review and negative examples for `[id] name`, `tabId`, `targetId`, `sessionId`.
  - **Owner:** Japneet Kalkat.

- [x] P1-T3 — Freeze lifecycle, lease, handoff, takeover, and recovery transitions.
  - **Files:** planned `crates/agentyc-core/src/{spaces.rs,leases.rs,actions.rs,pages.rs}`; `artifacts/p1-state-machines.md`; `scripts/check_state_machines.py`.
  - **Done when:** each space/page/lease/action/binding state has exactly one canonical owner, transition initiator, guard, side effect, durable record, duplicate behavior, error, and user-visible result; no side panel or extension can directly persist authoritative transitions; the checker is deterministic and owned by this task.
  - **Validation:** `python3 scripts/check_state_machines.py --artifact artifacts/p1-state-machines.md`; transition table covers stale epoch, duplicate claim, disconnect, extension restart, user takeover, extension fence barrier, page close, browser restart, rebind-required, and broker restart.
  - **Owner:** Japneet Kalkat.

- [x] P1-T4 — Freeze extension capability and permission policy.
  - **Files:** `extension/manifest.json` design, `docs/security/extension-permissions.md`, capability matrix from Phase 0, `scripts/check_extension_permissions.py`.
  - **Done when:** required/optional permissions, host access, debugger domains, content-script worlds, evaluate policy, cookies/storage/download/upload policy, and unsupported responses are explicit; `debugger` is never modeled as an optional permission; live grants, revocations, enterprise policy denial, incognito scope, restricted URLs, screenshot/DLP denial, and user-gesture requirements have typed behavior; the checker is deterministic and owned by this task.
  - **Validation:** `python3 scripts/check_extension_permissions.py --matrix artifacts/p0-capabilities.json`; security review against S-019–S-024; denial, revocation, policy, incognito, restricted-page, and mid-action permission tests pass; no permission lacks a user-visible reason and test.
  - **Owner:** Japneet Kalkat.

- [x] P1-T5 — Freeze host trust, local IPC, Native Messaging, and version policy.
  - **Files:** planned `crates/agentyc-core/src/protocol.rs`, `crates/agentyc-host/src/{security.rs,version.rs}`, `docs/security/host-protocol.md`, `scripts/check_host_protocol.py`.
  - **Done when:** framing, OS-peer admission, exact transport-origin handling, handshake, authentication boundary, nonce/sequence, broker epoch, cumulative size limits, cancellation, events, reconnect, host lock, version compatibility, and artifact transfer are specified; remote TCP is disabled unless a future decision explicitly enables it; the checker is deterministic and owned by this task.
  - **Validation:** `python3 scripts/check_host_protocol.py --artifact artifacts/p1-host-trust.md`; protocol threat model and failure matrix include forged origin, direct native-binary execution, symlink endpoint, replay, reconnect sequence reset, chunk flood, and same-user threat limits.
  - **Owner:** Japneet Kalkat.

- [x] P1-T6 — Freeze persistence/reconciliation and migration policy.
  - **Files:** planned `crates/agentyc-host/src/ledger.rs`, state-directory schema, `docs/configuration.md`, `scripts/check_recovery_matrix.py`.
  - **Done when:** logical records/action status survive client reconnect; browser/extension restart invalidates authority as needed; target hints are non-authoritative; corrupt/incompatible ledgers quarantine/fail closed; stop/crash/update/uninstall retain pages rather than implicitly closing them; the checker is deterministic and owned by this task.
  - **Validation:** `python3 scripts/check_recovery_matrix.py --artifact artifacts/p1-recovery.md`; recovery matrix covers partial writes, host crash, Chrome restart, profile mismatch, extension update, old binary, two-phase create/claim crash, and cleanup confirmation.
  - **Owner:** Japneet Kalkat.

- [x] P1-T7 — Freeze performance/context/reliability gates.
  - **Files:** README metrics, `docs/release-gate.md`, Phase 0 baseline addendum, `scripts/check_release_gate.py`.
  - **Done when:** end-to-end first action, batch round trips, snapshot scans/tokens, event lag, action deadlines, host/Chrome RSS, and user responsiveness have exact measurement methods and owners; the checker is deterministic and owned by this task.
  - **Validation:** `python3 scripts/check_release_gate.py --phase 0`; sign thresholds or explicitly carry a provisional threshold to Phase 7 direct-launch validation.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [x] Host/extension/client/MCP ownership is explicit.
- [x] Profile sharing is not described as isolation.
- [x] User-control states and raw-ID prohibition are contract-level invariants.
- [x] Every mutation has a lease/epoch and policy check.
- [x] Extension worker restart and Native Messaging reconnect are state-machine paths with distinct worker, browser, connection, and broker epochs.
- [x] Takeover cannot complete without a durable fence acknowledgement; missing acknowledgement fails closed.
- [x] Security, privacy, and distribution are architecture constraints, not follow-up notes.

## Handoff out

- **Artifacts:** component map, state machines, trust model, permission matrix, profile guarantees, persistence/recovery policy, module map, signed gates.
- **Next phase:** Phase 2 defines exact `agentyc-core`, local protocol, host, SDK, and MCP-adapter schemas.
- **Residuals:** implementation-specific API errors or platform installer details only; no primary architecture choice remains.

## Exit gate

Advance only when every task/checklist item is checked, every state transition and boundary has an owner/error path, the profile/permission guarantees are explicit, and Phase 2 can freeze schemas without deciding the architecture again.
