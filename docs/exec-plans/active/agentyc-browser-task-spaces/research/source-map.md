# Source map — agentyc Browser Task Spaces

**Research date:** 2026-10-01  
**Decision scope:** task-space/group isolation, target identity, reliability, context/token efficiency, automation speed, MCP transport compatibility, and direct-launch readiness for the current agentyc workspace at commit `0c97698`.

## Q-01 — How should groups isolate concurrent agents?

- **Why it matters:** A tab label alone cannot prevent one agent from navigating, reading, or closing another agent’s page.
- **Required source classes:** local repository; official CDP documentation; comparable implementation/source; operational/resource evidence; failure-mode evidence.
- **Not applicable:** legal/jurisdictional sources; agentyc has no external data tenancy in this feature.
- **Queries/framing:**
  - symptom: `agentyc multiple agents shared browser tab isolation`
  - mechanism: `Chrome DevTools Protocol BrowserContext createTarget attach target ownership`
  - alternative: `browser context per task versus browser process per task memory concurrency`
  - failure mode: `CDP target close external attached browser ownership race`
- **Artifacts that can change the decision:** broker/context design, attached-browser policy, resource limits, isolation tests.
- **Status:** bounded and closed; live resource measurements remain Phase 0 validation.

## Q-02 — What identity and ownership model prevents stale/cross-agent actions?

- **Why it matters:** Current four-character target aliases, mutable active page, and missing leases allow stale operations after target replacement or handoff.
- **Required source classes:** local repository; official CDP target/session contract; comparable ledger/ownership implementation; security/failure evidence.
- **Not applicable:** provider-specific API docs; no external provider is introduced.
- **Queries/framing:**
  - mechanism: `CDP attachToTarget sessionId target lifecycle targetDestroyed detachedFromTarget`
  - alternative: `durable logical page label target ID reconciliation browser automation`
  - failure mode: `stale session old target event after reattach`
  - operational: `browser automation task ownership handoff unmanaged tabs`
- **Artifacts:** identity schemas, lease/fencing state machine, handoff tests, cleanup policy.
- **Status:** bounded and closed; cross-process broker need remains a non-blocking follow-up.

## Q-03 — How should context be compact, incremental, and useful?

- **Why it matters:** `since_hash` currently avoids payload output only after a full scan; agents still receive unrelated tab state and refs lack provenance.
- **Required source classes:** local snapshot code/tests; ego-lite snapshot/ledger/reference source; token measurement method; failure-mode evidence.
- **Not applicable:** a single model-provider tokenizer; the plan requires a tokenizer adapter because client models vary.
- **Queries/framing:**
  - symptom: `browser automation accessibility snapshot token cost full DOM`
  - mechanism: `incremental snapshot cache delta refs backend node frame document identity`
  - alternative: `full snapshot min snapshot delta snapshot event dirty cache`
  - failure mode: `stale browser snapshot refs navigation rerender iframe`
- **Artifacts:** snapshot envelope, delta protocol, ref registry, token benchmark, resync behavior.
- **Status:** bounded and closed; tokenizer choice is a Phase 0 measurement task.

## Q-04 — Which automation mechanisms improve reliability and speed?

- **Why it matters:** Fixed sleeps, method-only event subscriptions, non-transactional page switching, weak actionability, and swallowed errors create false success and long waits.
- **Required source classes:** local implementation/tests; official CDP event/target contract; comparable implementation; fault-injection evidence.
- **Not applicable:** external SaaS provider docs; browser behavior is CDP/Chrome-owned.
- **Queries/framing:**
  - mechanism: `CDP event session scoping network idle navigation lifecycle`
  - alternative: `per page actor queue browser automation concurrent tabs`
  - failure mode: `broadcast receiver lagged browser automation event loss`
  - operational: `browser automation actionability overlay postcondition retry unknown outcome`
- **Artifacts:** event router, wait coordinator, actionability primitive, typed outcomes, fake-CDP tests.
- **Status:** bounded and closed; Chrome milestone variance is tested in Phase 7 direct-launch validation.

## Q-05 — Which MCP transport semantics should the refactor preserve?

- **Why it matters:** Current `rmcp 1.7` implements legacy Streamable HTTP, while current MCP documentation defines a modern per-request-metadata era. Mixing them would break clients or create unsafe identity assumptions.
- **Required source classes:** local `rmcp` usage and tests; official legacy MCP specification; official modern MCP specification; pinned SDK source/release notes.
- **Not applicable:** authenticated browser research; no login is needed for public specifications.
- **Queries/framing:**
  - official contract: `MCP 2025-11-25 Streamable HTTP session Mcp-Session-Id`
  - recent change: `MCP 2026-07-28 Streamable HTTP remove sessions GET DELETE`
  - implementation: `rmcp 1.7 StreamableHttpServerConfig stateful mode`
  - failure mode: `MCP legacy modern transport compatibility migration`
- **Artifacts:** transport context boundary, stdio/HTTP semantics, compatibility tests, upgrade trigger.
- **Status:** closed for this refactor: preserve the legacy `rmcp 1.7` behavior and current `2024-11-05` fixture; `2025-11-25` and `2026-07-28` are researched alternatives only, not supported or negotiated by this plan. Phase 8 must publish the accepted-version/feature matrix and reopen this decision only with a deliberate SDK upgrade and dual-era fixtures.

## Q-06 — What trust boundary is required for HTTP and attached browsers?

- **Why it matters:** A non-loopback MCP server or shared CDP endpoint can expose logged-in browser state and enable cross-agent mutation without authentication or ownership checks.
- **Required source classes:** local CLI/server configuration; official MCP HTTP security guidance; official CDP target/context contract; comparable attached-browser ownership pattern.
- **Queries/framing:**
  - official contract: `MCP Streamable HTTP Origin Host authentication DNS rebinding`
  - local surface: `agentyc serve host default cdp-url attached browser ownership`
  - failure mode: `shared CDP endpoint unauthorized tab close cookie leakage`
- **Artifacts that can change the decision:** host/auth policy, attached-mode matrix, ACL/cleanup tests.
- **Status:** closed: loopback/no-auth is the local default; non-loopback requires bearer auth/origin policy; shared external CDP is inventory-only; exclusive attached mode is explicit.

## Source-class coverage and limits

- **Local repository:** inspected workspace manifests, MCP tools/state, browser/runtime/CDP crates, tests, docs, CI/release gate, and current git state.
- **Official documentation:** directly scraped official MCP legacy/modern pages and current CDP Target protocol source.
- **Comparable implementation:** directly scraped ego-lite Space/Snapshot docs and pinned `page-ledger.ts`/`browser-runtime.ts`; inspected the cloned reference tree.
- **Operational evidence:** existing agentyc benchmark/release thresholds and static reliability review were available; live Chrome and production telemetry were not available. New measurements are explicit Phase 0 tasks.
- **Search/access limit:** Firecrawl search returned HTTP 400 repeatedly. Known canonical URLs were scraped successfully; search-index saturation is therefore bounded, not claimed as internet-wide exhaustive.

## Final discovery passes

- **Pass A — mechanism analysis:** official MCP legacy/modern transport pages, rmcp 1.7 source, and CDP `Target.pdl` were read; this added the legacy/modern transport split, context creation, target/session lifecycle, and event-scoping requirements.
- **Pass B — failure/implementation analysis:** ego-lite ledger/runtime source and four independent repository reviews were read; this added atomic ledger writes, unmanaged-tab boundaries, OOPIF target graphs, event queue caps, unknown outcomes, and actionability/postcondition validation.
- **New decision-relevant facts in final two passes:** no additional architecture branch beyond the items captured in `research/decision-closure.md`.
- **Research status:** bounded with explicit ceiling; live browser/resource data and Firecrawl search results are carried into Phase 0 rather than hidden.

## Superseding source map — existing Chrome integration

The original Q-01–Q-06 map remains the evidence archive for reusable broker, identity, snapshot, and reliability mechanisms. The following questions supersede the old MCP-first/runtime-isolation framing.

### Q-07 — How can agentyc control ordinary installed Chrome without a CDP port?

- **Why it matters:** Chrome 136+ rejects remote-debugging switches for the default profile; a CDP-only sidecar cannot satisfy the existing-Chrome requirement.
- **Required source classes:** official Chrome remote-debugging guidance; official debugger API; official Native Messaging contract; local extension/host feasibility probe; security/failure evidence.
- **Queries/framing:** `Chrome 136 remote debugging default profile`; `chrome.debugger attach sendCommand onDetach flat sessions`; `Chrome Native Messaging allowed_origins framing size`; `Chrome MV3 service worker native messaging reconnect`.
- **Artifacts:** extension/native-host handshake, capability matrix, permission review, version floor, reconnect tests.
- **Status:** architecture selected; Phase 0 must prove the vertical slice and record any unsupported domains.

### Q-08 — How should spaces appear and coexist with user tabs?

- **Why it matters:** A product-level group must be visible and controllable without treating tab labels or numeric Chrome group IDs as authorization.
- **Required source classes:** official `chrome.tabs`, `chrome.tabGroups`, and `chrome.sidePanel` contracts; ego-lite task-space source/docs; local UX/ownership tests.
- **Queries/framing:** `Chrome extension tabGroups group tabs events`; `Chrome sidePanel persistent extension UI`; `Chrome tab IDs session lifetime`; `ego-lite task space handoff finish retain pages`.
- **Artifacts:** space/page state machine, side-panel interaction model, unmanaged/adoption behavior, no-`[id] name` output audit.
- **Status:** product recommendation selected; exact visual behavior is Phase 1/3 contract work.

### Q-09 — What profile and permission guarantees are honest?

- **Why it matters:** Existing Chrome gives access to live login state but does not automatically isolate cookies/storage between logical spaces.
- **Required source classes:** official extension permission/storage/debugger docs; local capability probes; privacy/security review; user-control tests.
- **Queries/framing:** `chrome debugger restricted domains`; `Chrome extension content scripts isolated world`; `chrome storage service worker persistence`; `Chrome extension distribution native messaging allowed origins`.
- **Artifacts:** inherited/shared/unavailable matrix for cookies, storage, extensions, downloads, upload, permissions, and user tabs; consent and redaction checklist.
- **Status:** sharing limitation is closed as a product constraint; capability details remain Phase 0/7 measurements.

### Q-10 — What interface gives agents fast batched automation without MCP?

- **Why it matters:** Per-command process startup and MCP round trips conflict with the context/speed goal.
- **Required source classes:** local CLI/runtime evidence; protocol framing/recovery tests; ego-lite script API patterns; benchmark evidence.
- **Queries/framing:** `persistent local agent browser automation CLI JSON RPC`; `length delimited local IPC request cancellation events`; `ego-lite taskSpace page snapshot script batching`.
- **Artifacts:** versioned local protocol, CLI/SDK contract, host lifecycle, batch/stream semantics, end-to-end latency benchmark.
- **Status:** local host + persistent CLI + thin SDK selected; Phase 2/5 freeze exact schemas.

## Source-class additions required by the superseding plan

- **Official Chrome extension documentation:** debugger, Native Messaging, service-worker lifecycle, content scripts, scripting, tabs, tabGroups, storage, sidePanel, and distribution.
- **Local feasibility:** a real user-approved Chrome run with the installed extension and host, not only fake CDP or a temporary profile.
- **Operational evidence:** host/extension crash, reconnect, Chrome restart, service-worker restart, permissions, and installation/update/uninstall tests.
- **Comparable product:** ego-lite remains a mechanism source only; its proprietary browser host is not treated as an available dependency.

## Saturation limit for the superseding decision

Direct official Chrome pages were successfully retrieved on 2026-10-01; Firecrawl search remained HTTP 400 and was not used as evidence. The architecture is bounded rather than internet-wide exhaustive until the Phase 0 real-Chrome vertical slice confirms the exact Chrome-version, enterprise-policy, extension-distribution, and debugger-domain matrix.
