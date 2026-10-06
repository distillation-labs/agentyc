# Extension permissions and capability policy

**Status:** Historical Phase 1 artifact; superseded for the current product
**Owner:** host policy and extension adapter owners  
**Scope:** MV3 existing-Chrome mode; the managed test extension is a separate fixture and does not widen product authority.

> **Superseded for the current product:** This Phase 1 permission model describes an earlier extension-owned debugger, content-script, and side-panel design. The current extension is a background-only Native Messaging tab-creation bridge; it does not request `debugger`, `tabs`, `tabGroups`, or `sidePanel` permissions, host permissions, or content scripts. It calls `chrome.tabs.create` only. Do not use the historical permission matrix below as current install guidance.

The extension is a browser adapter, not an authority store. Chrome permission grants are necessary but never sufficient for a mutation: the host must admit the enrolled profile binding, principal, space/page, lease epoch, capability, policy, generation, and user-intent ticket where required. A successful Chrome API call does not authenticate the caller.

### Direct manifest check

The permission checker parses `extension/manifest.json` directly; it does not infer permissions from this document. It requires Manifest V3, a string-list `permissions` field with exactly one required `debugger` entry, and `incognito: "not_allowed"`. It rejects `optional_permissions`, `host_permissions`, `optional_host_permissions`, and `scripting`, and recursively rejects every `world: "MAIN"` declaration. The current manifest therefore has no optional, host, scripting, or MAIN-world capability.

## 1. Manifest and host-access policy

### Required baseline permissions

| Permission/API    | Install-time status              | Allowed purpose                                                                                                         | Denial behavior                                                                                   |
| ----------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `debugger`        | **required; never optional**     | allowlisted CDP domains for target-scoped attach, events, snapshots, waits, and approved actions                        | extension reports `permission_denied`/`capability_unavailable`; host grants no mutation authority |
| `nativeMessaging` | required                         | connect the extension context to the registered local native host                                                       | `native_host_unavailable`; no direct client or browser fallback                                   |
| `tabs`            | required                         | inspect bounded tab metadata, create agent pages, observe replacement/removal, and act on host-authorized page bindings | typed capability error; no user-tab adoption or cleanup                                           |
| `tabGroups`       | required for visual presentation | create/update a presentation mapping for an agent space                                                                 | space remains valid without a group; never affects authorization                                  |

| `storage` | required | persist profile/connection metadata, installation state, and bounded UI preferences | host re-enrollment or read-only UI; authoritative state stays in host |
| `sidePanel` | required for user-control UI | render pause, takeover, return, finish, retain, release, and confirmation controls | agent remains host-controlled; user operation returns a typed UI-unavailable result |

### Chrome disclosures

Chrome documents user-visible warnings for `debugger` (page debugger access and website data), `nativeMessaging` (communication with cooperating native applications), `tabs` (browsing history), and `tabGroups` (view and manage tab groups). The broad `http://*/*` and `https://*/*` static content-script match patterns can also trigger host-access disclosure. These warnings are expected, disclosed during enrollment, and are not treated as proof of host authorization.

`debugger` MUST be declared in the required permission set. It MUST NOT be placed in an optional permission list, modeled as a live grant that silently widens authority, or bypassed through a copied debugger endpoint.

The product manifest intentionally omits both `host_permissions` and `scripting`: the bridge is a statically declared, isolated-world content script, and the current product does not use programmatic script injection. Its explicit `http://*/*` and `https://*/*` content-script match patterns still cause Chrome's documented host-access warning; that warning is disclosed to the user and is not replaced with a hidden or silent grant. Narrower origin enrollment remains a follow-up capability decision.

### Optional permissions and host access

Optional capabilities are explicit, separately disclosed, and denied by default:

| Permission/access        | Optional policy                                              | Required user/host controls                                                                                                                       |
| ------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cookies`                | disabled unless a reviewed capability profile enables it     | host policy, space/page lease, origin scope, redacted result, and a single-use user-intent ticket for reads that expose or mutate sensitive state |
| `downloads`              | disabled unless the user enables download handling           | explicit destination/path policy, lease, user intent, DLP result, and postcondition; no silent filesystem access                                  |
| exact origin host access | requested only for a content/page bridge that needs it       | user-approved origin pattern, capability profile, frame/document generation, and host lease                                                       |
| `activeTab`              | only for a user-gesture-bound, short-lived content operation | the gesture is the grant; it cannot authorize background work or a different page                                                                 |

The current manifest has no broad hidden host grant and no optional host permissions. A future reviewed capability may request exact, user-approved origin patterns, but the extension MUST NOT use a wildcard host grant as a substitute for policy, and it MUST NOT treat a URL, title, focus state, label, or group membership as permission. `debugger` permission is not a host-authentication mechanism.

No permission permits automatic browser download, browser launch, profile switching, or silent attachment to a debugger endpoint. Unsupported upload/download flows return typed `upload_denied`/`download_denied` and do not retry or fall back. Existing-Chrome mode requires a user-approved running Chrome and the enrolled extension/host binding.

## 2. Debugger domain allowlist

The bridge sends only the smallest domain/method set required by the capability matrix. It calls `chrome.debugger.attach` with the official documented minimum required protocol version `0.1`; the disposable P0 probe's separate `1.3` revision is test-fixture evidence only and is not the product adapter contract. The allowlist is versioned and enforced before dispatch:

| Domain          | Phase 1 use                                                                         | Boundary                                                                                               |
| --------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `Accessibility` | bounded accessibility snapshot data                                                 | read; page data remains untrusted                                                                      |
| `DOM`           | bounded DOM topology and node reads; typed actions use approved input/content paths | generation and lease checks; arbitrary DOM mutation and `DOM.setFileInputFiles` are denied             |
| `DOMSnapshot`   | compact snapshot acquisition                                                        | bounded size and scan budget                                                                           |
| `Input`         | click/type/fill/key/scroll actions                                                  | mutation policy, actionability, and postcondition                                                      |
| `IO`            | bounded artifact/stream reads                                                       | chunk and aggregate limits                                                                             |
| `Log`           | bounded diagnostic events                                                           | redaction; no secret/page-body persistence                                                             |
| `Network`       | request/response events and waits                                                   | no unrestricted body persistence; global cache/cookie/blocking mutations are denied                    |
| `Page`          | navigation, lifecycle, dialogs, and page events                                     | lease, deadline, and unknown outcome rules                                                             |
| `Runtime`       | approved evaluation and bridge calls                                                | evaluate policy below; no unrestricted string execution                                                |
| `Target`        | not in the current command or event allowlists                                      | no public target control; related-target execution is unavailable until separately observed and tested |

`Browser`, `Target` control/events, unrestricted `Storage`, arbitrary CDP command passthrough, arbitrary runtime script compilation/calls, file-input CDP injection, and unreviewed domains are not in the baseline allowlist. An unsupported domain or method returns `capability_unavailable` with the required Chrome/policy reason. It never falls back to a second browser or direct client-side CDP.

### Separate debugger event allowlist

Command authorization and event routing are separate allowlists. `DEBUGGER_EVENT_ALLOWLIST` admits only the documented Accessibility, DOM, Log, Network, Page, and Runtime event names; `Target` events are not allowed and unsupported events are dropped before attribution or forwarding. Event payloads remain bounded and redacted, and an event never grants command authority.

## 3. Content-script and page worlds

- The content script runs in an isolated world by default and carries only a versioned, nonce-bound bridge envelope. The current product executes its four named DOM/ARIA operations directly in that isolated world; it does not relay them through page JavaScript.
- Page `postMessage` is untrusted. The bridge checks a per-connection channel nonce, schema, origin/frame/document binding, size, and direction before forwarding anything.
- Content scripts cannot call Native Messaging directly. Only the extension service-worker/approved extension contexts may use the host channel. The service worker validates `sender.id`, `sender.origin`/`sender.url`, managed-tab ownership, and the current document binding before forwarding content data.
- The `MAIN` world is denied by default. A reviewed capability may use it only with an explicit script hash, origin, frame/document generation, deadline, lease, and user-intent ticket where the operation can mutate or expose sensitive state.
- Injected code cannot become a principal, renew a lease, approve takeover, release a page, or authorize cleanup.
- Page text, markup, labels, URLs, network data, cookies, and screenshots are data. Prompt-like content from a page is never policy or an instruction to the host.

The bridge uses versioned named operations rather than accepting arbitrary source text from an agent. Any result that cannot be bounded, typed, redacted, and associated with the current generation is rejected.

## 4. Capability matrix

The host evaluates the following policy at request admission and again immediately before extension dispatch.

| Capability                  | Baseline permission(s)                             | Host checks                                                                                      | User/Chrome failure                                           |
| --------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------- |
| inventory/read tab metadata | `tabs`                                             | enrolled binding, space scope, non-sensitive fields                                              | `permission_denied` or `unmanaged_page`                       |
| create an agent page        | `tabs`, `debugger`                                 | space lease, creation policy, no user-tab adoption                                               | `capability_unavailable`                                      |
| navigate/reload/wait        | `debugger` (`Page`, `Network`, `Runtime`)          | page ownership, lease epoch, deadline, policy                                                    | `stale_lease`, `restricted_url`, `unknown_outcome`            |
| snapshot/DOM/AX             | `debugger` (`DOM`, `DOMSnapshot`, `Accessibility`) | page/frame/document generation, size/token budget                                                | `capability_unavailable`, `resync_required`                   |
| click/type/fill/key/scroll  | `debugger` (`Input`, `DOM`, `Runtime`)             | actionability, lease, policy, postcondition                                                      | `user_control_required`, `unknown_outcome`                    |
| screenshot/PDF              | `debugger` (`Page`, `IO`)                          | artifact budget, page scope, DLP policy, confirmation profile                                    | `artifact_denied`/`policy_denied`; no artifact emitted        |
| evaluate                    | `debugger` (`Runtime`) or approved bridge          | explicit capability, script hash/template, origin/frame scope, lease, user intent when sensitive | `evaluate_denied`; never raw passthrough                      |
| cookies                     | optional `cookies`                                 | explicit policy, origin, lease, single-use user intent, redaction                                | `permission_denied`/`user_confirmation_required`              |
| storage read/write          | `debugger`/approved bridge                         | reads are scoped; writes are mutations with policy, lease, and intent                            | `policy_denied`/`permission_denied`                           |
| download                    | optional `downloads`                               | path allowlist, DLP, user intent, postcondition                                                  | `download_denied`; preserve existing files                    |
| upload                      | future reviewed user-controlled flow               | explicit file path policy, user intent, DLP, page generation                                     | `upload_denied`; no retry after unknown dispatch              |
| adopt/close/release         | `tabs`, `debugger`, `sidePanel`                    | individual ownership proof, fresh generation, current lease, single-use ticket                   | `unmanaged_page`/`user_control_required`; user page preserved |
| pause/takeover/return       | `sidePanel`, `nativeMessaging`                     | side-panel ticket, current binding, durable host transition                                      | `user_confirmation_required` or `fence_pending`               |

The matrix distinguishes a permission grant from a product capability. A capability can remain unavailable even when Chrome reports a granted permission, because host policy, enterprise policy, restricted URL, incognito scope, DLP, lease, generation, or user intent can deny it.

## 5. Denial, revocation, and special Chrome states

### Stable Chrome error classifier

The Chrome error classifier consumes only bounded error text and explicit test/adapter codes. It emits only codes from the extension's closed public error registry. Chrome access failures are classified as `restricted_url`, `incognito_not_supported`, `policy_denied`, `artifact_denied`, `permission_denied`, or `unknown`; reviewed unsupported file flows return `upload_denied` or `download_denied`. Raw Chrome messages, URLs, and policy details are not returned across the protocol. `unknown` is fail-closed and is never silently converted into success.

### Live revocation and enterprise policy

Every mutating dispatch rechecks the effective permission/capability state. If Chrome revokes a permission, an enterprise policy denies it, or the debugger detaches, the extension:

1. stops issuing the affected operation;
2. returns a typed denial with capability, policy, and retry classification, without secrets or page bodies;
3. lets the host mark a dispatched mutation `unknown` when delivery may have occurred;
4. acknowledges the highest accepted fence/sequence only for messages actually validated; and
5. performs no retry loop and no fallback, launch of another browser, or downgrade to direct CDP.

A permission prompt or policy result is a user-visible state change. It is not evidence that a requested operation succeeded.

### Incognito

Incognito is not enrolled or mutated by default. An incognito tab/window without a separately approved binding returns `incognito_not_supported` or `profile_binding_required`; it is not auto-adopted, grouped, closed, or used to infer the normal-profile binding. If a future product decision adds incognito support, it requires a distinct binding, retention policy, storage disclosure, and test matrix.

### Restricted URLs and special pages

`chrome://`, browser-internal, extension-store, policy-restricted, and otherwise debugger/content-inaccessible pages return `restricted_url` or `capability_unavailable` before mutation authority. The extension does not evade restrictions through another world, a navigation side effect, or a managed browser.

### User gestures

The following require an explicit side-panel or browser user gesture and a single-use, expiring host ticket bound to profile, space/page, generation, action hash, and lease epoch where required:

- adoption of an unmanaged page;
- takeover, return, release, or destructive cleanup;
- cookie/storage exposure or mutation where policy marks it sensitive;
- upload, download destination selection, login/payment, and other destructive actions;
- evaluation that can mutate or expose sensitive page state.

A payload boolean, page message, label, tab-group action, or client-supplied principal cannot manufacture a ticket.

### Runtime evaluation approval binding

`Runtime.evaluate` requires an exact `purpose: "runtime.evaluate"`, a host-issued approval, a matching script hash, and the current `space_id`, `page_id`, `lease_epoch`, `target_generation`, `navigation_generation`, `document_generation`, origin, and logical `frame_scope`. The approval is bounded and single-use. A navigation, document change, origin change, or frame-scope change invalidates it; the extension does not dispatch a subframe evaluation unless a current logical frame binding proves that scope. This is separate from screenshot/PDF artifact approval.

### Screenshot and DLP denial

Screenshot/PDF/artifact capabilities are subject to page policy, enterprise/DLP policy, artifact size, redaction, and retention. If any check denies capture, return `artifact_denied`/`policy_denied`, persist only the typed outcome, and do not send a partial or unredacted artifact. A denied screenshot is never represented as a successful empty screenshot.

No `Page.captureScreenshot` or `Page.printToPDF` dispatch is permitted without a current, host-issued, expiring, single-use artifact approval. The canonical approval fields are:

- `issued_by_host: true`, a bounded `approval_id`, and `purpose: "screenshot"` or `"pdf"` matching the exact debugger method;
- `expires_at` (or the compatible `expires_at_ms` spelling), bounded to the short approval lifetime;
- exact `space_id`, `page_id`, `lease_epoch`, `target_generation`, `navigation_generation`, and `document_generation`;
- the current page/frame `origin` and logical `frame_scope` (`"main"` is the supported capture scope); and
- `user_gesture: true` (the compatible `gesture: true` spelling is accepted).

The approval is checked before attachment and again immediately before dispatch, then consumed once even if the browser result becomes unknown. The origin/frame/navigation/document scope is exact and must describe the current target. Missing, malformed, expired, replayed, cross-origin, cross-frame, stale-generation, or wrong-purpose approvals return `artifact_denied`; a missing current gesture returns `user_confirmation_required`. Chrome or DLP capture failures classify as `artifact_denied`, and no successful artifact is emitted.

## 6. Profile binding and worker state

The extension persists only profile/connection metadata and bounded UI state. The host persists the authoritative ledger, leases, action journal, and reconciliation state. A `profile_instance_id` is a selector for the enrolled binding, not proof of identity. The host requires exact extension identity, profile binding state, protocol version, transport origin, nonce, sequence, and broker/connection epochs before granting authority.

MV3 worker restarts create a new `worker_instance_epoch`. The worker rehydrates through the host and does not recreate leases from storage. Browser-session changes create a new `browser_session_epoch` and invalidate target/session/frame/document bindings. Lower epochs are rejected at execution time.

## 7. Required negative tests

The Phase 1 checker and later extension tests MUST cover:

- `debugger` omitted, moved to optional, revoked, or denied by enterprise policy;
- wrong Native Messaging origin, forged origin field, wrong extension identity, stale profile binding, replayed nonce/sequence, and reconnect sequence reset;
- content script attempting direct Native Messaging or page bridge nonce/schema forgery;
- restricted URL, incognito tab, missing host access, missing user gesture, and side-panel denial;
- debugger detachment during a mutation, screenshot/DLP denial, upload/download policy denial, and an unknown post-dispatch result;
- tab-group deletion/rename/regrouping without logical space loss;
- attempted automatic browser launch, copied debugger endpoint, raw browser-ID authorization, and cleanup of an unmanaged/user tab.

A checker pass confirms that the direct manifest, policy markers, and input matrix are structurally valid. It does not claim live Chrome permission evidence. Live Chrome grants, revocation, enterprise policy, restricted-page, incognito, DLP, screenshot, and PDF behavior remain separate evidence requirements.
