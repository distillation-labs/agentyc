# Phase 3 Chrome API audit

Audit date: 2026-10-04. Sources are current Chrome Developer Documentation pages retrieved for this phase. This is a design/contract audit, not live-browser evidence.

## Official sources

- Native Messaging: <https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging>
- Debugger API: <https://developer.chrome.com/docs/extensions/reference/api/debugger>
- Extension service-worker lifecycle: <https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle>
- Tabs API: <https://developer.chrome.com/docs/extensions/reference/api/tabs>
- Side Panel API: <https://developer.chrome.com/docs/extensions/reference/api/sidePanel>

## Verified requirements and implementation mapping

| Chrome requirement | Phase 3 mapping | Status |
| --- | --- | --- |
| Native host manifests use exact `allowed_origins`; wildcards are forbidden | `NativeMessagingConfig`, `ALLOWED_EXTENSION_ORIGINS`, origin tests | covered |
| macOS/Linux host paths are absolute; Windows uses registry locations | Phase 0 installer docs; Phase 3 records macOS-first topology and leaves Windows registration later | macOS covered; Windows deferred |
| Chrome passes caller origin as the first argument and Windows may pass `--parent-window` | `parse_native_messaging_arguments` validates both without treating argv as sole authentication | covered |
| Native messages use native-endian 32-bit length; host-to-extension is 1 MiB and extension-to-host is 64 MiB | Native frame reader and product control/artifact bounds | covered |
| `connectNative` is long-lived; `sendNativeMessage` starts a process per message | production extension uses long-lived port; shim/owner topology is only for the port path | covered |
| stdout is protocol-only; diagnostics belong on stderr | native host uses `eprintln!`; forwarding is raw bytes only | covered |
| native messaging is unavailable to content scripts | extension architecture keeps service worker as privileged relay | Phase 4 integration |
| worker globals are lost on idle shutdown; storage must retain reconnect metadata | host remains authoritative; extension epochs/metadata are re-admitted | Phase 4 integration |
| `connectNative()` can keep an MV3 worker alive from Chrome 105; a closed host port must be handled | manifest floor is 125; reconnect path and host bridge loss are typed | Phase 4 integration |
| debugger attach is target-scoped and supports `onEvent`, `onDetach`, flat sessions from Chrome 125 | host bridge exposes logical operations only; target/session mapping remains extension-owned | Phase 4 |
| debugger detach occurs for tab close or DevTools; no blind reattach/replay | `BridgeRouter` replaces a disconnected bridge only after a fresh handshake; action outcomes are not replayed | host boundary covered; real Chrome Phase 4 |
| tabs IDs are session-scoped; `onRemoved`, `onReplaced`, `onUpdated`, `onActivated` are required | raw IDs never enter host records; extension event reconciliation is Phase 4 | Phase 4 |
| side-panel `open()` requires a user action | host only sends control data; side-panel gesture/UI enforcement is Phase 4 | Phase 4 |

## Findings closed in Phase 3

- The Native Messaging topology no longer opens a second ledger/broker when Chrome launches a duplicate shim. The owner lock is acquired before the handshake is consumed.
- The owner publishes atomic endpoint metadata and accepts same-OS-user forwarded connections through a private socket. Fresh extension/core handshakes replace the bridge through `BridgeRouter`.
- The host does not infer profile authority from Native Messaging JSON or argv. The exact extension origin is allowlisted and the profile binding is checked by the core handshake.
- Host lifecycle now distinguishes ready, degraded, recovering, orphaned, and stopped states. Bridge loss is retained as a typed degraded state; mutation replay remains forbidden.

## Residuals intentionally owned by later phases

No Phase 3 deterministic check claims headed existing-profile Chrome, debugger/OOPIF/session behavior, service-worker termination in real Chrome, side-panel gesture behavior, extension packaging/distribution, or Windows/Linux installation. Those remain Phase 4/7/8 evidence gates.
