(() => {
  "use strict";

  // This file is deliberately self-contained and classic. MV3 content_scripts
  // are not extension modules, and this isolated-world bridge must never cross
  // into the page world or the Native Messaging transport.
  const VERSION = 1;
  const TTL_MS = 60 * 1000;
  const MAX_MESSAGE_BYTES = 64 * 1024;
  const MAX_REQUEST_ID_LENGTH = 128;
  const MAX_SELECTOR_LENGTH = 512;
  const MAX_TEXT_BYTES = 32 * 1024;
  const MAX_ATTRIBUTE_COUNT = 32;
  const MAX_ATTRIBUTE_NAME_BYTES = 128;
  const MAX_ATTRIBUTE_VALUE_BYTES = 512;
  const MAX_ARIA_NODES = 64;
  const MAX_ARIA_ROLE_BYTES = 128;
  const MAX_ARIA_NAME_BYTES = 512;
  const OPERATIONS = new Set([
    "document.title",
    "document.text",
    "aria.summary",
    "element.attributes",
    "element.actionability",
  ]);
  const ACTIONABILITY_RECT_KEYS = [
    "left",
    "top",
    "right",
    "bottom",
    "width",
    "height",
  ];

  const chromeApi = globalThis.chrome;
  const runtime = chromeApi?.runtime;
  const windowLike = globalThis.window;
  const documentLike = globalThis.document;

  const utf8Bytes = (value) => {
    try {
      return new TextEncoder().encode(value).byteLength;
    } catch {
      return Number.POSITIVE_INFINITY;
    }
  };

  const boundedText = (
    value,
    maxCharacters,
    maxBytes = Number.POSITIVE_INFINITY,
  ) => {
    if (typeof value !== "string") return "";
    let output = value.slice(0, maxCharacters);
    while (utf8Bytes(output) > maxBytes && output.length > 0)
      output = output.slice(0, -1);
    return output;
  };

  const validObject = (value) => {
    if (value === null || typeof value !== "object" || Array.isArray(value))
      return false;
    try {
      return Object.prototype.toString.call(value) === "[object Object]";
    } catch {
      return false;
    }
  };

  const requestIdIsBounded = (value) =>
    typeof value === "string" &&
    /^[A-Za-z0-9._:-]{8,128}$/.test(value) &&
    utf8Bytes(value) <= MAX_REQUEST_ID_LENGTH;

  const selectorIsBounded = (value) =>
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= MAX_SELECTOR_LENGTH &&
    utf8Bytes(value) <= MAX_SELECTOR_LENGTH * 4;

  const messageIsBounded = (value) => {
    try {
      const serialized = JSON.stringify(value);
      return (
        typeof serialized === "string" &&
        utf8Bytes(serialized) <= MAX_MESSAGE_BYTES
      );
    } catch {
      return false;
    }
  };

  const randomToken = (prefix) => {
    let randomValue;
    try {
      randomValue = globalThis.crypto?.randomUUID?.();
    } catch {
      randomValue = undefined;
    }
    if (typeof randomValue !== "string" || randomValue.length === 0)
      randomValue = `${Date.now()}_${Math.random()}`;
    return `${prefix}_${randomValue.replace(/[^A-Za-z0-9]/g, "")}`.slice(0, 96);
  };

  const nonce = randomToken("nonce");
  const documentId = randomToken("document");

  const sameExtensionSender = (sender) =>
    Boolean(
      typeof runtime?.id === "string" &&
      runtime.id.length > 0 &&
      sender?.id === runtime.id,
    );

  const fail = (code, message) => {
    const error = new Error(message);
    error.code = code;
    throw error;
  };

  const payloadIsValid = (operation, payload) => {
    if (!validObject(payload)) return false;
    let keys;
    try {
      keys = Object.keys(payload);
    } catch {
      return false;
    }
    if (keys.length === 0)
      return (
        operation !== "element.attributes" &&
        operation !== "element.actionability"
      );
    if (keys.length !== 1 || keys[0] !== "selector") return false;
    return (
      (operation === "document.text" ||
        operation === "element.attributes" ||
        operation === "element.actionability") &&
      selectorIsBounded(payload.selector)
    );
  };

  const validRequest = (message) => {
    try {
      if (!validObject(message)) return false;
      const now = Date.now();
      return (
        message.type === "agentyc.content.request" &&
        message.version === VERSION &&
        message.nonce === nonce &&
        message.document_id === documentId &&
        requestIdIsBounded(message.request_id) &&
        OPERATIONS.has(message.operation) &&
        Number.isSafeInteger(message.expires_at) &&
        message.expires_at > now &&
        message.expires_at <= now + TTL_MS &&
        payloadIsValid(message.operation, message.payload) &&
        messageIsBounded(message)
      );
    } catch {
      return false;
    }
  };

  const responseExpiry = (value) => {
    const now = Date.now();
    return Number.isSafeInteger(value) && value > now && value <= now + TTL_MS
      ? value
      : now + 1000;
  };

  const sendResult = (requestId, operation, ok, value, expiresAt) => {
    if (!requestIdIsBounded(requestId)) return;
    const message = {
      type: "agentyc.content.result",
      version: VERSION,
      request_id: requestId,
      nonce,
      document_id: documentId,
      operation: OPERATIONS.has(operation) ? operation : "document.title",
      expires_at: responseExpiry(expiresAt),
      ok,
      ...(ok ? { result: validObject(value) ? value : {} } : { error: value }),
    };
    if (!messageIsBounded(message)) return;
    try {
      const pending = runtime?.sendMessage?.(message);
      pending?.catch?.(() => {});
    } catch {
      // The service worker can stop between dispatch and delivery.
    }
  };

  const resolveElement = (payload = {}) => {
    if (
      !documentLike?.querySelector ||
      !validObject(payload) ||
      !selectorIsBounded(payload.selector)
    )
      fail("schema_invalid", "typed DOM operation requires a bounded selector");
    let element;
    try {
      element = documentLike.querySelector(payload.selector);
    } catch {
      fail("schema_invalid", "selector is invalid");
    }
    if (!element) fail("element_not_found", "element was not found");
    return element;
  };

  const readRect = (element) => {
    try {
      const rect = element?.getBoundingClientRect?.();
      if (
        !rect ||
        !ACTIONABILITY_RECT_KEYS.every((key) => Number.isFinite(rect[key]))
      )
        return undefined;
      return Object.fromEntries(
        ACTIONABILITY_RECT_KEYS.map((key) => [key, rect[key]]),
      );
    } catch {
      return undefined;
    }
  };

  const sameRect = (first, second) =>
    Boolean(
      first &&
      second &&
      ACTIONABILITY_RECT_KEYS.every((key) => first[key] === second[key]),
    );

  const viewport = () => {
    const view = documentLike?.defaultView ?? windowLike;
    const width = Number.isFinite(view?.innerWidth)
      ? view.innerWidth
      : documentLike?.documentElement?.clientWidth;
    const height = Number.isFinite(view?.innerHeight)
      ? view.innerHeight
      : documentLike?.documentElement?.clientHeight;
    return Number.isFinite(width) &&
      Number.isFinite(height) &&
      width > 0 &&
      height > 0
      ? { width, height }
      : undefined;
  };

  const computedStyle = (element) => {
    try {
      const view = documentLike?.defaultView ?? windowLike;
      const getComputedStyle = view?.getComputedStyle;
      if (typeof getComputedStyle !== "function") return undefined;
      const style = getComputedStyle.call(view, element);
      if (
        !style ||
        typeof style.display !== "string" ||
        typeof style.visibility !== "string" ||
        !Number.isFinite(Number(style.opacity))
      )
        return undefined;
      return style;
    } catch {
      return undefined;
    }
  };

  const attributeValue = (element, name) => {
    try {
      return typeof element?.getAttribute === "function"
        ? element.getAttribute(name)
        : undefined;
    } catch {
      return undefined;
    }
  };

  const stateIsDisabled = (element) => {
    try {
      if (element.disabled === true) return true;
      if (attributeValue(element, "disabled") !== undefined) {
        if (attributeValue(element, "disabled") !== null) return true;
        return attributeValue(element, "aria-disabled") === "true";
      }
      return typeof element.disabled === "boolean" ? element.disabled : true;
    } catch {
      return true;
    }
  };

  const stateIsReadonly = (element) => {
    try {
      if (element.readOnly === true) return true;
      if (attributeValue(element, "readonly") !== undefined) {
        if (attributeValue(element, "readonly") !== null) return true;
        return attributeValue(element, "aria-readonly") === "true";
      }
      return typeof element.readOnly === "boolean" ? element.readOnly : true;
    } catch {
      return true;
    }
  };

  const elementActionability = (payload) => {
    const element = resolveElement(payload);
    let connected = false;
    try {
      connected =
        element.isConnected === true &&
        (!("ownerDocument" in element) ||
          element.ownerDocument === documentLike);
    } catch {
      connected = false;
    }

    const firstRect = readRect(element);
    const secondRect = readRect(element);
    const stableLayout = sameRect(firstRect, secondRect);
    const rect = firstRect;
    const view = viewport();
    const style = computedStyle(element);
    const layoutKnown = Boolean(connected && rect && view && style);
    const visible = Boolean(
      layoutKnown &&
      rect.width > 0 &&
      rect.height > 0 &&
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.visibility !== "collapse" &&
      Number(style.opacity) > 0,
    );
    const offscreen = Boolean(
      !layoutKnown ||
      rect.right <= 0 ||
      rect.bottom <= 0 ||
      rect.left >= view.width ||
      rect.top >= view.height,
    );

    let hitTarget = false;
    if (
      !offscreen &&
      rect &&
      view &&
      typeof documentLike?.elementFromPoint === "function"
    ) {
      const x = Math.min(
        Math.max(rect.left + rect.width / 2, 0),
        view.width - 0.001,
      );
      const y = Math.min(
        Math.max(rect.top + rect.height / 2, 0),
        view.height - 0.001,
      );
      try {
        const hit = documentLike.elementFromPoint(x, y);
        hitTarget =
          hit === element ||
          (hit !== null &&
            typeof element.contains === "function" &&
            element.contains(hit) === true);
      } catch {
        hitTarget = false;
      }
    }

    return {
      connected,
      visible,
      disabled: stateIsDisabled(element),
      readonly: stateIsReadonly(element),
      covered: !hitTarget,
      overlay_present: !hitTarget,
      hit_target: hitTarget,
      moving: !stableLayout,
      offscreen,
      // User ownership is decided by the service worker/host lease. The DOM
      // probe never grants ownership and cannot override a user-control fence.
      user_control: false,
    };
  };

  const executeOperation = (operation, payload = {}) => {
    switch (operation) {
      case "document.title":
        return { title: boundedText(documentLike?.title, 512, 2048) };
      case "document.text": {
        const element = payload.selector
          ? resolveElement(payload)
          : (documentLike?.body ?? documentLike?.documentElement);
        return {
          text: boundedText(
            element?.innerText || element?.textContent,
            16 * 1024,
            MAX_TEXT_BYTES,
          ),
        };
      }
      case "aria.summary": {
        const root = documentLike?.body ?? documentLike?.documentElement;
        if (!root?.querySelectorAll) return { nodes: [] };
        const nodes = [
          ...root.querySelectorAll(
            "[role],button,a,input,select,textarea,[aria-label]",
          ),
        ].slice(0, MAX_ARIA_NODES);
        return {
          nodes: nodes.map((node) => ({
            role: boundedText(
              node.getAttribute?.("role") || node.tagName?.toLowerCase(),
              64,
              MAX_ARIA_ROLE_BYTES,
            ),
            name: boundedText(
              node.getAttribute?.("aria-label") ||
                node.innerText ||
                node.textContent,
              256,
              MAX_ARIA_NAME_BYTES,
            ),
            disabled: Boolean(
              node.disabled || node.getAttribute?.("aria-disabled") === "true",
            ),
          })),
        };
      }
      case "element.attributes": {
        const element = resolveElement(payload);
        const attributes = Object.create(null);
        for (const attribute of [...(element.attributes ?? [])].slice(
          0,
          MAX_ATTRIBUTE_COUNT,
        )) {
          const name = boundedText(
            attribute.name,
            64,
            MAX_ATTRIBUTE_NAME_BYTES,
          );
          if (name.length === 0) continue;
          attributes[name] = boundedText(
            attribute.value,
            256,
            MAX_ATTRIBUTE_VALUE_BYTES,
          );
        }
        return { attributes };
      }
      case "element.actionability":
        return elementActionability(payload);
      default:
        fail("capability_unavailable", "content operation is not allowlisted");
    }
  };

  const runtimeListener = (message, sender) => {
    if (!sameExtensionSender(sender)) return undefined;
    if (!validObject(message) || message.type !== "agentyc.content.request")
      return undefined;

    if (!validRequest(message)) {
      if (requestIdIsBounded(message.request_id))
        sendResult(
          message.request_id,
          message.operation,
          false,
          { code: "schema_invalid", message: "content request is invalid" },
          Date.now() + 1000,
        );
      return { ok: false };
    }

    try {
      const result = executeOperation(message.operation, message.payload);
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
          message: boundedText(
            error instanceof Error ? error.message : String(error),
            256,
            1024,
          ),
        },
        message.expires_at,
      );
      return { ok: false };
    }
  };

  const cleanup = () => {
    try {
      const pending = runtime?.sendMessage?.({
        type: "agentyc.content.closed",
        version: VERSION,
        nonce,
        document_id: documentId,
      });
      pending?.catch?.(() => {});
    } catch {
      // Page teardown is best effort.
    }
  };

  windowLike?.addEventListener?.("pagehide", cleanup, { once: true });
  runtime?.onMessage?.addListener?.(runtimeListener);

  try {
    const pending = runtime?.sendMessage?.({
      type: "agentyc.content.ready",
      version: VERSION,
      nonce,
      document_id: documentId,
      expires_at: Date.now() + TTL_MS,
    });
    pending?.catch?.(() => {});
  } catch {
    // Registration is retried by the next document; no page authority is used.
  }
})();
