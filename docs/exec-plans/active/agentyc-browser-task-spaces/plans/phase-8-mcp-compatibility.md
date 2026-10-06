---
phase: 8
name: Host-backed MCP compatibility and release validation
status: pending
owner: Japneet Kalkat
primary_outcome: Validate the host-backed logical MCP stdio adapter without presenting declared routes as live support; MCP remains unavailable for distribution until all Phase 8 release gates pass.
depends_on: phase-7
---

# Phase 8 — Host-backed MCP compatibility and release validation

## Objective

Validate the current host-backed logical MCP adapter as a separate compatibility surface. Preserve the direct CLI/SDK as the primary interface. Do not restore the removed direct-CDP `BrowserServer`, `browser_*` MCP tools, legacy CDP MCP mode, or MCP HTTP `serve` route as part of this phase.

## Current verified facts

- `agentyc mcp` (and `agentyc` with no subcommand) runs the host-backed logical MCP service over stdio only.
- The deterministic in-process offline server exposes 29 logical routes.
- The connected remote catalog declares 30 routes. Eleven fail closed with typed `capability_unavailable` before forwarding: `host_space_describe`, `host_lease_takeover_with_control_ticket`, `host_lease_control_ticket`, `host_lease_acknowledge_return_control`, `host_page_bind`, `host_page_mark_lost`, `host_snapshot_put`, `host_snapshot_mark_dirty`, `host_action_enqueue`, `host_action_dispatch`, and `host_event_publish`.
- `host_lease_acknowledge_fence` is supported by the local protocol. It retries the same pending takeover epoch and renews its lease before repeating durable extension fencing and retained-page rebind.
- A route declaration is not proof of connected support. A limited headed existing-profile run exercised MCP stdio, the local host socket, Native Messaging, and extension takeover/rebind. Snapshot/action reconciliation did not complete; the full live workflow gate remains open.
- MCP is **not distribution-ready**. Offline contracts do not close the live or release gates.
- The standalone direct-CDP `browser`, `run`, and `repl` CLI commands have been removed. Independent test/installation harnesses may use CDP internally, but they are not user-facing commands; the extension's `chrome.debugger` backend remains.

Source of truth: `docs/mcp-compatibility.md` and the host MCP implementation under `crates/agentyc-mcp/src/`.

## Scope

### In scope

- Verify the offline and connected MCP route catalogs, argument/result schemas, and fail-closed handling for unavailable remote routes.
- Decide and document the support disposition for each unavailable remote route; do not infer support from catalog declaration.
- Validate stdio lifecycle, cancellation, structured tool errors, and unknown-outcome behavior against the host-backed implementation.
- Run headed Chrome workflows over the real host socket, Native Messaging bridge, extension, and existing Chrome profile.
- Define and pass the independent MCP release gate using reproducible, redacted artifacts.
- Keep public docs, skills, plugins, and evidence manifests aligned with the shipped MCP surface.

### Out of scope

- MCP HTTP or Streamable HTTP transport.
- Direct-CDP or temporary-browser MCP modes.
- The deleted `BrowserServer` and `browser_*` MCP tool catalog.
- Adding standalone direct-CDP CLI commands. Internal test/installation harnesses may use CDP without creating a user-facing command; the extension's `chrome.debugger` backend remains.
- Treating deterministic offline tests as live Chrome, distribution, or release evidence.

## Tasks

- [ ] P8-T1 — Freeze the current logical stdio contract.
  - **Files:** `crates/agentyc-mcp/src/{host_adapter.rs,host_server.rs,remote_host_server.rs}`, `tests/mcp_protocol.rs`, MCP contract fixtures, `docs/mcp-compatibility.md`.
  - **Done when:** tests establish the 29-route offline surface and 30-route connected catalog; each of the 11 unavailable remote routes returns the typed error before forwarding; no removed legacy MCP route is advertised; schema and result/error behavior are versioned.
  - **Validation:** focused offline stdio tests and exact route/schema comparison pass; every assertion distinguishes offline route availability from connected capability.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T2 — Close support decisions for unavailable remote routes.
  - **Files:** route support decision record, adapter implementation/tests, `docs/mcp-compatibility.md`.
  - **Done when:** each unavailable route is either implemented and independently tested through the host protocol or explicitly remains unsupported with its typed fallback and user-facing limitation; no route is called supported merely because it is declared.
  - **Validation:** route-by-route disposition review and tests prove unsupported calls fail closed without forwarding or side effects.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T3 — Run live headed-Chrome MCP workflows.
  - **Files:** host/extension integration tests and redacted Phase 8 live artifacts.
  - **Done when:** the stdio process is exercised through the owner-only host socket, Native Messaging bridge, enrolled extension, and headed Chrome; every supported workflow has observed browser-side outcomes, while unsupported routes return their documented typed errors. Cover logical space/page scoping, navigation and actions, snapshots/refs, cancellation and disconnect, takeover/fencing, reconnect/reconciliation, and unrelated user-tab safety.
  - **Validation:** repeatable live run with environment/build tuple, route results, semantic assertions, cleanup evidence, and redacted artifacts. A failed or unavailable preflight is a blocker, not a pass.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T4 — Pass the independent MCP release gate.
  - **Files:** MCP section of `docs/release-gate.md`, Phase 8 report/artifacts, relevant CI policy and manifests.
  - **Done when:** protocol and schema tests, security/error mapping, concurrency and disconnect behavior, supported-route live Chrome evidence, unsupported-route dispositions, release support decision, and redacted artifacts all pass; the report explicitly distinguishes measured, unavailable, and unsupported capabilities.
  - **Validation:** the release checker rejects missing live evidence, offline-only evidence, skipped required tests, undeclared routes, unsupported-route claims, and incomplete artifacts. No gate is marked passed until its actual evidence is present.
  - **Owner:** Japneet Kalkat.

- [ ] P8-T5 — Complete migration and support documentation.
  - **Files:** README, CLI/configuration/architecture docs, skills and plugin docs, Phase 8 plan and registry.
  - **Done when:** documentation describes only host-backed logical MCP over stdio; the 29/30 route distinction and 11 unavailable routes are explicit where relevant; the CLI lists only shipped host-backed logical commands; no documentation recommends removed direct-CDP CLI or MCP paths, flags, or HTTP route.
  - **Validation:** documentation reference and stale-surface audit passes; release readiness remains explicitly false until P8-T1 through P8-T4 and the release decision are complete.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [ ] MCP exposes host-backed logical operations only.
- [ ] MCP transport is stdio only; no HTTP route is claimed.
- [ ] Offline 29-route and remote 30-route catalogs are described distinctly.
- [ ] All 11 currently unavailable remote routes fail closed as `capability_unavailable` unless their support is implemented and proven.
- [ ] Headed Chrome validation covers the real host, Native Messaging bridge, extension, and Chrome path.
- [ ] No offline, declaration-only, source-inspection, or operator-acknowledgment evidence is treated as live support.
- [ ] Required release artifacts and security/error gates pass before MCP is described as distribution-ready.
- [ ] No removed direct-CDP CLI path is presented as shipped; the Node SDK and extension `chrome.debugger` backend remain accurately documented.

## Handoff out

- **Artifacts:** versioned stdio route/schema contract, route-disposition record, headed-Chrome evidence, release report, migration/support docs.
- **Next phase:** maintenance follows the release decision; new transports or remote service work require a separate plan.
- **Residuals:** any unsupported route remains listed with its typed fallback and owner/trigger for reconsideration.

## Exit gate

Keep this phase `pending` until the stdio contract is verified, all unavailable-route decisions are explicit, headed live Chrome workflows pass through the real host/extension path, and the independent MCP release gate and support decision genuinely pass. Offline tests and a declared route catalog alone cannot complete Phase 8 or establish distribution readiness.
