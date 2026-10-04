/*
 * The extension-side Native Messaging contract.
 *
 * Native Messaging provides framing, but it does not provide application
 * authentication, ordering, fencing, or a useful allocation bound. Those
 * properties are enforced here before a host message reaches Chrome.
 */

export const PROTOCOL_VERSION = 1;
export const MAX_CONTROL_BYTES = 1024 * 1024;
export const MAX_ARTIFACT_CHUNK_BYTES = 256 * 1024;
export const MAX_ARTIFACT_BYTES = 32 * 1024 * 1024;
export const MAX_ARTIFACT_CHUNKS = 256;
export const MAX_IN_FLIGHT_ARTIFACT_BYTES = 4 * 1024 * 1024;
export const MAX_CUMULATIVE_ARTIFACT_BYTES = 64 * 1024 * 1024;
export const MAX_STRING_BYTES = 64 * 1024;
export const MAX_NATIVE_CAPABILITIES = 32;
export const MAX_ARTIFACT_TRANSFERS = 16;
export const MAX_COLLECTION_ITEMS = 256;
export const MAX_ENVELOPE_DEPTH = 8;

export const ENVELOPE_KINDS = Object.freeze(
  new Set([
    "hello",
    "hello_ok",
    "request",
    "response",
    "event",
    "cancel",
    "error",
    "fence",
    "fence_ack",
    "action_result",
    "inventory",
    "artifact_begin",
    "artifact_chunk",
    "artifact_end",
  ]),
);

// Public extension errors are a closed vocabulary. Chrome's version-specific
// text and arbitrary adapter exceptions never become wire-level error codes.
export const PUBLIC_ERROR_CODES = Object.freeze(
  new Set([
    "approval_expired",
    "ambiguous_binding",
    "artifact_denied",
    "cancelled",
    "capability_unavailable",
    "content_bridge_error",
    "download_denied",
    "element_not_found",
    "evaluate_denied",
    "extension_error",
    "extension_not_connected",
    "focus_theft",
    "host_draining",
    "handshake_invalid",
    "handshake_required",
    "connection_not_ready",
    "incognito_not_supported",
    "invalid_argument",
    "invalid_json",
    "ledger_incompatible",
    "lease_expired",
    "message_too_large",
    "native_host_unavailable",
    "nonce_invalid",
    "nonce_replayed",
    "origin_invalid",
    "profile_disclosure_required",
    "profile_not_found",
    "protocol_invalid",
    "page_not_found",
    "page_not_owned",
    "permission_denied",
    "policy_denied",
    "proof_expired",
    "protocol_mismatch",
    "reconciliation_required",
    "replay_rejected",
    "resource_exhausted",
    "restricted_url",
    "schema_invalid",
    "sequence_gap",
    "sequence_invalid",
    "sequence_replayed",
    "space_forbidden",
    "space_not_found",
    "space_required",
    "stale_fence",
    "stale_generation",
    "stale_lease",
    "stale_ref",
    "stale_target",
    "target_not_attached",
    "target_replaced",
    "tabs_unavailable",
    "user_tab_close_denied",
    "stale_epoch",
    "timeout",
    "unknown",
    "unknown_outcome",
    "unmanaged_page",
    "upload_denied",
    "user_confirmation_required",
    "user_control_required",
  ]),
);

export function normalizePublicErrorCode(code) {
  return typeof code === "string" && PUBLIC_ERROR_CODES.has(code)
    ? code
    : "extension_error";
}

const RAW_BROWSER_KEYS = new Set([
  "tabid",
  "tab_id",
  "targetid",
  "target_id",
  "sessionid",
  "session_id",
  "groupid",
  "group_id",
  "windowid",
  "window_id",
  "frameid",
  "frame_id",
  "backendnodeid",
  "backend_node_id",
  "executioncontextid",
  "execution_context_id",
  "loaderid",
  "loader_id",
  "rawtabid",
  "raw_tab_id",
  "rawtargetid",
  "raw_target_id",
  "rawsessionid",
  "raw_session_id",
  "rawgroupid",
  "raw_group_id",
  "rawwindowid",
  "raw_window_id",
  "rawframeid",
  "raw_frame_id",
  "objectid",
  "object_id",
  "requestid",
  "scriptid",
  "script_id",
]);

const RAW_CDP_IDENTIFIER_REDACTION_KEYS = new Set([
  "objectid",
  "object_id",
  "requestid",
  "scriptid",
  "script_id",
]);

function normalizedKey(key) {
  return String(key).toLowerCase().replaceAll("-", "_");
}

function isRawBrowserKey(key, parentKey = "") {
  const normalized = normalizedKey(key);
  if (RAW_BROWSER_KEYS.has(normalized)) return true;
  const parent = normalizedKey(parentKey);
  return (
    normalized === "id" &&
    (parent.startsWith("frame") ||
      parent.startsWith("target") ||
      parent.startsWith("session") ||
      parent.startsWith("execution_context") ||
      parent.startsWith("executioncontext") ||
      parent.startsWith("loader") ||
      parent.startsWith("backend_node") ||
      parent.startsWith("backendnode"))
  );
}

function isRedactedBrowserKey(key, parentKey = "") {
  const normalized = normalizedKey(key);
  if (isRawBrowserKey(key, parentKey)) return true;
  if (RAW_CDP_IDENTIFIER_REDACTION_KEYS.has(normalized)) return true;
  const parent = normalizedKey(parentKey);
  return (
    normalized === "id" &&
    (parent.startsWith("object") ||
      parent.startsWith("request") ||
      parent.startsWith("script"))
  );
}

const MUTATING_METHODS = Object.freeze(
  new Set([
    "page.create",
    "page.adopt",
    "page.rebind",
    "page.close",
    "page.navigate",
    "page.reload",
    "action.click",
    "action.type",
    "action.fill",
    "action.press",
    "action.scroll",
    "action.evaluate",
    "action.execute",
    "action.storage_write",
    "action.cookie_write",
    "action.upload",
    "action.download",
    "debugger.command",
    "debugger.attach",
    "debugger.detach",
    "content.request",
    "group.present",
    "space.pause",
    "space.takeover",
    "space.return_control",
    "space.finish",
    "space.release",
    "space.fence",
    "fence.barrier",
    "fence",
  ]),
);

const RAW_ID_ERROR = "raw browser identifiers are extension-internal";

export class ProtocolError extends Error {
  constructor(code, message, details = undefined) {
    super(message);
    this.name = "ProtocolError";
    this.code = normalizePublicErrorCode(code);
    this.details = details;
  }
}

function textBytes(value) {
  if (typeof TextEncoder === "function") {
    return new TextEncoder().encode(value).byteLength;
  }
  return unescape(encodeURIComponent(value)).length;
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function walkBounded(value, depth, seen, parentKey = "") {
  if (depth > MAX_ENVELOPE_DEPTH) {
    throw new ProtocolError(
      "schema_invalid",
      "envelope nesting exceeds the bound",
    );
  }
  if (typeof value === "string") {
    if (textBytes(value) > MAX_STRING_BYTES) {
      throw new ProtocolError(
        "message_too_large",
        "envelope string exceeds the bound",
      );
    }
    return;
  }
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "number"
  ) {
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new ProtocolError(
        "schema_invalid",
        "envelope numbers must be finite",
      );
    }
    return;
  }
  if (typeof value !== "object") {
    throw new ProtocolError(
      "schema_invalid",
      "envelope contains an unsupported value",
    );
  }
  if (seen.has(value))
    throw new ProtocolError("schema_invalid", "envelope is cyclic");
  seen.add(value);
  if (Array.isArray(value)) {
    const maxItems =
      parentKey === "bytes" ? MAX_ARTIFACT_CHUNK_BYTES : MAX_COLLECTION_ITEMS;
    if (value.length > maxItems) {
      throw new ProtocolError(
        "message_too_large",
        "envelope array exceeds the bound",
      );
    }
    for (const item of value) walkBounded(item, depth + 1, seen, parentKey);
  } else {
    if (!isPlainObject(value)) {
      throw new ProtocolError(
        "schema_invalid",
        "envelope objects must be plain objects",
      );
    }
    const keys = Object.keys(value);
    if (keys.length > MAX_COLLECTION_ITEMS) {
      throw new ProtocolError(
        "message_too_large",
        "envelope object exceeds the key bound",
      );
    }
    for (const key of keys) {
      if (key === "__proto__" || key === "constructor" || key === "prototype") {
        throw new ProtocolError("schema_invalid", "unsafe envelope key");
      }
      walkBounded(value[key], depth + 1, seen, key);
    }
  }
  seen.delete(value);
}

function serializedBytes(value) {
  let serialized;
  try {
    serialized = JSON.stringify(value);
  } catch (error) {
    throw new ProtocolError(
      "schema_invalid",
      "envelope is not JSON serializable",
      {
        cause: error instanceof Error ? error.message : String(error),
      },
    );
  }
  if (typeof serialized !== "string") {
    throw new ProtocolError(
      "schema_invalid",
      "envelope must serialize to JSON",
    );
  }
  return { serialized, bytes: textBytes(serialized) };
}

export function assertNoRawBrowserIdentifiers(
  value,
  { path = "", parentKey = "" } = {},
) {
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) {
    value.forEach((item, index) =>
      assertNoRawBrowserIdentifiers(item, {
        path: `${path}[${index}]`,
        parentKey,
      }),
    );
    return value;
  }
  for (const [key, child] of Object.entries(value)) {
    if (isRawBrowserKey(key, parentKey)) {
      throw new ProtocolError(
        "schema_invalid",
        `${RAW_ID_ERROR}: ${path}${key}`,
      );
    }
    assertNoRawBrowserIdentifiers(child, {
      path: path ? `${path}.${key}.` : `${key}.`,
      parentKey: key,
    });
  }
  return value;
}

export function assertBoundedEnvelope(
  envelope,
  { maxBytes = MAX_CONTROL_BYTES, allowRawBrowserIdentifiers = false } = {},
) {
  if (!isPlainObject(envelope)) {
    throw new ProtocolError(
      "schema_invalid",
      "envelope must be a plain object",
    );
  }
  const { bytes } = serializedBytes(envelope);
  if (bytes > maxBytes) {
    throw new ProtocolError(
      "message_too_large",
      `envelope exceeds ${maxBytes} bytes`,
      {
        bytes,
        maxBytes,
      },
    );
  }
  walkBounded(envelope, 0, new Set());
  if (envelope.protocol !== PROTOCOL_VERSION) {
    throw new ProtocolError(
      "protocol_mismatch",
      `unsupported protocol ${envelope.protocol}`,
    );
  }
  if (typeof envelope.kind !== "string" || !ENVELOPE_KINDS.has(envelope.kind)) {
    throw new ProtocolError("schema_invalid", "unsupported envelope kind");
  }
  if (
    typeof envelope.nonce !== "string" ||
    envelope.nonce.length < 8 ||
    envelope.nonce.length > 128
  ) {
    throw new ProtocolError(
      "nonce_invalid",
      "envelope requires a bounded connection nonce",
    );
  }
  if (!Number.isSafeInteger(envelope.sequence) || envelope.sequence < 1) {
    throw new ProtocolError(
      "sequence_invalid",
      "envelope requires a positive sequence",
    );
  }
  if (!allowRawBrowserIdentifiers) assertNoRawBrowserIdentifiers(envelope);
  validateKindSpecificEnvelope(envelope);
  return envelope;
}

const COMMON_NATIVE_FIELDS = Object.freeze([
  "protocol",
  "kind",
  "nonce",
  "sequence",
]);

const POST_HANDSHAKE_FIELDS = Object.freeze([
  "broker_epoch",
  "connection_epoch",
  "worker_instance_epoch",
  "browser_session_epoch",
]);

function rejectUnknownFields(envelope, allowed) {
  const unknown = Object.keys(envelope).find((key) => !allowed.has(key));
  if (unknown) {
    throw new ProtocolError(
      "schema_invalid",
      `unknown ${envelope.kind} field: ${unknown}`,
    );
  }
}

function requiredTextField(envelope, key, max = MAX_STRING_BYTES) {
  if (
    typeof envelope[key] !== "string" ||
    envelope[key].length === 0 ||
    textBytes(envelope[key]) > max
  ) {
    throw new ProtocolError("schema_invalid", `${key} is required and bounded`);
  }
  return envelope[key];
}

function optionalTextField(envelope, key, max = MAX_STRING_BYTES) {
  if (envelope[key] === undefined || envelope[key] === null) return;
  requiredTextField(envelope, key, max);
}

function positiveIntegerField(envelope, key) {
  if (!Number.isSafeInteger(envelope[key]) || envelope[key] < 1) {
    throw new ProtocolError(
      "schema_invalid",
      `${key} must be a positive integer`,
    );
  }
  return envelope[key];
}

function nonNegativeIntegerField(envelope, key) {
  if (!Number.isSafeInteger(envelope[key]) || envelope[key] < 0) {
    throw new ProtocolError(
      "schema_invalid",
      `${key} must be a non-negative integer`,
    );
  }
  return envelope[key];
}

function optionalPositiveIntegerField(envelope, key) {
  if (envelope[key] === undefined || envelope[key] === null) return;
  positiveIntegerField(envelope, key);
}

function objectField(envelope, key, { optional = false } = {}) {
  if (envelope[key] === undefined || envelope[key] === null) {
    if (optional) return undefined;
    throw new ProtocolError("schema_invalid", `${key} must be an object`);
  }
  if (!isPlainObject(envelope[key])) {
    throw new ProtocolError("schema_invalid", `${key} must be an object`);
  }
  return envelope[key];
}

function arrayField(envelope, key, max = MAX_COLLECTION_ITEMS) {
  if (!Array.isArray(envelope[key])) {
    throw new ProtocolError("schema_invalid", `${key} must be an array`);
  }
  if (envelope[key].length > max) {
    throw new ProtocolError("message_too_large", `${key} exceeds its bound`);
  }
  return envelope[key];
}

function stringArrayField(envelope, key, max = MAX_COLLECTION_ITEMS) {
  const values = arrayField(envelope, key, max);
  if (
    values.some(
      (value) =>
        typeof value !== "string" ||
        value.length === 0 ||
        textBytes(value) > 128,
    )
  ) {
    throw new ProtocolError(
      "schema_invalid",
      `${key} contains an invalid string`,
    );
  }
  return values;
}

function logicalIdField(envelope, key, { optional = false } = {}) {
  if (optional && (envelope[key] === undefined || envelope[key] === null))
    return;
  const value = requiredTextField(envelope, key, 256);
  if (!/^[a-z][a-z0-9_-]{2,255}$/.test(value)) {
    throw new ProtocolError(
      "schema_invalid",
      `${key} is not a logical identifier`,
    );
  }
  return value;
}

function byteArrayField(envelope, key, max = MAX_ARTIFACT_CHUNK_BYTES) {
  const bytes = arrayField(envelope, key, max);
  if (bytes.some((byte) => !Number.isInteger(byte) || byte < 0 || byte > 255)) {
    throw new ProtocolError(
      "schema_invalid",
      `${key} contains an invalid byte`,
    );
  }
  return bytes;
}

function assertNativeLimits(limits) {
  if (!isPlainObject(limits)) {
    throw new ProtocolError("schema_invalid", "limits must be an object");
  }
  const keys = new Set([
    "max_control_bytes",
    "max_artifact_chunk_bytes",
    "max_artifact_bytes",
    "max_artifact_chunks",
    "max_in_flight_artifact_bytes",
    "max_cumulative_artifact_bytes",
  ]);
  rejectUnknownFields(
    { ...limits, kind: "limits" },
    new Set([...keys, "kind"]),
  );
  const bounds = {
    max_control_bytes: MAX_CONTROL_BYTES,
    max_artifact_chunk_bytes: MAX_ARTIFACT_CHUNK_BYTES,
    max_artifact_bytes: MAX_ARTIFACT_BYTES,
    max_artifact_chunks: MAX_ARTIFACT_CHUNKS,
    max_in_flight_artifact_bytes: MAX_IN_FLIGHT_ARTIFACT_BYTES,
    max_cumulative_artifact_bytes: MAX_CUMULATIVE_ARTIFACT_BYTES,
  };
  for (const [key, maximum] of Object.entries(bounds)) {
    if (
      !Number.isSafeInteger(limits[key]) ||
      limits[key] < 1 ||
      limits[key] > maximum
    ) {
      throw new ProtocolError(
        "schema_invalid",
        `${key} is outside its negotiated bound`,
      );
    }
  }
  return limits;
}

function assertArtifactDigest(envelope) {
  requiredTextField(envelope, "digest_algorithm", 32);
  requiredTextField(envelope, "digest", 128);
  if (
    envelope.digest_algorithm !== "fnv1a64" ||
    !/^fnv1a64:[0-9a-f]{16}$/.test(envelope.digest)
  ) {
    throw new ProtocolError(
      "schema_invalid",
      "artifact digest metadata is invalid",
    );
  }
}

/** Validate required and forbidden fields for each Native Messaging kind. */
export function validateKindSpecificEnvelope(envelope) {
  if (!isPlainObject(envelope)) return envelope;
  const kind = envelope.kind;
  const allowed = new Set(COMMON_NATIVE_FIELDS);
  if (kind === "hello") {
    [
      "worker_instance_epoch",
      "browser_session_epoch",
      "profile_instance_id",
      "profile_state",
      "extension_version",
      "capabilities",
      "limits",
    ].forEach((key) => allowed.add(key));
    rejectUnknownFields(envelope, allowed);
    positiveIntegerField(envelope, "worker_instance_epoch");
    positiveIntegerField(envelope, "browser_session_epoch");
    optionalTextField(envelope, "profile_instance_id", 128);
    optionalTextField(envelope, "extension_version", 128);
    stringArrayField(envelope, "capabilities", MAX_NATIVE_CAPABILITIES);
    if (
      envelope.profile_state !== undefined &&
      envelope.profile_state !== "bound"
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "hello profile_state must be bound",
      );
    }
    if (envelope.limits !== undefined) assertNativeLimits(envelope.limits);
    return envelope;
  }
  for (const key of POST_HANDSHAKE_FIELDS) allowed.add(key);
  const fields = {
    hello_ok: [
      "capabilities",
      "limits",
      "profile_instance_id",
      "profile_state",
    ],
    request: [
      "request_id",
      "action_id",
      "method",
      "params",
      "deadline_ms",
      "request_hash",
      "context",
      "space_id",
      "page_id",
      "lease_epoch",
      "operation",
      "idempotency_key",
      "payload",
      "postcondition",
      "ownership_proof",
      "url",
      "title",
      "now",
      "ttl",
      "timeout_ms",
      "target_generation",
      "navigation_generation",
      "document_generation",
      "expected_target_generation",
      "expected_navigation_generation",
      "expected_document_generation",
      "cleanup_proof",
      "control_ticket",
      "after_epoch",
      "after_sequence",
      "limit",
      "condition",
    ],
    response: ["request_id", "action_id", "ok", "result", "error", "warnings"],
    action_result: [
      "request_id",
      "action_id",
      "ok",
      "result",
      "error",
      "warnings",
    ],
    event: ["event", "payload", "space_id", "page_id"],
    inventory: ["payload"],
    fence: [
      "request_id",
      "request_token",
      "space_id",
      "old_epoch",
      "lease_epoch",
      "fence_epoch",
      "durable",
      "params",
    ],
    fence_ack: [
      "request_id",
      "action_id",
      "request_token",
      "space_id",
      "old_epoch",
      "lease_epoch",
      "fence_epoch",
      "durable",
      "ok",
      "result",
      "error",
      "warnings",
    ],
    cancel: ["request_id", "reason"],
    error: ["error"],
    artifact_begin: [
      "artifact_id",
      "request_id",
      "artifact_kind",
      "total_bytes",
      "chunk_size",
      "chunk_count",
      "digest_algorithm",
      "digest",
      "redacted",
    ],
    artifact_chunk: ["artifact_id", "chunk_sequence", "bytes"],
    artifact_end: [
      "artifact_id",
      "total_bytes",
      "chunk_count",
      "digest_algorithm",
      "digest",
    ],
  };
  const kindFields = fields[kind];
  if (!kindFields)
    throw new ProtocolError("schema_invalid", "unsupported envelope kind");
  for (const key of kindFields) allowed.add(key);
  rejectUnknownFields(envelope, allowed);
  for (const key of POST_HANDSHAKE_FIELDS) {
    if (envelope[key] !== undefined) positiveIntegerField(envelope, key);
  }
  if (kind === "hello_ok") {
    stringArrayField(envelope, "capabilities", MAX_NATIVE_CAPABILITIES);
    assertNativeLimits(envelope.limits);
    requiredTextField(envelope, "profile_instance_id", 128);
    if (envelope.profile_state !== "bound") {
      throw new ProtocolError(
        "schema_invalid",
        "hello_ok profile_state must be bound",
      );
    }
  } else if (kind === "request") {
    logicalIdField(envelope, "request_id");
    optionalTextField(envelope, "action_id", 256);
    requiredTextField(envelope, "method", 128);
    if (!/^[a-z0-9_-]+(?:\.[a-z0-9_-]+)*$/.test(envelope.method)) {
      throw new ProtocolError("schema_invalid", "request method is invalid");
    }
    objectField(envelope, "params");
    optionalTextField(envelope, "request_hash", 128);
    optionalTextField(envelope, "context", MAX_STRING_BYTES);
    if (envelope.deadline_ms !== undefined && envelope.deadline_ms !== null) {
      if (
        !Number.isSafeInteger(envelope.deadline_ms) ||
        envelope.deadline_ms < 1 ||
        envelope.deadline_ms > 24 * 60 * 60 * 1000
      ) {
        throw new ProtocolError(
          "schema_invalid",
          "request deadline_ms is invalid",
        );
      }
    }
  } else if (kind === "response" || kind === "action_result") {
    validateNativeResponseShape(envelope);
  } else if (kind === "event") {
    requiredTextField(envelope, "event", 128);
    objectField(envelope, "payload");
    optionalTextField(envelope, "space_id", 256);
    optionalTextField(envelope, "page_id", 256);
    if (envelope.page_id !== undefined && envelope.space_id === undefined) {
      throw new ProtocolError("schema_invalid", "page_id requires space_id");
    }
  } else if (kind === "inventory") {
    objectField(envelope, "payload");
  } else if (kind === "fence") {
    logicalIdField(envelope, "request_id");
    requiredTextField(envelope, "request_token", 256);
    requiredTextField(envelope, "space_id", 256);
    positiveIntegerField(envelope, "lease_epoch");
    positiveIntegerField(envelope, "fence_epoch");
    if (envelope.durable !== true)
      throw new ProtocolError("schema_invalid", "fence must be durable");
    objectField(envelope, "params");
  } else if (kind === "fence_ack") {
    logicalIdField(envelope, "request_id");
    validateNativeResponseShape(envelope);
  } else if (kind === "cancel") {
    logicalIdField(envelope, "request_id");
    optionalTextField(envelope, "reason", 256);
  } else if (kind === "error") {
    objectField(envelope, "error");
  } else if (kind === "artifact_begin") {
    logicalIdField(envelope, "artifact_id");
    logicalIdField(envelope, "request_id", { optional: true });
    requiredTextField(envelope, "artifact_kind", 32);
    nonNegativeIntegerField(envelope, "total_bytes");
    positiveIntegerField(envelope, "chunk_size");
    nonNegativeIntegerField(envelope, "chunk_count");
    if (
      envelope.total_bytes > MAX_ARTIFACT_BYTES ||
      envelope.chunk_size > MAX_ARTIFACT_CHUNK_BYTES ||
      envelope.chunk_count > MAX_ARTIFACT_CHUNKS
    ) {
      throw new ProtocolError(
        "message_too_large",
        "artifact declaration exceeds its bound",
      );
    }
    const expected = Math.ceil(envelope.total_bytes / envelope.chunk_size);
    if (envelope.chunk_count !== expected) {
      throw new ProtocolError(
        "schema_invalid",
        "artifact chunk_count does not match declaration",
      );
    }
    assertArtifactDigest(envelope);
    if (typeof envelope.redacted !== "boolean")
      throw new ProtocolError("schema_invalid", "artifact redacted is invalid");
  } else if (kind === "artifact_chunk") {
    logicalIdField(envelope, "artifact_id");
    nonNegativeIntegerField(envelope, "chunk_sequence");
    byteArrayField(envelope, "bytes");
  } else if (kind === "artifact_end") {
    logicalIdField(envelope, "artifact_id");
    nonNegativeIntegerField(envelope, "total_bytes");
    nonNegativeIntegerField(envelope, "chunk_count");
    if (
      envelope.total_bytes > MAX_ARTIFACT_BYTES ||
      envelope.chunk_count > MAX_ARTIFACT_CHUNKS
    ) {
      throw new ProtocolError(
        "message_too_large",
        "artifact completion exceeds its bound",
      );
    }
    assertArtifactDigest(envelope);
  }
  return envelope;
}

function validateNativeResponseShape(envelope) {
  logicalIdField(envelope, "request_id");
  if (typeof envelope.ok !== "boolean")
    throw new ProtocolError("schema_invalid", "response ok is required");
  const hasResult = envelope.result !== undefined && envelope.result !== null;
  const hasError = envelope.error !== undefined && envelope.error !== null;
  if (envelope.ok !== hasResult || envelope.ok === hasError) {
    throw new ProtocolError(
      "schema_invalid",
      "response result/error fields do not match ok",
    );
  }
  if (envelope.warnings !== undefined) {
    stringArrayField(envelope, "warnings", MAX_COLLECTION_ITEMS);
  }
}

export function validateEnvelope(
  envelope,
  {
    expectedNonce,
    expectedBrokerEpoch,
    expectedConnectionEpoch,
    expectedWorkerInstanceEpoch,
    expectedBrowserSessionEpoch,
    maxBytes = MAX_CONTROL_BYTES,
    allowRawBrowserIdentifiers = false,
    requireEpochs = false,
  } = {},
) {
  assertBoundedEnvelope(envelope, { maxBytes, allowRawBrowserIdentifiers });
  if (expectedNonce !== undefined && envelope.nonce !== expectedNonce) {
    throw new ProtocolError(
      "nonce_replayed",
      "envelope nonce does not match the live connection",
    );
  }
  checkEpoch(envelope.broker_epoch, expectedBrokerEpoch, "broker_epoch");
  checkEpoch(
    envelope.connection_epoch,
    expectedConnectionEpoch,
    "connection_epoch",
  );
  checkEpoch(
    envelope.worker_instance_epoch,
    expectedWorkerInstanceEpoch,
    "worker_instance_epoch",
  );
  checkEpoch(
    envelope.browser_session_epoch,
    expectedBrowserSessionEpoch,
    "browser_session_epoch",
  );
  if (requireEpochs) {
    for (const [name, value] of Object.entries({
      broker_epoch: envelope.broker_epoch,
      connection_epoch: envelope.connection_epoch,
      worker_instance_epoch: envelope.worker_instance_epoch,
      browser_session_epoch: envelope.browser_session_epoch,
    })) {
      if (!Number.isSafeInteger(value) || value < 1) {
        throw new ProtocolError("schema_invalid", `${name} is required`);
      }
    }
  }
  return envelope;
}

function checkEpoch(actual, expected, field) {
  if (actual !== undefined && (!Number.isSafeInteger(actual) || actual < 1)) {
    throw new ProtocolError(
      "schema_invalid",
      `${field} must be a positive integer`,
    );
  }
  if (expected !== undefined && actual !== undefined && actual !== expected) {
    throw new ProtocolError(
      "stale_epoch",
      `${field} does not match the live connection`,
      {
        expected,
        actual,
      },
    );
  }
}

export class SequenceValidator {
  constructor(next = 1) {
    if (!Number.isSafeInteger(next) || next < 1) {
      throw new ProtocolError(
        "sequence_invalid",
        "sequence validator must start at one or higher",
      );
    }
    this.next = next;
  }

  accept(sequence) {
    if (sequence !== this.next) {
      const code = sequence < this.next ? "sequence_replayed" : "sequence_gap";
      throw new ProtocolError(
        code,
        `expected sequence ${this.next}, received ${sequence}`,
        {
          expected: this.next,
          received: sequence,
        },
      );
    }
    this.next += 1;
    return sequence;
  }

  reset(next = 1) {
    if (!Number.isSafeInteger(next) || next < 1) {
      throw new ProtocolError(
        "sequence_invalid",
        "sequence validator reset is invalid",
      );
    }
    this.next = next;
  }
}

export function createNonce() {
  if (globalThis.crypto?.randomUUID)
    return `nonce_${globalThis.crypto.randomUUID().replaceAll("-", "")}`;
  const random = Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `nonce_${random}`.slice(0, 96);
}

export function createLogicalId(prefix) {
  const suffix = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}_${suffix}`;
}

export function isMutationMethod(method) {
  return (
    typeof method === "string" &&
    method !== "action.reconcile" &&
    (MUTATING_METHODS.has(method) || method.startsWith("action."))
  );
}

export function isFenceMethod(method) {
  return (
    method === "fence" || method === "fence.barrier" || method === "space.fence"
  );
}

function stripUndefined(value) {
  if (Array.isArray(value)) return value.map((item) => stripUndefined(item));
  if (value === null || typeof value !== "object") return value;
  const output = {};
  for (const [key, child] of Object.entries(value)) {
    if (child !== undefined) output[key] = stripUndefined(child);
  }
  return output;
}

export function makeEnvelope(kind, fields = {}) {
  const envelope = stripUndefined({
    protocol: PROTOCOL_VERSION,
    kind,
    ...fields,
  });
  assertBoundedEnvelope(envelope, { allowRawBrowserIdentifiers: false });
  return envelope;
}

export function errorResult(
  code,
  message,
  { retryable = false, outcome = undefined, details = undefined } = {},
) {
  const result = { code: normalizePublicErrorCode(code), message, retryable };
  if (outcome !== undefined) result.outcome = outcome;
  if (details !== undefined) result.details = details;
  return result;
}

export function redactBrowserIdentifiers(value, parentKey = "") {
  if (Array.isArray(value))
    return value.map((child) => redactBrowserIdentifiers(child, parentKey));
  if (value === null || typeof value !== "object") return value;
  const output = {};
  for (const [key, child] of Object.entries(value)) {
    if (isRedactedBrowserKey(key, parentKey)) continue;
    output[key] = redactBrowserIdentifiers(child, key);
  }
  return output;
}

export function browserHint(rawValue, salt = "") {
  const input = `${salt}:${String(rawValue)}`;
  let hash = 0xcbf29ce484222325n;
  for (const byte of new TextEncoder().encode(input)) {
    hash ^= BigInt(byte);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return `hint_${hash.toString(16).padStart(16, "0")}`;
}

function publicErrorDetails(details) {
  if (details === undefined) return undefined;
  try {
    const safe = redactBrowserIdentifiers(details);
    const serialized = JSON.stringify(safe);
    return typeof serialized === "string" && serialized.length <= 8 * 1024
      ? safe
      : { truncated: true };
  } catch {
    return { truncated: true };
  }
}

export function publicError(error, fallbackCode = "extension_error") {
  if (error instanceof ProtocolError) {
    return errorResult(normalizePublicErrorCode(error.code), error.message, {
      retryable: Boolean(error.retryable),
      outcome: error.outcome,
      details: publicErrorDetails(error.details),
    });
  }
  if (error && typeof error === "object" && typeof error.code === "string") {
    return errorResult(
      normalizePublicErrorCode(error.code),
      typeof error.message === "string" ? error.message : fallbackCode,
      {
        retryable: Boolean(error.retryable),
        outcome: error.outcome,
        details: publicErrorDetails(error.details),
      },
    );
  }
  return errorResult(
    normalizePublicErrorCode(fallbackCode),
    error instanceof Error ? error.message : String(error),
  );
}

export function assertLogicalScope(
  { spaceId, pageId },
  { pageRequired = false } = {},
) {
  if (
    typeof spaceId !== "string" ||
    !/^space_[a-z0-9][a-z0-9_-]{0,127}$/.test(spaceId)
  ) {
    throw new ProtocolError(
      "schema_invalid",
      "space_id must be a logical space identifier",
    );
  }
  if (
    pageRequired &&
    (typeof pageId !== "string" ||
      !/^page_[a-z0-9][a-z0-9_-]{0,127}$/.test(pageId))
  ) {
    throw new ProtocolError(
      "schema_invalid",
      "page_id must be a logical page identifier",
    );
  }
  if (
    pageId !== undefined &&
    pageId !== null &&
    (typeof pageId !== "string" ||
      !/^page_[a-z0-9][a-z0-9_-]{0,127}$/.test(pageId))
  ) {
    throw new ProtocolError(
      "schema_invalid",
      "page_id must be a logical page identifier",
    );
  }
}

export function isRestrictedUrl(url) {
  if (typeof url !== "string") return true;
  return (
    /^(?:chrome|edge|about|devtools|view-source|chrome-extension):/i.test(
      url,
    ) || url.startsWith("file:")
  );
}

export const CHROME_ERROR_CODES = Object.freeze([
  "restricted_url",
  "incognito_not_supported",
  "policy_denied",
  "artifact_denied",
  "permission_denied",
  "unknown",
]);

const CHROME_ERROR_MESSAGES = Object.freeze({
  restricted_url: "Chrome rejected access to a restricted page",
  incognito_not_supported: "Chrome rejected access to an incognito page",
  policy_denied: "Chrome policy rejected the operation",
  artifact_denied: "Chrome or DLP policy rejected the artifact",
  permission_denied: "Chrome permission rejected the operation",
  unknown: "Chrome returned an unclassified error",
});

function boundedChromeErrorText(error) {
  const values = [
    error?.message,
    error?.details?.message,
    error?.error?.message,
    error?.cause?.message,
    typeof error === "string" ? error : undefined,
  ];
  const value = values.find((candidate) => typeof candidate === "string");
  return typeof value === "string" ? value.slice(0, 512).toLowerCase() : "";
}

function explicitChromeErrorCode(error) {
  for (const value of [
    error?.chrome_code,
    error?.chromeCode,
    error?.error_code,
    error?.errorCode,
    error?.code,
  ]) {
    if (typeof value === "string" && CHROME_ERROR_CODES.includes(value))
      return value;
  }
  return undefined;
}

function isArtifactChromeOperation(operation) {
  return (
    typeof operation === "string" &&
    /captureScreenshot|printToPDF|artifact|dlp|screenshot|pdf/i.test(operation)
  );
}

/**
 * Reduce unbounded, version-specific Chrome failures to the bounded public
 * denial vocabulary. The input text is deliberately not returned to callers.
 */
export function classifyChromeError(
  error,
  { operation, url, incognito = false } = {},
) {
  const explicit = explicitChromeErrorCode(error);
  if (explicit) return explicit;
  if (incognito === true) return "incognito_not_supported";
  if (typeof url === "string" && isRestrictedUrl(url)) return "restricted_url";

  const text = boundedChromeErrorText(error);
  if (
    /incognito|private browsing|private window|off.the.record|guest mode/.test(
      text,
    )
  )
    return "incognito_not_supported";
  if (
    /cannot access (?:the )?(?:contents? of )?the page|cannot attach to .*target|restricted page|browser internal|debugger.accessible/.test(
      text,
    )
  )
    return "restricted_url";
  if (
    /data loss prevention|\bdlp\b|artifact.*(?:denied|blocked)|(?:screenshot|screen capture|print.*pdf|capture).*(?:restricted|denied|blocked|not allowed)/.test(
      text,
    ) ||
    (isArtifactChromeOperation(operation) &&
      /\b(?:restricted|blocked|disallowed|not allowed)\b/.test(text))
  )
    return "artifact_denied";
  if (
    /enterprise|managed policy|administrator|admin policy|blocked by policy|not allowed by policy|policy restriction/.test(
      text,
    )
  )
    return "policy_denied";
  if (
    /permission|not authorized|unauthori[sz]ed|access denied|not permitted|forbidden|requires debugger/.test(
      text,
    )
  )
    return "permission_denied";
  return "unknown";
}

export function chromeErrorMessage(code) {
  return CHROME_ERROR_MESSAGES[code] ?? CHROME_ERROR_MESSAGES.unknown;
}

export function chromeErrorCodes() {
  return CHROME_ERROR_CODES;
}

export const RAW_BROWSER_IDENTIFIER_KEYS = Object.freeze([
  "tabId",
  "tab_id",
  "targetId",
  "target_id",
  "sessionId",
  "session_id",
  "groupId",
  "group_id",
  "windowId",
  "window_id",
  "frameId",
  "frame_id",
  "backendNodeId",
  "backend_node_id",
  "executionContextId",
  "execution_context_id",
  "loaderId",
  "loader_id",
  "objectId",
  "object_id",
  "requestId",
  "scriptId",
  "script_id",
  "rawTabId",
  "raw_tab_id",
  "rawTargetId",
  "raw_target_id",
  "rawSessionId",
  "raw_session_id",
  "rawGroupId",
  "raw_group_id",
  "rawWindowId",
  "raw_window_id",
  "rawFrameId",
  "raw_frame_id",
]);
