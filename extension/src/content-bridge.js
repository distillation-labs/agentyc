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

  const random = () => {
    const uuid = globalThis.crypto?.randomUUID?.();
    return `nonce_${(uuid || `${Math.random()}-${Date.now()}`).replaceAll("-", "")}`.slice(
      0,
      96,
    );
  };
  const nonce = random();
  const documentId = `document_${random().slice(7)}`;

  const bounded = (value, max) =>
    typeof value === "string" ? value.slice(0, max) : "";
  const bytes = (value) => new TextEncoder().encode(value).byteLength;
  const validObject = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const sameExtensionSender = (sender) =>
    Boolean(
      globalThis.chrome?.runtime?.id &&
      sender?.id === globalThis.chrome.runtime.id,
    );

  const withinMessageBound = (value) => {
    try {
      return bytes(JSON.stringify(value)) <= MAX_MESSAGE_BYTES;
    } catch {
      return false;
    }
  };

  const fail = (code, message) => {
    const error = new Error(message);
    error.code = code;
    throw error;
  };

  const resolveElement = (payload = {}) => {
    if (
      !globalThis.document?.querySelector ||
      typeof payload.selector !== "string" ||
      payload.selector.length === 0 ||
      payload.selector.length > 512
    ) {
      fail("schema_invalid", "typed DOM operation requires a bounded selector");
    }
    let element;
    try {
      element = globalThis.document.querySelector(payload.selector);
    } catch {
      fail("schema_invalid", "selector is invalid");
    }
    if (!element) fail("element_not_found", "element was not found");
    return element;
  };

  const executeOperation = (operation, payload = {}) => {
    switch (operation) {
      case "document.title":
        return { title: bounded(globalThis.document?.title, 512) };
      case "document.text": {
        const element = payload.selector
          ? resolveElement(payload)
          : globalThis.document?.body || globalThis.document?.documentElement;
        return {
          text: bounded(element?.innerText || element?.textContent, 16_384),
        };
      }
      case "aria.summary": {
        const root =
          globalThis.document?.body || globalThis.document?.documentElement;
        const nodes = root?.querySelectorAll
          ? [
              ...root.querySelectorAll(
                "[role],button,a,input,select,textarea,[aria-label]",
              ),
            ].slice(0, 128)
          : [];
        return {
          nodes: nodes.map((node) => ({
            role: bounded(
              node.getAttribute?.("role") || node.tagName?.toLowerCase(),
              64,
            ),
            name: bounded(
              node.getAttribute?.("aria-label") ||
                node.innerText ||
                node.textContent,
              256,
            ),
            disabled: Boolean(
              node.disabled || node.getAttribute?.("aria-disabled") === "true",
            ),
          })),
        };
      }
      case "element.attributes": {
        const element = resolveElement(payload);
        const attributes = {};
        for (const attribute of [...(element.attributes || [])].slice(0, 64)) {
          attributes[bounded(attribute.name, 128)] = bounded(
            attribute.value,
            1024,
          );
        }
        return { attributes };
      }
      default:
        fail("capability_unavailable", "content operation is not allowlisted");
    }
  };

  const sendResult = (requestId, operation, ok, value, expiresAt) => {
    const message = {
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
    if (!withinMessageBound(message)) return;
    void globalThis.chrome?.runtime?.sendMessage?.(message);
  };

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
    ) {
      return false;
    }
    return withinMessageBound(message);
  };

  const runtimeListener = (message, sender) => {
    if (!sameExtensionSender(sender)) return undefined;
    if (!message || typeof message !== "object") return undefined;
    if (message.type !== "agentyc.content.request") return undefined;

    if (!validRequest(message)) {
      if (typeof message.request_id === "string") {
        sendResult(
          message.request_id,
          OPERATIONS.has(message.operation)
            ? message.operation
            : "document.title",
          false,
          { code: "schema_invalid", message: "content request is invalid" },
          Date.now() + 1000,
        );
      }
      return { ok: false };
    }

    try {
      const result = executeOperation(message.operation, message.payload || {});
      sendResult(
        message.request_id,
        message.operation,
        true,
        result,
        message.expires_at,
      );
      return { ok: true };
    } catch (error) {
      sendResult(
        message.request_id,
        message.operation,
        false,
        {
          code:
            typeof error?.code === "string"
              ? error.code
              : "content_bridge_error",
          message: error instanceof Error ? error.message : String(error),
        },
        message.expires_at,
      );
      return { ok: false };
    }
  };

  const cleanup = () => {
    void globalThis.chrome?.runtime?.sendMessage?.({
      type: "agentyc.content.closed",
      version: VERSION,
      nonce,
      document_id: documentId,
    });
  };

  globalThis.window?.addEventListener?.("pagehide", cleanup, { once: true });
  globalThis.chrome?.runtime?.onMessage?.addListener?.(runtimeListener);
  void globalThis.chrome?.runtime?.sendMessage?.({
    type: "agentyc.content.ready",
    version: VERSION,
    nonce,
    document_id: documentId,
    expires_at: Date.now() + TTL_MS,
  });
})();
