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
  let state;

  const validObject = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const bounded = (value, max) =>
    typeof value === "string" ? value.slice(0, max) : "";
  const validMessage = (message) => {
    if (!validObject(message)) return false;
    if (
      message.type !== "agentyc.page.message" ||
      message.version !== VERSION ||
      message.direction !== "extension_to_page" ||
      !state ||
      state.expiresAt <= Date.now() ||
      message.nonce !== state.nonce ||
      message.document_id !== state.documentId ||
      message.origin !== origin ||
      !OPERATIONS.has(message.operation) ||
      typeof message.request_id !== "string" ||
      message.request_id.length < 8 ||
      message.request_id.length > 128 ||
      !Number.isSafeInteger(message.expires_at) ||
      message.expires_at <= Date.now() ||
      message.expires_at > Date.now() + TTL_MS ||
      !validObject(message.payload || {})
    )
      return false;
    try {
      return (
        new TextEncoder().encode(JSON.stringify(message)).byteLength <=
        MAX_MESSAGE_BYTES
      );
    } catch {
      return false;
    }
  };
  const send = (message) => {
    try {
      if (
        new TextEncoder().encode(JSON.stringify(message)).byteLength >
        MAX_MESSAGE_BYTES
      )
        return;
      globalThis.window?.postMessage?.(message, origin || "*");
    } catch {
      // Untrusted page data is bounded and dropped on serialization failure.
    }
  };
  const resolveElement = (payload) => {
    if (
      !globalThis.document?.querySelector ||
      typeof payload?.selector !== "string" ||
      payload.selector.length > 512
    )
      throw new Error("typed DOM operation requires a bounded selector");
    const element = globalThis.document.querySelector(payload.selector);
    if (!element) throw new Error("element was not found");
    return element;
  };
  const operation = (name, payload) => {
    switch (name) {
      case "document.title":
        return { title: bounded(globalThis.document?.title, 512) };
      case "document.text": {
        const element = payload?.selector
          ? resolveElement(payload)
          : globalThis.document?.body;
        return {
          text: bounded(element?.innerText || element?.textContent, 16384),
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
            ].slice(0, 256)
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
        for (const attribute of [...(element.attributes || [])].slice(0, 64))
          attributes[bounded(attribute.name, 128)] = bounded(
            attribute.value,
            1024,
          );
        return { attributes };
      }
      default:
        throw new Error("page operation is not allowlisted");
    }
  };
  const initListener = (event) => {
    if (
      event.source !== globalThis.window ||
      (origin && event.origin !== origin)
    )
      return;
    const message = event.data;
    if (
      !validObject(message) ||
      message.type !== "agentyc.page.init" ||
      message.version !== VERSION ||
      message.origin !== origin ||
      typeof message.nonce !== "string" ||
      message.nonce.length < 8 ||
      message.nonce.length > 128 ||
      typeof message.document_id !== "string" ||
      message.document_id.length < 8 ||
      message.document_id.length > 128 ||
      !Number.isSafeInteger(message.expires_at) ||
      message.expires_at <= Date.now() ||
      message.expires_at > Date.now() + TTL_MS
    )
      return;
    state = {
      nonce: message.nonce,
      documentId: message.document_id,
      expiresAt: message.expires_at,
    };
    send({
      type: "agentyc.page.ready",
      version: VERSION,
      origin,
    });
  };
  const messageListener = (event) => {
    if (
      event.source !== globalThis.window ||
      (origin && event.origin !== origin)
    )
      return;
    const message = event.data;
    if (message?.type === "agentyc.page.cleanup") {
      if (
        state &&
        message.nonce === state.nonce &&
        message.document_id === state.documentId
      )
        state = undefined;
      return;
    }
    if (!validMessage(message)) return;
    try {
      const result = operation(message.operation, message.payload || {});
      send({
        type: "agentyc.page.message",
        version: VERSION,
        nonce: state.nonce,
        document_id: state.documentId,
        direction: "page_to_extension",
        request_id: message.request_id,
        operation: message.operation,
        payload: { ok: true, result },
        origin,
        expires_at: message.expires_at,
      });
    } catch (error) {
      send({
        type: "agentyc.page.message",
        version: VERSION,
        nonce: state.nonce,
        document_id: state.documentId,
        direction: "page_to_extension",
        request_id: message.request_id,
        operation: message.operation,
        payload: {
          ok: false,
          error: {
            code: "page_bridge_error",
            message: error instanceof Error ? error.message : String(error),
          },
        },
        origin,
        expires_at: message.expires_at,
      });
    }
  };
  const cleanup = () => {
    state = undefined;
  };
  globalThis.window?.addEventListener("message", initListener);
  globalThis.window?.addEventListener("message", messageListener);
  globalThis.window?.addEventListener("pagehide", cleanup, { once: true });
})();
