# Source ledger — agentyc Browser Task Spaces

## S-001 — Current agentyc repository baseline

- **Canonical repository:** local workspace `/Users/japneetkalkat/agentyc`, commit `0c97698`
- **Source class:** local repository
- **Version/freshness:** workspace version `2.0.0`, Rust edition 2024; retrieved 2026-10-01
- **Evidence locations:** `Cargo.toml`; `crates/agentyc-mcp/src/lib.rs`; `crates/agentyc-mcp/src/tools/mod.rs`; `crates/agentyc-mcp/src/state.rs`; `crates/agentyc-browser/src/session.rs`; `crates/agentyc-runtime/src/lib.rs`; `crates/agentyc-cdp/src/client.rs`; tests/docs listed in `README.md` and `docs/architecture.md`
- **Claim:** The current architecture has one MCP `ServerState`, one `BrowserSession.active_page`, four-character tab aliases, global event/capture state, full-scan state hashing, and unsafe global close/session placeholders.
- **Decision impact:** D-01, D-02, D-03, D-04, D-05; all phases
- **Limits:** Static code evidence; does not prove live Chrome behavior.
- **Confidence:** high
- **Corroborated by:** S-002, S-008, S-009, S-012, S-013

## S-002 — Existing tests and release gates

- **Canonical repository:** local `tests/`, `crates/agentyc-tests/`, `docs/release-gate.md`
- **Source class:** local operational evidence
- **Version/freshness:** commit `0c97698`; retrieved 2026-10-01
- **Evidence locations:** `tests/mcp_protocol.rs`; `tests/browser_automation.rs`; `tests/e2e_suite.rs`; `tests/benchmark.rs`; `crates/agentyc-tests/src/runner.rs`; `docs/release-gate.md`
- **Claim:** Current gates cover protocol/tool compatibility, basic browser lifecycle, and MCP transport overhead, but not group isolation, context tokens, concurrent ownership, or scoped event correctness.
- **Decision impact:** D-01, D-04, D-05; Phase 0 and Phase 7 direct-launch validation
- **Limits:** Some browser tests require Chrome and existing scenario runner has false-green paths.
- **Confidence:** high
- **Corroborated by:** S-001

## S-003 — MCP legacy Streamable HTTP transport

- **Canonical URL:** <https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>
- **Source class:** official specification
- **Version/freshness:** MCP `2025-11-25`; Firecrawl scrape 2026-10-01
- **Evidence locations:** Streamable HTTP, session management, protocol-version header, resumability sections
- **Claim:** Legacy Streamable HTTP uses POST/GET, may assign `MCP-Session-Id`, uses `Last-Event-ID` for resumability, and requires the negotiated protocol header on subsequent requests.
- **Decision impact:** D-06; Phase 2 and Phase 5
- **Limits:** Does not describe current modern `2026-07-28` behavior.
- **Confidence:** high
- **Corroborated by:** S-004, S-006

## S-004 — MCP legacy lifecycle

- **Canonical URL:** <https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle>
- **Source class:** official specification
- **Version/freshness:** MCP `2025-11-25`; Firecrawl scrape 2026-10-01
- **Evidence locations:** initialization, operation, shutdown, timeouts, cancellation sections
- **Claim:** Legacy clients initialize first, send `initialized`, and should use bounded request timeouts/cancellation; stdio shutdown is signaled by closing input.
- **Decision impact:** D-06; Phase 5 and Phase 6
- **Limits:** The transport SDK may expose only a subset of cancellation hooks.
- **Confidence:** high
- **Corroborated by:** S-003, S-006

## S-005 — MCP modern stdio and versioning

- **Canonical URLs:** <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio>, <https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning>
- **Source class:** official specification
- **Version/freshness:** MCP `2026-07-28`; Firecrawl scrape 2026-10-01
- **Evidence locations:** per-request metadata, cancellation, shutdown, modern/legacy era sections
- **Claim:** Modern MCP has no initialization/session handshake; requests carry protocol metadata and stdio uses `notifications/cancelled` for cancellation.
- **Decision impact:** D-06; future transport milestone
- **Limits:** Current `rmcp 1.7` does not advertise this version.
- **Confidence:** high
- **Corroborated by:** S-006, S-007

## S-006 — MCP modern Streamable HTTP and compatibility rules

- **Canonical URL:** <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>
- **Source class:** official specification
- **Version/freshness:** MCP `2026-07-28`; Firecrawl scrape 2026-10-01
- **Evidence locations:** POST-only transport, request-scoped SSE, Origin/Host security, header metadata, cancellation, removal of protocol sessions
- **Claim:** Modern Streamable HTTP removes `Mcp-Session-Id`, GET, DELETE, and resumable SSE; every request carries protocol metadata and closing a response stream cancels that request.
- **Decision impact:** D-06; future transport milestone and security review
- **Limits:** Must not be applied to the current legacy `rmcp 1.7` path without a deliberate SDK/contract upgrade.
- **Confidence:** high
- **Corroborated by:** S-005, S-007, S-008

## S-007 — MCP versioning and dual-era compatibility

- **Canonical URL:** <https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning>
- **Source class:** official specification
- **Version/freshness:** MCP `2026-07-28`; Firecrawl scrape 2026-10-01
- **Evidence locations:** modern/legacy terminology, compatibility matrix, server/discover guidance
- **Claim:** Modern and legacy MCP are distinct eras; a dual-era server must distinguish them rather than mixing session and per-request semantics.
- **Decision impact:** D-06; Phase 2/5 upgrade trigger
- **Limits:** No current agentyc client matrix was available.
- **Confidence:** high
- **Corroborated by:** S-003–S-006

## S-008 — rmcp 1.7 Streamable HTTP implementation

- **Canonical URL:** <https://docs.rs/crate/rmcp/1.7.0/source/src/transport/streamable_http_server/tower.rs>
- **Source class:** pinned implementation source
- **Version/freshness:** `rmcp 1.7.0`, released 2026-05-13; scrape 2026-10-01
- **Evidence locations:** `StreamableHttpServerConfig`, `stateful_mode`, `LocalSessionManager`, `handle_get`, `handle_post`, `handle_delete`
- **Claim:** `rmcp 1.7` defaults to stateful legacy HTTP with GET/POST/DELETE, `Mcp-Session-Id`, initialize workers, and local in-memory session management; its latest known protocol is `2025-11-25`.
- **Decision impact:** D-06; Phase 5
- **Limits:** SDK source can change in later releases; dependency is currently `rmcp = "1.7"`.
- **Confidence:** high
- **Corroborated by:** S-001, S-003, S-006

## S-009 — Chrome DevTools Protocol Target contract

- **Canonical URL:** <https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Target.pdl>
- **Source class:** official protocol source
- **Version/freshness:** rolling CDP source, retrieved 2026-10-01
- **Evidence locations:** `TargetInfo`, `attachToTarget`, `setAutoAttach`, `createBrowserContext`, `createTarget`, `disposeBrowserContext`, attach/detach/created/destroyed/info-changed events
- **Claim:** CDP provides browser contexts, context-scoped target creation, attached session IDs, target lifecycle events, and parent/frame metadata; target and session identity must be tracked separately.
- **Decision impact:** D-01, D-02, D-03, D-05; Phases 1, 3, 4
- **Limits:** Rolling tip-of-tree source is not a single Chrome milestone; Phase 0/7 direct-launch validation tests supported versions.
- **Confidence:** high
- **Corroborated by:** S-001, S-013

## S-010 — ego-lite Spaces

- **Canonical URL:** <https://lite.ego.app/document/en/docs/space>
- **Source class:** comparable product official docs
- **Version/freshness:** page retrieved 2026-10-01; docs describe current ego-lite behavior
- **Evidence locations:** Space definition, BrowserContext isolation, handoff, unmanaged tabs, task reuse, retention
- **Claim:** A Space is a parallel workspace in one browser process with an isolated BrowserContext, durable task reuse, explicit user-control handoff, and tabs retained for audit.
- **Decision impact:** D-01, D-03; product/API design
- **Limits:** Documentation is a product claim; agentyc must validate resource and behavior locally.
- **Confidence:** medium/high
- **Corroborated by:** S-012, S-013, S-014

## S-011 — ego-lite Snapshots

- **Canonical URL:** <https://lite.ego.app/document/en/docs/snapshot>
- **Source class:** comparable product official docs
- **Version/freshness:** page retrieved 2026-10-01
- **Evidence locations:** compact structured snapshot, token/noise rationale, temporary refs, resnapshot guidance
- **Claim:** Accessibility-derived compact snapshots reduce agent context versus raw HTML, and snapshot refs are temporary after page changes.
- **Decision impact:** D-04; Phase 4
- **Limits:** ego-lite’s token/benchmark claims are not agentyc measurements.
- **Confidence:** medium/high
- **Corroborated by:** S-001, S-012

## S-012 — ego-lite durable page ledger

- **Canonical URL:** <https://raw.githubusercontent.com/citrolabs/ego-lite/dca7003349c5f7132189ba00547cbbd7ff8e597e/package/ego-browser/src/page-ledger.ts>
- **Source class:** comparable implementation source
- **Version/freshness:** pinned upstream commit `dca7003`, retrieved 2026-10-01
- **Evidence locations:** `PageLedger`, `PageLedgerStore`, atomic rename, browser instance ID, unmanaged targets, user-control boundary, released labels, reconciliation
- **Claim:** Stable logical page labels, atomic ledger writes, browser-instance checks, unmanaged-tab tracking, and explicit reconciliation avoid trusting stale target IDs or silently adopting user tabs.
- **Decision impact:** D-02, D-03, D-07; Phases 1, 3, 6
- **Limits:** TypeScript implementation is a pattern source, not a drop-in Rust design; its raw target persistence must be treated as an internal reconciliation hint only.
- **Confidence:** high for mechanism, medium for direct fit
- **Corroborated by:** S-010, S-013, local tests

## S-013 — ego-lite target/session/event runtime

- **Canonical URL:** <https://raw.githubusercontent.com/citrolabs/ego-lite/dca7003349c5f7132189ba00547cbbd7ff8e597e/package/ego-browser/src/browser-runtime.ts>
- **Source class:** comparable implementation source
- **Version/freshness:** pinned upstream commit `dca7003`, retrieved 2026-10-01
- **Evidence locations:** `targetStates`, `sessionTargets`, parent/child target graph, page event subscribers, bounded queues, OOPIF attach/retry, request correlation, session invalidation
- **Claim:** Separating browser-level and page-level events, tracking target/session graphs, capping queues, and treating OOPIF disappearance as retryable improves reliability in a shared CDP connection.
- **Decision impact:** D-02, D-04, D-05; Phases 3/4/6
- **Limits:** Runtime is JavaScript/native-binding specific; behavior must be reimplemented and tested in Rust.
- **Confidence:** high for mechanism, medium for direct fit
- **Corroborated by:** S-009, local `agentyc-cdp` review

## S-014 — Vendored ego-lite checkout

- **Canonical repository:** local `reference/ego-lite-main/`
- **Source class:** local comparable implementation
- **Version/freshness:** user-provided current checkout; inspected before this plan
- **Evidence locations:** `package/ego-browser/src/page-model.ts`, `page-ledger.ts`, `page-ref-registry.ts`, `native-gate.ts`, `driver/page-actions.ts`, `driver/page-waits.ts`, `state.ts`, `skills/ego-browser/SKILL.md`
- **Claim:** The cloned project contains the implementation patterns used to compare task spaces, snapshots, refs, actionability, waits, and native control boundaries.
- **Decision impact:** D-01–D-05; findings and phase tasks
- **Limits:** Do not modify this reference tree; static inspection only.
- **Confidence:** high for local source observations
- **Corroborated by:** S-010–S-013

## S-015 — Firecrawl search access limit

- **Canonical operation:** Firecrawl MCP search attempts for MCP/CDP discovery
- **Source class:** research access evidence
- **Retrieved:** 2026-10-01
- **Claim:** Search calls returned HTTP 400; direct canonical scrapes succeeded and are the evidence used here.
- **Decision impact:** research confidence and Phase 0/7 external-version verification
- **Limits:** Search-index results were not available; this plan does not claim internet-wide saturation.
- **Confidence:** high
- **Corroborated by:** `.firecrawl/agentyc-browser-task-spaces-retrieval.md`

## S-016 — Parallel read-only architecture review

- **Canonical artifact:** `docs/exec-plans/active/agentyc-browser-task-spaces/research/findings-1.md` and `research/decision-exploration.md`
- **Source class:** local planning review
- **Version/freshness:** four independent read-only review passes, retrieved 2026-10-01
- **Evidence locations:** current MCP/control-plane review, CDP reliability review, architecture comparison, and protocol/source verification summarized in the linked findings and exploration files
- **Claim:** Independent reviews converged on brokered groups, explicit identity, lease fencing, scoped events/snapshots, and legacy transport compatibility; the review also identified the exact plan gaps corrected in the final audit.
- **Decision impact:** D-01–D-07; all phase gates
- **Limits:** Review output is design analysis, not runtime proof; Phase 0/7 measurements remain authoritative for performance and Chrome behavior.
- **Confidence:** medium/high
- **Corroborated by:** S-001–S-014

## S-017 — Local HTTP and CLI trust boundary

- **Canonical repository:** `crates/agentyc/src/main.rs::{Cmd::Serve,run_serve}`, `crates/agentyc-mcp/src/lib.rs::{run_stdio,BrowserServer::with_cdp_url}`, `docs/api.md`
- **Source class:** local repository/security surface
- **Version/freshness:** commit `0c97698`; retrieved 2026-10-01
- **Evidence locations:** `main.rs:39-49` default loopback host, `main.rs:186-204` HTTP service factory, `main.rs:31-37` CDP attach, API docs for `--host`/`--cdp-url`
- **Claim:** Current HTTP defaults to loopback but permits configurable host and current attached-CDP mode has no broker/auth trust boundary; the refactor must make non-loopback/auth and attached mode explicit.
- **Decision impact:** D-01, D-03, D-06; Phase 1 and Phase 5
- **Limits:** Local code does not prove deployment topology or user identity; bearer token/session policy is a planned server boundary.
- **Confidence:** high for current gap, medium for deployment policy
- **Corroborated by:** S-006, S-009, S-010, S-014

## Evidence categories

- **Confirmed facts:** S-001–S-009 and direct implementation/source observations.
- **Supported inferences:** S-010–S-013 patterns applied to agentyc, explicitly validated in Phase 0/7.
- **Working assumptions:** tokenizer representativeness, context resource cost, and Chrome milestone variance.
- **Unresolved:** modern `rmcp` support timing and whether a separate authenticated multi-process broker is worth adding after HTTP broker validation.

## Superseding evidence — existing Chrome, extension, and host

### S-018 — Chrome remote-debugging restriction

- **Canonical URL:** <https://developer.chrome.com/blog/remote-debugging-port>
- **Source class:** official Chrome security guidance
- **Version/freshness:** published 2025-03-17; retrieved 2026-10-01
- **Evidence locations:** changes to `--remote-debugging-port` and `--remote-debugging-pipe`
- **Claim:** From Chrome 136, remote-debugging switches are not honored against the default Chrome data directory and require a non-standard `--user-data-dir`.
- **Decision impact:** D-09; default path must use an extension/native bridge rather than a copied CDP URL or automatic profile launch.
- **Limits:** Does not describe extension debugger behavior or enterprise policy.
- **Confidence:** high

### S-019 — Chrome debugger extension API

- **Canonical URL:** <https://developer.chrome.com/docs/extensions/reference/api/debugger>
- **Source class:** official Chrome API reference
- **Version/freshness:** page updated 2026-09-11; retrieved 2026-10-01
- **Evidence locations:** permissions, restricted domains, target/frame semantics, flat sessions from Chrome 125, `onDetach`, `onEvent`, `sendCommand`
- **Claim:** An MV3 extension with the `debugger` permission can send an allowlisted subset of CDP commands to tabs, receive target-scoped events, and can attach related OOPIF sessions with flat sessions from Chrome 125; detachment occurs on tab close or DevTools use. The current agentyc adapter proves only root-target behavior; related-target/OOPIF execution remains an explicit unobserved residual.
- **Decision impact:** D-09, D-14, D-17; extension bridge, version floor, stale-target handling, and capability matrix.
- **Limits:** Does not grant all CDP domains; exact supported behavior must be tested on the chosen Chrome floor.
- **Confidence:** high

### S-020 — Chrome Native Messaging

- **Canonical URL:** <https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging>
- **Source class:** official Chrome API/host contract
- **Version/freshness:** page updated 2026-09-16; retrieved 2026-10-01
- **Evidence locations:** host manifest/`allowed_origins`, platform registration, stdio framing, sender origin argument, 1 MiB host-to-extension and 64 MiB extension-to-host message limits, content-script routing restriction
- **Claim:** Chrome starts a registered native host over stdin/stdout, uses length-prefixed UTF-8 JSON, passes the caller origin, requires exact allowed origins, and permits Native Messaging only from extension contexts rather than content scripts.
- **Decision impact:** D-09, D-13, D-17; thin bridge design, handshake/authentication, chunking, installer, and security tests.
- **Limits:** Native Messaging is IPC, not browser automation; extension APIs remain responsible for tab control.
- **Confidence:** high

### S-021 — MV3 service-worker lifecycle

- **Canonical URL:** <https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle>
- **Source class:** official Chrome extension lifecycle guidance
- **Version/freshness:** retrieved 2026-10-01
- **Evidence locations:** idle/shutdown behavior, persistence guidance, debugger/native messaging lifetime notes
- **Claim:** MV3 service workers can terminate after inactivity, global variables are not durable, debugger/native messaging activity affects lifetime, and reconnect handling is required after host failure.
- **Decision impact:** D-13/D-14; host ledger is authoritative and the worker stores only reconnect/profile metadata.
- **Limits:** Exact lifecycle timing varies by Chrome line and load.
- **Confidence:** high

### S-022 — Content scripts and scripting API

- **Canonical URLs:** <https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts>, <https://developer.chrome.com/docs/extensions/reference/api/scripting>
- **Source class:** official Chrome extension API reference
- **Version/freshness:** scripting page updated 2026-09-11; content-scripts page retrieved 2026-10-01
- **Evidence locations:** isolated worlds, messaging, host permissions, frame/document targeting, `executeScript` function serialization, `MAIN`/`ISOLATED` worlds
- **Claim:** Content scripts share the page DOM but run in isolated worlds and must relay privileged operations through extension contexts; scripting requires host permissions or `activeTab`, supports frame/document targets, and does not make arbitrary string evaluation the default.
- **Decision impact:** D-09, D-14, D-17; content/page bridge, prompt-injection boundary, evaluation policy, and frame routing.
- **Limits:** Host permission policy and page-specific CSP can affect coverage.
- **Confidence:** high

### S-023 — Chrome tabs, tab groups, and side panel

- **Canonical URLs:** <https://developer.chrome.com/docs/extensions/reference/api/tabs>, <https://developer.chrome.com/docs/extensions/reference/api/tabGroups>, <https://developer.chrome.com/docs/extensions/reference/api/sidePanel>
- **Source class:** official Chrome UX/control API reference
- **Version/freshness:** pages updated 2026-09-11/2026-09-24; retrieved 2026-10-01
- **Evidence locations:** tab create/group/remove/events, tab/group ID lifetimes, tab-group API availability, side-panel permissions and user-gesture rules
- **Claim:** Extensions can create/update/group/remove tabs, observe tab replacement/removal/update events, group tabs through `chrome.tabs.group`, manage tab-group presentation, and host persistent user UI in a side panel. A tab group belongs to one window, and Chrome tab/group IDs are session-scoped implementation values, not durable product identities.
- **Decision impact:** D-10–D-12; map product spaces to visual tab groups, preserve user tabs, and expose pause/takeover/finish UI without raw IDs.
- **Limits:** Side-panel and tab-group minimum Chrome versions must be checked against the chosen debugger floor and enterprise policy.
- **Confidence:** high

### S-024 — Extension storage and distribution

- **Canonical URLs:** <https://developer.chrome.com/docs/extensions/reference/api/storage>, <https://developer.chrome.com/docs/extensions/how-to/distribute>
- **Source class:** official Chrome persistence/distribution guidance
- **Version/freshness:** storage page updated 2026-09-11; distribution page retrieved 2026-10-01
- **Evidence locations:** storage areas/quotas/access levels; Web Store versus unpacked/self-hosted distribution
- **Claim:** Extension storage persists independently of page cache but has quotas/access levels; ordinary users install extensions signed and hosted by the Chrome Web Store, while self-hosting is for managed environments (including macOS); development can use trusted unpacked extensions only.
- **Decision impact:** D-13, D-17; worker metadata strategy, stable extension ID, Native Messaging `allowed_origins`, and installer.
- **Limits:** Distribution policy may vary by enterprise management and supported environment.
- **Confidence:** high

### S-025 — Superseding independent architecture reviews

- **Canonical artifact:** three read-only subagent reviews returned during the 2026-10-01 plan revision
- **Source class:** local planning review
- **Version/freshness:** retrieved 2026-10-01
- **Evidence locations:** review outputs summarized in `research/decision-supersession.md` and the revised phase plan
- **Claim:** Independent reviews converged on an existing-Chrome extension/native-host bridge, host-owned broker below MCP, a persistent local protocol, explicit shared-profile limits, user-visible task-space control, and a primary CLI/SDK.
- **Decision impact:** D-09–D-17 and all revised phase gates
- **Limits:** Reviews are design evidence, not runtime proof; Phase 0/7 real-Chrome validation remains authoritative.
- **Confidence:** medium/high

### S-026 — Current official Chrome contract refresh

- **Canonical URLs:** <https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging>, <https://developer.chrome.com/docs/extensions/reference/api/debugger>, <https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle>, <https://developer.chrome.com/docs/extensions/reference/api/tabs>, <https://developer.chrome.com/docs/extensions/reference/api/tabGroups>, <https://developer.chrome.com/docs/extensions/reference/api/sidePanel>, <https://developer.chrome.com/docs/extensions/reference/api/storage>, <https://developer.chrome.com/docs/extensions/develop/concepts/extensions-update-lifecycle>, <https://developer.chrome.com/blog/remote-debugging-port>, <https://developer.chrome.com/docs/extensions/how-to/distribute>
- **Source class:** official Chrome documentation and security guidance
- **Version/freshness:** directly fetched 2026-10-03; Context7 fallback resolved `/websites/developer_chrome_extensions` and `/websites/developer_chrome_extensions_reference_api` and returned the same official sources. Firecrawl MCP was unavailable (`ECONNREFUSED 127.0.0.1:3002`).
- **Claim:** Native Messaging requires exact origins/native-endian framing and bounded host-to-extension messages; MV3 storage/session state and `onStartup` govern worker/browser recovery; debugger attach/detach, flat child sessions, tab active/focus, group window/session scope, side-panel gestures, update-idle behavior, remote-debugging restrictions, permissions, and distribution limits are explicit.
- **Decision impact:** current Phase 0 hardening and Phase 1 residuals: same-session exact-hint recovery, session markers, focus fingerprint, lifecycle invalidation, event reduction, OOPIF partial status, and distribution scope.
- **Limits:** Official docs do not prove installed Chrome/enterprise policy behavior; headed artifacts and deterministic tests are required.
- **Confidence:** high for documented contracts; runtime confidence is bounded by the accepted artifact and test matrix.

### S-027 — Current ego-lite implementation comparison

- **Canonical repository:** `reference/ego-lite-main/`
- **Source class:** local comparable implementation and skill guidance
- **Version/freshness:** current checkout inspected 2026-10-03
- **Evidence locations:** `AGENTS.md`, `skills/ego-browser/SKILL.md`, `page-ledger.ts`, `page-ref-registry.ts`, `native-gate.ts`, `browser-runtime.ts`, `taskspace-e2e.test.mjs`
- **Claim:** Durable page labels, unmanaged-tab protection, browser-instance reconciliation, target/frame/document provenance, bounded queues, whole-operation serialization, explicit unknown outcomes, and cleanup/focus patterns were compared and carried forward where compatible.
- **Decision impact:** Phase 0 hardening and Phase 1 architecture boundaries.
- **Limits:** The reference's proprietary browser host and isolated BrowserContext are not product dependencies; selected-page retention, full OOPIF graph, and full actionability/waits remain later-phase work.
- **Confidence:** high for local source observations.

### S-028 — Current runtime and regression evidence

- **Canonical artifacts:** `artifacts/p0-coexistence/live-checkpoints-auto12/report.json`, extension/host/SDK tests, and `research/phase-0-*` audits
- **Source class:** current repository/runtime evidence
- **Version/freshness:** current checker/test runs 2026-10-03
- **Evidence locations:** Phase 0 checker report; 52 extension tests; 38 host tests including bridge event loss; 34 host-core integration tests; 10 direct CLI tests; 13 SDK tests; fmt/diff/compile checks.
- **Claim:** The bounded Phase 0 checker returns `status: pass`; hardening regressions are covered; primary changed files have no diagnostics.
- **Decision impact:** Phase 0 closure and Phase 1 activation.
- **Limits:** The accepted ten-scenario artifact predates the final source hardening. A current source-identical headed smoke at `artifacts/p0-coexistence/live-hardening-basic-3/` passed browser inventory, snapshots, actions, focus, isolation, and cleanup; its four disruptive restart/update checkpoints were intentionally not requested. Deterministic regressions cover the hardening, and a new approved full headed capture is required before treating the old ten-scenario artifact as a binary/source-identical release artifact.
- **Confidence:** high for deterministic code paths; medium for source-identical live runtime evidence.

## Superseding research limit

The direct official Chrome sources above were retrieved successfully. Firecrawl search remained unavailable, so no search-index result was treated as evidence. The accepted headed artifact closes the bounded Phase 0 coexistence gate; OOPIF/enterprise/distribution/deployed-tokenizer/selected-retention claims remain explicitly scoped to later phases rather than inferred from docs or disposable probes.
