---
phase: 4
name: Chrome extension, Native Messaging bridge, and task-space UI
status: active
owner: Japneet Kalkat
primary_outcome: A production-shaped MV3 extension connects the host broker to existing Chrome tabs, maps agent pages to visual tab groups, routes debugger/content events, and exposes safe task-space user control.
depends_on: phase-3
---

# Phase 4 — Chrome extension and task-space UI

## Objective

Implement the browser-side half of the new product. The extension must work in the user's existing Chrome, survive MV3 worker restarts, keep the host authoritative, never expose raw browser IDs in user/agent output, and make space ownership/takeover/finish visible without taking focus unexpectedly.

## Handoff in

- **Inputs:** Phase 0 Chrome matrix; Phase 1 permission/profile model; Phase 2 Native Messaging contract; Phase 3 host/ChromeBridge interfaces.
- **Must already be true:** one host broker/ledger exists, can receive an extension handshake, and Phase 3's blocking U3-1 host-daemon/shim topology is closed.
- **Do not reopen:** Native Messaging is a bridge only; task spaces are logical; Chrome tab groups are visual; no auto-adoption; no global close; no default launch/download.

## Confirmed facts

- MV3 service workers can terminate; authoritative state cannot live in worker globals (S-021).
- `chrome.debugger` attaches to tab targets, emits `onEvent`, and detaches when tabs close or DevTools opens (S-019).
- Flat related sessions and recursive OOPIF attachment need Chrome 125+ behavior (S-019).
- Content scripts are isolated and relay privileged messages through extension contexts (S-022).
- Tabs/tab groups and side panels provide the required browser-visible surfaces (S-023).

## Working assumptions

- Production extension uses a stable Web Store/managed ID; development uses a separate unpacked ID and host manifest.
- Required/optional permissions are not assumed: Phase 0/1 freeze the exact need, host match patterns, user-visible reasons, enterprise-policy behavior, and incognito policy for `debugger`, `nativeMessaging`, `storage`, `tabs`, `tabGroups`, `scripting`, and `sidePanel`. `<all_urls>`, cookies, downloads, file access, upload, screenshots, and incognito are not silently broadened.
- `chrome.storage.local/session` stores only profile instance/reconnect/UI metadata; host owns leases/ledger/action status.
- Agent-created tabs use an approved target window and `chrome.tabs.create({active:false})`; the extension verifies non-focus behavior. Agent commands never activate/highlight/focus a tab or open the side panel; side-panel opening requires an explicit user action.

## Decisions and remaining work

- **U4-1 — Decision:** target Chrome Web Store signing and updates. Production signing-key custody, Store listing/submission, and end-to-end update proof remain separate release work; the checked-in unpacked development identity is not a production distribution.
- **U4-2 — Resolved:** snapshot reads on restricted URLs fail closed with `capability_unavailable` before content-bridge or debugger collection; no fallback is attempted.
- **Limited live evidence (2026-10-06):** the existing-profile MCP/Native Messaging path completed an epoch-4 takeover and inactive managed-page rebind without focusing or closing the active unmanaged tab. Snapshot read returned `unknown_outcome` during debugger attachment; the prior navigation remains unknown and was not replayed. Side-panel, OOPIF, browser-restart, enterprise-policy, and production-distribution gates remain open.

## Scope

### In scope

- `extension/manifest.json` and build/package metadata.
- Service worker/native messaging client, debugger adapter, tabs/group/frame registry, content/page bridge, side panel UI.
- Host handshake/reconnect and capability reporting.
- Tab/group event reconciliation and explicit adoption/cleanup flows.
- User-control indicators and confirmation boundaries.
- Extension unit/fixture/e2e tests.

### Out of scope

- Snapshot delta/ref engine and actionability (Phase 5).
- Primary CLI/SDK packaging (Phase 6).
- MCP adapter migration (Phase 8).
- Site-specific logic or unrestricted page evaluation.

## Extension module map

```text
extension/
  manifest.json
  package.json / lockfile
  src/
    service-worker.ts
    native-messaging.ts
    protocol.ts
    debugger-bridge.ts
    tabs-registry.ts
    groups.ts
    frames.ts
    content-script.ts
    page-bridge.ts
    sidepanel/
      index.html
      app.ts
      state.ts
      controls.ts
      styles.css
  tests/
    protocol.test.ts
    worker-restart.test.ts
    debugger.test.ts
    tabs-groups.test.ts
    sidepanel.test.ts
    e2e/
```

## Browser-side invariants

1. Every Chrome `tabId`, debugger `targetId`, session ID, frame ID, and extension group ID is internal and scoped to a live browser session.
2. The service worker maps live IDs to host-provided logical page/space records but cannot grant ownership.
3. A host-issued command includes `connection_id`, `space_id`, `page_id`, lease epoch, command ID, and expected generation; the extension rejects mismatches.
4. `onDetach`, `tabs.onRemoved`, `tabs.onReplaced`, profile changes, and debugger errors invalidate the affected generation and notify the host; DevTools detach is user interference, not an automatic reattach/retry trigger; no mutation is replayed automatically.
5. Tab-group creation/update is best-effort presentation; failure does not create or destroy a logical space.
6. User-created/unknown tabs are inventory-only until explicit adoption; release removes only broker-claimed tabs.
7. Side-panel controls call host transitions and display the resulting state; the panel cannot mutate browser state directly without a host-approved command. User actions produce a single-use, expiring intent ticket bound to profile binding, space/page, document generation, action hash, lease epoch, and the side-panel connection; payload booleans cannot claim user authority.
8. Content/page messages require a per-document nonce, strict schema, origin/source checks, and bounded payloads.
9. Logs omit page bodies, cookies, headers, screenshots, and raw IDs by default.
10. Host takeover sends a fence barrier with broker and lease epochs; the extension rejects lower-epoch commands at execution time and acknowledges queued-command drain/rejection before user/new-agent control is active.
11. Reconnect, worker restart, extension update, Chrome restart, and host restart reconstruct live registries from host state and Chrome inventory; no pending mutation is persisted for replay.
12. Cleanup operates on a fresh tab/generation proof, never a stored tab ID alone; mixed user/agent Chrome groups are never cleanup units.

## Tasks

- [ ] P4-T1 — Add the MV3 manifest and build/package contract.
  - **Files:** `extension/manifest.json`, `extension/package.json`, lockfile/build config, `docs/installation.md`.
  - **Done when:** minimum Chrome version, exact permissions/host match patterns, side-panel entry, service worker, content scripts, CSP, development/production IDs, Native Messaging host name, enterprise-policy denial behavior, and incognito policy are explicit. `sidePanel.open()` is documented as user-action-gated.
  - **Validation:** `npm ci --prefix extension`; `npm test --prefix extension`; manifest lint; install unpacked in a clean test profile; permission review against S-019–S-024; reject undocumented `<all_urls>`/cookies/downloads/file/incognito additions.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T2 — Implement service-worker/native-host connection lifecycle.
  - **Files:** `extension/src/{service-worker.ts,native-messaging.ts,protocol.ts}`.
  - **Done when:** worker connects/reconnects, persists only profile/binding/reconnect metadata, validates transport origin/host hello/nonce/independent sequences/broker epoch, forwards requests/events, handles host crash/EOF, implements fence-barrier drain/reject acknowledgement, and never treats worker memory as authoritative or replays pending mutations.
  - **Validation:** `npm test --prefix extension -- --runInBand`; worker termination during every mutation, duplicate connect, host version mismatch, malformed/chunk-flood messages, backpressure, sequence reset, profile rebind, and reconnect; real Chrome host crash drill.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T3 — Implement debugger bridge and target/frame registry.
  - **Files:** `extension/src/{debugger-bridge.ts,frames.ts}`; ChromeBridge adapter in `crates/agentyc-host/src/chrome_bridge.rs`.
  - **Done when:** exact allowlisted domains/methods, Chrome floor, permission/policy failures, restricted URLs, DevTools conflicts, root `tabId`, child `sessionId`, frame/document mapping, and typed unsupported results are enforced; attach/detach/send/event, flat related sessions, recursive OOPIF attachment, execution-context mapping, target generation, and DevTools/tab-close handling are correct. Missing attribution never routes to an active page.
  - **Validation:** Chrome 125+ nested-frame/OOPIF fixtures, per-child auto-attach, DevTools detach without reattach/replay, tab replacement, target close, restricted URL, enterprise denial, browser restart, event ordering, and no blind replay tests.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T4 — Implement tabs, page claims, tab groups, and reconciliation.
  - **Files:** `extension/src/{tabs-registry.ts,groups.ts}`; host page reconciliation calls.
  - **Done when:** host-created pages specify an approved window and `active:false`, verify no focus theft, associate with visual tab groups best-effort, observe attach/detach/replace/move/discard/freeze/group removal events, and mark pages lost/ambiguous/rebind-required when identity is not provable; user tabs are never auto-adopted. Group title/color/collapse/group ID are presentation only, and mixed groups are not cleanup units.
  - **Validation:** create two spaces/pages; user opens/moves/closes tabs; cross-window attach/detach; tab replacement/discard/freeze; tab-group rename/collapse/move/removal; Chrome restart/reused tab ID; assert logical records and no unowned close.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T5 — Implement content-script/page bridge safely.
  - **Files:** `extension/src/{content-script.ts,page-bridge.ts}`.
  - **Done when:** the bridge supports only typed DOM/ARIA/event operations, rejects page-origin spoofing, preserves frame/document identity, does not expose Native Messaging to page scripts, and handles CSP/permission failure as a capability result.
  - **Validation:** isolated-world tests, hostile `postMessage`, prompt-injection fixture, cross-origin frame denial, rerender/document change, payload bounds, and no arbitrary string eval by default.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T6 — Build the side-panel task-space UI.
  - **Files:** `extension/src/sidepanel/*`, host event/client fixtures, `docs/user-control.md`.
  - **Done when:** the UI lists structured spaces/pages with labels/status/owner/capability warnings; supports create, pause, stop, take over, return control, handoff, finish, retain, and release; it never shows `[id] name` or raw IDs; confirmations create the bound single-use intent ticket required for adoption, login/payment/destructive actions, upload, cookies, and evaluate. The panel cannot silently rebind a profile or open itself from an agent command.
  - **Validation:** `npm test --prefix extension -- sidepanel`; headed workflow screenshots; keyboard/focus accessibility; takeover fence and return-control race tests; rebind/copy-profile confirmation tests.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T7 — Add extension observability and privacy redaction.
  - **Files:** extension logging/debug bundle adapter; `docs/security/logging.md`.
  - **Done when:** host/profile/space/page/action/sequence metadata is available without raw browser IDs or page content; logs distinguish worker restart, bridge loss, debugger detach, permission denial, and user takeover.
  - **Validation:** redaction fixtures; inspect generated bundle for cookies/tokens/headers/body/screenshot/raw-ID leakage.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [ ] Service-worker restart loses no authoritative lease/ledger state.
- [ ] Debugger detach is target loss/unknown, not an automatic replay or reattach trigger.
- [ ] Existing/user tabs are never silently claimed or closed.
- [ ] Tab groups are presentation only.
- [ ] Side-panel takeover is visible and fences host mutations.
- [ ] Content/page messages are treated as untrusted.
- [ ] No primary extension/host UI displays raw IDs or `[id] name`.
- [ ] Profile binding is explicit and cannot be forged with a profile UUID or payload role field.
- [ ] Stop, crash, update, uninstall, and ambiguous rebind retain pages; explicit cleanup uses fresh proof and confirmation.

## Handoff out

- **Artifacts:** installable extension build, host handshake integration, debugger/tabs/frame bridge, tab-group mapping, side panel, permission/privacy evidence.
- **Next phase:** Phase 5 connects the host runtime to compact snapshots, refs, waits, actionability, and typed automation outcomes.
- **Residuals:** exact unsupported capability fallbacks are carried into the runtime matrix; no fallback may broaden permissions silently.

## Exit gate

Advance only when the extension connects to a real existing Chrome, two spaces/pages coexist safely, the side panel can take over/return control, worker/host/browser restart behavior is explicit, and extension tests show no unauthorized tab mutation or raw identity leakage.
