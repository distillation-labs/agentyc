---
phase: 5
name: Context-efficient snapshots and reliable automation
status: pending
owner: Japneet Kalkat
primary_outcome: Existing-Chrome automation provides compact actionable context, provenance-safe refs, event-driven waits, actionability/postconditions, and typed recoverable outcomes through the host/extension bridge.
depends_on: phase-4
---

# Phase 5 — Context-efficient snapshots and reliable automation

## Objective

Move the useful ego-lite patterns into the transport-neutral host runtime: compact semantic snapshots, incremental deltas, temporary refs, event watermarks, fast warm operations, reliable actions, and safe user-control boundaries. The extension supplies Chrome events/commands; the host owns correctness and context policy.

## Handoff in

- **Inputs:** Phase 0 token/latency budgets; Phase 2 snapshot/action/event contracts; Phase 3 broker; Phase 4 debugger/content bridge.
- **Must already be true:** each operation resolves logical space/page and current generation; extension reports capabilities and events.
- **Do not reopen:** no full-scan-only `since_hash`; no raw IDs; no fixed sleeps on correctness paths; no blind replay; no unscoped event fallback.

## Confirmed facts

- Current `StateBuilder` scans before deciding `since_hash` is unchanged and returns refs without full provenance.
- Current waits use fixed sleeps/page promises in several navigation paths.
- `chrome.debugger` can detach on tab close/DevTools and has restricted domains; event attribution must include tab/session/frame/document generations.
- Native/extension payloads are bounded; large artifacts need host-side handles/chunks.

## Working assumptions

- Accessibility/DOMSnapshot plus content-script fallback can produce a compact semantic snapshot for supported pages.
- Chrome debugger events plus tabs/webNavigation/content events can mark pages dirty without polling every call.
- Host-side cache/ring memory fits Phase 0 budgets; event lag forces resync.
- A safe action can be validated immediately before dispatch and by a postcondition after dispatch.

## Unresolved questions

- **U5-1:** exact dirty-event coverage on complex pages; owner: Japneet Kalkat; resolve with fixture mutation matrix.
- **U5-2:** actionability fallback for cross-origin/OOPIF frames; owner: Japneet Kalkat; resolve with capability tests.
- **U5-3:** screenshot/PDF artifact throughput; owner: Japneet Kalkat; resolve with chunk/artifact benchmark.

## Scope

### In scope

- Host event router, bounded queues, sequences, lag/resync.
- Dirty/versioned snapshot cache, compact deltas, token budgets.
- Provenance-bearing refs and frame/document routing.
- Event-driven navigation/network/DOM/tab/download waits.
- Actionability, postconditions, idempotency, unknown/reconciliation.
- User confirmation boundaries for login/payment/destructive/evaluate/upload/cookie actions.
- Scoped logs/network/dialog/download state.

### Out of scope

- New space/ledger lifecycle.
- UI styling.
- Modern MCP transport.
- Hidden LLM reasoning or selector guessing.

## Snapshot invariants

- Clean cache returns `changed=false`, snapshot version/hash metadata, no DOM payload, and zero DOM/AX scan.
- Dirty capture returns full or bounded delta against a retained base; expired/lagged base returns `resync_required=true`.
- `mode=auto|full|min|focus` and legacy `since_hash` are compatibility inputs mapped to the canonical cache.
- Cache states are `cold`, `clean`, `dirty`, `rebuilding`, `resync_required`, and `degraded`; keys include space/page, mode, focus, budget, tokenizer, frame topology, and generation. Identical concurrent captures are single-flight; stale writers cannot overwrite newer generations; reconnect invalidates affected caches; raw page bodies are memory-only.
- Refs include space/page/frame/snapshot/document/navigation provenance plus bounded lifetime/ref epoch; navigation, rerender, frame replacement, raw evaluate, prerender/BFCache replacement, execution-context destruction, and relevant mutation invalidate them.
- Token metrics identify tokenizer/version/hash and separately report transport bytes, UTF-8 bytes, serialized tokens, and deployed model-context tokens; budgets reserve space for provenance/truncation metadata.
- Output is scoped to the requested space/page unless a compatibility inventory explicitly asks for metadata. Deltas are emitted only when measured final serialized cost is below a valid full/min alternative; partial multi-frame snapshots cannot issue refs.

## Action/wait invariants

Lease epoch is checked at enqueue, dequeue, and immediately before extension dispatch. Takeover also requires the extension fence barrier to reject lower-epoch commands at execution time. Waits register a watermark before triggering actions and replay only bounded cached events; lag forces resync. A lost response after dispatch is `unknown`; only reads, metadata, lease renewal, and re-registerable waits may retry automatically. Cancellation and timeout outcomes are determined by dispatch state, never by transport closure alone; late responses reconcile the original action only.

| Operation               | Pre-dispatch retry         | Lost response                             | Automatic replay       |
| ----------------------- | -------------------------- | ----------------------------------------- | ---------------------- |
| snapshot/list/read      | yes if identity valid      | reconcile/read                            | bounded                |
| wait                    | re-register from watermark | cancelled/unknown                         | re-register            |
| navigate/reload/history | only before send           | unknown                                   | never                  |
| click/input/drag/upload | only before send           | unknown + postcondition inspect           | never                  |
| storage/cookie/evaluate | only before send           | unknown + explicit confirmation if needed | never                  |
| close/release           | only before send           | reconcile target absence                  | only if absence proven |

## Tasks

- [ ] P5-T1 — Add typed host event envelopes, sequences, and bounded routing.
  - **Files:** `crates/agentyc-host/src/{events.rs,event_router.rs}`, `crates/agentyc-core/src/events.rs`, extension event adapter.
  - **Done when:** every event has profile/space/page/target/session/frame/document attribution internally, broker sequence/watermark, bounded queue behavior, and explicit lag/resync; missing attribution is rejected rather than routed to an active page. Events include event ID, broker epoch, scope, generation, dirty reason, coalescing marker, heartbeat, and at-least-once replay semantics; high-frequency DOM events coalesce into semantic dirty notifications.
  - **Validation:** two-page identical-request, OOPIF attach/detach, tab replacement, debugger detach (`target_closed` and `canceled_by_user`), overflow/backpressure, subscriber fairness, resume-gap, duplicate delivery, event-before-waiter, and extension reconnect tests.
  - **Owner:** Japneet Kalkat.

- [ ] P5-T2 — Implement snapshot cache, dirty tracking, and delta serializer.
  - **Files:** planned `crates/agentyc-host/src/{snapshot_cache.rs,snapshot_builder.rs}`, reuse `crates/agentyc-dom/src/{service.rs,clickable.rs}`, and document the Phase 8 migration seam from the existing `crates/agentyc-mcp/src/state.rs` path; do not change MCP ownership in this phase.
  - **Done when:** clean cache performs zero DOM/AX scan; dirty capture emits deterministic full/delta; base expiry emits resync; snapshots are space/page scoped and token bounded; dirty reasons conservatively include event gaps, navigation/document/frame replacement, geometry/scroll, raw evaluation, takeover, debugger/worker/host reconnect, and attribution failure; delta chains have bounded depth/operation count and single-flight rebuilds.
  - **Validation:** local fixtures for mutation, rerender, navigation, frame changes, dense tables, dynamic feeds, frame partiality, event gaps, reconnect, cache races, and stale writers; actual tokenizer assertions, hash/delta goldens, scan counters, actionable-control coverage, and resync tests.
  - **Owner:** Japneet Kalkat.

- [ ] P5-T3 — Implement provenance-bearing refs and frame-aware resolution.
  - **Files:** `crates/agentyc-host/src/ref_registry.rs`, `crates/agentyc-dom/src/service.rs`, extension frame bridge, legacy adapter.
  - **Done when:** refs carry frame/document/snapshot/navigation generations, ref epoch, bounded expiry, and last-validated generation; action execution re-resolves and validates the ref inside the same pre-dispatch gate; cached geometry/backend node IDs are never authoritative and stale hints never auto-select replacements.
  - **Validation:** rerender/navigation, same-origin nested frame, OOPIF, cross-origin denial, shadow DOM, detached node, prerender/BFCache replacement, reused frame/context IDs, late old-session events, raw-evaluate invalidation, and same-tab target replacement tests.
  - **Owner:** Japneet Kalkat.

- [ ] P5-T4 — Replace fixed sleeps with event-driven waits.
  - **Files:** `crates/agentyc-host/src/waits.rs`, extension event mapping, `crates/agentyc-runtime/src/lib.rs`, legacy navigation adapters.
  - **Done when:** URL/history/reload/network-idle/request/response/stable-DOM/element/page/download waits use identity/generation, watermarks, monotonic absolute deadlines, cancellation, and semantic postconditions; `network_idle` excludes scoped long-lived requests, WebSockets, downloads, and analytics; DOM stability requires mutation quiet plus geometry stability; polling is only a bounded fallback.
  - **Validation:** delayed navigation, redirect, same-document, response-before-request, event-before-waiter, long-lived requests, DOM/geometry churn, tab creation, generation replacement, disconnect, cancellation, deadline, and extension restart tests.
  - **Owner:** Japneet Kalkat.

- [ ] P5-T5 — Implement actionability and postcondition checks.
  - **Files:** `crates/agentyc-host/src/actionability.rs`, `actions.rs`, extension debugger/content action adapters.
  - **Done when:** click/type/fill/select/check/scroll/drag/key/upload/evaluate validates connectedness, frame/ref identity, visibility, disabled/readonly, geometry/hit target, user-control state, and final postcondition; only known transient failures retry with fresh resolution.
  - **Validation:** hidden/disabled/readonly/covered/moving/rerendered/offscreen/shadow/OOPIF/file chooser/coordinate fallback fixtures.
  - **Owner:** Japneet Kalkat.

- [ ] P5-T6 — Implement typed unknown outcomes and reconciliation.
  - **Files:** `crates/agentyc-host/src/{actions.rs,reconcile.rs}`; `agentyc-core` receipts; direct CLI/SDK result types. MCP maps these canonical results only in Phase 8 and is not a Phase 5 dependency.
  - **Done when:** disconnect after dispatch returns `unknown` with action/reconcile token; status survives reconnect; reconciliation inspects current state and never blindly replays a mutation.
  - **Validation:** navigation/click/form/close/storage/evaluate disconnect-after-send tests; user takeover during dispatch; proof success/failure/confirmation paths.
  - **Owner:** Japneet Kalkat.

- [ ] P5-T7 — Scope browser side state and implement confirmation boundaries.
  - **Files:** host observability/dialog/download/mock modules; extension event adapters; `docs/security/user-control.md`.
  - **Done when:** logs/dialogs/network/downloads/traces are space/page scoped; login challenge/payment/destructive submit/permission/upload/cookie/evaluate boundaries pause or request a bound single-use user-intent ticket; page text, focus, or a page click cannot suppress policy or prove takeover.
  - **Validation:** two-space event isolation, hostile page instructions, dialog routing, mock failure, user confirmation/cancel, and redaction tests.
  - **Owner:** Japneet Kalkat.

- [ ] P5-T8 — Run context/performance benchmark and tune only with evidence.
  - **Files:** `tests/direct_benchmark.rs`, snapshot/action fixtures, `docs/release-gate.md`, `research/production-test-strategy.md`.
  - **Done when:** clean scan, delta ratio with equivalent actionable-control coverage, tokenizer, warm action/wait, batch round trips, native artifact throughput, event lag, stale-ref/unknown rates, human-tab responsiveness, and RSS meet Phase 0 budgets or a new decision records the adjustment.
  - **Validation:** 10 warmups or warmup-until-stable, at least 200 valid samples for p95 and 1,000 for p99 per blocking cell, bootstrap 95% confidence intervals, raw samples and baseline manifest under `artifacts/p5-performance/`; cells cover cold/warm, clean/dirty/resync, full/min/focus/delta, 1/2/4/8 spaces, nested/OOPIF, mutation bursts, event gaps, reconnect, and user-tab activity.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [ ] Clean snapshots do not scan.
- [ ] Delta/resync and token metrics are measured, not guessed.
- [ ] Every ref/action/wait resolves current logical/generation identity immediately before use.
- [ ] No missing-session/event path mutates the selected page.
- [ ] No mutating operation silently succeeds after user takeover or disconnect.
- [ ] Cleanups after stop/crash/update/uninstall retain pages unless an explicit fresh-proof confirmation operation is executing.
- [ ] Extension capability gaps return explicit typed results.

## Handoff out

- **Artifacts:** event router, snapshot/ref cache, wait/actionability engines, typed outcomes, user-control guards, benchmark report.
- **Next phase:** Phase 6 exposes these operations through persistent CLI/SDK clients.
- **Residuals:** MCP serialization and legacy field mapping remain adapter work.

## Exit gate

Advance only when all focused host/extension/DOM tests pass, context/latency budgets are recorded, no correctness path depends on fixed sleep, stale refs/events are safe, and every mutation returns a typed terminal or unknown/reconciliation outcome.
