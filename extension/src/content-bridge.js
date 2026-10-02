(() => {
  "use strict";

  const VERSION = 1;
  const TTL_MS = 60 * 1000;
  const MAX_MESSAGE_BYTES = 64 * 1024;
  const OPERATIONS = new Set([
    "document.title",
    "document.text",
    "aria.summary",
    "element.attributes",
  ]);
  const origin = globalThis.location?.origin || "";
  const random = () => {
    const uuid = globalThis.crypto?.randomUUID?.();
    return `nonce_${(uuid || `${Math.random()}-${Date.now()}`).replaceAll("-", "")}`.slice(0, 96);
  };
  const nonce = random();
  const documentId = `document_${random().slice(7)}`;
  const pending = new Map();

  const bounded = (value, max) =>
    typeof value === "string" ? value.slice(0, max) : "";
  const bytes = (value) => new TextEncoder().encode(value).byteLength;
  const validObject = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const validRequest = (message) => {
    if (!validObject(message)) return false;
    if (
      message.type !== "agentyc.content.request" ||
      message.version !== VERSION ||
      message.nonce !== nonce ||
      message.document_id !== documentId ||
      typeof message.request_id !== "string" ||
      message.request_id.length < 8 ||
      message.request_id.length > 128 ||
      !OPERATIONS.has(message.operation) ||
      !Number.isSafeInteger(message.expires_at) ||
      message.expires_at <= Date.now() ||
      message.expires_at > Date.now() + TTL_MS ||
      !validObject(message.payload || {})
    )
      return false;
    try {
      return bytes(JSON.stringify(message)) <= MAX_MESSAGE_BYTES;
    } catch {
      return false;
    }
  };
  const pageMessage = (direction, operation, requestId, payload, expiresAt) => ({
    type: "agentyc.page.message",
    version: VERSION,
    nonce,
    document_id: documentId,
    direction,
    request_id: requestId,
    operation,
    payload,
    origin,
    expires_at: expiresAt,
  });
  const sendResult = (requestId, operation, ok, value, expiresAt) => {
    const result = {
      type: "agentyc.content.result",
      version: VERSION,
      request_id: requestId,
      nonce,
      document_id: documentId,
      operation,
      expires_at: expiresAt,
      ok,
      ...(ok ? { result: value || {} } : { error: value }),
    };
    try {
      if (bytes(JSON.stringify(result)) > MAX_MESSAGE_BYTES) return;
    } catch {
      return;
    }
    void globalThis.chrome?.runtime?.sendMessage?.(result);
  };
  const sendInit = () => {
    globalThis.window?.postMessage?.(
      {
        type: "agentyc.page.init",
        version: VERSION,
        nonce,
        document_id: documentId,
        origin,
        expires_at: Date.now() + TTL_MS,
      },
      origin || "*",
    );
  };

  const pageListener = (event) => {
    if (event.source !== globalThis.window || (origin && event.origin !== origin)) return;
    const message = event.data;
    if (!validObject(message) || message.type !== "agentyc.page.ready") return;
    if (message.version !== VERSION || message.origin !== origin) return;
    sendInit();
  };
  const resultListener = (event) => {
    if (event.source !== globalThis.window || (origin && event.origin !== origin)) return;
    const message = event.data;
    if (
      !validObject(message) ||
      message.type !== "agentyc.page.message" ||
      message.version !== VERSION ||
      message.direction !== "page_to_extension" ||
      message.nonce !== nonce ||
      message.document_id !== documentId ||
      message.origin !== origin ||
      !OPERATIONS.has(message.operation) ||
      typeof message.request_id !== "string" ||
      !Number.isSafeInteger(message.expires_at) ||
      message.expires_at <= Date.now()
    )
      return;
    const entry = pending.get(message.request_id);
    if (!entry || entry.operation !== message.operation || entry.expiresAt < Date.now()) {
      pending.delete(message.request_id);
      return;
    }
    pending.delete(message.request_id);
    const payload = validObject(message.payload || {}) ? message.payload : {};
    sendResult(
      message.request_id,
      message.operation,
      payload.ok === true,
      payload.ok === true
        ? payload.result || {}
        : payload.error || { code: "page_bridge_error", message: "page operation failed" },
      entry.expiresAt,
    );
  };
  const runtimeListener = (message, sender) => {
    if (sender?.id !== globalThis.chrome?.runtime?.id) return undefined;
    if (!validRequest(message)) {
      if (message?.request_id) {
        sendResult(
          message.request_id,
          OPERATIONS.has(message.operation) ? message.operation : "document.title",
          false,
          { code: "schema_invalid", message: "content request is invalid" },
          Date.now() + 1000,
        );
      }
      return { ok: false };
    }
    if (pending.size >= 128) {
      sendResult(
        message.request_id,
        message.operation,
        false,
        { code: "resource_exhausted", message: "content bridge request bound reached" },
        message.expires_at,
      );
      return { ok: false };
    }
    pending.set(message.request_id, {
      operation: message.operation,
      expiresAt: message.expires_at,
    });
    globalThis.window?.postMessage?.(
      pageMessage(
        "extension_to_page",
        message.operation,
        message.request_id,
        message.payload || {},
        message.expires_at,
      ),
      origin || "*",
    );
    return { ok: true };
  };
  const cleanup = () => {
    pending.clear();
    void globalThis.chrome?.runtime?.sendMessage?.({
      type: "agentyc.content.closed",
      version: VERSION,
      nonce,
      document_id: documentId,
    });
  };

  globalThis.window?.addEventListener("message", pageListener);
  globalThis.window?.addEventListener("message", resultListener);
  globalThis.window?.addEventListener("pagehide", cleanup, { once: true });
  globalThis.chrome?.runtime?.onMessage?.addListener?.(runtimeListener);
  void globalThis.chrome?.runtime?.sendMessage?.({
    type: "agentyc.content.ready",
    version: VERSION,
    nonce,
    document_id: documentId,
    expires_at: Date.now() + TTL_MS,
  });
  sendInit();
})();
