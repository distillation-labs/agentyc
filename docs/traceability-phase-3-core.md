# Phase 3 core traceability

Evidence mode: deterministic repository and process tests. The selected U3-1 topology is `single_broker_native_shim_forwarding`.

| Task | Implementation | Required evidence |
| --- | --- | --- |
| P3-T1 | `crates/agentyc-host/src/{broker.rs,host.rs,ledger.rs}` | `host_lifecycle` lock, stale-owner recovery, lifecycle, endpoint tests |
| P3-T2 | `crates/agentyc-host/src/{local_ipc.rs,protocol.rs}` | peer-credential admission, bounded framing, clean/truncated EOF, multi-client tests |
| P3-T3 | `crates/agentyc-host/src/{native_messaging.rs,bin/agentyc-native-host.rs}` | exact origin, Native Messaging framing, one-owner forwarding endpoint, reconnect router |
| P3-T4 | `crates/agentyc-host/src/{ledger.rs,broker.rs}` | atomic replacement, quarantine, schema/limits, profile rebind and lifecycle tests |
| P3-T5 | `crates/agentyc-host/src/{broker.rs,leases.rs}` | takeover, pause, handoff, expiry, fence acknowledgement, stale-epoch tests |
| P3-T6 | `crates/agentyc-host/src/{scheduler.rs,broker.rs}` | broker dispatch/read permit integration and cancellation/deadline/backpressure unit tests |
| P3-T7 | `crates/agentyc-host/src/{bridge.rs,native_messaging.rs}` | `BridgeRouter`, logical-only bridge boundary, extension epoch/fence tests |
| P3-T8 | `crates/agentyc-host/src/{broker.rs,ledger.rs}` | idempotency, unknown outcome, reconciliation, cleanup proof tests |
| P3-T9 | `crates/agentyc-runtime/src/{lib.rs,host_client.rs}` | explicit `HostClient` path and `LegacyBrowserRuntime` compatibility alias; no default flip |

## U3-1 decision

The first Native Messaging host process acquires the profile-scoped ledger lock before reading the extension handshake. It owns one broker and publishes owner-readable endpoint metadata. A later Chrome-launched shim validates its exact transport origin, observes the lock, and forwards its raw stdio to the owner's private Unix forwarding socket. The owner accepts a fresh handshake and installs it through `BridgeRouter`; it never opens a second broker.

## Safety properties

- The local socket and forwarding socket are private, symlink-checked, owner-peer checked, bounded, and removed only by their owner.
- Dead lock owners are recoverable only after a bounded PID liveness check; live owners remain protected by `AlreadyOwned`.
- A bridge disconnect marks the host degraded and does not replay mutations. A fresh extension connection must complete both Native Messaging and core admission before authority returns.
- Pause/handoff consume an acknowledged fence and persist a non-mutating lifecycle; user/unmanaged page cleanup remains individually authorized.

## Nonclaims

This artifact does not claim live existing-profile Chrome, production installation/distribution, Windows named-pipe support, OOPIF/session-graph automation, or Phase 4 extension completion.
