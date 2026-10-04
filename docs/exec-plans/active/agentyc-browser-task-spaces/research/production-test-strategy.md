# Production-grade browser and MCP test strategy

## Purpose

This strategy makes reliability, context efficiency, and speed release properties rather than aspirations. It covers the direct CLI/SDK path and the MCP compatibility adapter over the same host broker, with tests that exercise deterministic components, process boundaries, the extension, and real headed Chrome.

The MCP suite must simulate real browser use. Protocol-only tests are necessary but cannot release the adapter by themselves.

## Test pyramid and required lanes

Every change declares the affected layers:

1. **Pure unit/property tests** — IDs, schemas, state machines, framing, limits, redaction, error mapping, delta application, token metadata, and lease/epoch rules.
2. **Component tests** — fake Chrome bridge, deterministic clock, seeded scheduler, ledger, leases, snapshot/ref cache, event router, actionability, and CLI/SDK protocol client.
3. **Process integration tests** — host IPC, Native Messaging shim, extension worker lifecycle, CLI/SDK, MCP stdio/HTTP, installer, and ledger migration.
4. **Headed browser tests** — supported Chrome/platform/policy matrix with disposable profiles and a local fixture server.
5. **Load, soak, and chaos tests** — nightly and pre-release lanes; these supplement, never replace, lower-level required tests.

Required PR lanes fail on missing Chrome, skipped tests, swallowed tool errors, ignored tests used as coverage, leaked child processes, or absent artifacts. One diagnostic retry may classify a flake but cannot turn a failed required test into a pass.

## Deterministic harness contract

The shared harness provides:

- A monotonic `TestClock` for deadlines, lease expiry, quiet windows, and clock-jump tests.
- A seeded scheduler with controlled interleavings and fault injection at enqueue, dequeue, dispatch, bridge send, browser side effect, response, and commit boundaries.
- A fake `ChromeBridge` that emits serializable, replayable target/frame/document/tab/group/worker/debugger events.
- A local fixture server with redirects, delayed responses, dynamic DOM, mutation bursts, forms, dialogs, downloads/uploads, cross-origin frames, OOPIFs, renderer churn, and streaming/long-lived requests.
- Isolated temporary state directories, sockets, ports, profiles, artifact names, locale, timezone, and environment variables.
- A replay command that reproduces a failed seed and trace from CI.
- No live network or wall-clock sleeps in required deterministic lanes.

The same seed and trace must produce byte-identical logical outcomes, ledger state, event sequence, and redacted artifact manifest across 100 repetitions. The manifest records the exact replay command and repetition count; any divergence, missing repetition, unaccounted sample, or artifact mismatch fails the lane.

## Realistic MCP workflow corpus

Each supported MCP tool is mapped to a scenario containing setup, protocol transport, user-visible browser state, expected logical records, action receipt/postcondition, and cleanup proof. The corpus includes:

- Create/resume two spaces with multiple labeled pages and concurrent stdio/HTTP clients.
- Navigate through redirects, same-document history, delayed responses, downloads, dialogs, and network idle.
- Capture clean, full, min, focus, delta, truncated, and resync snapshots on static, dynamic, dense-table, nested-frame, and OOPIF pages.
- Use refs to click, type, fill, select, scroll, upload, and evaluate only when capability and user authority permit.
- Exercise hidden, disabled, covered, moving, rerendered, stale, cross-origin, and detached controls.
- Take over a space from the side panel while queued and dispatched actions exist, then return control and resume.
- Disconnect stdio, reset HTTP streams, issue DELETE, restart the host, reconnect the extension, terminate the MV3 worker, detach the debugger, restart Chrome, and reconcile without replaying mutations.
- Browse unrelated user tabs while two spaces work; activate or edit agent-owned tabs without treating focus or page events as takeover.
- Attempt cross-space actions, raw-ID bypasses, unowned closes, session spoofing, stale refs, wrong generations, and forged user-intent tickets.

A scenario is not successful because a process exits cleanly. It must assert semantic results, scope, action status, postconditions, event cursor behavior, cleanup, and redaction.

## MCP contract coverage

The MCP compatibility suite freezes and tests:

- Stdio initialize/initialized/shutdown lifecycle, notifications, malformed JSON/JSON-RPC, invalid params, unknown methods/tools, duplicate IDs, out-of-order responses, EOF, deadlines, and cancellation.
- Legacy Streamable HTTP method/status/header/session behavior, exact loopback/Origin/Host admission, `Mcp-Session-Id`, `Last-Event-ID`, SSE framing, GET/POST/DELETE, reset, reconnect, and session spoofing.
- Exact default and extended tool manifests: names, ordering, schemas, descriptions, defaults, output shape, side effects, required authority, deprecation markers, and error mapping.
- Canonical error mapping: tool execution errors preserve `CallToolResult.isError=true`; protocol/transport errors remain JSON-RPC errors. Every error includes stable code, retryability, action/reconcile guidance, and no secrets/raw browser IDs.
- Multiple connections sharing one broker, same-connection multiplexing, out-of-order responses, per-space mutation ordering, fairness, backpressure, event isolation, duplicate idempotency keys, lease expiry, takeover fences, cancellation, reconnect, and replay gaps.
- Host-backed real-browser execution for every supported tool; unsupported/partial capabilities are explicit typed results rather than false success.

## Context and speed measurements

Every benchmark reports separately:

- transport bytes;
- UTF-8 bytes;
- serialized payload tokens;
- model-context tokens after the deployed chat/template wrapper;
- tokenizer name, version, encoding mode, and hash;
- browser scan time, host queue time, bridge time, Chrome command time, serialization time, and response delivery time;
- cache state, snapshot mode, frame topology, fixture/data hash, concurrency, and seed.

Snapshot comparisons preserve equivalent actionable-control/ref coverage. The host chooses delta only when measured final serialized cost is below the valid full/min alternative; otherwise it returns full/min or `resync_required`. Partial multi-frame snapshots cannot issue refs.

Blocking benchmark cells include cold/warm host, clean/dirty/resync cache, full/min/focus/delta, 1/2/4/8 spaces, 1/2/4 agents, static/dynamic pages, nested/OOPIF frames, mutation bursts, event gaps, extension restart, and unrelated user-tab activity.

Use at least 200 valid samples for p95 and 1,000 for p99, with 10 warmups or warmup-until-stable. Every attempted sample is accounted for as success, timeout, error, invalid measurement, or infrastructure failure; exclusions require a predeclared rule and are reported. Timeouts, errors, missing measurements, and discarded samples count against the cell and fail it when the signed error/coverage budget is exceeded. Report bootstrap 95% CIs, raw samples, timeout/error rates, p50/p95/p99, actionable-control coverage, stale-ref rate, unknown outcomes, event lag, CPU, RSS, queue depth, and human-tab responsiveness. Thirty samples are smoke-only and never gate tail latency.

A committed baseline manifest includes commit, build mode, OS/CPU, Chrome build, extension/host tuple, fixture/data hash, tokenizer, concurrency, cache state, sample count, and statistical method. Absolute ceilings and relative regression budgets are signed in Phase 0. Threshold changes require a dated decision record and owner.

## Fault and adversarial matrix

Required injected faults include host/bridge disconnect, Native Messaging EOF/partial frame/exact-limit/oversize/invalid UTF-8, worker termination at each control boundary, extension reload/update, debugger detach (`target_closed` and `canceled_by_user`), DevTools conflict, renderer/page/Chrome restart, sleep/wake, disk-full/read-only state, partial ledger writes, corrupt/incompatible ledger, clock jumps, CPU/memory pressure, event-buffer gaps, and late old-generation responses/events. The release matrix maps each fault to its phase owner, command, expected outcome, artifact, and no-replay assertion.

Required security cases include local peer identity, endpoint races/symlinks, origin/profile/epoch/nonce/sequence forgery, replay across reconnect/profile/space/page/generation, chunk/resource exhaustion, path traversal, hostile page messages, prompt injection, evaluate/upload/cookie/download escalation, user-intent ticket replay, and redaction bypass through errors, URLs, headers, screenshots, traces, metrics, and ledger messages.

Release is an automatic no-go for any authorization bypass, cross-space mutation, user-tab close, stale-agent mutation after takeover, secret leak, blind replay, or silent unknown-success.

## CI and artifacts

The test manifest records layer, command, required/optional status, environment, fixture, timeout, artifact path, owner, and quarantine status. Required PR lanes are deterministic unit/component/process/MCP contract/redaction suites. Nightly lanes add headed Chrome matrices, load, soak, chaos, fuzz corpus, and install/update drills. Launch adds the supported OS/Chrome/policy matrix. Manual lanes cover real user-approved Chrome and managed distribution.

Each failure uploads sanitized logs, wire transcripts, redacted event traces, environment/build manifests, seeds, and a replay command. Quarantine entries require an issue, owner, reason, first-seen commit, expiry, and replacement test; they cannot cover security, data-loss, or cross-space gates.

## Source of truth

Phase 0 freezes the environment matrix, budgets, fixture corpus, and artifact schema. Phase 2 freezes protocol/snapshot/error contracts. Phase 5 owns context/action/wait correctness. Phase 7 owns direct-product release gates and operational evidence. Phase 8 owns MCP adapter conformance, host-backed browser workflows, and compatibility release evidence.
