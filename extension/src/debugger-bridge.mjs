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
    "disable",
    "enable",
    "getRequestPostData",
    "getResponseBody",
  ]),
  Page: Object.freeze([
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
    "stopLoading",
  ]),
  Runtime: Object.freeze([
    "disable",
    "enable",
    "getHeapUsage",
    "globalLexicalScopeNames",
    "evaluate",
  ]),
});

const MUTATING_DEBUGGER_METHODS = new Set([
  "DOM.focus",
  "Input.cancelDragging",
  "Input.dispatchKeyEvent",
  "Input.dispatchMouseEvent",
  "Input.dispatchTouchEvent",
  "Input.emulateTouchFromMouseEvent",
  "Input.insertText",
  "Input.setIgnoreInputEvents",
  "Page.handleJavaScriptDialog",
  "Page.navigate",
  "Page.navigateToHistoryEntry",
  "Page.reload",
  "Page.stopLoading",
  "Runtime.evaluate",
]);

const RUNTIME_EVALUATE_PARAMETER_ALLOWLIST = new Set([
  "expression",
  "script",
  "awaitPromise",
  "returnByValue",
  "userGesture",
  "silent",
  "throwOnSideEffect",
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

const MAX_APPROVAL_LIFETIME_MS = 15 * 60 * 1000;
const MAX_USED_APPROVALS = 1024;

export async function hashRuntimeScript(script) {
  if (typeof script !== "string" || script.length === 0)
    throw new ProtocolError(
      "schema_invalid",
      "runtime evaluation script is required",
    );
  if (!globalThis.crypto?.subtle)
    throw new ProtocolError(
      "capability_unavailable",
      "script hashing is unavailable",
    );
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(script),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
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
    now = () => Date.now(),
    profileInstanceId,
    browserSessionEpoch,
  } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.tabs = tabs;
    this.frames = frames;
    this.onEvent = onEvent;
    this.onStateChange = onStateChange;
    this.now = now;
    this.profileInstanceId = profileInstanceId;
    this.browserSessionEpoch = browserSessionEpoch;
    this.attached = new Map();
    this.usedEvaluationApprovals = new Map();
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
    this.usedEvaluationApprovals.clear();
  }

  setIdentity({ profileInstanceId, browserSessionEpoch } = {}) {
    if (profileInstanceId !== undefined)
      this.profileInstanceId = profileInstanceId;
    if (browserSessionEpoch !== undefined)
      this.browserSessionEpoch = browserSessionEpoch;
  }

  resetSession(browserSessionEpoch) {
    if (browserSessionEpoch !== undefined)
      this.browserSessionEpoch = browserSessionEpoch;
    for (const tabId of [...this.attached.keys()])
      this.frames.invalidateTab(tabId, "browser_session_changed");
    this.attached.clear();
    this.usedEvaluationApprovals.clear();
    this.frames.reset?.("browser_session_changed");
  }

  pruneEvaluationApprovals() {
    const now = this.now();
    for (const [id, expiresAt] of this.usedEvaluationApprovals) {
      if (expiresAt <= now) this.usedEvaluationApprovals.delete(id);
    }
  }

  validateAttachment(record, attachment, { spaceId, pageId, leaseEpoch } = {}) {
    if (
      attachment.spaceId !== spaceId ||
      attachment.pageId !== pageId ||
      attachment.leaseEpoch !== leaseEpoch ||
      attachment.browserSessionEpoch !== this.browserSessionEpoch ||
      attachment.targetGeneration !== record.targetGeneration ||
      attachment.navigationGeneration !== record.navigationGeneration ||
      attachment.documentGeneration !== record.documentGeneration
    ) {
      throw new ProtocolError(
        "stale_generation",
        "debugger attachment scope or generation is stale",
      );
    }
    return attachment;
  }

  invalidateTab(tabId, reason = "target_lost") {
    const attachment = this.attached.get(tabId);
    this.attached.delete(tabId);
    this.frames.invalidateTab(tabId, reason);
    if (attachment) {
      this.onStateChange("detached", {
        space_id: attachment.spaceId,
        page_id: attachment.pageId,
        reason,
      });
    }
  }

  handleDocumentChange(tabId, record) {
    const attachment = this.attached.get(tabId);
    if (!attachment || !record) return;
    attachment.navigationGeneration = record.navigationGeneration;
    attachment.documentGeneration = record.documentGeneration;
    this.frames.invalidateDocument(
      tabId,
      record.documentGeneration,
      record.navigationGeneration,
      "document_changed",
    );
    this.frames.bindTab({
      tabId,
      spaceId: attachment.spaceId,
      pageId: attachment.pageId,
      targetGeneration: attachment.targetGeneration,
      documentGeneration: attachment.documentGeneration,
      navigationGeneration: attachment.navigationGeneration,
    });
  }

  async attach({ spaceId, pageId, leaseEpoch, onDispatch = () => {} } = {}) {
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
    const existing = this.attached.get(record.rawTabId);
    if (existing) {
      this.validateAttachment(record, existing, {
        spaceId,
        pageId,
        leaseEpoch,
      });
      return this.publicAttachment(existing);
    }
    const attachFn = this.chrome?.debugger?.attach;
    if (typeof attachFn !== "function")
      throw new ProtocolError(
        "capability_unavailable",
        "Chrome debugger.attach is unavailable",
      );
    try {
      onDispatch();
      await chromeCall(
        attachFn.bind(this.chrome.debugger),
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
      targetGeneration: record.targetGeneration,
      navigationGeneration: record.navigationGeneration,
      documentGeneration: record.documentGeneration,
      browserSessionEpoch: this.browserSessionEpoch,
      attachedAt: this.now(),
    };
    try {
      const current = this.tabs.assertPageDispatch({
        spaceId,
        pageId,
        leaseEpoch,
        expectedTargetGeneration: record.targetGeneration,
        mutation: false,
      });
      if (current !== record)
        throw new ProtocolError(
          "stale_generation",
          "page changed during attachment",
        );
      this.attached.set(record.rawTabId, attachment);
      this.frames.bindTab({
        tabId: record.rawTabId,
        spaceId,
        pageId,
        targetGeneration: record.targetGeneration,
        navigationGeneration: record.navigationGeneration,
        documentGeneration: record.documentGeneration,
      });
    } catch (error) {
      this.invalidateTab(record.rawTabId, "attachment_commit_rejected");
      try {
        if (typeof this.chrome?.debugger?.detach === "function")
          await this.chrome.debugger.detach({ tabId: record.rawTabId });
      } catch (cleanupError) {
        throw unknownDispatch(
          "debugger attachment rollback was not confirmed",
          {
            cause:
              cleanupError instanceof Error
                ? cleanupError.message
                : String(cleanupError),
          },
        );
      }
      throw error;
    }
    this.onStateChange("attached", this.publicAttachment(attachment));
    return this.publicAttachment(attachment);
  }

  async detach({
    spaceId,
    pageId,
    leaseEpoch,
    reason = "host_requested",
    onDispatch = () => {},
  } = {}) {
    const record = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      mutation: false,
    });
    const attachment = this.attached.get(record.rawTabId);
    if (!attachment) return { detached: false, reason: "not_attached" };
    this.validateAttachment(record, attachment, {
      spaceId,
      pageId,
      leaseEpoch,
    });
    const detachFn = this.chrome?.debugger?.detach;
    if (typeof detachFn !== "function")
      throw new ProtocolError(
        "capability_unavailable",
        "Chrome debugger.detach is unavailable",
      );
    try {
      onDispatch();
      await chromeCall(detachFn.bind(this.chrome.debugger), {
        tabId: record.rawTabId,
      });
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
    expectedTargetGeneration,
    expectedNavigationGeneration,
    expectedDocumentGeneration,
    commandId,
    capability,
    approval,
    onDispatch = () => {},
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
      expectedTargetGeneration,
      expectedNavigationGeneration,
      expectedDocumentGeneration,
      mutation,
    });
    const attachment = this.attached.get(record.rawTabId);
    if (!attachment)
      throw new ProtocolError(
        "target_not_attached",
        "debugger target is not attached",
      );
    this.validateAttachment(record, attachment, {
      spaceId,
      pageId,
      leaseEpoch,
    });
    if (method === "Runtime.evaluate") {
      const expression = params?.expression ?? params?.script;
      if (
        !params ||
        typeof params !== "object" ||
        Array.isArray(params) ||
        typeof expression !== "string" ||
        expression.length === 0 ||
        Object.keys(params).some(
          (key) => !RUNTIME_EVALUATE_PARAMETER_ALLOWLIST.has(key),
        )
      ) {
        throw new ProtocolError(
          "schema_invalid",
          "runtime evaluation parameters are not allowlisted",
        );
      }
      if (capability !== "evaluate") {
        throw new ProtocolError(
          "evaluate_denied",
          "runtime evaluation requires an explicit capability",
        );
      }
      if (!approval || approval.issued_by_host !== true) {
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
    const sendCommandFn = this.chrome?.debugger?.sendCommand;
    if (typeof sendCommandFn !== "function")
      throw new ProtocolError(
        "capability_unavailable",
        "Chrome debugger.sendCommand is unavailable",
      );

    if (method === "Runtime.evaluate") {
      const expression = params?.expression ?? params?.script;
      if (
        !approval ||
        typeof approval.approval_id !== "string" ||
        !/^[A-Za-z0-9._:-]{8,128}$/.test(approval.approval_id) ||
        typeof approval.script_hash !== "string" ||
        !/^(?:sha256:)?[a-f0-9]{64}$/i.test(approval.script_hash)
      ) {
        throw new ProtocolError(
          "user_confirmation_required",
          "runtime evaluation approval is incomplete",
        );
      }
      const expiresAt = approval.expires_at ?? approval.expires_at_ms;
      if (
        !Number.isSafeInteger(expiresAt) ||
        expiresAt <= this.now() ||
        expiresAt > this.now() + MAX_APPROVAL_LIFETIME_MS
      ) {
        throw new ProtocolError(
          "approval_expired",
          "runtime evaluation approval is expired",
        );
      }
      if (
        approval.space_id !== spaceId ||
        approval.page_id !== pageId ||
        approval.lease_epoch !== leaseEpoch ||
        (approval.target_generation ?? approval.generation) !==
          record.targetGeneration ||
        (this.profileInstanceId !== undefined &&
          approval.profile_instance_id !== this.profileInstanceId) ||
        (this.browserSessionEpoch !== undefined &&
          approval.browser_session_epoch !== this.browserSessionEpoch)
      ) {
        throw new ProtocolError(
          "permission_denied",
          "runtime evaluation approval scope is not current",
        );
      }
      if (
        approval.purpose !== undefined &&
        approval.purpose !== "runtime.evaluate"
      ) {
        throw new ProtocolError(
          "permission_denied",
          "runtime evaluation approval purpose is invalid",
        );
      }
      this.pruneEvaluationApprovals();
      if (this.usedEvaluationApprovals.has(approval.approval_id))
        throw new ProtocolError(
          "replay_rejected",
          "runtime evaluation approval was already consumed",
        );
      const scriptHash = await hashRuntimeScript(expression);
      const approvedHash = approval.script_hash
        .toLowerCase()
        .replace(/^sha256:/, "");
      if (scriptHash !== approvedHash)
        throw new ProtocolError(
          "permission_denied",
          "runtime evaluation script does not match approval",
        );
      this.tabs.assertPageDispatch({
        spaceId,
        pageId,
        leaseEpoch,
        expectedTargetGeneration: record.targetGeneration,
        expectedNavigationGeneration: record.navigationGeneration,
        expectedDocumentGeneration: record.documentGeneration,
        mutation,
      });
      this.validateAttachment(record, attachment, {
        spaceId,
        pageId,
        leaseEpoch,
      });
      if (this.usedEvaluationApprovals.size >= MAX_USED_APPROVALS)
        throw new ProtocolError(
          "resource_exhausted",
          "evaluation approval cache is full",
        );
      this.usedEvaluationApprovals.set(approval.approval_id, expiresAt);
    }

    let dispatched = false;
    try {
      onDispatch();
      dispatched = true;
      const result = await chromeCall(
        sendCommandFn.bind(this.chrome.debugger),
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
        navigation_generation: attachment.navigationGeneration,
        document_generation: attachment.documentGeneration,
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
      navigation_generation: attachment.navigationGeneration,
      document_generation: attachment.documentGeneration,
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
