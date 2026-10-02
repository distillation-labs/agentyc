---
phase: 8
name: MCP compatibility adapter and legacy migration
status: pending
owner: Japneet Kalkat
primary_outcome: Existing MCP clients continue to work through a scoped adapter over the host broker without becoming a second state authority or bypassing extension/profile/user-control policy; MCP remains independently releasable after the direct product.
depends_on: phase-7
---

# Phase 8 — MCP compatibility adapter and legacy migration

## Objective

Preserve useful existing MCP clients while making the direct local host/CLI/SDK the canonical product. Map MCP sessions/connections to host principals/spaces, keep safe legacy responses, scope unsafe operations, and define a deprecation path without mixing MCP protocol eras into core state.

## Handoff in

- **Inputs:** direct rollout evidence; host/core contracts; direct CLI/SDK; extension capability matrix; existing `rmcp 1.7` tests and docs.
- **Target boundary for this phase:** after P8-T1, the MCP adapter calls host operations through a client and cannot access `BrowserRuntime`, `CdpClient`, active-page state, or extension internals directly. The current direct ownership is a known pre-phase condition and is removed by the scoped migration tasks below.
- **Do not reopen:** MCP is compatibility-only; local protocol/core are canonical; no automatic launch/download; no global close; raw IDs adapter-only.

## Confirmed facts

- `crates/agentyc-mcp/src/lib.rs` and `tools/` currently own tool routes/state.
- `crates/agentyc/src/main.rs::run_serve` creates a server per legacy HTTP session.
- `rmcp = 1.7` implements the existing legacy Streamable HTTP behavior; existing `2024-11-05` initialize fixtures must remain green during migration. Current repository evidence is 61 default tools, 76 total declarations, and 15 extended observability tools; comments and always-61 server metadata are inconsistent and must be corrected/frozen by this phase.
- Existing docs/skills recommend MCP and tab IDs and must be corrected by the direct-interface phase.

## Working assumptions

- `agentyc mcp` uses a local host client; if no host/extension exists it returns `extension_not_connected` rather than launching Chrome.
- `agentyc serve` shares one authenticated host client/broker outside the MCP session factory; each MCP transport connection gets a host-assigned principal and connection context. stdio gets a process-connection principal; HTTP obtains its context only after exact loopback/Origin/Host/auth admission. `Mcp-Session-Id` is not authentication.
- Legacy tool schemas remain stable where required; new space/page features are additive or exposed only through an opt-in adapter profile.
- A compatibility selected/default space is a convenience, not a process-global browser selection or authorization.

## Unresolved questions

- **U8-MCP-1:** exact legacy tool subset to deprecate first; owner: Japneet Kalkat; decide from client usage/evals.
- **U8-MCP-2:** end-of-life date for raw `tab_id`/`target_id`; owner: Japneet Kalkat; require a release note and adapter version before removal.

## Scope

### In scope

- MCP server/client adapter over host broker.
- stdio and legacy Streamable HTTP connection/principal mapping.
- Existing tool result/error/state compatibility.
- Scoped legacy tab/session mapping.
- MCP cancellation/disconnect/unknown mapping.
- Production-grade protocol, schema, concurrency, fault, replay, and host-backed real-browser test corpus.
- Default/extended tool profile and deprecation docs/tests.

### Out of scope

- Modern MCP wire upgrade.
- New primary task-space UX.
- Direct CDP/temporary browser behavior.
- Remote multi-tenant MCP service.

## Adapter rules

- MCP session ID is a transport connection ID only; never a space ID, lease, auth token, or browser target.
- Each connection gets an implicit compatibility space only when a legacy call requires it; new direct clients must create/select explicitly.
- `browser_list_tabs` maps to structured pages in the selected compatibility space; raw `tab_id`/`target_id` fields are included only in an explicitly marked legacy response and are never accepted as authority.
- `browser_close_tab`, `browser_close_session`, and `browser_close_all` all resolve through the host: close-tab requires the selected compatibility page to be broker-owned, freshly generation-proven, and covered by a single-use user-intent ticket; close-session closes only that connection's selected owned pages with the same ticket/proof; close-all means selected-space owned-page cleanup with the same ticket/proof. None may call old `BrowserSession::close_all` or close user/unmanaged tabs.
- An abrupt HTTP TCP reset/response-stream loss is not equivalent to graceful DELETE: queued work is cancelled, an already-dispatched mutation is `unknown`, waits are cancelled, the connection is marked disconnected, principal/space leases remain until their normal expiry or explicit release, and pages are retained. Graceful DELETE performs the same action classification but may release only connection-scoped selection, never page ownership implicitly.
- `browser_evaluate`, storage/cookie, upload, download, and permissions use host policy/capability/lease checks and may return typed unsupported/confirmation errors.
- MCP tool execution failures return a successful JSON-RPC result with `CallToolResult.isError=true` and canonical error code/action/reconcile metadata. Malformed requests, unknown tools, invalid protocol/session state, and transport failures remain JSON-RPC errors. The mapping is explicit for `timeout`, `cancelled`, `unknown_outcome`, `user_control_required`, `capability_unavailable`, `permission_denied`, and `host_draining`.
- Adapter event subscriptions filter host sequences by connection/space/page; event lag returns resync guidance.
- A legacy request cannot use raw target IDs to bypass logical resolution or ownership.

## Tasks

- [ ] P8-T1 — Make `BrowserServer` a host-client adapter.
  - **Files:** `crates/agentyc-mcp/{Cargo.toml,src/lib.rs,state.rs,connection.rs,adapter.rs,compat.rs,tools/mod.rs,tools/*.rs}`; `scripts/check_mcp_deps.py`; update workspace dependencies; add the host-client dependency selected by the final workspace graph.
  - **Done when:** `crates/agentyc-mcp/Cargo.toml` no longer depends directly on `agentyc-cdp`, `agentyc-browser`, or `agentyc-runtime` for production behavior; server state contains host client, connection context, compatibility selection, and bounded adapter caches only; tool modules call canonical operations; no MCP module owns browser/CDP/page state, snapshot construction, active-page authority, or global cleanup.
  - **Validation:** `python3 scripts/check_mcp_deps.py`; `rg -n "CdpClient|BrowserRuntime|active_page|close_all|Target\\.|Runtime\\.evaluate" crates/agentyc-mcp/src crates/agentyc-mcp/Cargo.toml` returns only documented adapter-test/compatibility references; `cargo metadata --format-version 1 --locked` verifies the MCP package's direct dependency list; adapter unit tests and a host-backed smoke test pass.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T2 — Integrate stdio and legacy HTTP with one host broker.
  - **Files:** `crates/agentyc/src/main.rs::{run_serve,Cmd::Mcp,Cmd::Serve}`, `crates/agentyc-mcp/src/{lib.rs,connection.rs}`; HTTP admission middleware; `tests/mcp_http_lifecycle.rs`.
  - **Done when:** stdio connects to host; HTTP service factory shares one host client/broker; MCP sessions have separate host-assigned principals/selected context; no session creates a new browser owner; loopback/Origin/Host/token policy rejects unauthenticated requests before session creation and non-loopback is disabled by default.
  - **Validation:** `cargo test -p agentyc-tests --test mcp_http_lifecycle --locked`; two HTTP sessions and one stdio client operate separate spaces concurrently; host/extension events remain scoped; HTTP tests cover missing/wrong Origin, Host, bearer token, session-header spoofing, DELETE/EOF teardown, and loopback-only binding.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T3 — Migrate navigation/state/interaction/inspection/frame/storage/tab tools.
  - **Files:** `crates/agentyc-mcp/src/tools/{navigation.rs,state_tools.rs,interaction.rs,inspection.rs,frames_storage.rs,tabs_session.rs,observability.rs}`.
  - **Done when:** every call resolves logical space/page and host policy before operation; legacy tab/session fields map only through compatibility; unsupported extension capability is typed; no active-page fallback can cross connections.
  - **Validation:** `cargo test -p agentyc-mcp --locked`; `cargo test -p agentyc-tests --test mcp_event_scope --locked`; existing tool tests plus cross-space, stale ref, user takeover, extension disconnect, and unsupported capability tests.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T4 — Preserve required legacy protocol behavior with wire-level conformance.
  - **Files:** `tests/mcp_protocol.rs`, new `tests/mcp_http_lifecycle.rs`, `tests/mcp_event_scope.rs`, `tests/mcp_wire_corpus.rs`, `crates/agentyc-tests/Cargo.toml` test manifest, `tests/fixtures/mcp/{stdio,http}/`.
  - **Done when:** the accepted-version matrix is empirically frozen: `2024-11-05` is accepted and the existing initialize/session fixture remains supported; `2025-11-25` is rejected/not claimed by this `rmcp 1.7` product unless a future deliberate SDK upgrade changes the decision; `2026-07-28` is unsupported. Stdio lifecycle covers `initialize`, `initialized`, shutdown, notifications, malformed JSON/JSON-RPC, invalid params, unknown methods/tools, duplicate IDs, out-of-order responses, EOF, deadlines, and cancellation. HTTP covers exact `Content-Type`/`Accept`/version headers, loopback/Origin/Host admission, session creation, missing/unknown/expired/spoofed `Mcp-Session-Id`, `Last-Event-ID`, SSE event IDs/data framing, GET/POST/DELETE, reset, reconnect, and status behavior.
  - **Validation:** `cargo build -p agentyc --locked`; `cargo test -p agentyc-tests --test mcp_protocol --locked`; `cargo test -p agentyc-tests --test mcp_http_lifecycle --locked`; `cargo test -p agentyc-tests --test mcp_event_scope --locked`; `cargo test -p agentyc-tests --test mcp_wire_corpus --locked`; accepted-version/feature report includes `rmcp 1.7.0`, `Cargo.toml`, `Cargo.lock`, accepted `2024-11-05`, rejected/not-claimed `2025-11-25`, and rejected `2026-07-28`; archive sanitized wire transcripts and status/header matrix under `artifacts/p8-mcp-protocol/`.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T5 — Map unknown/cancel/reconcile and user-control errors.
  - **Files:** adapter error mapping, MCP action status/reconcile tools if retained, connection teardown hooks, `tests/mcp_http_disconnect.rs`, `tests/mcp_error_matrix.rs`.
  - **Done when:** lost response after dispatch returns canonical `unknown_outcome` with action/reconcile guidance; user takeover is not auto-retried; a cancelled queued action is `cancelled`, a dispatched mutation with lost response is `unknown`, a wait is independently cancellable, abrupt HTTP reset is classified as specified above, stdio EOF and HTTP DELETE use their distinct teardown semantics, and leases are not released merely because an arbitrary payload/session ID disappears. Every canonical error maps to stable `{code,message,retryable,action_id,reconcile_token,next_action}` fields without string-substring classification; tool failures retain `isError=true`, while protocol/transport failures remain JSON-RPC errors.
  - **Validation:** `cargo test -p agentyc-tests --test mcp_http_disconnect --locked`; `cargo test -p agentyc-tests --test mcp_error_matrix --locked`; disconnect-after-send navigation/click/form/close; takeover during dispatch; queued cancellation; wait cancellation; stdio EOF; HTTP DELETE; reconnect status query and lease-retention tests; every error fixture asserts layer, status, canonical code, retryability, and redaction.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T6 — Publish deprecation and migration matrix.
  - **Files:** `docs/api.md`, `docs/migration-mcp-to-cli.md`, README/skills/plugin, changelog, `scripts/check_mcp_docs.py`.
  - **Done when:** direct CLI/SDK is primary; MCP is compatibility; raw tab fields and global/tab-centric tools have deprecation/replacement; no docs teach `[id] name`.
  - **Validation:** `python3 scripts/check_mcp_docs.py --negative-output-audit`; documentation examples and legacy client smoke test pass; no primary docs recommend MCP, tab IDs, global close, or browser launch.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T7 — Lock adapter security, schemas, and compatibility gates.
  - **Files:** MCP integration tests, host authorization, release docs, server metadata/profile code, `scripts/check_mcp_compat.py`, versioned tool manifests under `tests/fixtures/mcp/schemas/`.
  - **Done when:** MCP cannot bypass host leases, raw IDs, user-control state, capability denial, or cleanup ownership; compatibility profile is independently versioned; default profile is exactly the measured 61-tool set, extended profile is exactly the measured 76-tool set, profile selection/advertisement is explicit, `ServerInfo` no longer claims 61 when extended is active, and every tool manifest records schema, output, side effects, authority, deprecation, and error mapping.
  - **Validation:** `python3 scripts/check_mcp_compat.py --report artifacts/p8-mcp-compatibility`; adversarial adapter suite; deterministic default/extended tool-name/count/schema/order report; `isError` versus JSON-RPC error matrix; cancellation/disconnect report; legacy initialize/session fixtures; no-direct-CDP grep; raw-ID bypass and cross-space mutation attempts.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T8 — Validate MCP through the real host, extension, and headed Chrome.
  - **Files:** `tests/mcp_existing_chrome.rs`, `tests/fixtures/mcp/workflows/`, `scripts/run_mcp_existing_chrome.py`, extension/native-host test lane.
  - **Done when:** stdio and legacy HTTP execute against the real host, Native Messaging bridge, extension, and headed Chrome without a CDP URL, browser launch, or downloaded browser; every supported tool is proven or explicitly marked partial/unsupported with its typed fallback. Workflows cover navigation/redirects, snapshots/deltas/refs, click/type/fill/select/scroll, waits, frames/OOPIFs, dialogs, storage/cookies, downloads/uploads, screenshots/PDF, stale refs, takeover, worker/host/browser restart, disconnect/reconnect, and unrelated user-tab coexistence across concurrent spaces.
  - **Validation:** `python3 scripts/run_mcp_existing_chrome.py --headed --transports stdio,http --spaces 2 --agents 2 --artifact-dir artifacts/p8-mcp-existing-chrome`; every scenario asserts semantic result, scope, receipt, postcondition, event cursor, cleanup, and redaction; upload sanitized transcripts, screenshots only through protected artifact paths, environment/build manifest, and replay seed.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T9 — Integrate MCP quality gates into CI and define the independent MCP release lane.
  - **Files:** existing `.github/test-policy.yaml` and `.github/workflows/test.yaml`/`.github/workflows/workflow.yml` from Phase 7 (add only MCP lane sections), `tests/test-manifest.yaml` (existing manifest from Phase 0; add MCP entries only), MCP section of `docs/release-gate.md`, and planned `scripts/run_mcp_benchmark.py`, `scripts/run_mcp_load_test.py`, `scripts/run_mcp_soak_test.py`, `scripts/run_mcp_chaos_test.py`, `scripts/run_mcp_release_drill.py`.
  - **Done when:** required PR lanes cover pure/component/process/MCP contract/lifecycle/concurrency/fault/redaction suites; nightly covers MCP headed-Chrome workflows, context/token benchmarks, load/saturation, soak/leak, chaos/fault matrix, fuzz corpus, and install/update; an explicit `mcp-compatibility-gate` job emits a versioned report/artifact and is required by MCP compatibility publication, while `publish-binaries` remains dependent on the independent Phase 7 direct-product gate. The MCP gate covers supported OS/Chrome matrix, rollback, and every supported tool's real-browser evidence. Pinned Rust/rmcp/Node/npm/Chrome versions, isolated state/ports, bounded timeouts, descendant cleanup, sample accounting, and sanitized failure artifacts are enforced. No required MCP test is ignored or silently skipped; the frozen legacy MCP baseline runs through Phases 0–7.
  - **Validation:** `python3 scripts/check_test_manifest.py tests/test-manifest.yaml`; `python3 scripts/run_mcp_benchmark.py --min-samples-p95 200 --min-samples-p99 1000 --artifact-dir artifacts/p8-mcp-performance/`; `python3 scripts/run_mcp_load_test.py --artifact-dir artifacts/p8-mcp-load/`; `python3 scripts/run_mcp_soak_test.py --artifact-dir artifacts/p8-mcp-soak/`; `python3 scripts/run_mcp_chaos_test.py --manifest tests/test-manifest.yaml --artifact-dir artifacts/p8-mcp-chaos/`; `python3 scripts/run_mcp_release_drill.py --artifact-dir artifacts/p8-mcp-release/`; CI dry-run proves every required entry executed and every attempted sample/fault is accounted for; failures upload logs, wire transcripts, redacted traces, environment manifests, seeds, replay commands, and the separate direct/MCP gate reports.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [ ] MCP has no canonical browser state.
- [ ] HTTP sessions share the host broker intentionally and pass authentication/origin admission before MCP session creation.
- [ ] Stdio/HTTP connection identity is not browser/page ownership.
- [ ] Global close is gone or strictly scoped to owned pages.
- [ ] Legacy raw fields are adapter-only and deprecated.
- [ ] Existing protocol tests are green without changing modern/legacy claims.
- [ ] Every close path is scoped; no legacy global `close_all` reaches the new host.
- [ ] Default/extended profile counts and server metadata are consistent and frozen.
- [ ] Cancellation, EOF, DELETE, and lost-dispatch semantics are tested at the MCP boundary.
- [ ] Stdio and HTTP wire behavior has sanitized transcript/golden coverage.
- [ ] Default/extended tool manifests are exact, ordered, schema-golden, and side-effect documented.
- [ ] MCP concurrency, event replay/backpressure, cancellation, reconnect, and host-backed headed-Chrome workflows are release-gated.
- [ ] Required lanes cannot pass through skipped tests, swallowed errors, missing Chrome, or missing redacted artifacts.

## Handoff out

- **Artifacts:** MCP adapter, protocol/lifecycle/event tests, legacy mapping, migration/deprecation docs.
- **Next phase:** maintenance follows the direct product release; new transport or remote-service work opens a separate plan.
- **Residuals:** modern MCP and remote service remain separate future decisions.

## Exit gate

Advance only when legacy protocol/tool tests pass through the host adapter, two MCP connections share one broker safely, no MCP path bypasses policy/leases, migration docs make direct CLI/SDK primary, and MCP compatibility has a separate release/support decision.
