# Phase 4 Chrome extension audit

Audit date: 2026-10-04. This audit uses the current Chrome Developer Documentation and local extension fixtures. Deterministic extension tests are not headed existing-profile evidence.

## Official sources

- Native Messaging: <https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging>
- MV3 service-worker lifecycle: <https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle>
- Debugger API: <https://developer.chrome.com/docs/extensions/reference/api/debugger>
- Tabs API: <https://developer.chrome.com/docs/extensions/reference/api/tabs>
- Side Panel API: <https://developer.chrome.com/docs/extensions/reference/api/sidePanel>

## Closed deterministic requirements

- Native Messaging uses exact allowlisted extension origins, bounded native-endian frames, independent nonce/sequence/epoch checks, and no page/content-script access to the native port.
- MV3 worker state is reconstructable from host state plus bounded storage metadata; worker restart reports unknown mutations and does not replay them.
- Debugger attach now configures `Target.setAutoAttach` with `flatten: true` for Chrome 125+ related targets, recursively configures child iframe sessions, routes child-session events through logical frame bindings, and invalidates child sessions on detach.
- Public debugger commands still reject `Target.setAutoAttach` and raw session/target identifiers. Internal setup is not an agent-facing passthrough.
- `chrome.tabs.onRemoved`, `onReplaced`, `onUpdated`, `onAttached`, `onDetached`, and activation state remain adapter-private; discarded/frozen state is observed without changing ownership.
- Side-panel actions now preserve semantics: `pause -> space.pause`, `handoff -> space.handoff`, `stop -> space.return_control`; every sensitive transition remains ticketed.
- `sidePanel.open()` is documented as user-action gated; no agent/native request calls it.

## Remaining evidence gates

Headed Chrome 125+ nested-frame/OOPIF behavior, worker/host/browser restart drills, side-panel accessibility and focus workflow, enterprise policy denial, installation/update/uninstall, and existing-profile coexistence remain required live evidence before Phase 4 can be marked complete. No current artifact claims those results.
