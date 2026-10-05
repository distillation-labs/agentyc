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
- Root attachment publication is transactional: synchronous related-target events are attributed before auto-attach returns, and setup failure detaches the root before reporting failure.
- Runtime execution-context and frame lifecycle events now bind logical frame attribution and clean up destroyed/cleared contexts; production Chrome enables the required Page/DOM/Network/Runtime/Accessibility event domains.
- Enterprise debugger errors including Chrome's `Host access is restricted by policy.` are normalized to the stable policy-denial result.
- Public debugger commands still reject `Target.setAutoAttach` and raw session/target identifiers. Internal setup is not an agent-facing passthrough.
- `chrome.tabs.onRemoved`, `onReplaced`, `onUpdated`, `onAttached`, `onDetached`, and activation state remain adapter-private; discarded/frozen state is observed without changing ownership.
- Side-panel actions now preserve semantics: `pause -> space.pause`, `handoff -> space.handoff`, `stop -> space.return_control`; every sensitive transition remains ticketed.
- `sidePanel.open()` is documented as user-action gated; no agent/native request calls it.

## Remaining evidence gates

Headed Chrome 125+ nested-frame/OOPIF behavior, worker/host/browser restart drills, side-panel accessibility and focus workflow, enterprise policy denial, installation/update/uninstall, and existing-profile coexistence remain required live evidence before Phase 4 can be marked complete. The owned disposable production lifecycle lane now has a source-bound live install/update/downgrade/rollback/uninstall record, but it does not substitute for existing-profile, side-panel, OOPIF, restart, policy, or production-distribution evidence. No current artifact claims those remaining results.

## Official service-worker lifecycle refresh — 2026-10-04

Retrieved the current Chrome documentation directly from the official Chrome for Developers pages:

- [Events in service workers](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/events) says extension event listeners should be registered in global scope during synchronous service-worker script execution so Chrome can dispatch events as soon as the worker starts. Page last-updated date: 2023-05-02.
- [The extension service worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle) says workers may terminate, global state is lost, `connectNative()` keeps the worker alive while its port is open, and a crashed native host should be reconnected from `onDisconnect`. Page last-updated date: 2023-05-02.
- [Native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) reiterates synchronous native framing and origin validation, and its current page (last updated 2026-09-16) recommends reconnecting from the native port's `onDisconnect` handler.

The audit found that runtime listeners were registered before the first await, but tab, tab-group, and debugger listeners were registered only after asynchronous metadata recovery. Those registrations now occur before the first await in `ServiceWorkerController.start()`. `TabsRegistry` ignores event side effects until initialized and performs a fresh tab inventory after metadata is applied, so events arriving during initialization are reconciled from current Chrome state. A live Native Messaging disconnect now invokes the reconnect path from its `onDisconnect` callback; failed reconnects use bounded backoff instead of a zero-delay retry loop. These changes are covered by deterministic service-worker and Native Messaging lifecycle tests; they are not live Chrome restart evidence.
