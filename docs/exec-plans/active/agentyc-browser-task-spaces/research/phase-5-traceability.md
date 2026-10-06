# Phase 5 traceability

Status: `pending` until the Phase 4 real-Chrome gate and the Phase 5 production-path benchmark pass.

> **Architecture note:** This traceability table reflects the earlier extension-owned debugger/event design. The current product boundary makes the host the CDP owner and retains the extension only for tab creation. Extension debugger/content/UI entries below are historical implementation references, not evidence or current architecture requirements.

This document maps each Phase 5 task to the current implementation, deterministic validation, and remaining evidence. It deliberately separates code/tests from release evidence.

| Task  | Current implementation                                                                                                                                                                | Deterministic validation                                                         | Remaining gate                                                                                                                                                                                               |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| P5-T1 | `crates/agentyc-host/src/{events.rs,event_router.rs,broker.rs,native_messaging.rs}`, extension debugger/event adapter, explicit broker/source cursor resume and bridge-ingress resync | Host router/edge/native-resume tests; extension reconnect/debugger tests         | Native reconnect in real MV3/Chrome, queue overflow, duplicate/gap, fair subscriber, and live OOPIF evidence                                                                                                 |
| P5-T2 | `crates/agentyc-host/src/{snapshots.rs,context.rs,broker.rs}`, clean-cache bridge-observation guard, distinct bounded focus mode and focus-aware cache keys                           | Host cache/context/focus tests; `phase5_broker_integration`                      | Canonical keyed cache as the sole production read path, live delta/token measurements, frame partiality and reconnect evidence                                                                               |
| P5-T3 | `crates/agentyc-host/src/{refs.rs,actionability.rs,broker.rs}`, `refs.issue`, pre-dispatch registry resolution/invalidation                                                           | `phase5_broker_integration`; actionability unit tests                            | Live nested-frame/OOPIF, detached/reused frame, BFCache/prerender, and cross-origin evidence                                                                                                                 |
| P5-T4 | Typed wait wire schema, host wait engine, cooperative local IPC cancellation registry/worker path, extension `event.wait` adapter, logical event payload mapping                      | Host wait/edge tests including concurrent local cancel E2E; extension suite      | Native Messaging wait/cancel/reconnect E2E and stable-DOM/network fixture matrix                                                                                                                             |
| P5-T5 | Mandatory host proof for click/input/upload, broker ref/actionability gate, extension actionability checks                                                                            | `phase5_broker_integration`; extension actionability tests                       | Live hidden/covered/moving/offscreen/shadow/OOPIF/file-chooser matrix                                                                                                                                        |
| P5-T6 | Durable unknown receipts, no blind replay, extension unknown inventory, artifact unknown boundary                                                                                     | Host core reconciliation tests; extension reconnect tests                        | Live disconnect-after-send and reconnect inventory reconciliation                                                                                                                                            |
| P5-T7 | Scoped event adapter, policy ticket boundaries, broker-owned redacted observability routing/reads, host dialog/download/mock modules, resync ingress marker                           | Host module and broker side-state isolation tests; extension scoped-policy tests | Dialog/download/mock lifecycle routing, download capability decision, two-space live isolation and redaction evidence                                                                                        |
| P5-T8 | Offline/live benchmark generator and fail-closed performance checker                                                                                                                  | `scripts/test_p5_performance.py`; offline schema smoke                           | Production host/extension/Chrome package with 10 warmups, 200 p95/1000 p99 samples per blocking cell, bootstrap CIs, raw redacted samples, RSS, user-tab responsiveness, stale-ref and unknown-outcome rates |

## Source and test ownership

- Host production paths: `crates/agentyc-host/src/broker.rs`, `protocol.rs`, `native_messaging.rs`, `snapshots.rs`, `context.rs`, `refs.rs`, `actionability.rs`, `waits.rs`, `event_router.rs`.
- Extension production paths: `extension/src/service-worker.mjs`, `native-messaging.mjs`, `debugger-bridge.mjs`, `frames.mjs`, `scoped-events.mjs`.
- Host integration tests: `crates/agentyc-host/tests/host_core.rs`, `phase5_edge_cases.rs`, `phase5_broker_integration.rs`.
- Extension tests: `extension/tests/`.
- Performance contract: `scripts/run_p5_performance.py`, `scripts/check_p5_performance.py`, `scripts/p5_performance.py`, `scripts/p5_statistics.py`.
- Machine-readable manifest: `tests/phase-5-manifest.yaml`.

## Official Chrome contract audit

The implementation is checked against the relevant official Chrome documentation, not only local behavior:

- Native Messaging framing, host manifests, and allowed origins: <https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging>
- `chrome.debugger` target attachment, detach behavior, and restricted debugging domains: <https://developer.chrome.com/docs/extensions/reference/api/debugger>
- MV3 service-worker lifecycle and restart constraints: <https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle>
- debugger target/session attachment and related targets: <https://developer.chrome.com/docs/extensions/reference/api/debugger>
- screenshot and PDF capture methods: <https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-captureScreenshot> and <https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-printToPDF>
- tabs and tab replacement/removed lifecycle: <https://developer.chrome.com/docs/extensions/reference/api/tabs>

The local `reference/ego-lite-main` tree is treated as a pattern reference only; it does not override Chrome permissions, identity, Native Messaging, or user-control boundaries. The adopted patterns are bounded semantic snapshots, read/ref/act/verify, event-driven waits, explicit stale-ref recovery, and artifact handles rather than inline large bodies.

## Completion rule

The Phase 5 manifest remains `pending` while `dependency.real_chrome_gate` or any required live-evidence field is false. Offline performance artifacts are schema smoke only and must remain `release_eligible: false`.
