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
export const MAX_STRING_BYTES = 64 * 1024;
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
  ]),
);

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

const MUTATING_METHODS = Object.freeze(
  new Set([
    "page.create",
    "page.adopt",
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
    this.code = code;
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

function walkBounded(value, depth, seen) {
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
    if (value.length > MAX_COLLECTION_ITEMS) {
      throw new ProtocolError(
        "message_too_large",
        "envelope array exceeds the bound",
      );
    }
    for (const item of value) walkBounded(item, depth + 1, seen);
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
      walkBounded(value[key], depth + 1, seen);
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
  return envelope;
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
  const result = { code, message, retryable };
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
    if (isRawBrowserKey(key, parentKey)) continue;
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

export function publicError(error, fallbackCode = "extension_error") {
  if (error instanceof ProtocolError) {
    return errorResult(error.code, error.message, { details: error.details });
  }
  if (error && typeof error === "object" && typeof error.code === "string") {
    return errorResult(
      error.code,
      typeof error.message === "string" ? error.message : fallbackCode,
      {
        retryable: Boolean(error.retryable),
        outcome: error.outcome,
        details: error.details,
      },
    );
  }
  return errorResult(
    fallbackCode,
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
