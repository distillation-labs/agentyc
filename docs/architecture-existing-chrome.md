# Existing-Chrome task-space architecture

**Status:** Phase 1 normative architecture artifact  
**Authority:** `docs/exec-plans/active/agentyc-browser-task-spaces/plans/phase-1-architecture.md` and the active D-09–D-18 decision closure  
**Evidence status:** This document freezes design rules. It does not claim that the host, extension, or live Chrome path has been implemented or proven.

This document uses **MUST**, **MUST NOT**, **SHOULD**, and **MAY** as normative terms. The host broker is the authority below every client. Chrome is an execution system; it is not the product ledger.

## 1. Non-negotiable rules

1. `space` is the only logical task-space object. `space_id` is its canonical field and public handle. `page` is a durable logical child of a space.
2. The host broker owns identity, leases, epochs, policy, ordering, persistence, reconciliation, event watermarks, snapshot/ref provenance, and cleanup authorization.
3. The MV3 extension owns Chrome API calls and browser observations. It MUST NOT own authoritative leases, action state, profile authentication, or cleanup policy.
4. CLI/SDK clients use the persistent local host protocol. MCP remains a compatibility adapter over that protocol and never becomes a second state owner.
5. Chrome tab IDs, debugger target/session IDs, frame runtime IDs, extension worker IDs, and tab-group IDs are ephemeral reconciliation hints. They MUST NOT appear in primary output, authorize an action, or replace a logical handle.
6. A Chrome tab group is visual presentation only. Group title, color, membership, collapsed state, and movement MUST NOT authorize, identify, isolate, adopt, or clean up a space. A mixed user/agent group is never a cleanup unit.
7. The existing-Chrome product path MUST NOT download, launch, switch to, or silently create a browser/profile. It has no automatic browser launch. An explicit managed test lane and an explicit operator-supplied legacy debugger endpoint are separate non-default modes.
8. A profile instance selector chooses an enrolled binding; it is not authentication. Mismatch, copied profile, reinstall, storage reset, or extension identity change enters `rebind_required` and fences prior authority.
9. User takeover is a host transition with a durable fence acknowledgement. It increments the lease epoch, stops old-epoch issuance, and rejects stale commands at extension execution time. Missing acknowledgement fails closed.
10. A dispatched mutation whose result is lost is `unknown`. Click, input, navigation, upload, storage, cookie, evaluate, and close operations MUST NOT be blindly replayed.

## 2. Component and trust flow

```text
CLI / Node SDK / legacy MCP adapter
          |
          | persistent length-delimited local protocol
          v
agentyc-host HostServer (one broker per enrolled profile binding)
  |-- host lock, OS-peer admission, ledger, leases, scheduler
  |-- snapshot/ref cache, event/wait router, reconciliation
  |-- ChromeBridge and NativeMessagingBridge
          |
          | Chrome-mediated Native Messaging; exact origin and extension ID
          v
MV3 extension service worker + side panel
  |-- chrome.debugger (allowlisted domains and target-scoped events)
  |-- chrome.tabs / chrome.tabGroups / chrome.sidePanel
  |-- narrowly scoped content-script/page bridge
          |
          v
user-approved existing Chrome pages
```

The local client boundary and the Chrome Native Messaging boundary are different protocols and MUST have separate framing, limits, handshake fields, and failure handling. See [`docs/security/host-protocol.md`](security/host-protocol.md) and [`docs/security/extension-permissions.md`](security/extension-permissions.md).

### Phase 3 topology decision — U3-1 closed

The selected macOS-first topology is **one broker owner plus Native Messaging shims**:

1. The first Chrome-launched `agentyc-native-host` acquires the profile-scoped ledger lock before reading Native Messaging bytes, owns the broker, local IPC socket, endpoint metadata, and `native-forward.sock`, then accepts the extension handshake.
2. A later Chrome-launched shim validates Chrome's exact transport origin, observes the existing ledger owner, and forwards its raw Native Messaging stream to the owner's private Unix forwarding socket. It never opens a second ledger, starts a second broker, or authenticates a profile from extension JSON.
3. The owner admits a forwarded stream only after the same-OS-user peer check and a fresh Native Messaging/core handshake. `BridgeRouter` swaps the live extension adapter while the broker, logical records, scheduler, leases, and ledger remain unchanged; pending mutations are not replayed.
4. Endpoint metadata is atomically published as owner-readable `broker.endpoint.json` and is fenced by broker epoch/process ownership. Stale metadata is replaceable; symlinks, active endpoint replacement, wrong peer credentials, malformed metadata, and incompatible epochs fail closed.
5. The owner retains local clients and logical records across an extension disconnect for a bounded recovery window; it marks the bridge degraded and accepts a fresh forwarded handshake without granting authority until admission completes. Chrome/host process lifetime and actual profile reconnect remain live Phase 4 evidence, not a deterministic test claim.

This closes U3-1 for the supported macOS topology. Windows named-pipe registration and cross-platform supervision remain U3-2/later evidence.

### Ownership matrix

| Concern                              | Canonical owner                                   | Extension role                                        | CLI/SDK role              | MCP role                          |
| ------------------------------------ | ------------------------------------------------- | ----------------------------------------------------- | ------------------------- | --------------------------------- |
| space/page identity and records      | host ledger and core contracts                    | report live hints                                     | send logical handles      | map legacy fields                 |
| leases, epochs, fences               | host broker                                       | enforce bridge admission and execution fence          | present receipts          | map connections to principals     |
| Chrome tabs, targets, frames, groups | extension adapter                                 | create, attach, observe, group, and report            | never call Chrome APIs    | never call Chrome APIs            |
| snapshots, refs, events              | host runtime/core                                 | supply DOM/AX/events and capability results           | consume bounded envelopes | serialize compatibility responses |
| user control                         | host transition authority plus side-panel tickets | render and confirm; never self-authorize              | request and observe       | return typed errors               |
| persistence and recovery             | host ledger/journal                               | profile/connection metadata and bounded UI state only | reconnect                 | no independent copy               |
| cleanup                              | host authorization plus extension execution       | remove only proven claimed pages                      | request scoped release    | scope legacy close                |
| policy and evaluate                  | host policy                                       | execute an approved capability                        | request with capability   | reject unsafe bypass              |

## 3. Canonical records and identity

The following records are logical control-plane records. Browser-generated values may be stored only as bounded, redacted reconciliation hints and never as authority.

### Space record

A `SpaceRecord` MUST contain:

- `space_id`, stable and opaque, generated by the host;
- user-facing `label`, lifecycle, owner class, principal, lease and lease epoch;
- enrolled `profile_instance_id` binding state;
- durable page references and an optional `visual_group_hint`;
- capability/policy summary, warnings, retention, and last event watermark;
- broker, connection, browser-session, and worker epoch observations where relevant.

The record MUST NOT make `group_id` a peer object. `group_id` can occur only as a deprecated compatibility alias or an explicitly named visual-group hint; it is never a tab-group identifier in the public contract.

### Page record

A `PageRecord` MUST contain:

- `page_id`, `space_id`, label, ownership class, lifecycle, and retention;
- profile binding, target binding state, and bounded browser reconciliation hint;
- `target_generation`, `navigation_generation`, `document_generation`, and frame topology version;
- last-known URL/title metadata as untrusted data, never as an ownership proof;
- close/rebind/adoption status and the provenance required for snapshots and refs.

A Chrome tab, target, debugger session, frame, URL, title, focus state, visual group, or user click cannot prove that a page is the same logical page after a browser-session change. Ambiguous matches remain unmanaged until explicit confirmation and a fresh lease.

### Other durable identities

The host allocates distinct opaque identities for `frame_id`, `document_id`, `navigation_id`, `snapshot_id`/version, `ref_id`, `action_id`, and `event_id`. A ref is valid only for its `space_id`, `page_id`, frame/document/navigation generations, snapshot version, and refs epoch. An action additionally carries request identity, idempotency identity, lease epoch, request hash, dispatch state, and reconciliation state.

The four runtime epochs are distinct:

| Epoch                   | Changes when                                | Authority consequence                                                 |
| ----------------------- | ------------------------------------------- | --------------------------------------------------------------------- |
| `broker_epoch`          | host broker restarts                        | old broker commands and locks are invalid                             |
| `connection_epoch`      | each Native Messaging connection is created | sequence spaces restart only inside the new connection                |
| `browser_session_epoch` | the real Chrome/profile session changes     | target/session/frame/document bindings require reconciliation         |
| `worker_instance_epoch` | an MV3 service worker starts                | worker metadata is rehydrated; browser-session authority is preserved |

## 4. Existing-profile guarantee

The default product mode is `extension_existing_chrome`. It provides logical space/page ownership in the user's profile, not storage isolation.

| Data or behavior                                                | Guarantee                                                                                                      |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| cookies, local/session storage, history, bookmarks, permissions | shared according to Chrome and the origin/profile; never a space secret boundary                               |
| installed extensions and enterprise policy                      | shared inputs; capability denial is reported, not bypassed                                                     |
| downloads and filesystem                                        | explicit capability, path policy, and user intent required                                                     |
| pre-existing/user tabs                                          | unmanaged by default; no auto-adoption or global close                                                         |
| agent-created pages                                             | claimed by a space after host commit and extension confirmation                                                |
| Chrome tab groups                                               | visual mapping only; scoped to one Chrome window and may be absent, renamed, regrouped, or changed by the user |
| incognito                                                       | not enrolled by default; return a typed unsupported/binding error rather than silently sharing authority       |

The extension, host, and client MUST disclose shared profile state before a space is created. The external `space.create` contract requires `profile_scope: "shared_existing_profile"`, `shared_state_notice: "shared_profile_state"`, `isolation_claim: false`, and an explicit `profile_disclosure_acknowledged: true`; missing or mismatched disclosure is rejected before the ledger commit. A future isolated-profile mode is a separate product decision and is not implied by this architecture.

Chrome tab groups cannot span windows. The adapter therefore treats one group per space as a best-effort presentation within a single window; moving a page or group across windows, regrouping, renaming, collapsing, or deleting it creates visual drift only and never changes logical ownership. A space with pages in multiple windows may have no single visual group.

The pinned manifest key identifies the trusted unpacked-development build only. Ordinary macOS users require a Chrome Web Store-signed extension; self-hosted distribution on macOS is an enterprise-managed path. A stable unpacked ID is not production distribution evidence.

## 5. Lifecycle state machines

### Task-space lifecycle

```text
created
  -> agent_owned(principal, lease_epoch)
  -> handoff_requested
  -> draining
  -> agent_owned(new principal, lease_epoch + 1)
  -> paused
  -> user_owned
  -> recovering
  -> finished
  -> released

agent_owned -> orphaned on host/client/extension/browser loss
orphaned -> recovering only after an explicit claimant and reconciliation proof
user_owned -> agent_owned only after explicit user return and a fresh lease
finished -> released only through explicit retention/cleanup policy
```

`agent_owned -> user_owned` is not a boolean flag. It is the durable sequence `fence_pending -> fence_dispatched -> fence_acknowledged -> user_owned`. The host pauses new mutations while the sequence is incomplete, and the extension rejects lower-epoch commands at execution time.

### Page lifecycle

```text
planned -> creating -> managed
managed -> target_lost -> rebinding -> managed
managed -> user_owned -> managed only after explicit return
managed -> closing -> closed -> retired
unknown/unmanaged -> adoptable -> managed only after explicit claim and confirmation
```

URL/title or visual-group similarity never completes `rebinding` and never authorizes `closing`.

### Action lifecycle

```text
queued -> rejected
queued -> running
running -> succeeded
running -> failed(retryable | terminal)
running -> cancelled
running -> unknown
unknown -> reconciled(succeeded | failed | requires_confirmation)
```

Every mutation checks principal, `space_id`, `page_id`, lease epoch, policy, capability, and generation at enqueue, dequeue, and immediately before extension dispatch. A lost post-dispatch response is `unknown`; no raw browser command is replayed.

### Connection lifecycle

```text
disconnected -> handshaking -> connected
connected -> draining -> disconnected
connected -> bridge_lost -> reconnecting -> connected
handshaking -> rejected on origin/version/profile/nonce/sequence/schema failure
```

The host accepts an extension connection only after transport-origin, exact extension identity, enrolled profile binding, protocol version, nonce, independent sequence, and schema checks succeed. A Chrome API success does not grant authority without a host lease.

### Transition contract

Each transition has one initiating authority, one durable record, an explicit guard, a bounded side effect, duplicate behavior, a typed error, and a user-visible result.

| Transition/scenario       | Initiator and owner                            | Guard                                           | Durable record and side effect                    | Duplicate/error and user result                                        |
| ------------------------- | ---------------------------------------------- | ----------------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------- |
| create to agent-owned     | client requests; host commits                  | idempotency key and lease grant                 | create `space_id`, page records, lease            | repeat returns same record; conflict is typed                          |
| stale epoch mutation      | client; host rejects                           | presented epoch equals current epoch            | no dispatch; record rejection                     | `stale_lease`; client must reacquire                                   |
| duplicate claim           | client; host                                   | claimant and claim idempotency                  | one lease transition                              | same result is returned; competing claim is denied                     |
| disconnect after dispatch | host reconciles                                | dispatch may have reached extension             | action becomes `unknown`; no replay               | `unknown_outcome`; user sees reconciliation required                   |
| extension/worker restart  | host reconnects; extension rehydrates          | new connection/worker epoch                     | host remains authoritative                        | reads resume after proof; mutations wait for handshake                 |
| browser restart           | host observes new browser epoch                | old bindings invalid                            | pages become lost/unknown                         | reconciliation required; no auto-rebind                                |
| user takeover             | side-panel intent ticket; host owns transition | fresh single-use ticket and current lease       | increment lease epoch; fence queues and extension | stale agent gets `user_control_required`; user sees paused/owned state |
| extension fence barrier   | host dispatches; extension enforces            | fence ID, broker/connection/lease epochs        | lower epochs rejected; acknowledgement recorded   | missing ack leaves `fence_pending`; no new mutation                    |
| page close                | client/user requests; host authorizes          | ownership proof, live generation, intent ticket | close only proven claimed page                    | unmanaged/user page preserved; failure is typed                        |
| rebind-required           | host binding reconciliation                    | explicit user confirmation and fresh proof      | prior binding fenced; state retained              | remains paused until confirmation                                      |
| broker restart            | host lock owner; host rehydrates               | ledger checksum/schema compatible               | new broker epoch; in-flight actions unknown       | incompatible ledger quarantined; no guessed repair                     |

The transition table is also the expected shape of `artifacts/p1-state-machines.md`; `scripts/check_state_machines.py` checks the required coverage without treating a prose claim as live evidence.

## 6. Persistence, reconciliation, and recovery

The host ledger is the authoritative selective control-plane record. It stores logical records, leases, epochs, ownership transitions, retention, release decisions, action status, and bounded reconciliation hints. It does not store secrets, cookies, full page bodies, screenshots, or replayable raw browser commands. Target hints are explicitly non-authoritative and cannot complete reconciliation alone.

Writes use a restrictive state directory, an exclusive host lock, bounded records, checksum/version validation, temporary-file write, flush, and atomic replacement. A symlinked endpoint, lock, state directory, or replacement path fails closed. A corrupt, truncated, incompatible, or schema-unknown ledger is quarantined; repair never guesses ownership or closes tabs.

Recovery rules:

| Fault/scenario                               | Durable result                                                | Restart/reconciliation behavior                                                 | Cleanup and user result                                      |
| -------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| partial ledger write                         | previous valid generation retained                            | quarantine incomplete generation and load only the valid record                 | spaces/pages retained; no cleanup                            |
| host crash                                   | committed records retained; dispatched actions may be unknown | new broker epoch; reconcile each action and live page                           | no replay; user sees unknown/recovery state                  |
| Chrome restart                               | browser-session epoch changes                                 | invalidate target/session/frame/document bindings                               | pages retained and paused until explicit reconciliation      |
| profile mismatch or copied profile           | binding becomes `rebind_required`                             | reject mutation before a fresh enrollment proof                                 | retain pages; no adoption or close                           |
| extension update/reinstall or worker restart | extension/worker epoch changes as applicable                  | exact identity and handshake required; rehydrate metadata only                  | no authority until accepted; pages retained                  |
| old binary or incompatible ledger            | compatibility check fails                                     | refuse ledger mutation and report `ledger_incompatible`                         | retain pages; rollback must use a compatible reader          |
| rollback or kill switch                      | new mutations disabled                                        | pause spaces, drain only broker-owned work, preserve ledger                     | never close user tabs or kill user Chrome                    |
| two-phase create/claim crash                 | intent/commit record identifies incomplete operation          | resume or abort idempotently after reconciliation; never duplicate side effects | unresolved claim remains pending/orphaned                    |
| cleanup confirmation                         | release request is pending until proof                        | require current ownership, live generation, and single-use user ticket          | close only proven agent pages; preserve user/unmanaged pages |

`stop`, `crash`, `update`, `uninstall`, `rollback`, and ambiguous rebind are retention events, not implicit cleanup commands. They follow the `no implicit cleanup` rule. A page can be release-eligible without being closed; cleanup requires a fresh generation proof and individual ownership.

## 7. Capability and control boundaries

The extension capability policy is normative in [`docs/security/extension-permissions.md`](security/extension-permissions.md). The host protocol and local trust boundary are normative in [`docs/security/host-protocol.md`](security/host-protocol.md).

The host MUST return typed unsupported, permission, restricted-page, incognito, policy, stale-generation, user-control, and unknown-outcome results. It MUST NOT fall back from an extension failure to a copied debugger endpoint, a managed browser, or an automatic browser launch.

Page content, labels, URLs, network bodies, cookies, screenshots, and page messages are untrusted data. They are never instructions, principals, authentication, ownership proof, or policy. Arbitrary evaluation, cookies, storage writes, downloads, uploads, and destructive actions require explicit capability, lease, policy, and where specified a single-use user-intent ticket.

## 8. Release and residual decisions

The architecture is ready for Phase 2 schema freezing only when the normative checkers pass and Phase 0 evidence is separately recorded. A checker pass is not live Chrome evidence.

- Phase 0 remains the source of truth for measured Chrome versions, permissions, distribution, and budgets.
- U1-1 (distribution/installer), U1-2 (unsupported debugger-domain fallback), and U1-3 (isolated-profile mode) remain owned residuals from the Phase 1 plan.
- Direct CLI/SDK release and MCP compatibility release have separate gates.
- Any authorization bypass, cross-space mutation, user-tab close, stale mutation after takeover, secret/page-content leak, blind replay, or silent unknown-success is a release blocker.
