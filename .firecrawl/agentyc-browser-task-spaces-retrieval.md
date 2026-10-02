# Firecrawl retrieval log — agentyc browser task spaces

- **Retrieved:** 2026-10-01
- **Purpose:** Verify MCP transport/version behavior, CDP target/session contracts, and ego-lite Space/snapshot/ledger patterns.
- **Method:** Firecrawl MCP `firecrawl_scrape` on canonical URLs. Search attempts returned HTTP 400 and were not used as evidence.

## Successful retrievals

- MCP modern stdio: <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio>
- MCP modern Streamable HTTP: <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>
- MCP modern versioning: <https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning>
- MCP legacy transports: <https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>
- MCP legacy lifecycle: <https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle>
- rmcp 1.7 Streamable HTTP source: <https://docs.rs/crate/rmcp/1.7.0/source/src/transport/streamable_http_server/tower.rs>
- CDP Target protocol: <https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Target.pdl>
- ego-lite Space docs: <https://lite.ego.app/document/en/docs/space>
- ego-lite Snapshot docs: <https://lite.ego.app/document/en/docs/snapshot>
- ego-lite page ledger: <https://raw.githubusercontent.com/citrolabs/ego-lite/dca7003349c5f7132189ba00547cbbd7ff8e597e/package/ego-browser/src/page-ledger.ts>
- ego-lite browser runtime: <https://raw.githubusercontent.com/citrolabs/ego-lite/dca7003349c5f7132189ba00547cbbd7ff8e597e/package/ego-browser/src/browser-runtime.ts>

## Access limit

Firecrawl search repeatedly returned HTTP 400, so no search-index result was treated as evidence. Direct canonical scrapes succeeded. Live browser/Chrome validation was not performed during planning; it is an explicit Phase 0/7 validation task.

## Superseding retrievals — existing Chrome integration

Direct canonical scrapes completed 2026-10-01:

- <https://developer.chrome.com/blog/remote-debugging-port>
- <https://developer.chrome.com/docs/extensions/reference/api/debugger>
- <https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging>
- <https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle>
- <https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts>
- <https://developer.chrome.com/docs/extensions/reference/api/scripting>
- <https://developer.chrome.com/docs/extensions/reference/api/tabs>
- <https://developer.chrome.com/docs/extensions/reference/api/tabGroups>
- <https://developer.chrome.com/docs/extensions/reference/api/storage>
- <https://developer.chrome.com/docs/extensions/reference/api/sidePanel>
- <https://developer.chrome.com/docs/extensions/how-to/distribute>

Decision-relevant facts captured in `research/source-ledger.md` as S-018–S-024: default-profile remote debugging is restricted from Chrome 136; `chrome.debugger` is an allowlisted CDP transport with flat sessions from Chrome 125; Native Messaging uses exact extension origins and bounded length-prefixed messages; MV3 workers are restartable; content scripts are isolated and relay privileged messages; tabs/tab groups/side panels provide user-visible control; extension distribution and storage impose installation and persistence constraints.
