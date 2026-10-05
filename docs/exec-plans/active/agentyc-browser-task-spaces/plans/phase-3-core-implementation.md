---
phase: 3
name: Host broker, ledger, and Chrome bridge core
status: complete
owner: Japneet Kalkat
primary_outcome: A persistent local `agentyc-host` broker with durable logical spaces/pages, leases, scheduling, reconciliation, and a transport-neutral Chrome bridge boundary; no default browser launch.
depends_on: phase-2
---

# Phase 3 — Host broker, ledger, and Chrome bridge core

> This completed plan preserves the Phase 3 implementation record. References to `agentyc-runtime`, the Rust `agentyc-browser` crate, and `agentyc-cdp` describe the phase-era design; these standalone direct-CDP runtime crates and CLI paths were later removed. The Node SDK package and extension `chrome.debugger` backend remain, and internal test harnesses may still use CDP.

## Objective

Implement the authoritative host control plane below all frontends. It must serve multiple agent processes for one Chrome profile, preserve logical task spaces across client reconnect, fence stale clients, and communicate through a replaceable Chrome bridge without allowing MCP/CLI modules to own raw browser state.

## Handoff in

- **Inputs:** `agentyc-core`; Phase 0 budgets/capability matrix; Phase 1 state/trust model; Phase 2 protocol fixtures.
- **Must already be true:** local/native envelopes and state transitions are frozen.
- **Do not reopen:** host broker is canonical; extension is default bridge; no automatic launch/download; BrowserContext is not the existing-Chrome guarantee; MCP is adapter-only.

## Confirmed facts

- Current `BrowserSession` has one active page and unsafe global `close_all`; it cannot remain authoritative.
- At Phase 3 planning time, `agentyc-cdp` was considered as a legacy/test transport behind the extension bridge; that Rust crate was later removed.
- Native Messaging host processes may reconnect or be started more than once; a host lock/forwarding design is required.
- Logical ledger records cannot replay browser side effects after Chrome/host loss.

## Working assumptions

- Host state directory defaults to `${AGENTYC_STATE_DIR:-~/.agentyc/state}` with one profile-scoped subdirectory.
- Host lock and endpoint metadata are owner-readable; crash recovery invalidates old broker/lease epochs.
- The first broker process can serve extension and local agent clients concurrently; all commands are serialized through one authority.
- The bridge can report browser/profile capabilities before granting a mutation lease.

## Unresolved questions

- **U3-1 — closed:** the macOS-first single-broker Native Messaging shim-forwarding topology is recorded in `docs/architecture-existing-chrome.md` and implemented by the owner lock, endpoint metadata, `NativeForwardServer`, `forward_stdio_to_owner`, and `BridgeRouter`. It keeps one broker and never grants authority from a client-supplied profile UUID.
- **U3-2:** cross-platform process supervision; owner: Japneet Kalkat; macOS first, explicit later platform evidence.

## Scope

### In scope

- `agentyc-host` crate and broker lifecycle.
- Logical space/page records, atomic ledger, action journal, reconciliation.
- Local IPC server/client and Native Messaging forwarding boundary.
- Principal/connection/lease/fencing manager.
- Per-space scheduler and browser-wide backpressure.
- Chrome bridge trait and extension transport adapter interface.
- Safe cleanup/release and explicit legacy direct-CDP adapter.
- In-process backend for unit/MCP tests.

### Out of scope

- MV3 extension implementation/UI (Phase 4).
- Snapshot/actionability/wait behavior (Phase 5).
- Primary CLI/SDK commands (Phase 6).
- MCP compatibility migration (Phase 8).
- Automatic browser launch or browser download.

## Planned surfaces

```text
crates/agentyc-host/
  Cargo.toml
  src/lib.rs
  src/host.rs
  src/broker.rs
  src/ipc.rs
  src/native_messaging.rs
  src/security.rs
  src/ledger.rs
  src/spaces.rs
  src/pages.rs
  src/leases.rs
  src/scheduler.rs
  src/actions.rs
  src/reconcile.rs
  src/chrome_bridge.rs
  src/artifacts.rs
  tests/...

crates/agentyc-core/ (from Phase 2)
crates/agentyc-runtime/src/lib.rs (facade delegates to host/core)
crates/agentyc-browser/src/session.rs (legacy/direct adapter only)
crates/agentyc-browser/src/{launcher.rs,profile.rs} (explicit legacy/test only)
```

## Host lifecycle

```text
stopped -> starting -> waiting_for_extension -> ready
ready -> draining -> stopped
ready -> degraded(extension_lost|chrome_lost|ledger_error)
degraded -> recovering -> ready | orphaned
```

- `host start` creates/validates the state directory, acquires a lock, writes endpoint metadata, starts local IPC, and waits for an extension/profile handshake; it never launches Chrome.
- If a Native Messaging shim is launched by Chrome, it forwards to the existing host or starts exactly one host under the lock; it never creates a second broker. The shim validates the transport-supplied exact origin and forwards bounded messages; it does not authenticate a profile from extension JSON.
- `host stop` rejects new mutations, drains/cancels queued work, marks post-dispatch work unknown, persists the ledger, and retains pages as paused/orphaned/release-eligible. It never closes pages implicitly, closes a whole Chrome tab group, or kills attached user Chrome; explicit cleanup requires current ownership, fresh live-tab/generation proof, and a single-use user-intent ticket.
- A new broker instance invalidates old lease epochs. Stored tab/debugger IDs are reconciliation hints only.

## Authorization matrix

| Operation                | Required authority                           | User/unmanaged page              | Error                                     |
| ------------------------ | -------------------------------------------- | -------------------------------- | ----------------------------------------- |
| list spaces/pages        | connection visibility                        | inventory metadata only          | `space_forbidden`/`unmanaged_page`        |
| snapshot/read            | page/space read grant and profile capability | denied unless explicitly adopted | `page_not_owned`/`capability_unavailable` |
| navigate/action/evaluate | active lease + current epoch + policy        | denied                           | `stale_lease`/`user_control_required`     |
| adopt page               | owner request + user confirmation + proof    | no implicit adoption             | `unmanaged_page`                          |
| pause/takeover           | owner/user control authority                 | user action wins                 | `user_control_required`                   |
| close/release            | owner/explicit user-approved cleanup         | never close                      | `page_not_owned`                          |

## Tasks

- [x] P3-T1 — Add `agentyc-host` crate and broker lifecycle.
  - **Files:** workspace `Cargo.toml`; new `crates/agentyc-host/Cargo.toml`; `src/{lib.rs,host.rs,broker.rs}`.
  - **Done when:** host starts/stops without Chrome launch, owns one broker lock, publishes endpoint metadata, handles clean/crash startup, and exposes an in-process backend for tests.
  - **Validation:** `cargo test -p agentyc-host --test host_lifecycle --locked`; duplicate host, stale lock, clean EOF, SIGTERM, and bounded shutdown tests.
  - **Owner:** Japneet Kalkat.

- [x] P3-T2 — Implement local IPC server, client admission, and connection registry.
  - **Files:** `crates/agentyc-host/src/ipc.rs`, `security.rs`, `crates/agentyc-core/src/protocol.rs`.
  - **Done when:** owner-readable Unix socket/named-pipe abstraction verifies OS peer credentials/security descriptor, protects endpoint metadata from symlink/path replacement, assigns connection/principal IDs (never accepts a client-supplied principal as auth), bounds frames, supports out-of-order responses/cancellation, binds to the broker instance epoch, and rejects remote/unauthenticated access by default.
  - **Validation:** fragmented/coalesced/malformed/oversized frames, two clients, out-of-order response, cancel, disconnect, permission, symlink replacement, peer mismatch, endpoint epoch mismatch, and protocol mismatch tests.
  - **Owner:** Japneet Kalkat.

- [x] P3-T3 — Implement Native Messaging forwarding and host lock coordination.
  - **Files:** `crates/agentyc-host/src/native_messaging.rs`, platform registration templates, `docs/installation.md`.
  - **Done when:** the Phase 0 evidence selects and implements the exact daemon/shim topology; Chrome-origin validation, bridge handshake, local endpoint forwarding, bounded chunk/artifact transfer, reconnect, and second-shim forwarding preserve one broker; the decision is recorded before Phase 4.
  - **Validation:** real Chrome probe plus `cargo test -p agentyc-host --test native_messaging --locked`; no raw secrets in logs.
  - **Owner:** Japneet Kalkat.

- [x] P3-T4 — Implement space/page registry and atomic ledger.
  - **Files:** `crates/agentyc-host/src/{spaces.rs,pages.rs,ledger.rs,reconcile.rs}`; `${AGENTYC_STATE_DIR}` schema.
  - **Done when:** canonical `space_id`/`page_id` records, lifecycle/retention, enrolled profile binding, broker instance, visual-group hint, page binding states, generations, action status, schema versions, atomic rename, restrictive permissions, corrupt quarantine, and browser/profile mismatch handling work. Page creation uses a two-phase create/claim record so a host crash cannot make an uncommitted tab appear safely owned.
  - **Validation:** restart, partial write, corruption, version mismatch, profile mismatch, copied-profile/rebind-required, page retirement, label collision, target replacement, stale tab-ID reuse, two-phase create/claim crash, and unknown-target recovery tests.
  - **Owner:** Japneet Kalkat.

- [x] P3-T5 — Implement leases, handoff, pause, takeover, and fencing.
  - **Files:** `crates/agentyc-host/src/leases.rs`, `spaces.rs`, `broker.rs` authorization path; extension fence contract.
  - **Done when:** all mutations validate principal/space/epoch at enqueue/dequeue/dispatch; takeover atomically increments the epoch, sends a broker/lease fence barrier, waits for extension drain/reject acknowledgement, then allows user-owned/new-agent state; handoff drains; pause cancels queued work; user-owned state blocks agent mutation; renew/expiry are bounded.
  - **Validation:** `cargo test -p agentyc-host --test lease_state_machine --locked`; concurrent claim/renew/handoff/takeover/expiry property tests with commands queued in host, Native Messaging, worker, debugger, and content-script paths.
  - **Owner:** Japneet Kalkat.

- [x] P3-T6 — Implement per-space scheduling and backpressure.
  - **Files:** `crates/agentyc-host/src/scheduler.rs`, `actions.rs`, cancellation registry.
  - **Done when:** same-space mutations preserve order, independent spaces progress, reads have bounded concurrency, queue overflow is typed, cancellation removes queued work, browser-wide caps are enforced.
  - **Validation:** deterministic fairness/backpressure tests; two-space throughput and queue-depth benchmark.
  - **Owner:** Japneet Kalkat.

- [x] P3-T7 — Add transport-neutral ChromeBridge and legacy direct-CDP adapter.
  - **Files:** `crates/agentyc-host/src/chrome_bridge.rs`; `crates/agentyc-runtime/src/lib.rs`; `crates/agentyc-browser/src/session.rs`; existing `crates/agentyc-cdp/src/{client.rs,target_registry.rs}` only where present/required; do not invent a target registry path without first adding it.
  - **Done when:** host commands use a bridge interface with logical page resolution, capability reporting, target/frame/document generations, event delivery, and safe close; extension bridge is the default implementation slot; direct CDP is explicitly labeled legacy/test and cannot be selected by extension failure.
  - **Validation:** fake bridge tests for target replacement, missing events, disconnect, capability denial, stale/reused tab identity, retained-on-stop cleanup, and no-global-close; `rg` proves MCP/CLI do not bypass the bridge.
  - **Owner:** Japneet Kalkat.

- [x] P3-T8 — Implement action journal and reconciliation records.
  - **Files:** `crates/agentyc-host/src/actions.rs`, `ledger.rs`, `reconcile.rs`.
  - **Done when:** request/idempotency hashes prevent conflicting duplicates, unknown actions survive client reconnect, status lookup is bounded, and reconciliation records proof without replaying mutations.
  - **Validation:** disconnect-after-send for navigation/click/form/close; duplicate key same/different hash; host restart and stale epoch tests.
  - **Owner:** Japneet Kalkat.

- [x] P3-T9 — Migrate runtime facade without changing defaults.
  - **Files:** `crates/agentyc-runtime/src/lib.rs`; new `crates/agentyc-runtime/src/host_client.rs`; legacy browser session calls.
  - **Done when:** new host client path is selectable by tests/feature flag, old direct behavior remains explicit compatibility, and no default path launches a browser.
  - **Validation:** existing workspace tests plus host-backed in-process tests; no production default flip until Phase 6.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [x] One host lock/broker per profile instance.
- [x] No browser process launch/download in the new host path.
- [x] Ledger stores logical state and action metadata only.
- [x] All mutation paths check lease epoch three times.
- [x] User/unmanaged pages cannot be closed by release, stop, crash recovery, update, or uninstall.
- [x] Profile binding/rebinding and user-intent tickets are explicit security gates.
- [x] Direct CDP and `BrowserSession` are behind explicit legacy/test boundaries.
- [x] Host crash/extension loss/Chrome loss classify work correctly.

## Handoff out

- **Artifacts:** host binary/library, local IPC, Native Messaging forwarding, ledger, broker, leases, scheduler, ChromeBridge trait, action journal, tests.
- **Next phase:** Phase 4 implements the actual MV3 extension, debugger/tabs bridge, tab-group mapping, and side panel.
- **Residuals:** no real Chrome automation behavior is claimed until extension implementation and Phase 0/4/7 tests pass.

## Exit gate

Advance only when host/local protocol/ledger/lease tests pass, U3-1 is closed, two independent clients share one broker without cross-space mutation, crash/reconnect behavior is bounded, and no new path launches/downloads a browser or bypasses ChromeBridge ownership. Deterministic Phase 3 exit evidence is recorded in `artifacts/p3-core-review.md`; live existing-profile Chrome remains Phase 4 evidence.
