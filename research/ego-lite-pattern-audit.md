# ego-lite Pattern Audit

Date: 2026-10-03

This audit compares the checked-in `reference/ego-lite-main` ego-browser API and lifecycle guidance with agentyc's existing-Chrome MCP/host architecture. The reference is used for behavior patterns only; its browser host, profile model, and native implementation are not copied.

## Compatible patterns carried forward

| ego-lite pattern | agentyc contract | Evidence |
| --- | --- | --- |
| One durable task space per user goal | Host-owned opaque `space_id`; agents must reuse a space rather than create recovery spaces | `docs/architecture-existing-chrome.md`, `crates/agentyc-host/src/broker.rs` |
| Stable labeled pages (`p1`, `p2`, ...) | Host-owned `page_id` plus durable user label; SDK `TaskSpace.page(label)` is lazy | `crates/agentyc-core/src/records.rs`, `packages/agentyc-browser/src/space.mjs` |
| Separate task ownership from browser identity | Host ledger owns space/page authority; Chrome tab/target IDs stay inside the extension | `docs/architecture-existing-chrome.md`, `extension/src/tabs-registry.mjs` |
| User-created or unknown-origin tabs are protected | Startup inventory is unmanaged; adoption requires a short-lived host proof and user intent ticket | `extension/src/tabs-registry.mjs` |
| `release()` relinquishes management without closing | Page/space release is host-authorized and cleanup never globally closes user tabs | `crates/agentyc-host/src/broker.rs` |
| `finish({ keep })` retains selected result pages | The current host lifecycle has scoped finish/release and retention policy; selected-page retention remains a Phase 1/SDK contract item and is not falsely exposed as complete in Phase 0 | `docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-6-direct-cli-sdk.md` |
| Explicit user handoff and takeover | Durable fence sequence, epoch increment, stale-command rejection, and explicit return/takeover paths | `crates/agentyc-host/src/broker.rs`, `extension/src/service-worker.mjs` |
| Reuse pages and enforce a page budget | Ledger bounds pages per space and queued actions per space | `crates/agentyc-host/src/ledger.rs` |
| Event isolation | Events are scoped by logical space/page and resumed with broker-epoch cursors | `crates/agentyc-core/src/events.rs`, `docs/api-local.md` |
| Focus/keyboard safety | Agent-created tabs request `active: false`; focus theft is rejected; debugger input checks user control | `extension/src/tabs-registry.mjs`, `extension/src/debugger-bridge.mjs` |
| Visual tab grouping | One best-effort Chrome group per space/window; group IDs are redacted hints and never authorization, isolation, adoption, or cleanup boundaries | `extension/src/groups.mjs`, `docs/architecture-existing-chrome.md` |

## Deliberate differences

- agentyc controls the user's already-running Chrome through an MV3 extension and Chrome Native Messaging. It does not install a second browser, create an isolated ego-lite profile, or attach to arbitrary CDP endpoints.
- Chrome tab groups are presentation only, scoped to a window and session. A user may rename, regroup, move, collapse, or delete them without changing logical ownership.
- The public agentyc API exposes logical spaces/pages and bounded records, not raw tab, target, debugger, session, window, or group IDs.
- A lost mutation response is `unknown` and must be reconciled; raw browser commands are never replayed.

## Remaining boundary

Selected-page retention equivalent to ego-lite's `finish({ keep })` is intentionally not claimed as a Phase 0 implementation. Current cleanup closes only proven agent-owned pages, and current retention policies govern space-level lifecycle. Phase 1/6 must define and test a host-authorized keep set that transitions retained pages to user ownership without allowing stale agent mutation or group-based cleanup.

This is not a Phase 0 blocker. The Phase 0 blocker remains live existing-profile coexistence evidence: two independently enrolled agents/spaces, ten required scenarios, zero user-tab closes, zero focus theft, zero cross-space mutations, and zero stale-agent mutations after takeover.
