# Extension and host logging boundary

Logs are diagnostics, not a second ledger. The host owns authoritative state; the extension may log bounded lifecycle observations only.

## Allowed fields

- broker, connection, worker, browser-session, space, page, action, request, and lease epochs;
- typed error codes, lifecycle transitions, queue/backpressure counts, and reconnect reasons;
- redacted capability names and bounded timing/status metadata;
- evidence mode and artifact schema version.

## Forbidden fields

Do not log cookies, authorization headers, credentials, Native Messaging tokens/nonces, page bodies, DOM text, screenshots, network bodies, filesystem paths, profile paths, websocket URLs, raw tab/target/session/frame/group IDs, or user-entered secrets.

Raw Chrome identifiers may exist only in adapter-private in-memory maps. Before an event crosses the host or agent boundary, use the logical `space_id`/`page_id` scope and the repository redaction helpers.

## Failure handling

- Native Messaging stdout is protocol-only; diagnostics go to stderr or a redacted artifact.
- Unknown mutation outcomes are logged as `unknown_outcome` with action identity and epoch context, never as inferred success.
- Debugger detach, worker restart, host loss, Chrome loss, permission denial, and user takeover use distinct typed reasons.
- Debug bundles are bounded and must pass a raw-identifier/secret/page-content scan before publication.
