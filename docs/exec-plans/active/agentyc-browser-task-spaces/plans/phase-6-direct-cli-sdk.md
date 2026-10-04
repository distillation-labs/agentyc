---
phase: 6
name: Direct CLI and SDK
status: pending
owner: Japneet Kalkat
primary_outcome: Coding agents can use a persistent JSON CLI and typed Node SDK as the primary non-MCP interface for task spaces, pages, snapshots, actions, waits, events, handoff, and recovery.
depends_on: phase-5
---

# Phase 6 — Direct CLI and SDK

## Objective

Replace the current one-shot/active-page CLI experience with a persistent host client. Give agents the ego-lite-like task-space/page object model without embedding arbitrary code in the host or requiring MCP.

## Handoff in

- **Inputs:** core/local protocol contracts; host broker; extension bridge; snapshot/action/wait behavior.
- **Must already be true:** host-backed operations are stable and tested independently of frontends.
- **Do not reopen:** local protocol is canonical; CLI/SDK are clients; no browser launch by default; MCP remains a later adapter.

## Confirmed facts

- `crates/agentyc/src/main.rs::run_action` opens/closes a runtime per command.
- `crates/agentyc/src/frontend.rs` only supports active-page navigation/state/evaluate/basic tab commands.
- Current `Cmd::Browser` launches a temporary profile and remote-debugging port.
- `SKILL.md` and plugin guidance currently recommend MCP and tab IDs.

## Working assumptions

- Agents can call a long-lived `agentyc host` or `agentyc run --session` process and can install/use a thin Node SDK where code batching is valuable. npm is the selected package manager; the Node floor, package lockfile, and registry/private distribution are frozen by Phase 0.
- CLI stdout remains machine-readable when `--json`; diagnostics/progress go to stderr.
- A persistent SDK connection can multiplex requests and events and keep space/page handles in user code, not process-global host selection.

## Unresolved questions

- **U6-1:** package registry/distribution for Node SDK; owner: Japneet Kalkat; Phase 0 must record the Node floor and distribution approach before P6-T3.
- **U6-2:** none; naming is closed. User-facing CLI/SDK/domain term is `space`, canonical field is `space_id`; `group_id` is a deprecated compatibility alias only and never a Chrome visual-group identifier.

## Scope

### In scope

- `agentyc host`, `space`, `page`, `action`, `wait`, `event`, `extension`, and diagnostics commands.
- Persistent connection/session behavior, JSON schemas, exit codes, cancellation, reconnect, unknown outcomes.
- Thin Node SDK task-space/page wrappers and batch scripts.
- Agent skill/plugin/docs migration to CLI/SDK primary.
- No `[id] name` or raw browser ID in primary output.

### Out of scope

- MCP adapter implementation (Phase 8).
- Arbitrary code execution inside host/extension.
- New browser capabilities beyond the host contract.
- Automatic browser launch/download or silent fallback to legacy CDP.

## CLI contract

Representative commands:

```text
agentyc host start|status|stop
agentyc extension status|install-check
agentyc space create|list|get|claim|renew|handoff|accept|pause|takeover|return|finish|release
agentyc page create|list|get|adopt|snapshot|navigate|close
agentyc action click|type|fill|press|scroll|select|upload|evaluate|status|reconcile|cancel
agentyc wait url|network-idle|request|response|dom-stable|element|page
agentyc event subscribe|resume
```

Rules:

- `--json` is stable machine output; human formatting is optional and still uses structured records.
- `--space`/`--page` accept opaque logical IDs or durable labels only where the API says labels are unambiguous; names never locate across spaces. `--group` is not a new primary command; any legacy alias is marked deprecated.
- `--yes`/explicit confirmation is required for adoption, destructive actions, upload, cookies, and evaluate according to policy.
- Exit codes distinguish usage, host unavailable, permission/capability, runtime failure, timeout/cancel, and unknown action outcome.
- `run` executes a sequence through one persistent connection; `repl` uses the same host client and is not an independent browser runtime.
- `agentyc browser` and `--cdp-url` are marked `legacy-cdp` and do not start from the default command path.

### Planned output lifecycle

The direct interface should use the following reference-derived output contract; it is planned behavior, not evidence that agentyc implements it:

- Structured JSON is emitted on stdout; diagnostics and progress use stderr.
- Business output is buffered until round completion and has one final flush.
- Clean completion flushes business output, then emits final unhandled-page notices.
- Hard stops discard business output and notices; swallowed hard stops emit owned guidance once, while thrown hard stops remain silent so the propagating error is not duplicated.
- Notices are round-local, keyed by logical space/target, merged/refreshed, suppressed when the page is observed, and consumed once.

The exact agentyc schema, stream framing, and lifecycle hooks remain to be frozen by implementation and tests.

## SDK contract

- Add the canonical thin package:

```text
packages/agentyc-browser/
  package.json
  src/client.ts
  src/space.ts
  src/page.ts
  src/actions.ts
  src/waits.ts
  src/events.ts
  src/errors.ts
  test/...
```

Public shape (planned; aligned to the checked-in ego-lite reference):

```js
const client = await connect({ profile: "default" });
const task = await client.taskSpace("research competitors");
const results = task.page("p1");
const scratch = await task.newPage();

await results.goto("https://example.test");
const snapshot = await results.snapshot({ mode: "min" });
await results.click(snapshot.refs.submit);
await results.waitForURL(/\/done$/);

await task.finish({ keep: ["p1"] });
```

`task.page(label)` returns a lazy durable page handle; `task.newPage()` creates a new blank page with a durable label. This is the intended contract shape only: no agentyc SDK implementation or live Chrome evidence is claimed.

The SDK:

- keeps a persistent local connection;
- multiplexes response/event sequences;
- returns typed errors and action receipts;
- exposes snapshot/ref provenance and resync hints;
- never treats a label as authorization without host resolution;
- makes user-control errors explicit and non-retryable until the user returns control;
- does not provide unrestricted raw CDP or page eval without explicit capability.

## Tasks

- [ ] P6-T1 — Add host client and command routing to the Rust CLI.
  - **Files:** `crates/agentyc/src/main.rs`, new `crates/agentyc/src/commands/{host.rs,spaces.rs,pages.rs,actions.rs,waits.rs,events.rs,extension.rs}`, replace `frontend.rs` dispatcher with host client.
  - **Done when:** commands use the local protocol, persist across calls through the host, return typed JSON, and never open/close a browser implicitly.
  - **Validation:** `cargo test -p agentyc --locked`; local host lifecycle/CLI integration tests; stdout/stderr assertions.
  - **Owner:** Japneet Kalkat.

- [ ] P6-T2 — Implement CLI JSON/error/exit-code and cancellation behavior.
  - **Files:** `crates/agentyc/src/frontend.rs`, command modules, `docs/cli.md`.
  - **Done when:** all contract errors map to stable JSON/exit behavior; Ctrl-C/cancel stops queued work; unknown outcomes return action ID/reconcile guidance; no progress contaminates stdout.
  - **Validation:** malformed input, timeout, host loss, user takeover, unknown action, SIGINT, and artifact-handle tests.
  - **Owner:** Japneet Kalkat.

- [ ] P6-T3 — Implement Node SDK over the same protocol.
  - **Files:** `packages/agentyc-browser/*` and the Phase 0-selected lockfile.
  - **Done when:** `connect`, `taskSpace`, `page`, actions, waits, events, handoff, finish, reconnect, cancellation, and errors wrap the canonical envelopes without a second semantic implementation.
  - **Validation:** `npm ci --prefix packages/agentyc-browser`; `npm test --prefix packages/agentyc-browser`; `npm run typecheck --prefix packages/agentyc-browser`; Node unit tests with fake host; integration test with real local host/extension fixture; TypeScript declaration/API golden test.
  - **Owner:** Japneet Kalkat.

- [ ] P6-T4 — Implement batch script and persistent REPL behavior.
  - **Files:** CLI run/repl command modules, SDK batch helper, `docs/cli.md`.
  - **Done when:** a multi-step task uses one host connection and one space/page object; separate calls do not recreate browser state; partial results identify action IDs and unknowns.
  - **Validation:** round-trip benchmark proves ≥50% reduction versus separate process-per-command baseline on the Phase 0 scenario, or a signed threshold adjustment exists.
  - **Owner:** Japneet Kalkat.

- [ ] P6-T5 — Migrate skills, plugin metadata, examples, and README.
  - **Files:** root `SKILL.md`, `.agents/skills/agentyc-browser-automation/SKILL.md` and references/evals, `plugins/agentyc-browser-automation/plugin.json`, `README.md`, `crates/agentyc/Cargo.toml` product description, `crates/agentyc-mcp/Cargo.toml` compatibility description, `docs/api-local.md`, `docs/installation.md`.
  - **Done when:** CLI/SDK/space/page read-ref-act-verify loop is primary; MCP is labeled compatibility; docs explain shared-profile limits, user takeover, no launch/download, structured outputs, and no `[id] name`.
  - **Validation:** docs examples execute against fake host; grep negative audit for MCP-recommended/default, `[id] name`, `tab_id`, and raw target presentation outside adapter sections.
  - **Owner:** Japneet Kalkat.

- [ ] P6-T6 — Add direct-interface metrics and debug bundle command.
  - **Files:** CLI diagnostics, host metrics, `docs/release-gate.md`.
  - **Done when:** task completion, first action, batch round trips, snapshot scans/tokens, action outcomes, event lag, host/extension reconnect, and user-control events are reportable without secrets.
  - **Validation:** direct benchmark and redaction tests; report under `artifacts/p6-direct-interface/`.
  - **Owner:** Japneet Kalkat.

## Quality checklist

- [ ] CLI/SDK do not create a second broker or browser.
- [ ] Primary examples use spaces/pages, not tabs or `[id] name`.
- [ ] Persistent connections support cancellation, reconnect, and event resume.
- [ ] Unknown outcomes remain explicit and are not converted to success.
- [ ] Shared-profile limits and user-control boundaries are visible.
- [ ] Legacy launch/CDP flags are explicit and deprecated.

## Handoff out

- **Artifacts:** direct CLI, Node SDK, batch/repl, docs/skill/plugin migration, direct performance metrics.
- **Next phase:** Phase 7 hardens, validates, and launches the direct existing-Chrome path; Phase 8 then migrates existing MCP stdio/HTTP clients onto the same host broker.
- **Residuals:** direct launch evidence and legacy MCP protocol behavior remain.

## Exit gate

Advance only when direct CLI/SDK task-space workflows pass with persistent host state, batch/round-trip targets pass, output/security audits pass, and a fresh agent can use the primary product without MCP, a copied CDP URL, or a launched browser.
