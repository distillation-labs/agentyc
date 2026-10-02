const PROTOCOL_VERSION = 1;
let requestSequence = 0;

function nextRequestId() {
  requestSequence = (requestSequence + 1) % 1_000_000_000;
  return `req_sdk_${Date.now().toString(36)}_${requestSequence.toString(36)}`;
}

/**
 * Adapt a generic in-process or local IPC handler to the SDK transport seam.
 * The handler receives one bounded batch and returns a response batch.
 */
export function createLocalTransport(handler) {
  if (typeof handler === "function") return { request: handler };
  if (handler && typeof handler.request === "function") return handler;
  if (handler && typeof handler.send === "function") {
    return {
      request: (payload) => handler.send(payload),
      reconnect: typeof handler.reconnect === "function" ? () => handler.reconnect() : undefined,
      close: typeof handler.close === "function" ? () => handler.close() : undefined,
    };
  }
  throw new TypeError("transport must provide request(payload) or send(payload)");
}

export function makeRequest(method, params = {}, options = {}) {
  return {
    request_id: options.requestId ?? nextRequestId(),
    method,
    params,
    deadline_ms: options.deadlineMs,
    idempotency_key: options.idempotencyKey,
  };
}

export function makeBatch(requests) {
  return {
    protocol: PROTOCOL_VERSION,
    requests,
  };
}

export function requestId() {
  return nextRequestId();
}
