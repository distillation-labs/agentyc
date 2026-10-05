# Decision supersession — existing Chrome and non-MCP primary architecture

**Recorded:** 2026-10-01  
**Reason:** The product requirement changed after the original MCP-centered plan was closed. The target is now the full ego-lite-style task-space experience in the user's already-running Chrome, without downloading or launching another browser by default and without MCP as the primary agent interface.

## Supersession rule

The original research remains valuable for identity, leases, snapshots, event routing, actionability, unknown outcomes, and cleanup. The original decisions about the primary transport, browser ownership, and isolation boundary are superseded below. No implementation may follow the old MCP-first or managed-BrowserContext-first recommendations without a new decision record.

## Mapping

| Old decision                                | Status                                                                                                               | New owner        |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ---------------- |
| D-01 — CDP BrowserContext per group         | Superseded as the primary product boundary; retained only for explicit legacy/test mode                              | D-09, D-10       |
| D-02 — explicit group/page IDs              | Retained and strengthened; raw tab/target IDs are internal everywhere except the legacy MCP adapter                  | D-12             |
| D-03 — brokered leases/handoff              | Retained; broker moves below all frontends and adds user-visible pause/takeover/finish states                        | D-11, D-13       |
| D-04 — cached compact snapshots             | Retained; canonical output moves below MCP and must work through the extension bridge                                | D-14             |
| D-05 — event-driven automation              | Retained; extension debugger events and Chrome tab events become transport inputs                                    | D-14             |
| D-06 — legacy MCP as release-line transport | Superseded as the product interface; retained only as a versioned compatibility adapter                              | D-15             |
| D-07 — local control-plane ledger           | Retained and moved into the host broker                                                                              | D-13             |
| D-08 — attached-browser trust matrix        | Superseded; existing Chrome is the default supported mode, with explicit user consent and no storage-isolation claim | D-09, D-10, D-17 |

## New decisions

### D-09 — Canonical browser integration

Use a Chrome MV3 extension plus a local Native Messaging host bridge. The extension controls the user's existing Chrome through `chrome.debugger`, `chrome.tabs`, `chrome.tabGroups`, `chrome.scripting`, content scripts, and a Chrome side panel. The Native Messaging executable is a thin authenticated bridge to the host broker; it is not the agent-facing protocol.

The default path never downloads a browser, launches a browser, requires a copied CDP URL, or relies on `--remote-debugging-port`. The existing `agentyc-browser` launcher and direct CDP path remain explicit compatibility/test modes only.

**Why this wins now:** Chrome 136+ rejects remote debugging switches for the default profile; Chrome officially exposes debugger and Native Messaging APIs for installed extensions; tab groups and side panels provide the user-visible task-space affordance; the user explicitly requires existing Chrome.

**Trade-offs accepted:** Extension installation and sensitive permissions are required; Chrome extension APIs do not provide BrowserContext cookie/storage isolation; a thin native bridge and platform installer become part of the product.

**Rejected:** managed BrowserContext as the default because it is a different browser/profile; CDP-only attachment because it does not reliably attach to ordinary Chrome and cannot provide the requested user-facing task-space experience; copying the proprietary Ego Lite browser host because it is not in the open-source checkout.

**Reopen when:** Chrome removes a required API, enterprise policy blocks the debugger permission in a supported environment, or a reviewed browser-native integration becomes available with stronger guarantees.

**Confidence:** medium/high for the integration shape; exact API capability matrix and Chrome-version floor are Phase 0 gates.

### D-10 — Existing-profile guarantees

Task spaces are logical ownership and presentation boundaries inside one Chrome profile, not storage-security boundaries. Cookies, local/session storage, installed extensions, history, permissions, downloads, and browser profile state are shared unless Chrome itself provides a separate profile/window boundary. Agent-created pages are placed in a Chrome tab group associated with the task space. Existing user tabs are unmanaged until explicit adoption; labels and tab-group IDs never authorize access.

The product must display this limitation before the first mutation. A future isolated-profile mode is separate scope and cannot be silently substituted.

### D-11 — Task-space lifecycle and user control

A task space is the primary object; pages are durable logical children; Chrome tabs are implementation inventory. The lifecycle is `created -> agent_owned -> paused|user_owned -> agent_owned -> finished|released`, with `orphaned` and `recovering` states for failure. The side panel exposes create, pause, stop, take over, return control, handoff, finish, retain, and release. User takeover fences the lease epoch and rejects new agent mutations; an already-dispatched command becomes `unknown` until reconciled.

### D-12 — Canonical identity and presentation

The canonical public identities are opaque `space_id` and durable `page_id`, plus frame/document generations, snapshot/ref versions, and action IDs. `group_id` is only a deprecated MCP alias or visual-group hint, never a second logical object. Chrome `tabId`, CDP target/session IDs, and extension group IDs are internal reconciliation hints. New CLI, SDK, extension UI, host, debug bundle, and documentation outputs must never render `[id] name` or raw target/tab IDs. The legacy MCP adapter may expose old `tab_id`/`target_id` fields only in an explicitly marked compatibility response.

### D-13 — Host-owned broker and local agent protocol

`agentyc-host` owns one broker per enrolled Chrome extension profile binding. A profile instance UUID selects a binding but is not authentication; first enrollment, mismatch, copied-profile detection, reinstall/storage reset, and extension-ID change require explicit rebind confirmation and fence prior authority. A persistent local, OS-peer-authenticated Unix-domain socket on macOS/Linux and named pipe on Windows is the primary agent connection. The wire protocol is versioned, length-delimited JSON envelopes with request IDs, deadlines, cancellation, structured errors, action receipts, event sequence numbers, broker epochs, and bounded artifact handles. The Native Messaging bridge forwards validated messages to the same broker; separate agent processes never create independent brokers for one profile.

The broker owns the ledger, leases, scheduler, page registry, snapshot cache, action journal, and reconciliation. It persists logical task/page metadata and action status, not replayable browser commands or secrets. Crash recovery invalidates old leases and re-proves live pages before mutation.

### D-14 — Context-efficient automation

The canonical snapshot/action runtime is transport-neutral and works through the extension bridge. It combines compact accessibility/DOM snapshots, clean-cache no-scan responses, bounded deltas, provenance-bearing temporary refs, event-driven waits, actionability checks, postconditions, typed unknown outcomes, and no blind replay. `browser_evaluate`/raw page code is mutation-capable and requires an explicit capability/policy; deterministic high-level actions are the default.

### D-15 — MCP compatibility boundary

MCP is a compatibility-only adapter over `agentyc-host` and `agentyc-core`, not the authority or primary product API. The shipped service is host-backed logical stdio only; the direct-CDP MCP server, `browser_*` MCP tools, 61/76 profiles, legacy MCP flags, and MCP HTTP `serve` route were removed. No backward-compatibility promise remains for those interfaces. The offline server lists 29 routes; the connected catalog declares 30, with 12 failing closed as `capability_unavailable`. Raw browser IDs are not authority, and MCP failures cannot bypass host leases or extension ownership. Headed live-Chrome and release gates remain open, so MCP is not distribution-ready.

### D-16 — Primary SDK and CLI

The canonical agent surface is the local protocol plus a persistent JSON CLI and a thin typed Node SDK that uses the same envelopes. Both expose task-space-scoped operations and can batch a multi-step script over one connection. The SDK is a client only; it does not embed a second browser runtime or execute arbitrary code inside the host.

Example shape (planned; aligned to the checked-in ego-lite reference, not an implemented agentyc API):

```js
const client = await connect();
const task = await client.taskSpace("research competitors");
const results = task.page("p1");
const scratch = await task.newPage();

await results.goto("https://example.test");
const snapshot = await results.snapshot({ mode: "min" });
await results.click(snapshot.refs.submit);
await results.waitForURL(/\/done$/);

await task.finish({ keep: ["p1"] });
```

### D-17 — Permissions, privacy, and distribution

Use a stable Web Store extension ID for production so Native Messaging `allowed_origins` can be exact. Development uses an explicitly separate unpacked extension ID and manifest. The installer registers the native host per platform with owner-only permissions. Host and extension validate protocol version, extension ID, profile instance, nonce/sequence, message size, and command capability. Logs and ledgers exclude cookies, tokens, headers, page bodies, screenshots, and arbitrary code by default.

The core permission set is reviewed in Phase 0 and includes `debugger`, `nativeMessaging`, `storage`, `tabs`, `tabGroups`, `scripting`, and `sidePanel`; optional capabilities such as cookies/downloads/file upload require separate policy and tests.

## Revisit triggers

Reopen a decision only after one of these observable events:

- the Phase 0 Chrome/extension vertical slice cannot control a user-approved existing Chrome tab without a copied CDP URL;
- a required debugger/content/Native Messaging capability is unavailable on the supported Chrome floor;
- user-tab interference, cross-space mutation, or stale-agent mutation appears in validation;
- profile-sharing guarantees prove unacceptable for the intended workflow;
- a required agent client cannot use the local protocol/CLI/SDK;
- Chrome Web Store/enterprise distribution constraints block the supported environment;
- a validated performance or recovery gate fails.
