# Decision exploration — agentyc Browser Task Spaces

**Exploration mode:** full bounded parallel exploration plus separate critic/reliability passes. The branches were read-only and did not edit the repository. Ideas below are condensed from the parallel repository/control-plane, reliability, transport, and architecture reviews; they are hypotheses until mapped to evidence in the ledger.

## Decision cards

### D-01 — Runtime isolation

```yaml
question: Which runtime should back first-class groups?
job_to_be_done: Let many agents share fast browser infrastructure without cross-group mutations or false cookie/storage isolation.
immutable_constraints:
  - preserve direct CDP and native binary design
  - support existing attached-browser mode without closing unrelated user tabs
  - improve concurrent reliability and speed
working_constraints:
  - supported Chrome versions expose Target.createBrowserContext
why_now: A label over one active tab does not create isolation.
exit_condition: choose a runtime boundary and an attached-browser fallback.
```

### D-02 — Identity and routing

```yaml
question: What should agents use to address groups/pages?
job_to_be_done: Keep references stable across target/session replacement while preventing ambiguous or stale routing.
immutable_constraints:
  - never render or parse `[id] name` tab strings
  - keep existing tools usable during migration
working_constraints:
  - connection-scoped selected context can remain as a compatibility aid
why_now: Four-character target suffixes and process-global active-page state are unsafe.
exit_condition: define group/page/target/session/frame/ref identity and compatibility behavior.
```

### D-03 — Ownership, handoff, and durability

```yaml
question: How should multiple agents claim, share, hand off, and release groups?
job_to_be_done: Make cleanup and ownership provable even when a client disappears or a target changes.
immutable_constraints:
  - no stale client may mutate after handoff
  - user-owned/unmanaged pages must not be silently closed
working_constraints:
  - local durable metadata is acceptable
why_now: Current MCP sessions have no group ACL, lease, or recovery model.
exit_condition: define states, lease fencing, handoff, adoption, and selective durability.
```

### D-04 — Context and snapshots

```yaml
question: How should state reduce context while staying actionable?
job_to_be_done: Give agents only relevant changed state with refs that fail safely.
immutable_constraints:
  - retain deterministic/no-LLM architecture
  - measure real model tokens, not only bytes
working_constraints:
  - existing modes and since_hash need compatibility
why_now: Current since_hash still scans the page and state includes unrelated tabs.
exit_condition: define cache, dirty/version model, delta/resync, token budget, and refs.
```

### D-05 — Concurrency and automation reliability

```yaml
question: How should browser operations be scheduled and recovered?
job_to_be_done: Make actions fast, cancellable, observable, and correct under target churn and concurrent groups.
immutable_constraints:
  - no blind replay of non-idempotent side effects
  - preserve CDP directness
working_constraints:
  - per-group serialization is acceptable; independent groups should progress concurrently
why_now: One global lifecycle mutex and method-only event streams cause contention/misrouting.
exit_condition: define queues, deadlines, retries, unknown outcomes, event scope, waits, and actionability.
```

### D-06 — MCP transport era

```yaml
question: Should this refactor switch agentyc to modern MCP now?
job_to_be_done: Support groups across MCP connections without breaking current clients.
immutable_constraints:
  - current rmcp 1.7 client compatibility matters
  - do not mix legacy and modern semantics silently
working_constraints:
  - a transport abstraction can isolate a later SDK upgrade
why_now: Current official MCP is modern, but the checked SDK is legacy.
exit_condition: choose the release-line transport and a revisit trigger.
```

### D-07 — Control-plane persistence

```yaml
question: What must survive process/agent restarts?
job_to_be_done: Preserve task/page intent and recover safely without trusting stale browser handles.
immutable_constraints:
  - no partial ledger writes
  - stale browser instances cannot inherit mutation authority
working_constraints:
  - browser state itself is reconciled, not replayed
why_now: Ego-lite makes task spaces durable across invocations; agentyc has no ledger.
exit_condition: choose persisted data, atomic update, instance fencing, and recovery behavior.
```

## Parallel branch outputs

### Branch A — simplest viable / migration-first

- Add a connection-scoped compatibility group first; use explicit group/page fields for new tools.
- Keep a single broker per server process and make the HTTP factory share it.
- Do not expose raw CDP targets; retain `tab_id` only as a temporary adapter.
- Add a snapshot cache before introducing a durable intent log.

### Branch B — adversary / failure hunter

- Treat every attached tab as unknown until adopted; never let `close_all` enumerate-and-close the browser.
- Fence leases with monotonically increasing epochs; selection without an epoch is not authorization.
- Mark command results unknown on transport loss and reconcile before retrying.
- Treat broadcast lag, missing session IDs, and target/session replacement as explicit errors, not empty results.

### Branch C — operator / on-call

- Use one per-group mailbox for mutations, bounded queues, and a browser-wide concurrency cap.
- Make every wait and action emit a correlation ID and a typed terminal state.
- Persist only control-plane transitions and high-level intents; raw CDP replay would make incidents worse.
- Provide a group-scoped debug bundle that contains identity/versions/metrics without secrets or full page content.

### Branch D — context and user workflow

- Return structured group/page records, never presentation strings like `[id] name`.
- Keep finished pages open by default for audit, but only within the group’s ownership boundary.
- Return compact semantic snapshots and delta/resync instructions; agents should resnapshot after mutations.
- Pause for login challenges, payments, destructive submits, and authorization boundaries rather than guessing.

### Branch E — remove the current architecture anchor

- Replace the active-page assumption with a target/session registry; switching becomes pointer selection, not detach/reattach.
- Model BrowserContext as an isolation cell separate from group identity; use a weaker logical cell when CDP cannot create contexts.
- Put the broker below MCP transport so stdio, legacy HTTP, and future modern HTTP share contracts.
- Consolidate DOM/ref/actionability logic instead of adding another parallel MCP-only pipeline.

## Critic scoring and clusters

Scores are sorting aids, not scientific measurements. Scale: fit, viability, evidence potential, reversibility, novelty 0–10; operational burden is inverse (10 is low burden).

| Candidate                                        | Cluster            | Fit | Viability | Evidence | Reversible | Burden | Novelty | Critic result                               |
| ------------------------------------------------ | ------------------ | --: | --------: | -------: | ---------: | -----: | ------: | ------------------------------------------- |
| Broker + BrowserContext per group                | isolation cell     |  10 |         8 |        9 |          8 |      7 |       6 | shortlist                                   |
| Separate browser per group                       | process isolation  |   8 |         7 |        8 |          6 |      3 |       5 | reject as default; retain escalation        |
| Logical labels over shared tabs                  | compatibility-only |   5 |         9 |        9 |          9 |      9 |       2 | reject as final architecture; useful bridge |
| Explicit group/page IDs + selected compatibility | routing            |  10 |         9 |       10 |          9 |      8 |       5 | shortlist                                   |
| Lease epoch + handoff state machine              | ownership          |  10 |         8 |        9 |          8 |      6 |       7 | shortlist                                   |
| Durable raw CDP command log/replay               | durability         |   3 |         4 |        5 |          2 |      2 |       8 | trap                                        |
| Selective control-plane ledger                   | durability         |   9 |         8 |        8 |          8 |      7 |       6 | shortlist                                   |
| Full snapshot every call + hash                  | context            |   4 |         9 |        9 |          9 |      8 |       1 | reject                                      |
| Snapshot cache + bounded deltas                  | context            |  10 |         8 |        8 |          8 |      6 |       7 | shortlist                                   |
| Whole-round output buffer                        | context            |   5 |         6 |        5 |          6 |      5 |       6 | defer; no round model exists                |
| Global lifecycle mutex                           | concurrency        |   4 |         9 |        9 |          7 |      7 |       1 | reject as final architecture                |
| Per-group mutation queue + read concurrency      | concurrency        |  10 |         8 |        8 |          8 |      7 |       6 | shortlist                                   |
| Switch MCP to modern protocol now                | transport          |   5 |         5 |        9 |          3 |      4 |       5 | reject for this release                     |
| Preserve legacy transport + adapter seam         | transport          |   9 |         9 |       10 |          9 |      8 |       4 | shortlist                                   |

## Seductive traps

- **“A group label is isolation.”** It fails because current tools still resolve one active session and global buffers.
- **“Close all is convenient cleanup.”** It can close user/unmanaged attached tabs and cannot be repaired after the fact.
- **“Replay the last click after disconnect.”** A submit/click may have succeeded before the response was lost; replay can duplicate side effects.
- **“Hash means incremental.”** Current code scans the DOM before comparing the hash.
- **“Modern MCP is automatically better.”** Using modern semantics through a legacy SDK would break lifecycle/session behavior.
- **“Keep every target attached forever.”** It improves switching latency but can exhaust sessions/resources; use bounded attachment and reconciliation.
- **“Copy ego-lite’s execution surface.”** Arbitrary native/browser execution is a security and maintenance boundary, not needed for agentyc’s deterministic MCP.

## Verification queues

- **C-01 Broker/context cells:** verify `Target.createBrowserContext`, context-scoped target creation/disposal, attached-browser capability detection, resource cost, and cleanup.
- **C-02 Identity/leases:** verify stale target/session event behavior, lease fencing under reconnect/handoff, durable browser-instance reconciliation, and cross-group denial.
- **C-03 Snapshot deltas:** verify dirty-event coverage, ref invalidation, delta/base expiry, actual tokenizer counts, and output correctness after rerender/frame navigation.
- **C-04 Queues/waits:** verify concurrent group progress, cancellation, lag handling, request/response ordering, navigation milestones, and action postconditions with fake CDP.
- **C-05 Transport:** verify current `rmcp 1.7` session behavior remains compatible and define the exact dependency/version trigger for modern MCP support.

## Exploration closure condition

The wide set is narrowed to the recommendations in `decision-closure.md`. A candidate may be reopened only after new evidence, a failed Phase 0/7 gate, a changed security requirement, or an owner-approved scope change.

# Superseding exploration — existing Chrome and non-MCP primary

The original exploration above is retained as the historical MCP-first analysis. The following independent divergent pass was run after the product boundary changed.

## New decision cards

### D-09 — Browser integration

```yaml
question: How can agents control the user's already-running Chrome without a downloaded browser or copied CDP URL?
job_to_be_done: Provide reliable page automation and task-space control in ordinary Chrome.
immutable_constraints:
  - existing installed Chrome is the default browser
  - no automatic browser download or launch
  - MCP cannot be the primary authority
working_constraints:
  - a user-installed extension and local host are acceptable
exit_condition: choose an integration with a real-Chrome vertical-slice gate.
```

### D-10 — Profile guarantees

```yaml
question: What does a task space isolate inside one Chrome profile?
job_to_be_done: Give agents durable ownership and user-visible grouping without false cookie/storage claims.
immutable_constraints:
  - no label or Chrome tab-group ID is a security boundary
  - user tabs must survive agent cleanup
exit_condition: publish inherited/shared/unavailable data guarantees.
```

### D-11 — Primary agent surface

```yaml
question: What should agents use instead of MCP?
job_to_be_done: Batch multi-step work with persistent task-space/page objects and low context/round-trip cost.
immutable_constraints:
  - one host authority must serve multiple agent processes
  - contracts must be transport-neutral
exit_condition: freeze local protocol, CLI, SDK, cancellation, events, and lifecycle.
```

### D-12 — User control and presentation

```yaml
question: How should people see and control agent spaces?
job_to_be_done: Make ownership, pause, takeover, handoff, finish, and retained pages understandable in Chrome.
immutable_constraints:
  - no [id] name output
  - user takeover must fence mutations
exit_condition: freeze group/page/owner state and side-panel behavior.
```

### D-13 — Extension capability split

```yaml
question: Which work belongs to chrome.debugger, content scripts, or the host?
job_to_be_done: Preserve reliable automation while minimizing extension privilege and avoiding untrusted page-code execution.
immutable_constraints:
  - page text and page messages are untrusted
  - unsupported Chrome domains must fail explicitly
exit_condition: capability matrix and permission policy pass review.
```

## Divergent branches

### Branch F — simplest installable product

Use an MV3 extension with Native Messaging and `chrome.tabs`/`scripting`; keep the host broker authoritative; use content-script snapshots and high-level actions; defer broad CDP domains. This minimizes debugger privilege but may lose frame/network parity.

### Branch G — reliability-first transport

Use `chrome.debugger` as the primary tab transport, flat-session OOPIF routing, host-side CDP/event registry, and content scripts only for page-world bridges/UI. Gate the debugger permission and unsupported domains explicitly.

### Branch H — user-workflow-first

Treat spaces as product objects mapped to Chrome tab groups and a side panel. Create only agent-owned pages by default, expose user takeover/return/finish, and make profile-sharing warnings unavoidable. Do not claim BrowserContext isolation.

### Branch I — security/adversary

Assume page content, Native Messaging payloads, extension messages, and agent-provided labels are hostile. Require exact extension origin, host lock, nonce/sequence, size limits, schema validation, lease epoch checks, no raw code by default, and no cleanup without ownership proof.

### Branch J — speed/context-first

Use a persistent local protocol and SDK batch execution, host-side snapshot cache/deltas, event watermarks, and concurrent read scheduling. Measure end-to-end first-action latency and model tokens rather than MCP serialization alone.

## Critic result and shortlist

| Candidate                                  | Fit | Viability | Evidence | Reversible | Operational burden | Result                                                                       |
| ------------------------------------------ | --: | --------: | -------: | ---------: | -----------------: | ---------------------------------------------------------------------------- |
| CDP-only sidecar to existing Chrome        |   4 |         5 |        8 |          7 |                  8 | reject as default; Chrome 136/profile restriction and no UI/consent boundary |
| Managed BrowserContext per group           |   5 |         9 |        9 |          7 |                  5 | reject as default; different browser/profile and no live user Chrome         |
| Extension + Native Messaging + host broker |  10 |         8 |       10 |          8 |                  5 | shortlist/recommend                                                          |
| Content-script-only extension              |   7 |         8 |        8 |          8 |                  7 | partial fallback; frame/network parity is weaker                             |
| Extension + debugger + content bridge      |  10 |         8 |       10 |          7 |                  5 | shortlist/recommend as implementation shape                                  |
| MCP-first broker                           |   3 |         9 |        9 |          8 |                  7 | reject as primary; retain adapter                                            |
| Local protocol + persistent CLI/SDK        |  10 |         9 |        9 |          9 |                  7 | shortlist/recommend                                                          |
| Chrome tab groups as the security boundary |   2 |        10 |       10 |          9 |                  9 | trap; visual grouping only                                                   |

## Traps

- **“Native Messaging is automation.”** It is only an IPC channel; the extension still owns Chrome APIs.
- **“The extension gives BrowserContext isolation.”** It does not isolate cookies/storage in the user's profile.
- **“A Chrome tab group is ownership.”** Group IDs are session-scoped and user-editable; the host lease is authoritative.
- **“Service-worker globals are durable.”** MV3 workers terminate; host ledger and storage-backed profile identity are required.
- **“Debugger detach means retry.”** DevTools/tab closure can detach after a side effect; classify unknown and reconcile.
- **“A local CLI call is fast enough by itself.”** Per-command runtime startup still loses state; use a persistent host connection and batch SDK.
- **“Keep MCP as the canonical contract for compatibility.”** It would recreate the old transport/state coupling; map MCP into the broker instead.

## Verification queue

- V-09: real Chrome extension attaches to an agent-created tab, sends a CDP command, receives an event, and reconnects after worker/host restart.
- V-10: two spaces in the same Chrome profile have durable logical pages, separate leases, visible tab groups, and no cross-space mutation; user tab remains untouched.
- V-11: side-panel takeover fences actions; dispatched actions become `unknown`; return-control resumes only after a fresh lease.
- V-12: Native Messaging frames reject malformed/oversized payloads and exact-origin failures; host lock prevents duplicate brokers.
- V-13: persistent CLI/SDK batches a multi-step task over one socket and achieves the Phase 0 first-action/round-trip target.
- V-14: capability matrix covers debugger restricted domains, frames/OOPIFs, cookies/storage, downloads/uploads, screenshots, dialogs, and evaluate.

## Superseding exploration closure

The recommendation is the intersection of Branches G, H, I, and J: an extension/native-host bridge with `chrome.debugger` as the primary transport, a host-owned broker/ledger, Chrome tab groups and a side panel as presentation, and a persistent local CLI/SDK as the primary agent surface. Branch F remains a restricted fallback for capability gaps. The original MCP/managed-browser decisions are superseded by `research/decision-supersession.md` and the revised `decision-closure.md`.
