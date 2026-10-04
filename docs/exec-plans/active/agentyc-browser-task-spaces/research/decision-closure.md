# Decision closure — agentyc Browser Task Spaces

> **Supersession notice (2026-10-01):** The original D-01/D-06/D-08 recommendations below were closed for the former MCP-first, managed/attached-CDP scope. The user then clarified the product boundary: full ego-lite-like task spaces in the user's existing Chrome, no automatic browser download/launch, and no MCP primary surface. Those records remain as historical evidence; the active recommendations are D-09–D-17 below. See `decision-supersession.md`.

# Active closure — existing Chrome and non-MCP primary

## D-09 — Canonical browser integration

**Recommendation:** Use a Chrome MV3 extension plus a local Native Messaging bridge to control the user's already-running Chrome. The extension uses `chrome.debugger` as the primary CDP transport, `chrome.tabs`/`chrome.tabGroups` for page creation and visual grouping, content scripts/page bridges for narrowly scoped DOM work, and a Chrome side panel for user control. The native host forwards validated messages to the host broker; Native Messaging is not the agent-facing API.

**Why this wins now:** Chrome 136+ blocks remote-debugging switches against the default profile (S-018); Chrome officially supports tab-scoped debugger commands/events and flat related-target sessions from Chrome 125 (S-019); Native Messaging provides an exact-origin local bridge but does not itself automate pages (S-020).

**Trade-offs accepted:** The extension needs sensitive permissions and a production distribution/install path; debugger domains are allowlisted; the broker must handle MV3 restarts and bounded message sizes.

**Alternatives rejected:** CDP-only sidecar requires an exposed non-default profile/port and lacks the requested user UI; managed BrowserContext is a different browser/profile; content-script-only control has weaker frame/network parity.

**Confidence:** medium/high; the architecture is evidenced, while the exact Chrome/enterprise capability matrix is a Phase 0 gate.

**What would change the decision:** the real-Chrome vertical slice cannot attach/control agent-owned tabs, a required domain is unavailable on the supported Chrome floor, or a reviewed browser-native alternative provides stronger guarantees.

**Stop rationale:** Official Chrome debugger, Native Messaging, service-worker, content, distribution, tab-group, and side-panel sources were retrieved; independent reviews converged; remaining uncertainty is runtime/version validation, not the integration branch.

## D-10 — Existing-profile guarantees

**Recommendation:** Treat spaces as logical ownership/presentation boundaries inside one Chrome profile. Cookies, local/session storage, extensions, history, permissions, and downloads are shared unless independently proven otherwise. Agent-created pages are placed in a product-owned Chrome tab group. Existing/user tabs remain unmanaged until explicit adoption with user confirmation. No label, tab-group ID, or active-tab state grants authority.

**Why this wins now:** Chrome tab/group IDs are session-scoped (S-023), and the extension APIs do not create BrowserContext-like storage isolation. The honest guarantee is live user login availability plus logical ownership, not per-space cookie isolation.

**Trade-offs accepted:** Agents can operate with existing login state, but cross-space data separation is not promised; a future isolated-profile mode requires a new product decision.

**Confidence:** high for the limitation; medium for site-specific permission/storage behavior until Phase 0 capability tests.

**What would change the decision:** users require hard storage isolation for the primary workflow; then add an explicit managed/isolated-profile product mode rather than silently changing existing Chrome behavior.

**Stop rationale:** The requirement explicitly selects the user's Chrome and official tab/storage/debugger contracts do not provide logical BrowserContexts; the remaining work is to enumerate capability-specific exceptions.

## D-11 — Task-space lifecycle and user control

**Recommendation:** Make the task space the primary object, durable pages its children, and Chrome tabs internal inventory. Use `created`, `agent_owned`, `paused`, `user_owned`, `handoff_requested`, `orphaned`, `recovering`, `finished`, and `released` states. Provide side-panel controls for pause, stop, take over, return control, handoff, finish, retain, and release. User takeover increments/fences the lease epoch and rejects new mutations; dispatched actions become `unknown` until reconciled.

**Why this wins now:** It transfers ego-lite's task-space/handoff/retention pattern without copying its proprietary host and gives the user a visible control boundary in ordinary Chrome (S-010–S-014, S-023).

**Trade-offs accepted:** A side panel and explicit user prompts add UX/installation work; “finish” retains pages by default unless the user or agent explicitly requests owned-page cleanup.

**Confidence:** medium/high; state semantics are closed, visual details are Phase 1/3 contracts.

**What would change the decision:** headed Chrome testing shows users cannot distinguish agent-owned pages or safely take control.

**Stop rationale:** The lifecycle is necessary for safe coexistence and is independently supported by the product pattern and Chrome side-panel capability.

## D-12 — Canonical identity and presentation

**Recommendation:** Use canonical opaque `space_id` and durable `page_id` labels, plus frame/document/navigation generations, snapshot/ref versions, and action IDs. `space` is the user-facing term; `group_id` is only a deprecated MCP alias or visual-group hint and never a second logical object. Keep Chrome `tabId`, CDP target/session IDs, and extension tab-group IDs internal. New outputs never render `[id] name` or raw tab/target IDs; legacy MCP fields are adapter-only.

**Why this wins now:** Existing short IDs and one active page are unsafe; ego-lite separates durable page labels from live targets; Chrome IDs are session-scoped (S-001, S-012, S-019, S-023).

**Trade-offs accepted:** The host resolves more handles and compatibility adapters remain temporarily complex.

**Confidence:** high.

**What would change the decision:** a required client contract cannot use structured opaque handles; selection can remain a connection convenience but never authority.

**Stop rationale:** No competing identity scheme offers stable routing without exposing browser internals.

## D-13 — Host-owned broker and local protocol

**Recommendation:** Add `agentyc-core` for transport-neutral domain contracts and `agentyc-host` for the persistent broker, ledger, local IPC, Native Messaging bridge, Chrome adapter, leases, scheduling, event routing, snapshots, and reconciliation. Agents connect over an owner-readable Unix socket on macOS/Linux or named pipe on Windows. Use versioned length-delimited JSON envelopes with request IDs, deadlines, cancellation, structured errors, action receipts, events/watermarks, and bounded artifact handles.

**Why this wins now:** Separate agent processes must share one authority; Native Messaging only connects Chrome extension contexts to a host (S-020); MV3 workers can terminate and cannot own leases in memory (S-021).

**Trade-offs accepted:** A daemon/bridge/installer is more work than an in-process MCP server; local IPC, host locks, and version migration become release surfaces.

**Alternatives rejected:** One broker per MCP/stdio process cannot coordinate multiple agents; newline-only CLI invocations lose warm state; Native Messaging alone cannot serve independent agents.

**Confidence:** high for boundary, medium for exact platform startup/installer behavior.

**What would change the decision:** a platform cannot provide a safe local IPC/host registration path or a single broker cannot serve the required Chrome profile.

**Stop rationale:** The authority boundary follows the actual multi-process requirement and is independent of MCP.

## D-14 — Context-efficient automation

**Recommendation:** Keep the cached compact snapshot/delta, provenance-bearing refs, event watermarks, per-group queues, event-driven waits, actionability checks, postconditions, typed `unknown` outcomes, and no blind replay. Make the runtime transport-neutral and feed it debugger/content-script events from the extension. Gate arbitrary page evaluation behind explicit capability/policy.

**Why this wins now:** These mechanisms are supported by the prior local/ego-lite/CDP evidence (D-04/D-05, S-001, S-011–S-014) and remain necessary regardless of transport.

**Trade-offs accepted:** Extension capability gaps require partial/unsupported results; cache invalidation and chunking add complexity.

**Confidence:** high for direction; medium for exact scan/token/latency budgets until real-Chrome benchmarks.

**What would change the decision:** real Chrome misses dirty events or debugger/content capability prevents actionable snapshots/actions; then narrow supported operations or add a tested fallback.

**Stop rationale:** Transport changes do not invalidate the reliability/context mechanisms; only runtime evidence can tune them.

## D-15 — MCP compatibility boundary

**Recommendation:** Keep MCP as an adapter over the host broker/core. Preserve legacy `agentyc mcp`/`serve` and required `rmcp 1.7` stdio/legacy Streamable HTTP behavior during migration, but do not make MCP the canonical state owner, primary UX, or release acceptance gate. Freeze the measured default and extended tool profiles, preserve `isError=true` for tool execution failures, distinguish protocol JSON-RPC errors, scope every close path to broker-owned pages, keep raw IDs adapter-only, and map each transport connection to a host-assigned principal.

**Why this wins now:** Existing clients need a migration path, but the clarified product explicitly removes MCP as primary. The adapter can preserve compatibility without coupling host identity to `rmcp 1.7` (S-003–S-008).

**Trade-offs accepted:** Two surfaces need contract tests and deprecation docs; some legacy calls will return typed unsupported/scope errors.

**Confidence:** high.

**What would change the decision:** no meaningful clients remain, or a future transport is approved as a separate product decision.

**Stop rationale:** Compatibility is valuable, but it no longer defines the architecture.

## D-16 — Primary SDK and CLI

**Recommendation:** Ship a persistent JSON CLI and a thin typed Node SDK over the same local protocol. Task-space/page objects, batch execution, snapshots, actions, waits, events, handoff, finish, and reconciliation are canonical. The SDK is a client only and never embeds arbitrary browser code.

**Why this wins now:** It gives coding agents a direct interface without MCP round trips, preserves the ego-lite script ergonomics, and keeps one broker/ledger for multiple processes.

**Trade-offs accepted:** A small JS/TS distribution and protocol versioning are added; CLI and SDK outputs must stay synchronized.

**Alternatives rejected:** One-shot CLI runtime startup loses state; an embedded JS engine expands trust and maintenance scope; MCP cannot be the primary contract under the clarified requirement.

**Confidence:** medium/high; exact SDK packaging is Phase 2/5 work.

**What would change the decision:** agent environments cannot install/use the SDK or local CLI, in which case the versioned local protocol remains canonical and a new client adapter is added.

**Stop rationale:** The direct surface is reversible, testable, and required for non-MCP operation.

## D-17 — Permissions, privacy, and distribution

**Recommendation:** Use a stable production extension ID with exact Native Messaging `allowed_origins`; use an unpacked development ID separately. Treat the caller origin as Native Messaging transport metadata, not an extension-supplied JSON field. Validate exact origin, enrolled profile binding, nonce/sequence, protocol version, schema, capability, and cumulative payload limits. A presented `profile_instance_id` is only a binding selector, never authentication: copied profiles, reinstall/storage reset, extension-ID change, or mismatch enter `rebind_required` and require explicit user confirmation before mutation authority returns. Keep sensitive capabilities opt-in and redact secrets/page contents from logs and ledgers.

**Why this wins now:** Chrome Native Messaging requires exact origins and bounded frames (S-020); debugger/content/scripting permissions are powerful and page data is untrusted (S-019, S-022, S-024).

**Trade-offs accepted:** Production distribution depends on Chrome Web Store or managed deployment; some users/enterprise policies may deny debugger or host permissions.

**Confidence:** high for requirements, medium for distribution readiness.

**What would change the decision:** distribution or policy constraints make the supported distribution impossible; record a new supported-distribution decision.

**Stop rationale:** Security/installation constraints are explicit before implementation rather than hidden in a later hardening phase.

---

## D-01 — Runtime isolation

**Recommendation:** Use one `BrowserBroker` per browser instance/server and map each managed group to one CDP `BrowserContext`/cell. For an attached endpoint, require explicit `exclusive_attached` mode before mutation: use contexts when available; otherwise allow at most one explicitly opted-in logical group with a shared-profile warning. Treat ordinary `shared_external` attachment as inventory-only and deny page reads, adoption, and mutation. Separate-browser escalation is deferred to a new decision.

**Why this wins now:** CDP directly supports context-scoped targets and disposal (S-009). ego-lite’s Space model shows the desired user workflow—parallel tasks in one browser process without stealing the user’s active page (S-010). It preserves warm-browser startup and avoids pretending that tab labels isolate cookies/storage.

**Trade-offs accepted:** Context creation/disposal adds lifecycle complexity and memory; the one-group logical fallback shares profile state and is deliberately not a multi-agent isolation mode; shared external attachment becomes less permissive than the current unsafe behavior.

**Alternatives rejected:** Separate browser per group wastes process/memory and defeats fast shared infrastructure; labels over shared tabs do not isolate state or cleanup.

**Traps avoided:** Never claim group cookie/storage isolation when the CDP endpoint cannot create a context.

**Confidence:** high for architecture, medium for resource thresholds until Phase 0 measurements.

**What would change the decision:** context creation is unsupported or unreliable on a supported Chrome line, the one-group logical fallback is insufficient for a validated use case, or measured memory/latency exceeds the approved budget; then open a separate process-isolation decision.

**Stop rationale:** Official CDP and ego-lite sources cover the mechanism; local runtime evidence identifies the missing boundary; the remaining uncertainty is measurable resource cost, not an architectural unknown.

## D-02 — Identity and routing

**Recommendation:** Make opaque canonical `space_id` and `page_id` the public logical handles; resolve them to ephemeral target/session/frame state through a broker. Use explicit space/page fields on new tools. Keep a connection-scoped selected space/page only as a compatibility convenience. New APIs never expose raw target IDs or render `[id] name`; the legacy `TabInfo.target_id`/`tab_id` fields remain only in the explicitly scoped compatibility adapter until deprecation.

**Why this wins now:** CDP target IDs and session IDs have different lifetimes (S-009). ego-lite’s ledger separates durable page labels from live target IDs and reconciles them (S-012). The current short suffix can remain only in a scoped legacy adapter while the new API removes ambiguity.

**Trade-offs accepted:** New schemas carry more context and the server must resolve handles; existing MCP/CLI compatibility types need an adapter and deprecation period, so raw target IDs remain temporarily visible only to legacy callers.

**Alternatives rejected:** Selected-only routing races across clients; raw target IDs become stale; names are mutable and can collide.

**Traps avoided:** Do not infer ownership from the current active tab or from a target appearing after reconnect.

**Confidence:** high.

**What would change the decision:** a required client contract makes explicit space/page fields impossible; then the selected context remains connection-scoped, never process-global.

**Stop rationale:** Local API evidence, CDP identity semantics, and ego-lite’s page ledger all agree; no additional identity alternative changes the safety result.

## D-03 — Ownership, handoff, and durability

**Recommendation:** Make group ownership a lease with `principal_id`, `lease_epoch`, expiry, and capability checks. Use explicit `handoff_requested -> draining -> owned(new principal)` transitions. Persist a selective control-plane ledger atomically; do not persist/replay every raw CDP command. Existing/unmanaged pages require explicit adoption, and cleanup closes only proven broker-owned pages/contexts.

**Why this wins now:** Current agentyc has no ACL/lease/ownership layer (S-001). ego-lite records unmanaged targets, user-control boundaries, released labels, browser instance identity, and atomic ledger updates (S-012). CDP can emit multiple attach/detach events for one target, so fencing is needed beyond a mutex (S-009).

**Trade-offs accepted:** Lease expiry can temporarily block work and requires recovery UX; the ledger cannot recreate browser state and must reconcile live targets.

**Alternatives rejected:** No ownership is unsafe; boolean “in use” flags do not fence stale clients; raw command replay can duplicate irreversible actions.

**Traps avoided:** Treat target disappearance and lost responses as `unknown`, not as permission to replay.

**Confidence:** high for control-plane design, medium for exact TTL and storage locking.

**What would change the decision:** deployment requires multi-host shared brokers or a durable external store; add an authenticated broker/store adapter then.

**Stop rationale:** Ownership failure modes and comparable implementation patterns are covered; TTL/storage details are tunable Phase 0/6 parameters.

## D-04 — Context and snapshots

**Recommendation:** Build an atomic per-space/page snapshot cache with dirty/version tracking, bounded history, compact added/removed/updated deltas, token budgets, and full-resync fallback. Preserve `auto`, `full`, `min`, `focus`, and `since_hash` compatibility. Attach ref provenance to page/frame/document/snapshot/navigation generations; return stale-ref errors with a compact current hint.

**Why this wins now:** Current `since_hash` follows a full scan and its hash omits action-critical fields (S-001). ego-lite documents compact semantic snapshots and temporary refs (S-011) and its source maintains page-scoped event/delta state (S-013). A cached clean result is the only way to reduce both tokens and unnecessary browser work.

**Trade-offs accepted:** Cache invalidation and resync logic add complexity; token estimates depend on a selected tokenizer adapter.

**Alternatives rejected:** Full snapshots waste context; a hash-only response cannot recover a changed base; whole-round buffering has no agentyc execution-round model.

**Traps avoided:** Do not call byte/character counts “tokens”; measure with a declared tokenizer and report the estimator.

**Confidence:** high for direction, medium for dirty-event completeness until local fixtures validate it.

**What would change the decision:** cache invalidation misses meaningful page changes or the measured delta protocol is not smaller than compact min mode.

**Stop rationale:** Local code and ego-lite mechanisms establish the gap and candidate; benchmark data is intentionally deferred to Phase 0.

## D-05 — Concurrency and automation reliability

**Recommendation:** Replace one global active-page lifecycle with a target/session registry, per-group mutation mailbox, concurrent snapshot readers, browser-wide bounded CDP concurrency, event-driven wait coordinator, typed deadlines/cancellation, actionability checks, safe retries, and postconditions. Keep page switch as selection/validation, not detach-then-attach.

**Why this wins now:** Current switch is non-transactional and fixed sleeps/event loss are documented in S-001. CDP and ego-lite both require target/session-aware routing and target lifecycle handling (S-009, S-013).

**Trade-offs accepted:** Per-group queues can delay an agent behind its own long action; fairness/backpressure and unknown outcomes need explicit telemetry.

**Alternatives rejected:** A global mutex prevents concurrency; lock-free page actions allow ordering races; blind retries duplicate side effects.

**Traps avoided:** Treat broadcast lag, missing session ID, and target replacement as explicit reconciliation conditions.

**Confidence:** high for reliability mechanisms, medium for exact concurrency limits.

**What would change the decision:** benchmarks show per-group queues reduce throughput or a target cannot support multiple attached sessions; tune scheduler or isolate a group in another browser cell.

**Stop rationale:** The mechanism is supported by local fault analysis and CDP/ego-lite runtime patterns; limits are measured later.

## D-06 — MCP transport era

**Recommendation:** Keep legacy MCP semantics for this refactor because the repository pins `rmcp 1.7` and current tests use initialize/session behavior. Accept and preserve the existing `2024-11-05` initialize fixture; reject/not-claim `2025-11-25` and `2026-07-28` in this product until a deliberate SDK upgrade with dual-era fixtures. Introduce a transport-independent `ConnectionContext` and a versioned adapter.

**Why this wins now:** Official sources define materially different legacy and modern transports (S-003–S-007), and `rmcp 1.7` source confirms the current implementation is legacy (S-008). Group ownership can be correct within current HTTP session semantics without changing the wire protocol.

**Trade-offs accepted:** Modern clients/features are deferred; the current HTTP server keeps legacy session IDs and GET/DELETE until a separate migration.

**Alternatives rejected:** Switching now risks client breakage and requires an SDK that is not present; mixing both eras without detection is unsafe.

**Traps avoided:** Do not treat MCP session identity as browser target ownership; a connection must claim a group explicitly.

**Confidence:** high.

**What would change the decision:** `rmcp` gains stable modern support and a compatibility test matrix passes, or users require modern-only deployment.

**Stop rationale:** Current dependency and official version contracts are verified; the migration seam keeps the future decision reversible.

## D-07 — Control-plane persistence

**Recommendation:** Persist space/page logical records, browser-instance epoch, ownership transitions, released labels, and reconciliation hints in an atomic local ledger at `${AGENTYC_STATE_DIR:-~/.agentyc/state}` with restrictive permissions. Treat stored target/session IDs as non-authoritative hints; if the browser instance changes, mark pages unknown/unmanaged until adoption/recovery. Corrupt ledgers are quarantined and never repaired by guessing.

**Why this wins now:** ego-lite’s page ledger uses atomic replacement, browser instance identity, released labels, and explicit reconciliation (S-012). The agentyc use case benefits from task-space continuity, but browser side effects are not safely replayable.

**Trade-offs accepted:** A local ledger is single-host durability, not a multi-host database; recovery may require explicit user/agent action.

**Alternatives rejected:** In-memory-only state loses task continuity; raw command/event sourcing cannot safely recreate browser side effects; a new database is unnecessary for this local native binary.

**Traps avoided:** Do not auto-adopt pages after a user-control boundary or new browser instance.

**Confidence:** medium/high; file locking and restart tests are required.

**What would change the decision:** multiple broker hosts or shared network deployment becomes supported; add an authenticated external store and lease service.

**Stop rationale:** The persistence boundary is clear and reversible; implementation details are bounded by Phase 0/3 tests.

## D-08 — HTTP and attached-browser trust boundary

**Recommendation:** Keep new-mode HTTP loopback-only by default and require exact `Origin`/`Host` validation before session creation. Non-loopback binds are disabled unless a separate authenticated-service decision enables a protected bearer token, exact allowed origins, and no wildcard CORS. `Mcp-Session-Id` is never authentication. Direct attached-CDP modes remain explicit legacy/test paths; they cannot be selected implicitly by extension failure.

**Why this wins now:** The current CLI defaults HTTP to loopback but accepts arbitrary host configuration and attached CDP without broker/auth ownership (S-017). MCP’s official HTTP guidance requires Origin validation and recommends authentication/localhost binding (S-006). CDP contexts can isolate managed groups, but a shared external endpoint cannot prove cookie/storage or target ownership (S-009).

**Trade-offs accepted:** Non-loopback deployment needs explicit configuration; ordinary attached-browser use becomes read-limited; users who need mutations must declare exclusivity or use a managed browser.

**Alternatives rejected:** Trusting `Mcp-Session-Id` as authentication is not valid; silently adopting shared tabs is unsafe; a broad unauthenticated remote mode is out of scope.

**Traps avoided:** `browser_evaluate` is mutation-capable even when it appears to be a read, so it requires the owner lease.

**Confidence:** high for the local security boundary, medium for future remote deployment policy.

**What would change the decision:** a reviewed authenticated multi-tenant broker, a validated shared-read permission model, or a separate process-isolation architecture is added.

**Stop rationale:** Local CLI/server evidence, official MCP security guidance, CDP context semantics, and attached-browser failure modes are covered; remote multi-tenant auth remains explicitly deferred.
