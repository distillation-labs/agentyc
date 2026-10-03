# Phase 0 Chrome documentation audit

**Captured:** 2026-10-03 UTC
**Status:** static audit complete; live existing-profile gate remains blocked
**Sources:** official Chrome for Developers documentation only

## Sources checked

- [Manifest file format](https://developer.chrome.com/docs/extensions/reference/manifest)
- [Manifest key](https://developer.chrome.com/docs/extensions/reference/manifest/key)
- [Manifest icons](https://developer.chrome.com/docs/extensions/reference/manifest/icons)
- [Declare permissions](https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions)
- [Permissions](https://developer.chrome.com/docs/extensions/reference/permissions-list)
- [Content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts)
- [Native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)
- [Debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger)
- [Tabs API](https://developer.chrome.com/docs/extensions/reference/api/tabs)
- [Tab groups API](https://developer.chrome.com/docs/extensions/reference/api/tabGroups)
- [Side Panel API](https://developer.chrome.com/docs/extensions/reference/api/sidePanel)
- [Service worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
- [Extension distribution](https://developer.chrome.com/docs/extensions/how-to/distribute)
- [Remote debugging security change](https://developer.chrome.com/blog/remote-debugging-port)

## Findings and fixes

| Area | Official contract | Repository result |
| --- | --- | --- |
| MV3 identity | `key` preserves an unpacked development ID; Web Store identity is controlled by the uploaded item. | Kept the pinned development key and documented it as development-only. Web Store/managed distribution remains a separate release decision. |
| Web Store packaging | Web Store extensions should provide 128px and 48px icons; SVG is not supported. | Added PNG icons at 16px, 48px, and 128px and a manifest test. |
| Incognito | The manifest can explicitly disallow incognito. | Added `"incognito": "not_allowed"`; runtime policy already rejects incognito pages. |
| Permissions | `debugger`, `nativeMessaging`, `tabs`, and `tabGroups` have user-visible warnings; static content-script matches can also trigger host-access warnings. | Added the warning/disclosure contract to the permissions document. No broad host permission or optional capability was added. |
| Native Messaging registration | macOS Google Chrome uses the user-level `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/` path; `allowed_origins` is exact and has no wildcards. | Registration and manifest remain exact for Google Chrome on macOS. Chrome for Testing and Chromium paths remain separate platform work. |
| Native Messaging argv | Chrome supplies the caller origin as `argv[1]`; Windows may also supply `--parent-window=<handle>`. | The host now accepts and bounds the documented Windows argument instead of rejecting it. |
| Native Messaging framing | Length is a 32-bit value in native byte order; Chrome limits host-to-extension messages to 1 MiB and extension-to-host messages to 64 MiB. | The host now uses native-endian framing. The product's stricter 1 MiB control-envelope cap remains intentional and tested. |
| Content-script trust | Content scripts cannot call Native Messaging. The service worker must validate `sender.origin`/`sender.url` and sanitize forwarded payloads. | The service worker now checks the sender origin and URL against the managed page origin before accepting content messages. |
| MV3 lifecycle | Workers can terminate; `connectNative()` keeps a worker alive while connected, and a crashed host requires reconnect handling from `onDisconnect`. | The Native Messaging client now schedules an immediate reconnect after a live port disconnect and backs off after failed connection attempts. |
| Debugger | `debugger` is required; supported CDP domains are restricted; flat related-target sessions are available from Chrome 125. | Root-target debugger behavior is implemented and allowlisted. Public `Target` control remains denied; OOPIF/related-target execution is still an explicit unobserved residual, not a claimed Phase 0 pass. |
| Tabs and focus | `tabs.create({active:false})` creates an inactive tab; `active` does not control window focus. | Agent page creation explicitly uses `active:false` and never calls a focus/bring-to-front API. Live focus coexistence is still required evidence. |
| Tab groups | Groups are visual; group IDs are unique only within a browser session; a group belongs to one window. `tabs.group` can create or extend a group. | Group membership remains non-authoritative. Documentation now states the one-window limitation and treats cross-window grouping as best-effort visual drift. |
| Side panel | `sidePanel.open()` requires a user action; `setPanelBehavior({openPanelOnActionClick:true})` is the documented toolbar path. | The service worker configures toolbar clicks to open the panel; it never calls `open()` autonomously. |
| Chrome 136+ debugging | Remote-debugging switches require a non-standard user-data directory; the existing-profile product must not use a copied endpoint. | Disposable probes use an owned temporary profile. Existing-Chrome mode remains extension/Native Messaging only. |
| Distribution | Unpacked extensions are for trusted personal development; ordinary users install Web Store-signed extensions; macOS self-hosting requires enterprise management. | The plan now distinguishes unpacked development enrollment from production distribution. No Phase 0 gate is marked complete from unpacked disposable evidence. |

## Remaining Phase 0 evidence gap

The documentation and static contracts are corrected, but this audit does not create live evidence. `scripts/check_phase_0_baseline.py research/phase-0-baseline.md` must remain blocked until an approved extension/host enrollment exists in the user's existing Chrome profile and all ten coexistence scenarios execute with zero user-tab closes, focus theft, cross-space mutations, and stale-agent mutations.
