import net from "node:net";

import { AgentycError, mapWireError } from "./errors.mjs";
import { normalizeDeadline } from "./constants.mjs";

export const PROTOCOL_VERSION = 1;
export const DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024;

let requestSequence = 0;
let connectionSequence = 0;

function nextRequestId() {
  requestSequence = (requestSequence + 1) % 1_000_000_000;
  return `req_sdk_${Date.now().toString(36)}_${requestSequence.toString(36)}`;
}

function transportError(code, message, details = undefined) {
  const error = new AgentycError({
    code,
    message,
    retryable: code === "native_host_unavailable",
    guidance: code === "native_host_unavailable" ? "retry" : "none",
    details,
    transportFailure: true,
  });
  return error;
}

function cancelledTransportError(
  reason = "the request was cancelled",
  dispatched = false,
) {
  const error = new Error(reason);
  error.name = "AbortError";
  error.cancelled = true;
  error.dispatched = dispatched;
  return error;
}

function protocolError(message, details = undefined) {
  return transportError("invalid_json", message, details);
}

function normalizeIdentity(value, prefix, field) {
  const input = value ?? `${prefix}sdk`;
  const identity = input.startsWith(prefix) ? input : `${prefix}${input}`;
  if (!/^[a-z][a-z0-9_-]*$/.test(identity.slice(prefix.length))) {
    throw new AgentycError({
      code: "invalid_argument",
      message: `${field} must use a bounded logical identity suffix`,
      retryable: false,
      guidance: "none",
    });
  }
  return identity;
}

function normalizeMaxPayloadBytes(value) {
  const limit = value ?? DEFAULT_MAX_PAYLOAD_BYTES;
  if (!Number.isInteger(limit) || limit <= 0 || limit > 0xffffffff) {
    throw new AgentycError({
      code: "invalid_argument",
      message: "maxPayloadBytes must be an integer between 1 and 4294967295",
      retryable: false,
      guidance: "none",
    });
  }
  return limit;
}

function jsonField(value) {
  if (value === undefined) return undefined;
  if (typeof value === "string") return value;
  const encoded = JSON.stringify(value);
  if (encoded === undefined)
    throw new TypeError("protocol parameter is not JSON serializable");
  return encoded;
}

function coreParams(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return {};
  return Object.fromEntries(
    Object.entries(params)
      .filter(([, value]) => value !== undefined)
      .map(([key, value]) => [key, jsonField(value)]),
  );
}

function decodeJsonField(value) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    // Some injected fixtures and older adapters return plain scalar strings.
    // Keep those strings instead of rejecting the whole response.
    return value;
  }
}

// Rust's default protocol payload is BTreeMap<String, String>. Every value
// produced by put_json is JSON text, including scalar strings, numbers, and
// booleans. Decode every string-map value, while retaining plain legacy strings.
function decodeResult(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, entry]) => [key, decodeJsonField(entry)]),
  );
}

function responseFromEnvelope(envelope) {
  return {
    ...envelope,
    request_id: envelope.request_id,
    ok: envelope.ok,
    result: decodeResult(envelope.result),
    error: envelope.error,
  };
}

function normalizeWatermark(value, field) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 0) {
    throw new AgentycError({
      code: "invalid_argument",
      message: `${field} must be a non-negative safe integer`,
      retryable: false,
      guidance: "none",
    });
  }
  return number;
}

function resumeResponseMatches(requestId, response) {
  const match = /^req_resume-(\d+)-(\d+)$/.exec(requestId);
  if (!match) return false;
  if (response.ok !== true) return true;
  const cursor = decodeJsonField(response.result?.cursor);
  return (
    cursor &&
    typeof cursor === "object" &&
    !Array.isArray(cursor) &&
    String(cursor.broker_epoch) === match[1] &&
    String(cursor.sequence) === match[2]
  );
}

function frameFor(envelope, maxPayloadBytes) {
  let json;
  try {
    json = JSON.stringify(envelope);
  } catch (error) {
    throw protocolError(
      `could not encode local protocol envelope: ${error.message}`,
    );
  }
  const payload = Buffer.from(json, "utf8");
  if (payload.length > maxPayloadBytes) {
    throw transportError(
      "message_too_large",
      `local protocol payload exceeds ${maxPayloadBytes} bytes`,
      { length: payload.length, max: maxPayloadBytes },
    );
  }
  const frame = Buffer.allocUnsafe(4 + payload.length);
  frame.writeUInt32BE(payload.length, 0);
  payload.copy(frame, 4);
  return frame;
}

function responseRequestId(envelope) {
  return typeof envelope?.request_id === "string"
    ? envelope.request_id
    : undefined;
}

/**
 * Persistent Unix-domain local host transport using the Rust Envelope/frame
 * contract: one UTF-8 JSON envelope per four-byte big-endian length frame.
 */
export class LocalProtocolTransport {
  constructor({
    socketPath = process.env.AGENTYC_HOST_SOCKET,
    profile,
    principal = "principal_sdk",
    clientId = "client_sdk",
    clientName = "@agentyc/browser",
    clientVersion = "2.0.0",
    maxPayloadBytes = DEFAULT_MAX_PAYLOAD_BYTES,
  } = {}) {
    if (!socketPath) {
      throw new AgentycError({
        code: "native_host_unavailable",
        message:
          "no local host socket is configured; pass socketPath or AGENTYC_HOST_SOCKET",
        retryable: true,
        guidance: "retry",
        details: { profile: profile ?? "default" },
      });
    }
    this.socketPath = socketPath;
    this.profileBindingId = profile
      ? normalizeIdentity(profile, "profile_", "profile")
      : undefined;
    this.principalId = normalizeIdentity(principal, "principal_", "principal");
    this.clientId = normalizeIdentity(clientId, "client_", "clientId");
    this.clientName = clientName;
    this.clientVersion = clientVersion;
    this.maxPayloadBytes = normalizeMaxPayloadBytes(maxPayloadBytes);
    this.socket = undefined;
    this.connectPromise = undefined;
    this.helloPromise = undefined;
    this.connected = false;
    this.closed = false;
    // The client uses this marker to distinguish a pre-dispatch abort while
    // the local transport is still establishing a socket from a dispatched
    // mutation whose response was cancelled.
    this.dispatchAware = true;
    this.readBuffer = Buffer.alloc(0);
    this.pending = new Map();
    this.ignoredRequestIds = new Set();
    this.resumeWaiter = undefined;
    this.eventListeners = new Set();
    this.deliveredEventCursors = new Set();
    this.lastCursor = undefined;
    this.connectionNonce = undefined;
  }

  async connect() {
    this.closed = false;
    await this._ensureConnected();
  }

  async request(payload, { signal, onDispatch } = {}) {
    const requests = payload?.requests;
    if (!Array.isArray(requests) || requests.length === 0) {
      throw new AgentycError({
        code: "invalid_argument",
        message: "local protocol request requires a non-empty requests array",
        retryable: false,
        guidance: "none",
      });
    }
    const requestIds = requests.map((request) => request?.request_id);
    if (
      requestIds.some(
        (requestId) => typeof requestId !== "string" || requestId.length === 0,
      )
    ) {
      throw new AgentycError({
        code: "invalid_argument",
        message: "local protocol requests require request_id values",
        retryable: false,
        guidance: "none",
      });
    }
    if (new Set(requestIds).size !== requestIds.length) {
      throw new AgentycError({
        code: "invalid_argument",
        message: "local protocol request IDs must be unique",
        retryable: false,
        guidance: "none",
      });
    }
    if (signal?.aborted) throw cancelledTransportError();

    await this._ensureConnected();
    if (signal?.aborted) throw cancelledTransportError();

    let abort;
    let dispatched = false;
    const result = new Promise((resolve, reject) => {
      const batch = { requestIds, responses: new Map(), resolve, reject };
      for (const requestId of requestIds) this.pending.set(requestId, batch);
      abort = () => {
        void this.cancel(requestIds, signal.reason?.message);
      };
      signal?.addEventListener("abort", abort, { once: true });
      try {
        for (const request of requests) {
          if (!dispatched) {
            dispatched = true;
            onDispatch?.();
          }
          this._writeEnvelope({
            kind: "request",
            protocol: PROTOCOL_VERSION,
            request_id: request.request_id,
            method: request.method,
            params: coreParams(request.params),
            deadline_ms: request.deadline_ms,
            idempotency_key: request.idempotency_key,
          });
        }
      } catch (error) {
        this._removeBatch(batch, error);
      }
    });
    const cleanup = () => signal?.removeEventListener("abort", abort);
    result.then(cleanup, cleanup);
    return result;
  }

  async cancel(requestIds, reason = "request cancelled") {
    const ids = Array.isArray(requestIds) ? requestIds : [requestIds];
    const batches = new Set();
    for (const requestId of ids) {
      const batch = this.pending.get(requestId);
      if (batch) batches.add(batch);
    }
    for (const batch of batches) {
      for (const requestId of batch.requestIds) {
        this.pending.delete(requestId);
        this.ignoredRequestIds.add(requestId);
      }
      batch.reject(cancelledTransportError(reason, true));
    }
    if (!this.connected || batches.size === 0) return;
    const cancelIds = new Set();
    for (const batch of batches) {
      for (const requestId of batch.requestIds) cancelIds.add(requestId);
    }
    for (const requestId of cancelIds) {
      try {
        this._writeEnvelope({
          kind: "cancel",
          protocol: PROTOCOL_VERSION,
          request_id: requestId,
          reason,
        });
      } catch {
        // The request has already been classified as cancelled/unknown by the client.
      }
    }
  }

  async resume({ afterEpoch = 0, afterSequence = 0, signal } = {}) {
    if (signal?.aborted) throw cancelledTransportError();
    await this._ensureConnected();
    if (signal?.aborted) throw cancelledTransportError();
    if (this.resumeWaiter) {
      throw new AgentycError({
        code: "invalid_argument",
        message: "only one event resume request may be active",
        retryable: false,
        guidance: "none",
      });
    }
    const brokerEpoch = normalizeWatermark(afterEpoch, "afterEpoch");
    const sequence = normalizeWatermark(afterSequence, "afterSequence");
    let abort;
    const result = new Promise((resolve, reject) => {
      const waiter = {
        requestId: undefined,
        cancelled: false,
        resolve,
        reject,
      };
      this.resumeWaiter = waiter;
      abort = () => {
        if (this.resumeWaiter !== waiter) return;
        waiter.cancelled = true;
        reject(cancelledTransportError(signal.reason?.message));
      };
      signal?.addEventListener("abort", abort, { once: true });
      try {
        this._writeEnvelope({
          kind: "resume",
          protocol: PROTOCOL_VERSION,
          after: {
            broker_epoch: brokerEpoch,
            sequence,
          },
        });
      } catch (error) {
        this.resumeWaiter = undefined;
        signal?.removeEventListener("abort", abort);
        reject(error);
      }
    });
    let response;
    try {
      response = await result;
    } finally {
      signal?.removeEventListener("abort", abort);
    }
    if (!response.ok) throw mapWireError(response.error);
    const raw = response.result ?? {};
    const resumeResult = decodeJsonField(raw.resume_result);
    const cursor = decodeJsonField(raw.cursor);
    const events = decodeJsonField(raw.events);
    if (
      cursor &&
      typeof cursor === "object" &&
      !Array.isArray(cursor) &&
      cursor.broker_epoch !== undefined &&
      cursor.sequence !== undefined
    ) {
      this.lastCursor = {
        broker_epoch: cursor.broker_epoch,
        sequence: cursor.sequence,
      };
    }
    if (Array.isArray(events)) {
      for (const event of events) this._deliverEvent(event);
    }
    return {
      ...raw,
      resume_result: resumeResult,
      cursor,
      events,
    };
  }

  onEvent(listener) {
    if (typeof listener !== "function")
      throw new TypeError("event listener must be a function");
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  async subscribe(listener, options = {}) {
    const unsubscribe = this.onEvent(listener);
    try {
      if (
        options.afterEpoch !== undefined ||
        options.afterSequence !== undefined
      ) {
        await this.resume(options);
      }
      return unsubscribe;
    } catch (error) {
      unsubscribe();
      throw error;
    }
  }

  async reconnect() {
    await this.close();
    this.closed = false;
    await this._ensureConnected();
  }

  async close() {
    this.closed = true;
    const error = transportError(
      "native_host_unavailable",
      "local host transport closed",
    );
    this._failPending(error);
    if (this.resumeWaiter) {
      this.resumeWaiter.reject(error);
      this.resumeWaiter = undefined;
    }
    const socket = this.socket;
    this.socket = undefined;
    this.connected = false;
    this.connectPromise = undefined;
    this.helloPromise = undefined;
    this.readBuffer = Buffer.alloc(0);
    if (socket && !socket.destroyed) socket.destroy();
    this.ignoredRequestIds.clear();
  }

  async _ensureConnected() {
    if (this.closed)
      throw transportError(
        "native_host_unavailable",
        "local host transport is closed",
      );
    if (this.connected && this.socket && !this.socket.destroyed) return;
    if (this.connectPromise) return this.connectPromise;
    this.readBuffer = Buffer.alloc(0);

    this.connectPromise = new Promise((resolve, reject) => {
      const socket = net.createConnection({ path: this.socketPath });
      this.socket = socket;
      let settled = false;
      const fail = (error) => {
        const mapped =
          error instanceof AgentycError
            ? error
            : transportError(
                "native_host_unavailable",
                `local host socket unavailable: ${error.message}`,
              );
        if (!settled) {
          settled = true;
          reject(mapped);
        }
        this._failConnection(mapped, socket);
      };

      socket.on("data", (chunk) => this._feed(chunk));
      socket.once("connect", () => {
        connectionSequence = (connectionSequence + 1) % 1_000_000_000;
        this.connectionNonce = `nonce_sdk_${Date.now().toString(36)}_${connectionSequence.toString(36)}`;
        this.helloPromise = new Promise((resolveHello, rejectHello) => {
          this._helloResolve = resolveHello;
          this._helloReject = rejectHello;
        });
        try {
          const hello = {
            kind: "hello",
            protocol: PROTOCOL_VERSION,
            supported_protocols: [PROTOCOL_VERSION],
            principal_id: this.principalId,
            client_metadata: {
              client_id: this.clientId,
              client_name: this.clientName,
              client_version: this.clientVersion,
              connection_nonce: this.connectionNonce,
              ...(this.profileBindingId
                ? { profile_binding_id: this.profileBindingId }
                : {}),
            },
            ...(this.lastCursor ? { resume_from: this.lastCursor } : {}),
          };
          this._writeEnvelope(hello);
          this.helloPromise.then(() => {
            this.connected = true;
            if (!settled) {
              settled = true;
              resolve();
            }
          }, fail);
        } catch (error) {
          fail(error);
        }
      });
      socket.once("error", fail);
      socket.once("close", () => {
        if (!settled) {
          fail(
            transportError(
              "native_host_unavailable",
              "local host socket closed during handshake",
            ),
          );
        } else {
          this._failConnection(
            transportError(
              "native_host_unavailable",
              "local host socket closed",
            ),
            socket,
          );
        }
      });
    });

    try {
      await this.connectPromise;
    } finally {
      this.connectPromise = undefined;
    }
  }

  _writeEnvelope(envelope) {
    if (!this.socket || this.socket.destroyed) {
      throw transportError(
        "native_host_unavailable",
        "local host socket is not connected",
      );
    }
    this.socket.write(frameFor(envelope, this.maxPayloadBytes));
  }

  _feed(chunk) {
    if (!Buffer.isBuffer(chunk)) chunk = Buffer.from(chunk);
    this.readBuffer = Buffer.concat([this.readBuffer, chunk]);
    while (this.readBuffer.length >= 4) {
      const length = this.readBuffer.readUInt32BE(0);
      if (length > this.maxPayloadBytes) {
        this._failConnection(
          transportError(
            "message_too_large",
            "local host frame exceeds the configured payload bound",
          ),
          this.socket,
        );
        return;
      }
      const frameLength = 4 + length;
      if (this.readBuffer.length < frameLength) return;
      const payload = this.readBuffer.subarray(4, frameLength);
      this.readBuffer = this.readBuffer.subarray(frameLength);
      let text;
      let envelope;
      try {
        text = new TextDecoder("utf-8", { fatal: true }).decode(payload);
        envelope = JSON.parse(text);
      } catch (error) {
        this._failConnection(
          protocolError(`invalid local host frame: ${error.message}`),
          this.socket,
        );
        return;
      }
      this._handleEnvelope(envelope);
    }
  }

  _handleEnvelope(envelope) {
    if (!envelope || typeof envelope !== "object") {
      this._failConnection(
        protocolError("local host envelope must be an object"),
        this.socket,
      );
      return;
    }
    if (envelope.protocol !== PROTOCOL_VERSION) {
      this._failConnection(
        transportError(
          "protocol_mismatch",
          "local host protocol version is unsupported",
          {
            protocol: envelope.protocol,
          },
        ),
        this.socket,
      );
      return;
    }
    switch (envelope.kind) {
      case "hello_ok":
        if (!this.helloPromise || typeof this._helloResolve !== "function") {
          this._failConnection(
            protocolError("unexpected hello_ok envelope"),
            this.socket,
          );
          return;
        }
        const hostMetadata = envelope.host_metadata;
        if (
          !hostMetadata ||
          typeof hostMetadata.host_name !== "string" ||
          typeof hostMetadata.host_version !== "string" ||
          hostMetadata.host_name.length === 0 ||
          hostMetadata.host_name.length > 128 ||
          hostMetadata.host_version.length === 0 ||
          hostMetadata.host_version.length > 128 ||
          hostMetadata.connection_nonce !== this.connectionNonce
        ) {
          this._failConnection(
            protocolError("hello_ok must echo the client connection nonce"),
            this.socket,
          );
          return;
        }
        if (
          !Number.isSafeInteger(envelope.broker_epoch) ||
          !Number.isSafeInteger(envelope.connection_epoch) ||
          !Array.isArray(envelope.capabilities) ||
          !envelope.resume ||
          typeof envelope.resume.kind !== "string"
        ) {
          this._failConnection(
            protocolError("hello_ok is missing required host handshake fields"),
            this.socket,
          );
          return;
        }
        if (
          (hostMetadata.profile_binding_id ?? undefined) !==
          (this.profileBindingId ?? undefined)
        ) {
          this._failConnection(
            protocolError(
              "hello_ok profile binding does not match the request",
            ),
            this.socket,
          );
          return;
        }
        this._helloResolve(envelope);
        this._helloResolve = undefined;
        this._helloReject = undefined;
        return;
      case "response": {
        const requestId = responseRequestId(envelope);
        if (!requestId) {
          this._failConnection(
            protocolError("local host response is missing request_id"),
            this.socket,
          );
          return;
        }
        const batch = this.pending.get(requestId);
        if (batch) {
          if (batch.responses.has(requestId)) {
            this._failConnection(
              protocolError("local host returned a duplicate response", {
                request_id: requestId,
              }),
              this.socket,
            );
            return;
          }
          batch.responses.set(requestId, responseFromEnvelope(envelope));
          if (batch.responses.size === batch.requestIds.length) {
            for (const id of batch.requestIds) this.pending.delete(id);
            batch.resolve({
              responses: batch.requestIds.map((id) => batch.responses.get(id)),
            });
          }
          return;
        }
        if (this.resumeWaiter) {
          const response = responseFromEnvelope(envelope);
          if (!resumeResponseMatches(requestId, response)) {
            this._failConnection(
              protocolError("local host returned an invalid resume response", {
                request_id: requestId,
              }),
              this.socket,
            );
            return;
          }
          const waiter = this.resumeWaiter;
          this.resumeWaiter = undefined;
          if (!waiter.cancelled) waiter.resolve(response);
          return;
        }
        if (this.ignoredRequestIds.delete(requestId)) return;
        this._failConnection(
          protocolError(
            "local host returned a response for an unknown request",
            {
              request_id: requestId,
            },
          ),
          this.socket,
        );
        return;
      }
      case "event":
        this._deliverEvent(envelope);
        return;
      default:
        this._failConnection(
          protocolError(
            `unsupported local host envelope kind: ${String(envelope.kind)}`,
          ),
          this.socket,
        );
    }
  }

  _deliverEvent(event) {
    if (!event || typeof event !== "object") return;
    const hasCursor =
      event.broker_epoch !== undefined && event.sequence !== undefined;
    const cursorKey = hasCursor
      ? `${String(event.broker_epoch)}:${String(event.sequence)}`
      : undefined;
    if (cursorKey && this.deliveredEventCursors.has(cursorKey)) return;
    if (cursorKey) {
      this.deliveredEventCursors.add(cursorKey);
      if (this.deliveredEventCursors.size > 10_000) {
        const oldest = this.deliveredEventCursors.values().next().value;
        this.deliveredEventCursors.delete(oldest);
      }
      this.lastCursor = {
        broker_epoch: event.broker_epoch,
        sequence: event.sequence,
      };
    }
    for (const listener of this.eventListeners) {
      try {
        listener(event);
      } catch {
        // An observer cannot invalidate the host connection.
      }
    }
  }

  _removeBatch(batch, error) {
    for (const requestId of batch.requestIds) this.pending.delete(requestId);
    batch.reject(error);
  }

  _failPending(error) {
    const batches = new Set(this.pending.values());
    this.pending.clear();
    for (const batch of batches) batch.reject(error);
  }

  _failConnection(error, socket) {
    this.connected = false;
    if (this.socket === socket) this.socket = undefined;
    if (typeof this._helloReject === "function") this._helloReject(error);
    this._helloResolve = undefined;
    this._helloReject = undefined;
    if (this.resumeWaiter) {
      this.resumeWaiter.reject(error);
      this.resumeWaiter = undefined;
    }
    this._failPending(error);
    if (socket && !socket.destroyed) socket.destroy();
  }
}

/** Create the real framed local host transport. */
export function createLocalProtocolTransport(options = {}) {
  return new LocalProtocolTransport(options);
}

/** Adapt a generic in-process or local IPC handler to the SDK transport seam. */
export function createLocalTransport(handler) {
  if (typeof handler === "function") return { request: handler };
  if (handler && typeof handler.request === "function") return handler;
  if (handler && typeof handler.send === "function") {
    return {
      request: (payload, options) => handler.send(payload, options),
      reconnect:
        typeof handler.reconnect === "function"
          ? (...args) => handler.reconnect(...args)
          : undefined,
      close:
        typeof handler.close === "function"
          ? (...args) => handler.close(...args)
          : undefined,
      cancel:
        typeof handler.cancel === "function"
          ? (...args) => handler.cancel(...args)
          : undefined,
      resume:
        typeof handler.resume === "function"
          ? (...args) => handler.resume(...args)
          : undefined,
      subscribe:
        typeof handler.subscribe === "function"
          ? (...args) => handler.subscribe(...args)
          : undefined,
      onEvent:
        typeof handler.onEvent === "function"
          ? (...args) => handler.onEvent(...args)
          : undefined,
    };
  }
  throw new TypeError(
    "transport must provide request(payload) or send(payload)",
  );
}

export function makeRequest(method, params = {}, options = {}) {
  const deadlineMs = normalizeDeadline(options.deadlineMs);
  return {
    request_id: options.requestId ?? nextRequestId(),
    method,
    params,
    deadline_ms: deadlineMs,
    idempotency_key: options.idempotencyKey,
  };
}

/** The legacy injected transport batch shape remains supported for compatibility. */
export function makeBatch(requests) {
  return {
    protocol: PROTOCOL_VERSION,
    requests,
  };
}

export function requestId() {
  return nextRequestId();
}
