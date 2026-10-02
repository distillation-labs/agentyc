# Host trust and local protocol

**Status:** Phase 1 normative security artifact  
**Authority:** `agentyc-host` broker and the transport-neutral core contract  
**Scope:** local CLI/SDK clients, the host broker, and the Chrome Native Messaging bridge.

The host broker is the sole mutation authority for an enrolled profile binding. The local client protocol is not MCP, and Native Messaging is not the agent-facing API. A transport connection, session label, profile selector, Chrome API success, or client-supplied principal never grants authority by itself.

## 1. Trust boundaries

```text
agent client -- local OS IPC --> host broker -- Native Messaging --> extension
                                      |                              |
                                      |                              +-- Chrome APIs
                                      +-- ledger, leases, policy       +-- existing Chrome
```

| Boundary              | Trusted input                                            | Untrusted input                                                                    | Required admission                                                                                              |
| --------------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| local client to host  | OS peer identity and a live host lock                    | all bytes, claimed principal, labels, profile selectors, methods, and params       | OS credentials/ACL, bounded framing, protocol/version/schema/nonce/sequence checks, host-assigned principal     |
| Chrome to native host | caller origin supplied by Chrome transport metadata      | JSON fields that claim an origin, extension identity, profile, lease, or authority | exact registered origin, exact extension identity, binding state, handshake, nonce/sequence, epochs, capability |
| extension to Chrome   | validated host command and current extension lease fence | page content, page messages, URLs, titles, tab groups, browser IDs                 | extension-side schema, nonce, generation, capability, and lease checks at execution                             |
| host to ledger        | host-owned state and atomic writer                       | symlink/path replacement, partial or incompatible records                          | restrictive directory, exclusive lock, checksum/schema validation, atomic replacement                           |

The product trusts the installed extension and host processes running as the same OS user for the local boundary. It does **not** claim protection from malware or a hostile process running as that same OS user. A direct execution of the native host binary cannot be cryptographically distinguished from a Chrome-launched native host under that threat model; it still cannot bypass local OS admission, exact protocol checks, leases, epochs, or policy.

Remote TCP is disabled by default and is not a fallback when local IPC or Native Messaging is unavailable. Enabling remote access requires a new reviewed authentication and multi-tenant security decision; a session identifier, loopback assumption, or wildcard CORS is not authentication.

## 2. Endpoint and host-lock rules

- macOS/Linux use a Unix-domain socket below a host-owned state directory with restrictive directory and socket permissions. Windows uses a named pipe with an ACL restricted to the enrolled user/service identity.
- The state directory, socket, lock, temporary ledger, and replacement path are checked for symlinks and path replacement. A symlink component, unexpected owner, permissive mode, or replacement race fails closed.
- One broker owns one enrolled profile binding and one local endpoint. The lock is acquired before serving and held for the broker lifetime. A second process exits with a typed `host_already_running` result; it never steals a lock based only on a stale PID or timestamp.
- The broker allocates `principal_id`, `broker_epoch`, and connection identity. Client-provided principal names and profile selectors are metadata or lookup requests, not authentication.
- A host restart creates a new `broker_epoch`. In-flight post-dispatch actions become `unknown` until reconciliation; raw commands are not replayed.
- The host has no authority to launch/download Chrome or switch profiles; there is no automatic browser launch. Extension connection failure returns a typed unavailable result and leaves pages retained.

## 3. Framing and bounded allocation

### Local protocol

The local protocol is a persistent, length-delimited JSON protocol:

```text
4-byte unsigned big-endian payload length
UTF-8 JSON envelope
```

The maximum local control payload is **1 MiB (1,048,576 bytes)**. The receiver validates the length before allocating or reading the payload. Zero-length, truncated, invalid UTF-8, invalid JSON, duplicate envelope fields where the schema forbids them, and unsupported kinds fail closed. Newline boundaries have no meaning and are not a second framing mode.

### Native Messaging bridge

Chrome's Native Messaging transport remains a separate length-prefixed UTF-8 JSON stream. Chrome's platform limits are treated as hard ceilings: host-to-extension messages are at most **1 MiB**, and extension-to-host messages are at most **64 MiB**. The product control envelope is capped at 1 MiB in both directions; a larger extension-to-host allowance is not permission to send an unbounded command.

Large screenshots, PDFs, traces, HTML, and other artifacts use an opaque artifact handle plus bounded chunks:

| Limit                                                             | Normative value |
| ----------------------------------------------------------------- | --------------: |
| control payload                                                   |           1 MiB |
| artifact chunk                                                    |         256 KiB |
| artifact aggregate                                                |          32 MiB |
| chunks per artifact                                               |             256 |
| in-flight artifact bytes per connection                           |           4 MiB |
| cumulative artifact bytes per connection before an explicit reset |          64 MiB |

The host accounts for frame, chunk, artifact, assembly, queue, and in-flight bytes cumulatively per connection and per principal. A limit violation cancels the transfer, releases bounded buffers, records a redacted typed error, and may drain the connection. It never grows allocation based on a client-declared total.

## 4. Envelope and handshake contract

Every accepted envelope is one of `hello`, `hello_ok`, `request`, `response`, `event`, `cancel`, `artifact_begin`, `artifact_chunk`, `artifact_end`, or `error` with a versioned schema. Control envelopes carry bounded strings and fields such as:

- `protocol_version`, `kind`, `request_id`, and `method`;
- host-assigned `principal_id`/connection identity where returned;
- `space_id` and optional `page_id` for logical scope;
- `action_id`, `idempotency_key`, request hash, deadline, and cancellation identity;
- `broker_epoch`, `connection_epoch`, lease epoch, sequence, and event watermark;
- capability, generation, result/error, retry classification, and warnings.

A client cannot send a browser target/session/tab identifier as a public routing field. Any browser-generated value used internally by the extension is an opaque, redacted reconciliation hint and is never accepted as an authorization proof.

### Local client handshake

1. The host accepts the transport only after OS peer credentials/ACL and endpoint checks pass.
2. The client sends a bounded `hello` with protocol version, client kind/version, requested profile binding selector, and a fresh connection nonce.
3. The host negotiates an explicitly supported version, allocates a principal and `connection_epoch`, and returns `hello_ok` with the broker epoch, limits, capabilities, and resume requirements.
4. The host validates every request schema, scope, deadline, capability, lease epoch, and generation. A claimed principal or profile selector never overrides the OS admission result.
5. Version, schema, nonce, sequence, profile-binding, or compatibility failure happens before mutation authority and returns `protocol_mismatch`, `profile_not_found`, `permission_denied`, or a more specific typed error.

### Extension Native Messaging handshake

The Chrome-supplied caller origin is transport metadata. It is read from the Native Messaging launch/connection context and is **not** read from, compared with, or replaced by a JSON origin field. The host requires:

- exact stable extension origin/ID in the registered `allowed_origins` set; wildcards are forbidden;
- exact protocol version and schema;
- an enrolled profile binding in `unbound`, `bound`, `rebind_required`, or `revoked` state as appropriate;
- a fresh connection nonce and independent monotonic host-to-extension and extension-to-host sequence;
- `broker_epoch`, `connection_epoch`, `browser_session_epoch`, and `worker_instance_epoch` declarations plus capability;
- bounded message/chunk/artifact counters.

A presented `profile_instance_id` only selects a binding. A mismatch, copied profile, reinstall, storage reset, or extension identity change enters `rebind_required`, fences the previous authority, and requires explicit side-panel/installer confirmation. No payload boolean can clear that state.

Sequence numbers start at one for each direction of a new connection epoch. Reconnect always creates a new connection epoch and nonce; a sequence from a prior connection cannot be replayed in the new one. Duplicate sequence/request identities are idempotent only when the stored request hash and connection/lease context match.

## 5. Requests, actions, cancellation, and events

A request is transport work; an action is a durable broker mutation. The host matches responses by `request_id`, permits out-of-order responses, and records `action_id`/`idempotency_key` for mutations. Every mutation is checked at enqueue, dequeue, and immediately before extension dispatch.

- A deadline is required or bounded by the host. A client cannot extend it beyond the policy ceiling by retrying.
- Cancellation before dispatch removes or rejects queued work. Cancellation after dispatch stops waiting but does not claim that the browser side effect did not happen; the action becomes `unknown` until reconciled.
- A `cancel` message is scoped to the connection, principal, request/action, and current lease epoch. Stale cancellation cannot cancel a replacement action.
- Events are broker-sequenced, scope-filtered by `space_id`/`page_id`, and resumed from an event watermark. A lagged or invalid watermark returns `event_lagged` and a bounded resync path.
- A worker restart, Native Messaging EOF, host crash, browser restart, or debugger detach creates an explicit reconnect/reconciliation path. The host never replays clicks, inputs, navigation, uploads, storage writes, cookie operations, evaluation, or close operations.
- User takeover increments the lease epoch, fences old queues, sends an extension fence barrier, and waits for a durable acknowledgement. Lower-epoch commands are rejected at extension execution time. Missing acknowledgement leaves the space paused in `fence_pending`.

## 6. Artifact transfer

Artifacts are not control envelopes. `artifact_begin` declares a host-issued handle, kind, schema, total size within the aggregate limit, chunk size within the chunk limit, digest algorithm, and redaction status. Each chunk carries the handle, monotonically increasing chunk number, connection epoch, and bounded bytes. `artifact_end` is accepted only after all chunks are present, ordered, size-accounted, digest-verified, and policy-approved.

Artifact handles are scoped to principal, connection epoch, space/page, and retention. A missing, duplicate, out-of-order, oversized, late, or digest-mismatched chunk yields a typed transfer failure and discards the incomplete artifact. The ledger stores handles and redacted metadata, not screenshots, full page bodies, cookies, tokens, or secrets.

## 7. Version compatibility and failure matrix

The release tuple includes Chrome milestone/platform/policy, extension identity/build, Native Messaging host, broker/ledger schema, local protocol, CLI/SDK, and MCP adapter profile. Each component declares a compatible range. Incompatible versions fail before mutation authority with `protocol_mismatch` or `ledger_incompatible`.

| Failure                                      | Detection point                 | Required result                                                           | Recovery                                                                       |
| -------------------------------------------- | ------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| forged origin JSON field                     | Native Messaging handshake      | ignore payload claim; reject connection                                   | install/registration review; no mutation                                       |
| wrong transport origin or extension identity | handshake                       | reject before `hello_ok`                                                  | exact distribution/allowlist correction                                        |
| direct native-binary execution               | OS/host admission and handshake | treat as same-user process; no claimed-origin trust                       | local OS boundary and normal lease/policy checks; no false cryptographic claim |
| symlinked endpoint/socket/lock/state path    | endpoint/ledger open            | fail closed; do not follow or replace                                     | operator repairs restrictive state path                                        |
| replayed nonce or sequence                   | handshake/envelope validation   | reject and drain connection as policy requires                            | establish a fresh connection epoch                                             |
| reconnect with sequence reset in same epoch  | handshake/sequence validation   | reject; do not reset counters silently                                    | allocate a new connection epoch and nonce                                      |
| chunk flood or cumulative budget overflow    | frame/chunk accounting          | abort transfer, release buffers, typed `message_too_large`/`rate_limited` | bounded retry only with a new approved transfer                                |
| invalid UTF-8/JSON/schema or truncated EOF   | framing/parser                  | reject frame; distinguish clean EOF from malformed/truncated input        | reconnect and reconcile; never replay mutation                                 |
| protocol/version/ledger mismatch             | negotiation before authority    | `protocol_mismatch`/`ledger_incompatible`; no mutation                    | install a compatible release tuple or explicit migration                       |
| Native Messaging or worker restart           | reconnect                       | new connection/worker epoch; host ledger remains authority                | rehydrate metadata, fence stale commands, reconcile unknown actions            |
| host crash or broker lock loss               | host lifecycle                  | new broker epoch; pause/reconcile                                         | retain pages and records; no implicit cleanup                                  |
| user takeover fence acknowledgement lost     | fence state machine             | remain paused/`fence_pending`; reject all new mutations                   | reconcile highest accepted epoch and fence, then resume explicitly             |

The same-user threat boundary is deliberate: OS-peer admission prevents an unrelated user from using the endpoint under the supported platform model, but a malicious same-user process can often impersonate a local client or launch the native binary. The system limits damage with least privilege, exact extension-origin checks, leases, epochs, policy, redaction, and no automatic browser launch; it does not claim stronger isolation than the OS provides.

## 8. Required negative tests and checker contract

The Phase 1 host checker and later protocol tests MUST cover fragmented/coalesced/truncated frames, invalid UTF-8/JSON, wrong origin, forged origin field, direct host execution, symlink endpoint, replay, reconnect sequence reset, protocol/ledger mismatch, duplicate/out-of-order requests, cancellation before/after dispatch, event lag/resume, chunk flood, artifact digest failure, host lock contention, worker restart, Native Messaging EOF, browser restart, and missing takeover fence acknowledgement.

`scripts/check_host_protocol.py` validates this written contract and a supplied sanitized trust artifact. It is deterministic, read-only, stdlib-only, and does not claim that any live Chrome or host probe has passed.
