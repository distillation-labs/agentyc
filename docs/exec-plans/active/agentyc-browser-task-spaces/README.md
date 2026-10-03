# agentyc Existing-Chrome Task Spaces and Reliable Automation

- **Tier:** Initiative — cross-crate runtime migration, new Chrome extension/native host, multi-process local protocol, user-control boundary, compatibility adapter, and staged rollout.
- **Owner:** Japneet Kalkat
- **Operational owner:** Japneet Kalkat
- **Primary outcome:** Coding agents can create and resume ego-lite-like task spaces in the user's already-installed Chrome, work on durable labeled pages concurrently, receive compact actionable context, and stop safely when the user takes control.
- **Status:** In execution; Phase 0 active
- **Active phase:** Phase 0 — existing-Chrome feasibility and baseline
- **Phase authority:** `plans/PLAN_INDEX.md` is the canonical phase registry; exactly one phase may be `active`, and a phase cannot start until its predecessor exit gate is complete
- **Release posture:** Internal existing-Chrome vertical slice, then opt-in preview, then staged rollout; MCP compatibility is independently gated and is not the product launch gate.

## Decision brief

**Recommendation:** Build a host-owned browser control plane below all frontends:

```text
Agent CLI / persistent Node SDK
  -> agentyc-host local protocol
  -> host broker, ledger, leases, scheduler, snapshots, events
  -> Native Messaging bridge
  -> Chrome MV3 extension in the user's existing Chrome
       -> chrome.debugger CDP transport
       -> chrome.tabs / chrome.tabGroups
       -> content-script/page bridge
       -> Chrome side panel for task-space control
  -> existing Chrome pages

Legacy MCP stdio/HTTP
  -> compatibility adapter
  -> same agentyc-host broker
```

The default path does **not** download a browser, launch a second browser, require a copied CDP URL, or expose MCP as the primary agent interface. The existing `agentyc-browser` launcher/CDP path remains explicit legacy/test compatibility only.

**Why this wins now:**

1. Chrome 136+ restricts remote-debugging switches against the default profile (S-018), so a CDP-only sidecar is not a reliable existing-Chrome product path.
2. Chrome provides a debugger extension transport with target-scoped events and flat related-target sessions from Chrome 125, plus Native Messaging for a local host bridge (S-019–S-020).
3. Chrome tabs/tab groups and a side panel provide the visible task-space workflow; logical `space_id`/`page_id` handles remain the security and durability boundary (S-023). `space` is the user-facing and canonical domain term; `group_id` is not a second logical object and is reserved for deprecated compatibility aliases/internal visual-group hints.
4. A persistent local protocol and SDK remove per-command runtime startup and let multiple agent processes share one authoritative broker.
5. The retained lease, ledger, snapshot, event, actionability, and unknown-outcome mechanisms transfer the useful ego-lite patterns without copying its proprietary browser host or arbitrary execution model.

**Accepted trade-offs:** Extension installation and sensitive permissions are required; ordinary Chrome task spaces share profile cookies/storage/extensions/history/permissions; the native host, installer, side panel, and local protocol add product surfaces; MCP compatibility needs a second adapter contract.

**Top risks:** debugger/enterprise policy restrictions, profile-sharing surprises, Native Messaging origin or payload bugs, MV3 worker restarts, stale tab/document/ref routing, user takeover races, extension distribution, and false token/performance claims.

**Stop rationale:** The repository and cloned ego-lite harness were audited; official Chrome remote-debugging, debugger, Native Messaging, service-worker, content-script, scripting, tabs, tab-groups, storage, side-panel, distribution, and browser-target CDP Extensions sources were retrieved; independent architecture reviews converged on the same host/extension/CLI direction. The automated P0-T2 probe has now passed against installed Chrome 154 using `Extensions.loadUnpacked`/`getExtensions`/`uninstall` with a disposable profile; the remaining uncertainty is explicitly gated in Phase 0 rather than hidden.

See `research/decision-supersession.md` for the old-plan mapping and `research/decision-closure.md` for active decisions D-09–D-18.

## Evidence status

This document is an execution plan and evidence registry. Phase 0 remains active; the canonical registry still keeps Phases 1–8 pending until their predecessor gates are closed. Implementation slices for the core/host, MV3 extension, context/reliability, direct CLI/SDK, rollout gates, and host-backed MCP adapter now exist and have deterministic tests, but no production rollout or live existing-Chrome evidence is claimed for the target path.

**Proven by repository/source inspection:**

- Current agentyc legacy behavior and its one-shot/active-page constraints are recorded in the Phase 0 plan.
- The checked-in ego-lite API/schema/source/tests establish reference behavior for task/page handles, output handling, page discovery, and error classification; the compatibility mapping and deliberate existing-Chrome differences are recorded in `research/ego-lite-pattern-audit.md`, and that reference does not prove agentyc behavior.
- The checked-in plan and manifest checker definitions establish required validation structure; they do not prove that the planned targets have run or passed.

**Implemented or evidenced:**

- Host/extension logical task-space contracts, Native Messaging protocol checks, and the live Chrome 154 P0-T2 probe.
- macOS disposable-profile test-extension load/reload/uninstall evidence and a live 64-cell/64,000-sample CDP benchmark accepted by the current checkers. These are not production broker rollback, deployed-tokenizer, CLI/SDK end-to-end, or human-coexistence proof.

**Planned and unproven:**

- Independently enrolled existing-Chrome coexistence evidence.
- User-tab preservation, focus safety, takeover/restart fencing, and live Chrome capability coverage across the residual matrix.
- Production readiness, rollout, and MCP compatibility through the planned host-backed path.

Offline source inspection and reference tests do not close live Chrome, installation, production, context, or reliability gates.

**Current Phase 0 blocker:** P0-T2 has recorded Chrome 154 disposable-profile debugger, Native Messaging, and tab-group evidence. Its test-only extension/host is not the production broker path. Extension load/inventory/uninstall uses the owned browser-target CDP session; fixture/control-page instrumentation also uses owned page targets. No transient MV3 worker attachment, private extension API, or file-picker automation is used. The experimental public CDP `Extensions` domain is a version-observed test mechanism, not a normal-profile installation fallback.

The product host/extension/local-socket path and Rust `agentyc-existing-chrome-probe` exist. The Rust smoke checks logical records/fences with one client and skips returned-space cleanup; it is not a browser coexistence artifact. `run_existing_chrome.py` now invokes the public host-backed direct CLI with separate `agent-a`/`agent-b` principals, records logical isolation/fence/cleanup receipts, and offers bounded operator checkpoints. It does not create managed browser pages or observe all ten browser scenarios. `--harness` is ignored; descriptors and acknowledgments cannot create a live pass. Current output remains `live_required_unavailable`, `live_observation_incomplete`, or `operator_checkpoint_required`, with unmeasured safety counters left null. All ten existing-user-profile scenarios still lack complete live proof. Current benchmark and installation checkers accept the disposable artifacts; the broader P0-T6/P0-T7 done-when requirements remain open because their runners do not measure the production CLI/SDK/tokenizer or broker kill switch/ledger rollback. Owner: Japneet Kalkat. Release posture: Phase 0 stays active, Phases 1–8 stay pending, and the exit gate remains open. Next action: verify approved enrollment, execute the real host-backed scenarios with operator checkpoints, and retain measured evidence; see [the probe and checkpoint audit](../../../../research/phase-0-host-backed-probe.md).

## Planned capability target — not yet proven

Every bullet below is a target or requirement for the staged work, not a statement that agentyc implements it or that live Chrome has validated it. It remains gated by Phase 0 and the later production phases.

### Agent and task-space experience

- Persistent local CLI with machine-readable JSON output and a thin typed Node SDK over the same protocol.
- Task-space creation, list, resume, claim, renew, handoff, accept, pause, stop, take over, return control, finish, retain, release, and recovery.
- Durable labeled pages inside each space; no process-global active tab.
- Pages created by the agent are placed in a corresponding Chrome tab group when the API is available; because Chrome groups are window-scoped, this is best-effort presentation and may drift when pages move across windows.
- Explicit adoption of a user tab only after ownership proof and user confirmation; no implicit adoption.
- Structured space/page records only; no `[id] name`, raw Chrome tab IDs, raw CDP target/session IDs, or numeric tab-group IDs in primary output.

### Existing Chrome integration

- MV3 extension installed into the user's normal Chrome profile. The pinned unpacked identity is a trusted development build; ordinary macOS distribution requires a Web Store-signed extension or enterprise management.
- Native Messaging bridge with exact extension-origin allowlisting and platform registration.
- `chrome.debugger` transport for supported CDP domains, related targets, frames, network, runtime, DOM, accessibility, input, screenshots, dialogs, and lifecycle events where the capability matrix proves support.
- `chrome.tabs`, `chrome.tabGroups`, and `chrome.sidePanel` integration for pages, visual grouping, and user control.
- Content-script/page bridge only for narrowly scoped DOM/ARIA/event operations; page messages and page text are untrusted.
- Extension/host reconnect and Chrome/worker restart recovery without replaying side effects.

### Reliability and context

- `agentyc-core` typed IDs, state machines, protocol envelopes, errors, snapshots, refs, leases, action receipts, and events.
- `agentyc-host` authoritative broker, local IPC, ledger, per-space scheduling, target/frame/document generations, event watermarks, snapshot cache, action journal, and reconciliation.
- Clean snapshot calls return no DOM payload and perform no page scan when the cache is valid.
- Compact accessibility/DOM snapshots, bounded deltas, token budgets, truncation/resync markers, and provenance-bearing temporary refs.
- Event-driven URL/network/DOM/tab/download waits with deadlines and cancellation.
- Actionability checks, postconditions, typed `unknown` outcomes, idempotency, and no blind replay after a lost mutating response.
- User takeover fences the lease epoch; queued mutations stop; dispatched actions reconcile as unknown/succeeded/failed.
- Cleanup proves broker ownership and closes only agent-owned pages; user tabs and attached user Chrome are never globally closed or killed.

### Compatibility

- Existing MCP stdio/legacy HTTP remains available through an adapter over the host broker.
- Existing safe tool names and required response conventions remain supported where possible.
- Legacy `tab_id`/`target_id` fields are adapter-only and deprecated; compatibility calls resolve through a selected/default space but cannot bypass leases.
- Existing direct CDP/temporary-browser workflows remain explicit legacy/test modes and never become the default fallback.

## Canonical space, page, and Chrome tab-group semantics

- `space` is the only canonical logical task-space object.
- `page` is a durable logical child/label of a space, not a Chrome tab or raw target.
- Chrome tab, target, debugger-session, and extension runtime IDs are inventory/reconciliation hints only; they are not public logical identity.
- A Chrome tab group is visual presentation only. Its title, color, collapsed state, membership, and movement are non-authoritative.
- A visual group is scoped to one Chrome window and may be missing, renamed, regrouped, moved across windows, or changed by the user without invalidating the logical space or its durable pages.
- `group_id` is only a deprecated compatibility alias or an explicitly named visual-group hint. It is never a second logical object, an authorization proof, or a storage-isolation boundary.
- Mixed user/agent groups are never cleanup units; cleanup requires proof of individual agent ownership.

## Explicit non-goals and deferred work

- Cookie/storage isolation between logical spaces inside one ordinary Chrome profile. This requires a separate product decision for isolated profiles/contexts and must not be claimed by tab groups.
- Automatic browser download, browser launch, or silent profile switching.
- A public remote multi-tenant broker. The first host is local and owner-authenticated by OS IPC; remote access requires a new security decision.
- Raw arbitrary CDP command passthrough or unrestricted page JavaScript by default.
- Replay of raw browser commands after host/extension/browser failure.
- Making MCP the canonical schema, lifecycle, or product UX.
- Copying ego-lite's proprietary browser host or native binding implementation.
- Auto-adoption or global cleanup of user-created tabs.
- Site-specific LLM reasoning or hidden model fallback.

Reopen deferred items only on a documented trigger in `research/decision-supersession.md`.

## Success measures

| Area              | Initial gate                                                                                                                                                                            | Evidence                                                     |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Existing Chrome   | Normal user-approved Chrome works without `--cdp-url`, remote-debugging flags, downloaded browser, or automatic launch                                                                  | Phase 0/4/7 headed Chrome artifacts                          |
| Task spaces       | Two or more spaces can run concurrently; durable page labels resume across agent reconnect; zero cross-space mutation                                                                   | Host/extension isolation suite                               |
| User safety       | Zero user-tab closes, zero stale-agent mutations after takeover, and stop/takeover behavior is visible and bounded                                                                      | Adversarial headed-Chrome tests                              |
| Reliability       | No operation exceeds its deadline; disconnect after dispatch is typed `unknown`; stale refs/events cannot affect replacement pages                                                      | Fault-injection and recovery tests                           |
| Context           | Clean cache causes zero DOM scans; delta median target ≤35% of equivalent full snapshot and actual tokenizer metadata is reported                                                       | Snapshot/token benchmark; threshold may be signed in Phase 0 |
| Speed             | Warm host metadata p95 ≤50 ms; batch script cuts agent round trips by ≥50% on the reference scenario; action latency is within the Phase 0 budget                                       | Direct CLI/SDK benchmark                                     |
| Human coexistence | User can browse unrelated tabs while two spaces work; extension does not steal focus except explicit user action                                                                        | Manual headed workflow                                       |
| Native bridge     | Malformed, oversized, wrong-origin, truncated, replayed, and version-mismatched messages fail closed                                                                                    | Native host/extension protocol tests                         |
| Compatibility     | Frozen legacy MCP baseline remains green through Phases 0–7; MCP compatibility release additionally passes wire, concurrency, fault, context, and host-backed headed-Chrome conformance | Phase 7 baseline plus Phase 8 MCP release suite              |
| Operations        | Metrics identify broker/profile/space/page/action/lease epoch without cookies, tokens, headers, page bodies, screenshots, or raw browser IDs                                            | Redaction and runbook review                                 |
| Rollback          | Mutation kill switch pauses spaces without closing user tabs or killing user Chrome; incompatible binaries refuse the ledger                                                            | Rollback drill                                               |

Targets marked initial are provisional until Phase 0 records real Chrome, model-token, and end-to-end baselines.

## Output lifecycle (reference behavior; agentyc contract planned)

The following behavior is proven by the checked-in ego-lite source and tests, but remains a planned contract for agentyc:

- Business output is buffered until a round completes, and the flush happens at most once.
- Clean completion flushes buffered business output first, then emits final unhandled-page notices.
- Hard stops discard buffered business output and notices; a swallowed hard stop emits owned guidance once, while a thrown hard stop stays silent so the propagating error is not duplicated.
- Unhandled-page notices are round-local and keyed by space/target; notices merge and refresh, observing a page suppresses its final notice, and each notice is consumed once.
- Lifecycle handling uses `beforeExit` and `exit`; fd-backed output uses synchronous writes.

The exact agentyc output schema, stream framing, and lifecycle hooks remain unfrozen until implementation and tests; no agentyc runtime evidence is claimed.

## Context and reliability gates (planned and unproven)

These gates apply to the planned agentyc path and remain unproven until Phase 0, Phase 5, and Phase 7 evidence exists.

### Context gates

- A valid clean cache returns zero DOM/accessibility scans and no element payload.
- A delta is used only when its measured serialized cost is lower than a valid full/min representation and actionable-control/ref coverage is equivalent; otherwise the result is full/min or an explicit resync.
- Transport bytes, UTF-8 bytes, serialized tokens, and deployed model-context tokens are reported separately with tokenizer metadata.
- Partial multi-frame snapshots cannot issue actionable refs.
- Tail gates use at least 200 valid samples for p95 and 1,000 valid samples for p99 with confidence intervals; thirty samples are smoke-only.

### Reliability gates

- Routing is generation- and provenance-aware across space, page, frame, document, snapshot, and action identities.
- Actionability is checked before dispatch, and a lost response after dispatch is typed `unknown`.
- Mutating actions are never blindly replayed after an uncertain dispatch.
- User takeover is a hard stop and non-retry condition until the user explicitly returns control.
- Event watermarks, deadlines, cancellation, and reconnect/resume behavior are explicit and bounded.
- Cleanup proves individual agent ownership and never closes an unowned or user tab.

## Production-grade validation bar

The direct CLI/SDK path and the MCP compatibility adapter share one mandatory evidence program, but retain separate release gates: direct rollout is gated by direct-product evidence plus the frozen legacy MCP baseline; MCP compatibility release is additionally gated by Phase 8 host-backed protocol and real-browser conformance. Protocol-only tests cannot release browser automation. The test strategy is defined in `research/production-test-strategy.md` and is a phase-gated requirement.

- Required layers are pure unit/property, deterministic component, process/Native Messaging, headed existing-Chrome, and nightly/pre-release load/soak/chaos.
- Required tests fail on missing Chrome, skipped/ignored coverage, swallowed tool errors, leaked child processes, or missing redacted artifacts; retries may classify flakes but cannot make a failed required test pass.
- Realistic MCP scenarios must exercise navigation, redirects, dynamic DOM, frames/OOPIFs, dialogs, downloads/uploads, waits, snapshots/refs, user takeover, concurrent spaces, host/extension/worker/browser restarts, reconnects, and unrelated user-tab coexistence through the real host/extension/Chrome path before MCP compatibility release.
- The MCP suite must verify wire lifecycle, HTTP/SSE/session behavior, exact tool/schema manifests, stable error mapping, cancellation, disconnect/unknown outcomes, event replay/backpressure, concurrency/fairness, ownership, and every supported tool in real headed Chrome; these are Phase 8 MCP gates, not hidden prerequisites for the direct Phase 7 release.
- Context and speed gates report transport bytes, serialized tokens, deployed model-context tokens, scan/queue/bridge/browser/serialization timings, cache state, actionable-control coverage, stale-ref/unknown rates, event lag, CPU/RSS, and human-tab responsiveness separately. Tail gates use at least 200 valid samples for p95 and 1,000 for p99 with bootstrap confidence intervals; thirty samples are smoke-only.
- Any authorization bypass, cross-space mutation, user-tab close, stale-agent mutation after takeover, secret leak, blind mutation replay, or silent unknown-success is an automatic release blocker.

## Naming, identity, and trust rules

- `space_id` is the only canonical logical task-space identifier. User-facing commands, SDK types, host records, extension UI, and new protocol examples use `space`; they never use `group_id` as a competing object.
- `group_id` may appear only as a deprecated MCP compatibility input/output alias or as an explicitly named Chrome visual-group hint. It is never a Chrome tab-group ID, an authorization proof, or a storage-isolation boundary.
- `tabId`, `targetId`, debugger `sessionId`, extension tab-group IDs, URLs/titles, focus, and user clicks inside a page are internal/untrusted hints. Primary outputs never expose them.
- `profile_instance_id` selects an enrolled profile binding but is not authentication. Binding states are `unbound`, `bound`, `rebind_required`, and `revoked`; mismatch, copied profile, reinstall, storage reset, or extension identity change requires explicit side-panel/installer confirmation before mutation authority returns.
- The trusted local boundary is the installed extension plus same-OS-user host processes. The product does not claim protection from malware running as the same OS user.
- User authority is distinct from agent authority. Adoption, takeover, return, release, upload, cookie/storage access, evaluate, login/payment, and destructive actions require a single-use expiring user-intent ticket bound to the profile binding, space/page, generation, action hash, lease epoch, and side-panel connection.

## Component/version matrix

The release tuple is versioned and published before preview: Chrome milestone/platform/policy, extension ID/build, native-host manifest/binary, host/ledger schema, CLI, Node SDK, and MCP adapter profile. A component may connect only when its declared compatibility range and ledger/protocol schema are accepted; otherwise it fails closed with `protocol_mismatch` or `ledger_incompatible` before mutation authority.

- Rust workspace: edition 2024; the supported compiler floor is the pinned `rust-toolchain.toml` value created in Phase 0, not the historical README claim.
- Planned Node package: `packages/agentyc-browser/`, with its own `package.json` and `package-lock.json`; npm is the selected package manager, and the Node floor is frozen by Phase 0 before SDK implementation.
- MCP: current `rmcp = 1.7` legacy behavior is preserved first; default and extended profiles are measured and frozen from the repository, not assumed from comments.

## Architecture ownership

### Canonical crates and surfaces

| Surface                               | Owns                                                                                                             | Must not own                                             |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `crates/agentyc-core/`                | transport-neutral IDs, records, states, errors, envelopes, snapshots, refs, receipts, events                     | Chrome APIs, MCP, process lifecycle                      |
| `crates/agentyc-host/`                | broker, local IPC, Native Messaging bridge, ledger, leases, scheduler, Chrome adapter, reconciliation, redaction | UI rendering, MCP-specific schemas, arbitrary page code  |
| `crates/agentyc-runtime/`             | compatibility facade and shared operation implementation while modules migrate                                   | a second authority or default browser launch             |
| `extension/`                          | MV3 manifest, service worker, debugger/tabs/frame bridge, side panel, browser events                             | authoritative leases, secrets, raw agent policy          |
| `crates/agentyc/`                     | `host`, `space`, `page`, `action`, `wait`, `extension`, and legacy commands; stdout/stderr discipline            | direct MCP state ownership                               |
| `crates/agentyc-mcp/`                 | legacy protocol/tool adapter over host client                                                                    | direct CDP, active-page authority, canonical space state |
| `crates/agentyc-browser/`             | explicit legacy CDP/managed-browser compatibility and test harness during migration                              | default discovery/launch/download of Chrome              |
| `packages/agentyc-browser/` (planned) | thin typed Node client over the local protocol                                                                   | a browser runtime or hidden evaluator                    |

New/touched implementation files stay at or below 400 lines where practical; extract protocol, state-machine, bridge, scheduler, snapshot, and UI modules rather than creating a single broker file.

### Server/extension/client boundary

- **Host-owned:** identity, leases, ownership, space/page state, policy, action ordering, snapshot/ref cache, event sequencing, redaction, persistence, reconciliation, and cleanup authorization.
- **Extension-owned:** Chrome API calls, debugger attachment, tab/window/group inventory, frame/session event capture, content-script lifecycle, side-panel rendering, and browser-specific capability reporting.
- **Agent-owned:** labels, task intent, requested URLs/actions, snapshot budgets, explicit confirmation choices, and follow-up decisions after typed outcomes.
- **User-owned:** Chrome profile, unmanaged tabs, takeover/return control, permission approval, login/payment/destructive confirmation, and final page retention.
- **MCP adapter-owned:** legacy schema conversion and session/connection mapping only.

### Profile and trust matrix

| Mode                        | Default? | Guarantee                                                                                             | Allowed behavior                                                                   |
| --------------------------- | -------: | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `extension_existing_chrome` |      yes | Logical task-space ownership in the user's profile; shared cookies/storage/profile state is disclosed | Agent-created/claimed pages, explicit user-approved adoption, scoped actions       |
| `extension_read_only`       | fallback | Extension connected but mutation permission/policy unavailable                                        | Inventory and supported reads; mutations return typed capability/permission errors |
| `legacy_cdp_explicit`       |       no | Explicit operator-provided CDP endpoint; guarantees depend on endpoint                                | Compatibility/test only; no silent browser launch or profile assumption            |
| `legacy_managed_test`       |       no | Temporary profile/context isolation for deterministic tests                                           | CI/manual test lane only; never automatic product fallback                         |

No mode may globally close or kill a browser it did not prove ownership of.

## Primary protocol shape

The local protocol is not MCP. It uses a persistent connection and length-delimited JSON envelopes:

```text
4-byte big-endian payload length
UTF-8 JSON envelope
```

The Native Messaging bridge separately speaks Chrome's native length-prefixed framing and forwards bounded validated envelopes to the host. Control messages have a bounded limit; screenshots, PDFs, traces, and large HTML use artifact handles/chunks.

Canonical request concepts:

- `request_id`: one transport request.
- `action_id`: durable broker action.
- `idempotency_key`: caller retry identity.
- `space_id`/`page_id`: public logical handles. `space` is canonical; `group_id` is accepted only as a deprecated legacy alias and never means a Chrome tab-group ID.
- `lease_epoch`: fencing value checked at enqueue, dequeue, and dispatch.
- `event_sequence`: broker-assigned resume watermark.

Representative methods:

```text
space.create/list/get/claim/renew/handoff/accept/pause/takeover/return/finish/release
page.create/list/get/adopt/select/close/snapshot
page.navigate/reload/history
action.click/type/fill/press/scroll/select/upload/evaluate/status/reconcile/cancel
wait.url/network_idle/request/response/dom_stable/element/page
event.subscribe/resume
artifact.screenshot/pdf/html
```

## Rollout and rollback

1. **Feasibility:** prove extension/host handshake and two-space headed-Chrome vertical slice in a synthetic profile; no production default changes.
2. **Internal existing-Chrome:** install the signed/unpacked extension and native host for a controlled profile; validate login coexistence, user tabs, side-panel takeover, restart, and cleanup.
3. **Opt-in preview:** enable existing-Chrome mode for named users; keep legacy MCP adapter available; collect task-level token/latency/recovery metrics.
4. **Staged expansion:** broaden supported Chrome versions/cohorts only after isolation, permission, bridge, performance, and support gates pass.
5. **Default candidate:** make extension/host CLI the documented default; retain MCP and legacy CDP as explicit compatibility paths.

Rollback disables new mutations, marks spaces paused, retains pages, drains only broker-owned work, preserves ledger/action records, and leaves user tabs/Chrome untouched. It never invokes the old global `close_all`, closes a whole tab group, or downgrades to a binary that cannot read the current ledger. Explicit page cleanup remains a separate user-confirmed operation with a fresh live-page/generation proof.

## Research and phases

- [Decision supersession](research/decision-supersession.md)
- [Source map](research/source-map.md)
- [Source ledger](research/source-ledger.md)
- [Findings](research/findings-1.md)
- [Decision exploration](research/decision-exploration.md)
- [Decision closure](research/decision-closure.md)
- [Production-grade test strategy](research/production-test-strategy.md)
- [Phase 0 — existing-Chrome feasibility](plans/phase-0-discovery.md)
- [Phase 1 — architecture and invariants](plans/phase-1-architecture.md)
- [Phase 2 — core/local protocol contracts](plans/phase-2-contracts.md)
- [Phase 3 — host broker and ledger](plans/phase-3-core-implementation.md)
- [Phase 4 — Chrome extension and task-space UI](plans/phase-4-extension.md)
- [Phase 5 — context and automation](plans/phase-5-context-and-automation.md)
- [Phase 6 — direct CLI and SDK](plans/phase-6-direct-cli-sdk.md)
- [Phase 7 — hardening, validation, direct rollout](plans/phase-7-direct-rollout.md)
- [Phase 8 — MCP compatibility and deprecation](plans/phase-8-mcp-compatibility.md)
- [Phase registry and execution rules](plans/PLAN_INDEX.md)

**Planning note:** Phase 0 remains active. Its test/probe scaffolding and validation scripts are present, but production implementation is still gated on real headed-Chrome evidence. No later phase may start until Phase 0's exit gate is fully checked; offline and host-only results do not close live gates.
