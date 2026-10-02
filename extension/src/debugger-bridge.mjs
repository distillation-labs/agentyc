import {
  ProtocolError,
  assertLogicalScope,
  isRestrictedUrl,
  redactBrowserIdentifiers,
} from "./protocol.mjs";

export const DEBUGGER_DOMAIN_ALLOWLIST = Object.freeze({
  Accessibility: Object.freeze([
    "disable",
    "enable",
    "getFullAXTree",
    "getPartialAXTree",
    "getRootAXNode",
  ]),
  DOM: Object.freeze([
    "collectClassNamesFromSubtree",
    "describeNode",
    "disable",
    "enable",
    "focus",
    "getAttributes",
    "getDocument",
    "getFlattenedDocument",
    "getNodeForLocation",
    "getOuterHTML",
    "getRelayoutBoundary",
    "getSearchResults",
    "performSearch",
    "pushNodeByPathToFrontend",
    "pushNodesByBackendIdsToFrontend",
    "querySelector",
    "querySelectorAll",
    "releaseNode",
    "requestChildNodes",
    "requestNode",
    "resolveNode",
    "setAttributeValue",
    "setAttributesAsText",
    "setFileInputFiles",
    "setOuterHTML",
  ]),
  DOMSnapshot: Object.freeze([
    "captureSnapshot",
    "disable",
    "enable",
    "getSnapshot",
  ]),
  Input: Object.freeze([
    "cancelDragging",
    "dispatchKeyEvent",
    "dispatchMouseEvent",
    "dispatchTouchEvent",
    "emulateTouchFromMouseEvent",
    "insertText",
    "setIgnoreInputEvents",
  ]),
  IO: Object.freeze(["close", "read", "resolveBlob"]),
  Log: Object.freeze([
    "clear",
    "disable",
    "enable",
    "startViolationsReport",
    "stopViolationsReport",
  ]),
  Network: Object.freeze([
    "clearBrowserCache",
    "clearBrowserCookies",
    "disable",
    "emulateNetworkConditions",
    "enable",
    "getRequestPostData",
    "getResponseBody",
    "setBlockedURLs",
    "setCacheDisabled",
    "setCookie",
    "setCookies",
  ]),
  Page: Object.freeze([
    "addScriptToEvaluateOnLoad",
    "addScriptToEvaluateOnNewDocument",
    "bringToFront",
    "captureScreenshot",
    "disable",
    "enable",
    "getFrameTree",
    "getLayoutMetrics",
    "getNavigationHistory",
    "getResourceTree",
    "handleJavaScriptDialog",
    "navigate",
    "navigateToHistoryEntry",
    "printToPDF",
    "reload",
    "removeScriptToEvaluateOnLoad",
    "removeScriptToEvaluateOnNewDocument",
    "stopLoading",
  ]),
  Runtime: Object.freeze([
    "addBinding",
    "awaitPromise",
    "callFunctionOn",
    "compileScript",
    "disable",
    "discardConsoleEntries",
    "enable",
    "getIsolateId",
    "getHeapUsage",
    "getProperties",
    "globalLexicalScopeNames",
    "queryObjects",
    "releaseObject",
    "releaseObjectGroup",
    "removeBinding",
    "evaluate",
    "runIfWaitingForDebugger",
    "runScript",
  ]),
  Target: Object.freeze([
    "activateTarget",
    "attachToTarget",
    "autoAttachRelated",
    "closeTarget",
    "detachFromTarget",
    "getTargetInfo",
    "getTargets",
    "setAutoAttach",
  ]),
});

const MUTATING_DEBUGGER_METHODS = new Set([
  "DOM.focus",
  "DOM.setAttributeValue",
  "DOM.setAttributesAsText",
  "DOM.setFileInputFiles",
  "DOM.setOuterHTML",
  "Input.cancelDragging",
  "Input.dispatchKeyEvent",
  "Input.dispatchMouseEvent",
  "Input.dispatchTouchEvent",
  "Input.emulateTouchFromMouseEvent",
  "Input.insertText",
  "Input.setIgnoreInputEvents",
  "Network.clearBrowserCache",
  "Network.clearBrowserCookies",
  "Network.emulateNetworkConditions",
  "Network.setBlockedURLs",
  "Network.setCacheDisabled",
  "Network.setCookie",
  "Network.setCookies",
  "Page.addScriptToEvaluateOnLoad",
  "Page.addScriptToEvaluateOnNewDocument",
  "Page.bringToFront",
  "Page.handleJavaScriptDialog",
  "Page.navigate",
  "Page.navigateToHistoryEntry",
  "Page.reload",
  "Page.removeScriptToEvaluateOnLoad",
  "Page.removeScriptToEvaluateOnNewDocument",
  "Page.stopLoading",
  "Runtime.addBinding",
  "Runtime.callFunctionOn",
  "Runtime.evaluate",
  "Runtime.compileScript",
  "Runtime.removeBinding",
  "Runtime.runIfWaitingForDebugger",
  "Runtime.runScript",
  "Target.activateTarget",
  "Target.closeTarget",
  "Target.detachFromTarget",
  "Target.setAutoAttach",
]);

function chromeApiOrGlobal(chromeApi) {
  return chromeApi ?? globalThis.chrome;
}

async function chromeCall(fn, ...args) {
  if (typeof fn !== "function")
    throw new ProtocolError(
      "capability_unavailable",
      "Chrome debugger API is unavailable",
    );
  return fn(...args);
}

function unknownDispatch(message, details = undefined) {
  const error = new ProtocolError("unknown_outcome", message, details);
  error.outcome = "unknown";
  error.retryable = false;
  return error;
}

export function isAllowedDebuggerCommand(method) {
  if (typeof method !== "string") return false;
  const separator = method.indexOf(".");
  if (separator < 1 || separator === method.length - 1) return false;
  const domain = method.slice(0, separator);
  const command = method.slice(separator + 1);
  return (
    Object.prototype.hasOwnProperty.call(DEBUGGER_DOMAIN_ALLOWLIST, domain) &&
    DEBUGGER_DOMAIN_ALLOWLIST[domain].includes(command)
  );
}

export function isMutatingDebuggerCommand(method) {
  return MUTATING_DEBUGGER_METHODS.has(method);
}

export function assertAllowedDebuggerCommand(method) {
  if (!isAllowedDebuggerCommand(method)) {
    throw new ProtocolError(
      "capability_unavailable",
      `debugger command is not allowlisted: ${method}`,
    );
  }
  return method;
}

export class DebuggerBridge {
  constructor({
    chromeApi,
    tabs,
    frames,
    onEvent = () => {},
    onStateChange = () => {},
  } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.tabs = tabs;
    this.frames = frames;
    this.onEvent = onEvent;
    this.onStateChange = onStateChange;
    this.attached = new Map();
    this.started = false;
    this.removeListeners = [];
  }

  start() {
    if (this.started) return;
    this.started = true;
    const onEvent = (source, method, params) =>
      this.handleEvent(source, method, params);
    const onDetach = (source, reason) => this.handleDetach(source, reason);
    this.chrome?.debugger?.onEvent?.addListener?.(onEvent);
    this.chrome?.debugger?.onDetach?.addListener?.(onDetach);
    this.removeListeners.push(() =>
      this.chrome?.debugger?.onEvent?.removeListener?.(onEvent),
    );
    this.removeListeners.push(() =>
      this.chrome?.debugger?.onDetach?.removeListener?.(onDetach),
    );
  }

  stop() {
    for (const remove of this.removeListeners.splice(0)) remove();
    this.started = false;
    this.attached.clear();
  }

  async attach({ spaceId, pageId, leaseEpoch } = {}) {
    const record = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      mutation: false,
    });
    if (isRestrictedUrl(record.url))
      throw new ProtocolError(
        "restricted_url",
        "page cannot accept debugger attachment",
      );
    if (this.attached.has(record.rawTabId))
      return this.publicAttachment(this.attached.get(record.rawTabId));
    try {
      await chromeCall(
        this.chrome?.debugger?.attach?.bind(this.chrome.debugger),
        { tabId: record.rawTabId },
        "1.3",
      );
    } catch (error) {
      throw new ProtocolError(
        "permission_denied",
        "Chrome rejected debugger attachment",
        {
          cause: error instanceof Error ? error.message : String(error),
        },
      );
    }
    const attachment = {
      rawTabId: record.rawTabId,
      spaceId,
      pageId,
      leaseEpoch,
      targetGeneration: record.generation,
      attachedAt: Date.now(),
    };
    this.attached.set(record.rawTabId, attachment);
    this.frames.bindTab({
      tabId: record.rawTabId,
      spaceId,
      pageId,
      targetGeneration: record.generation,
    });
    this.onStateChange("attached", this.publicAttachment(attachment));
    return this.publicAttachment(attachment);
  }

  async detach({
    spaceId,
    pageId,
    leaseEpoch,
    reason = "host_requested",
  } = {}) {
    const record = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      mutation: false,
    });
    const attachment = this.attached.get(record.rawTabId);
    if (!attachment) return { detached: false, reason: "not_attached" };
    try {
      await chromeCall(
        this.chrome?.debugger?.detach?.bind(this.chrome.debugger),
        { tabId: record.rawTabId },
      );
    } catch (error) {
      throw unknownDispatch("debugger detach result was lost", {
        cause: error instanceof Error ? error.message : String(error),
      });
    }
    this.attached.delete(record.rawTabId);
    this.frames.invalidateTab(record.rawTabId, reason);
    this.onStateChange("detached", {
      space_id: spaceId,
      page_id: pageId,
      reason,
    });
    return { detached: true };
  }

  async sendCommand({
    spaceId,
    pageId,
    leaseEpoch,
    method,
    params = {},
    expectedGeneration,
    commandId,
    capability,
    approval,
  } = {}) {
    assertAllowedDebuggerCommand(method);
    const mutation = isMutatingDebuggerCommand(method);
    if (method === "Page.navigate" && isRestrictedUrl(params?.url)) {
      throw new ProtocolError(
        "restricted_url",
        "navigation target is not debugger-accessible",
      );
    }
    const record = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      expectedGeneration,
      mutation,
    });
    if (method === "Runtime.evaluate" || method === "Runtime.callFunctionOn") {
      if (method === "Runtime.callFunctionOn" || capability !== "evaluate") {
        throw new ProtocolError(
          "evaluate_denied",
          "runtime evaluation requires an explicit capability",
        );
      }
      if (
        !approval ||
        approval.issued_by_host !== true ||
        typeof approval.script_hash !== "string"
      ) {
        throw new ProtocolError(
          "user_confirmation_required",
          "runtime evaluation requires host approval",
        );
      }
    }
    if (method === "Page.bringToFront" || method === "Target.activateTarget") {
      throw new ProtocolError(
        "user_control_required",
        "agent commands cannot steal focus",
      );
    }
    if (
      mutation &&
      capability === "evaluate" &&
      (!approval || approval.issued_by_host !== true)
    ) {
      throw new ProtocolError(
        "user_confirmation_required",
        "sensitive debugger mutation requires host approval",
      );
    }
    const attachment = this.attached.get(record.rawTabId);
    if (!attachment)
      throw new ProtocolError(
        "target_not_attached",
        "debugger target is not attached",
      );

    let dispatched = false;
    try {
      dispatched = true;
      const result = await chromeCall(
        this.chrome?.debugger?.sendCommand?.bind(this.chrome.debugger),
        { tabId: record.rawTabId },
        method,
        params,
      );
      const safeResult = redactBrowserIdentifiers(result ?? {});
      const serialized = JSON.stringify(safeResult);
      if (
        typeof serialized === "string" &&
        new TextEncoder().encode(serialized).byteLength > 1024 * 1024
      ) {
        throw new ProtocolError(
          "message_too_large",
          "debugger result exceeds the control bound",
        );
      }
      return {
        ok: true,
        command_id: commandId,
        method,
        result: safeResult,
        target_generation: attachment.targetGeneration,
      };
    } catch (error) {
      if (dispatched && mutation) {
        throw unknownDispatch("debugger mutation dispatch result was lost", {
          command_id: commandId,
          cause: error instanceof Error ? error.message : String(error),
        });
      }
      if (error instanceof ProtocolError) throw error;
      throw new ProtocolError(
        "debugger_command_failed",
        "debugger command failed",
        {
          cause: error instanceof Error ? error.message : String(error),
        },
      );
    }
  }

  handleEvent(source = {}, method, params = {}) {
    if (!Number.isInteger(source.tabId) || typeof method !== "string")
      return null;
    const routed = this.frames.routeDebuggerEvent({
      tabId: source.tabId,
      sessionId: source.sessionId,
      method,
      params,
    });
    if (!routed) return null;
    this.onEvent(routed);
    return routed;
  }

  handleDetach(source = {}, reason = "detached") {
    if (!Number.isInteger(source.tabId)) return null;
    const attachment = this.attached.get(source.tabId);
    if (source.sessionId) {
      this.frames.invalidateSession(source.tabId, source.sessionId, reason);
    } else {
      this.frames.invalidateTab(source.tabId, reason);
      this.attached.delete(source.tabId);
    }
    const event = {
      event: "debugger.detached",
      reason,
      ...(attachment
        ? { space_id: attachment.spaceId, page_id: attachment.pageId }
        : {}),
    };
    if (attachment) this.onEvent(event);
    this.onStateChange("detached", event);
    return event;
  }

  publicAttachment(attachment) {
    return {
      space_id: attachment.spaceId,
      page_id: attachment.pageId,
      target_generation: attachment.targetGeneration,
      attached: true,
    };
  }

  isAttached(pageId) {
    const record = this.tabs.getInternalByPage(pageId);
    return Boolean(record && this.attached.has(record.rawTabId));
  }
}

export function debuggerOperationError(error) {
  if (error instanceof ProtocolError)
    return {
      code: error.code,
      message: error.message,
      retryable: Boolean(error.retryable),
      outcome: error.outcome,
      details: error.details,
    };
  return {
    code: "debugger_command_failed",
    message: error instanceof Error ? error.message : String(error),
    retryable: false,
  };
}
