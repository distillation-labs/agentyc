---
phase: 4
name: Host-owned CDP and minimal tab-creation extension
status: active
owner: Japneet Kalkat
primary_outcome: The host owns browser control through loopback CDP; the MV3 extension is a minimal Native Messaging worker used only to create tabs, with no task-space UI.
depends_on: phase-3
---

# Phase 4 — Host-owned CDP and minimal tab-creation extension

> **Current scope:** The earlier side-panel, content-script, and extension-debugger design remains superseded. The extension creates tabs and presents each agent space in a required visual Chrome tab group; all page navigation, snapshots, actions, waits, and lifecycle operations belong to the host. The user launches the dedicated Chrome profile. There is no extension popup or side panel, and clicking the extension icon does nothing. Group IDs remain internal presentation state, never authorization or cleanup authority.

## Current acceptance criteria

- The host connects only to the user-launched profile's loopback CDP endpoint and owns navigation, snapshots, actions, waits, and lifecycle operations.
- The extension authenticates to the host through Native Messaging and performs only requested tab creation and required tab-group presentation.
- The extension manifest exposes no popup, side panel, content scripts, debugger permission, or tab-inventory capability; it declares `tabGroups`; clicking the extension icon has no effect.
- Deterministic tests prove that no extension route can perform browser control beyond creating/grouping a managed tab, and that CDP endpoints outside loopback are rejected.
- Dedicated-profile Chrome E2E and store-distribution/update proof remain release gates. Host/browser restart recovery and cross-origin frame behavior are release gates only when those capabilities are claimed; deterministic tests do not substitute for any required live evidence.

## Superseded historical design and checklists

The sections below preserve the original extension-debugger, tab-group, content-script, and side-panel proposal for historical context. They are not current requirements and do not override the acceptance criteria above or the current exit gates at the end of this plan. In particular, do not implement or validate a side panel, extension debugger, extension-owned OOPIF handling, or tab inventory for this redesign.

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

## Current decisions and live evidence

- **U4-1 — Decision:** target Chrome Web Store signing and updates. Production signing-key custody, Store listing/submission, and end-to-end update proof remain separate release work; the checked-in unpacked development identity is not a production distribution.
- **U4-2 — Superseded:** the earlier extension bridge failed closed on restricted URLs before content-bridge or debugger collection. That result does not establish restricted-target behavior for the host-CDP implementation.
- **Limited live evidence (2026-10-06):** the existing-profile MCP/Native Messaging path completed an epoch-4 takeover and inactive managed-page rebind without focus theft. A navigation remained `unknown_outcome` and was not replayed; snapshot reads remained blocked with `unknown_outcome` and produced no logical refs. The run also recorded one unmanaged-tab close with an unattributed actor, so tab-cleanup safety is unresolved. It predates the required-group contract and does not satisfy the current-profile E2E, recovery, or distribution gates.
- **Policy scope (2026-10-06):** managed-policy evidence is explicitly deferred by the user and is not a blocker for this Phase 4 scope.
- **Disposable MCP/extension E2E (2026-10-06):** an isolated Chrome profile passed extension load, native-host connection, task-space/page creation, MCP snapshot read with a snapshot hash, and MCP close-action receipt with the page subsequently observed as `target_lost`. It produced no logical element refs and does not establish dedicated-profile snapshot/ref or tab-safety proof, conditional host/browser restart or cross-origin-frame behavior, or store-distribution evidence; managed-policy evidence is deferred.

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
5. Every managed page must be presented in its space's visual group. Group failure returns a typed error while retaining the managed page for explicit recovery; it never closes the page or changes authorization.
6. User-created/unknown tabs are inventory-only until explicit adoption; release removes only broker-claimed tabs.
7. Side-panel controls call host transitions and display the resulting state; the panel cannot mutate browser state directly without a host-approved command. User actions produce a single-use, expiring intent ticket bound to profile binding, space/page, document generation, action hash, lease epoch, and the side-panel connection; payload booleans cannot claim user authority.
8. Content/page messages require a per-document nonce, strict schema, origin/source checks, and bounded payloads.
9. Logs omit page bodies, cookies, headers, screenshots, and raw IDs by default.
10. Host takeover sends a fence barrier with broker and lease epochs; the extension rejects lower-epoch commands at execution time and acknowledges queued-command drain/rejection before user/new-agent control is active.
11. Reconnect, worker restart, extension update, Chrome restart, and host restart reconstruct live registries from host state and Chrome inventory; no pending mutation is persisted for replay.
12. Cleanup operates on a fresh tab/generation proof, never a stored tab ID alone; mixed user/agent Chrome groups are never cleanup units.

## Tasks

- [x] P4-T1 — Freeze the required tab-group boundary.
  - **Files:** `extension/manifest.json`, `extension/src/tab-creation-worker.mjs`, `extension/src/groups.mjs`, manifest/worker tests, permission docs/checkers.
  - **Done when:** the extension declares `nativeMessaging`, `storage`, and `tabGroups`; it exposes only `tab.create` and `group.present`; it never returns raw tab/group IDs; group failure is typed and retains the page.
  - **Validation:** extension manifest, worker, grouping, and permission tests.
  - **Owner:** Japneet Kalkat.

- [x] P4-T2 — Connect host CDP page creation to grouping.
  - **Files:** `crates/agentyc-host/src/{cdp.rs,native_messaging.rs,bridge.rs,broker.rs}`.
  - **Done when:** host-created pages are inactive, grouped by logical space using the space label, and grouping failure returns an error without closing or unowning the managed page.
  - **Validation:** bridge wire tests, managed-page success/failure-retention tests, and no-raw-ID assertions.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T3 — Validate required groups in headed Chrome.
  - **Files:** host/extension integration tests, `scripts/run_phase4_live_probe.py`, Phase 4 artifacts.
  - **Done when:** two spaces create distinct groups, multiple pages in one space share its group, pages remain inactive, user tabs remain untouched, and group drift never changes authorization.
  - **Validation:** dedicated-profile run covering group creation, group removal/rename/move, reconnect, restart, and cleanup.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T4 — Resolve current live unknown outcomes and logical refs.
  - **Files:** host CDP snapshot/ref path and Phase 4 live artifacts.
  - **Done when:** current-build validation produces logical refs, resolves unknown navigation/snapshot outcomes without replay, and records unsupported frame/recovery behavior explicitly.
  - **Validation:** repeatable redacted live run with build tuple, refs, receipts, cleanup, and recovery evidence.
  - **Owner:** Japneet Kalkat.

- [ ] P4-T5 — Complete distribution evidence and documentation closure.
  - **Files:** Phase 4 plan, architecture/security docs, checkers, installation/update artifacts.
  - **Done when:** required groups, failure retention, visual-only group authorization, and current live claims are consistent; signing/update/install/uninstall evidence is recorded; managed-policy evidence remains explicitly deferred.
  - **Validation:** all Phase 4 static checkers and required live evidence pass.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [ ] Service-worker restart loses no authoritative lease/ledger state.
- [ ] Existing/user tabs are never silently claimed or closed.
- [ ] Required groups are presentation only and never authorization, isolation, adoption, or cleanup units.
- [ ] Group creation/update failure returns a typed error and retains the managed page.
- [ ] No primary extension/host output displays raw IDs or `[id] name`.
- [ ] Profile binding is explicit and cannot be forged with a profile UUID or payload role field.
- [ ] Stop, crash, update, uninstall, and ambiguous rebind retain pages; explicit cleanup uses fresh proof and confirmation.

## Handoff out

- **Artifacts:** installable extension build, host handshake integration, host-CDP/tab-creation/group bridge, required-group mapping, permission/privacy evidence, and headed Chrome evidence.
- **Next phase:** Phase 5 connects the host runtime to compact snapshots, refs, waits, actionability, and typed automation outcomes.
- **Residuals:** exact unsupported capability fallbacks are carried into the runtime matrix; no fallback may broaden permissions silently.

## Current Phase 4 exit gates

Phase 4 remains active and release-ineligible until the release evidence gate passes. Do not use completion of deterministic implementation work as a claim of live validation or production readiness.

### Redesigned implementation gate

- Host browser-control operations use only the user's loopback CDP endpoint; the extension performs only requested tab creation and required tab-group presentation.
- The extension has no popup, side panel, content scripts, debugger permission, or tab-inventory route; it declares `tabGroups` and clicking its icon has no effect.
- Deterministic tests and the Phase 4 evidence checker enforce these boundaries, required grouping, failure retention, and non-loopback CDP rejection.
- Documentation and evidence artifacts reflect the redesigned architecture and distinguish verified outcomes from nonclaims.

### Release evidence gate (open)

- Run current-build end-to-end validation in the user's dedicated Chrome profile, including required per-space groups, a snapshot with logical refs, and safe page lifecycle behavior.
- Resolve the existing-profile `unknown_outcome` snapshot/navigation results and the unattributed unmanaged-tab close; preserve no-replay and no-unowned-close guarantees.
- Record host/Chrome restart-recovery and cross-origin frame/OOPIF behavior only where those behaviors are part of the host-owned CDP design; deterministic tests do not replace required live evidence.
- Provide real Chrome Web Store listing, signing, and update/distribution evidence.
- Managed-policy evidence remains deferred and is not a blocker for this scope.

The historical side-panel, extension-debugger, and extension-worker/browser restart checklist above is not an exit criterion for the redesigned extension.
