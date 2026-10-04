import {
  ProtocolError,
  assertLogicalScope,
  chromeErrorMessage,
  classifyChromeError,
  isRestrictedUrl,
  redactBrowserIdentifiers,
} from "./protocol.mjs";
import { isAllowedDebuggerEvent } from "./frames.mjs";

export { DEBUGGER_EVENT_ALLOWLIST, isAllowedDebuggerEvent } from "./frames.mjs";

// Chrome documents "0.1" as the minimum required debugger protocol version;
// use it so newer compatible protocol revisions remain attachable.
export const REQUIRED_DEBUGGER_PROTOCOL_VERSION = "0.1";

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

const INTERNAL_RELATED_TARGET_METHOD = "Target.setAutoAttach";
const RELATED_TARGET_TYPES = new Set(["iframe"]);
const INTERNAL_EVENT_DOMAIN_METHODS = Object.freeze([
  "Accessibility.enable",
  "DOM.enable",
  "Network.enable",
  "Page.enable",
  "Runtime.enable",
]);

const ARTIFACT_METHOD_PURPOSES = Object.freeze({
  "Page.captureScreenshot": "screenshot",
  "Page.printToPDF": "pdf",
});

export function isArtifactDebuggerCommand(method) {
  return Object.prototype.hasOwnProperty.call(ARTIFACT_METHOD_PURPOSES, method);
}

const MAX_ARTIFACT_APPROVAL_LIFETIME_MS = 15 * 60 * 1000;
const MAX_USED_ARTIFACT_APPROVALS = 1024;
const APPROVAL_ID_RE = /^[A-Za-z0-9._:-]{8,128}$/;

function pageOrigin(url) {
  if (typeof url !== "string") return undefined;
  try {
    const parsed = new URL(url);
    return parsed.origin === "null" ? undefined : parsed.origin;
  } catch {
    return undefined;
  }
}

function approvalExpiry(approval) {
  return approval?.expires_at ?? approval?.expires_at_ms;
}

function chromeProtocolError(error, operation, context = {}) {
  const code = classifyChromeError(error, { operation, ...context });
  if (code === "unknown") return undefined;
  return new ProtocolError(code, chromeErrorMessage(code), {
    chrome_error: code,
  });
}

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
    enableEventDomains = false,
  } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.tabs = tabs;
    this.frames = frames;
    this.onEvent = onEvent;
    this.onStateChange = onStateChange;
    this.now = now;
    this.profileInstanceId = profileInstanceId;
    this.browserSessionEpoch = browserSessionEpoch;
    this.enableEventDomains = enableEventDomains === true;
    this.attached = new Map();
    this.relatedSessions = new Map();
    this.usedEvaluationApprovals = new Map();
    this.usedArtifactApprovals = new Map();
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
    this.relatedSessions.clear();
    this.usedEvaluationApprovals.clear();
    this.usedArtifactApprovals.clear();
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
    this.relatedSessions.clear();
    this.usedEvaluationApprovals.clear();
    this.usedArtifactApprovals.clear();
    this.frames.reset?.("browser_session_changed");
  }

  pruneEvaluationApprovals() {
    const now = this.now();
    for (const [id, expiresAt] of this.usedEvaluationApprovals) {
      if (expiresAt <= now) this.usedEvaluationApprovals.delete(id);
    }
  }

  pruneArtifactApprovals() {
    const now = this.now();
    for (const [id, expiresAt] of this.usedArtifactApprovals) {
      if (expiresAt <= now) this.usedArtifactApprovals.delete(id);
    }
  }

  approvalFrameScope(approval) {
    return approval?.frame_scope ?? approval?.frameScope;
  }

  assertCurrentFrameScope(record, approval) {
    const frameScope = this.approvalFrameScope(approval);
    if (typeof frameScope !== "string" || frameScope.length === 0)
      throw new ProtocolError(
        "permission_denied",
        "approval must name a logical frame scope",
      );
    if (typeof this.frames?.assertFrameScope === "function") {
      return this.frames.assertFrameScope({
        tabId: record.rawTabId,
        frameScope,
      });
    }
    if (frameScope !== "main")
      throw new ProtocolError(
        "permission_denied",
        "logical frame scope is unavailable",
      );
    return { frameScope, origin: pageOrigin(record.url) };
  }

  validateRuntimeEvaluationApproval(
    approval,
    { record, spaceId, pageId, leaseEpoch } = {},
  ) {
    if (!approval || typeof approval !== "object" || Array.isArray(approval))
      throw new ProtocolError(
        "user_confirmation_required",
        "runtime evaluation requires host approval",
      );
    if (
      approval.issued_by_host !== true ||
      typeof approval.approval_id !== "string" ||
      !APPROVAL_ID_RE.test(approval.approval_id)
    )
      throw new ProtocolError(
        "user_confirmation_required",
        "runtime evaluation approval is incomplete",
      );
    if (approval.purpose !== "runtime.evaluate")
      throw new ProtocolError(
        "permission_denied",
        "runtime evaluation approval purpose is invalid",
      );
    const expiresAt = approvalExpiry(approval);
    const now = this.now();
    if (
      !Number.isSafeInteger(expiresAt) ||
      expiresAt <= now ||
      expiresAt > now + MAX_APPROVAL_LIFETIME_MS
    )
      throw new ProtocolError(
        "approval_expired",
        "runtime evaluation approval is expired",
      );
    const targetGeneration = approval.target_generation ?? approval.generation;
    if (
      targetGeneration !== record.targetGeneration ||
      approval.navigation_generation !== record.navigationGeneration ||
      approval.document_generation !== record.documentGeneration
    )
      throw new ProtocolError(
        "permission_denied",
        "runtime evaluation approval generation scope is not current",
      );
    if (
      approval.space_id !== spaceId ||
      approval.page_id !== pageId ||
      approval.lease_epoch !== leaseEpoch ||
      (this.profileInstanceId !== undefined &&
        approval.profile_instance_id !== this.profileInstanceId) ||
      (this.browserSessionEpoch !== undefined &&
        approval.browser_session_epoch !== this.browserSessionEpoch)
    )
      throw new ProtocolError(
        "permission_denied",
        "runtime evaluation approval scope is not current",
      );
    const frame = this.assertCurrentFrameScope(record, approval);
    const origin = frame.origin ?? pageOrigin(record.url);
    const approvedOrigin = approval.origin ?? approval.origin_scope;
    if (!origin || approvedOrigin !== origin)
      throw new ProtocolError(
        "permission_denied",
        "runtime evaluation approval origin scope is not current",
      );
    return { approvalId: approval.approval_id, expiresAt };
  }

  validateArtifactApproval(
    approval,
    {
      record,
      spaceId,
      pageId,
      leaseEpoch,
      method,
      requireFrameBinding = true,
    } = {},
  ) {
    const deny = (message) => new ProtocolError("artifact_denied", message);
    if (!isArtifactDebuggerCommand(method))
      throw new ProtocolError(
        "capability_unavailable",
        "command is not an artifact operation",
      );
    if (!approval || typeof approval !== "object" || Array.isArray(approval))
      throw deny("artifact capture requires a host-issued approval");
    if (
      approval.issued_by_host !== true ||
      typeof approval.approval_id !== "string" ||
      !APPROVAL_ID_RE.test(approval.approval_id)
    )
      throw deny("artifact approval is incomplete");
    if (approval.purpose !== ARTIFACT_METHOD_PURPOSES[method])
      throw deny("artifact approval purpose is invalid");
    const expiresAt = approvalExpiry(approval);
    const now = this.now();
    if (
      !Number.isSafeInteger(expiresAt) ||
      expiresAt <= now ||
      expiresAt > now + MAX_ARTIFACT_APPROVAL_LIFETIME_MS
    )
      throw deny("artifact approval is expired");
    if (approval.user_gesture !== true && approval.gesture !== true)
      throw new ProtocolError(
        "user_confirmation_required",
        "artifact capture requires a current user gesture",
      );
    const targetGeneration = approval.target_generation ?? approval.generation;
    if (
      approval.space_id !== spaceId ||
      approval.page_id !== pageId ||
      approval.lease_epoch !== leaseEpoch ||
      targetGeneration !== record.targetGeneration ||
      approval.navigation_generation !== record.navigationGeneration ||
      approval.document_generation !== record.documentGeneration ||
      (this.profileInstanceId !== undefined &&
        approval.profile_instance_id !== this.profileInstanceId) ||
      (this.browserSessionEpoch !== undefined &&
        approval.browser_session_epoch !== this.browserSessionEpoch)
    )
      throw deny("artifact approval scope is not current");
    const frameScope = this.approvalFrameScope(approval);
    if (frameScope !== "main")
      throw deny("artifact frame scope is not supported");
    let frame;
    if (requireFrameBinding) {
      try {
        frame = this.assertCurrentFrameScope(record, approval);
      } catch (error) {
        if (error instanceof ProtocolError)
          throw deny("artifact frame scope is not current");
        throw error;
      }
    } else {
      frame = { frameScope, origin: pageOrigin(record.url) };
    }
    const origin = frame.origin ?? pageOrigin(record.url);
    const approvedOrigin = approval.origin ?? approval.origin_scope;
    if (!origin || approvedOrigin !== origin)
      throw deny("artifact origin is not current");
    return { approvalId: approval.approval_id, expiresAt };
  }

  assertArtifactApproval({
    spaceId,
    pageId,
    leaseEpoch,
    method,
    approval,
  } = {}) {
    const record = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      mutation: false,
    });
    if (record.incognito === true)
      throw new ProtocolError(
        "incognito_not_supported",
        "incognito pages are not enrolled",
      );
    if (isRestrictedUrl(record.url))
      throw new ProtocolError(
        "restricted_url",
        "page cannot accept artifact capture",
      );
    return this.validateArtifactApproval(approval, {
      record,
      spaceId,
      pageId,
      leaseEpoch,
      method,
      requireFrameBinding: false,
    });
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

  clearAttachmentState(tabId, reason = "attachment_setup_failed") {
    this.attached.delete(tabId);
    this.relatedSessions.delete(tabId);
    if (typeof this.frames?.clearTab === "function")
      this.frames.clearTab(tabId);
    else this.frames?.invalidateTab?.(tabId, reason);
  }

  invalidateTab(tabId, reason = "target_lost") {
    const attachment = this.attached.get(tabId);
    this.attached.delete(tabId);
    this.relatedSessions.delete(tabId);
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
      origin: pageOrigin(record.url),
      targetGeneration: attachment.targetGeneration,
      documentGeneration: attachment.documentGeneration,
      navigationGeneration: attachment.navigationGeneration,
    });
  }

  async configureRelatedTargets(tabId, sessionId = undefined) {
    const sendCommandFn = this.chrome?.debugger?.sendCommand;
    if (typeof sendCommandFn !== "function")
      throw new ProtocolError(
        "capability_unavailable",
        "Chrome debugger.sendCommand is unavailable",
      );
    const send = chromeCall.bind(
      null,
      sendCommandFn.bind(this.chrome.debugger),
    );
    const target = { tabId, ...(sessionId ? { sessionId } : {}) };
    if (this.enableEventDomains)
      for (const method of INTERNAL_EVENT_DOMAIN_METHODS)
        await send(target, method);
    await send(target, INTERNAL_RELATED_TARGET_METHOD, {
      autoAttach: true,
      waitForDebuggerOnStart: false,
      flatten: true,
      filter: [{ type: "iframe", exclude: false }],
    });
  }

  handleRelatedTargetAttached(source = {}, params = {}) {
    if (
      !Number.isInteger(source.tabId) ||
      typeof params.sessionId !== "string" ||
      params.sessionId.length === 0
    )
      return false;
    const attachment = this.attached.get(source.tabId);
    const targetInfo = params.targetInfo;
    if (!attachment || !RELATED_TARGET_TYPES.has(targetInfo?.type))
      return false;
    try {
      this.frames.bindSession({
        tabId: source.tabId,
        sessionId: params.sessionId,
        spaceId: attachment.spaceId,
        pageId: attachment.pageId,
        origin: pageOrigin(targetInfo.url),
        targetGeneration: attachment.targetGeneration,
        navigationGeneration: attachment.navigationGeneration,
        documentGeneration: attachment.documentGeneration,
        targetId: targetInfo.targetId,
      });
      const sessionGeneration = this.frames.getSessionGeneration(
        source.tabId,
        params.sessionId,
      );
      let sessions = this.relatedSessions.get(source.tabId);
      if (!sessions) {
        sessions = new Set();
        this.relatedSessions.set(source.tabId, sessions);
      }
      sessions.add(params.sessionId);
      void this.configureRelatedTargets(source.tabId, params.sessionId).catch(
        () => {
          const current = this.frames.isCurrentSession(
            source.tabId,
            params.sessionId,
            {
              sessionGeneration,
              targetId: targetInfo.targetId,
            },
          );
          if (!current) return;
          this.frames.invalidateSession(
            source.tabId,
            params.sessionId,
            "related_target_setup_failed",
            {
              expectedSessionGeneration: sessionGeneration,
              targetId: targetInfo.targetId,
            },
          );
          sessions.delete(params.sessionId);
          if (sessions.size === 0) this.relatedSessions.delete(source.tabId);
        },
      );
      return true;
    } catch {
      return false;
    }
  }

  handleRelatedTargetDetached(source = {}, params = {}) {
    if (!Number.isInteger(source.tabId) || typeof params.sessionId !== "string")
      return false;
    const invalidated = this.frames.invalidateSession(
      source.tabId,
      params.sessionId,
      "related_target_detached",
      params.targetId === undefined ? {} : { targetId: params.targetId },
    );
    if (!invalidated) return false;
    const sessions = this.relatedSessions.get(source.tabId);
    sessions?.delete(params.sessionId);
    if (sessions?.size === 0) this.relatedSessions.delete(source.tabId);
    return true;
  }

  async attach({ spaceId, pageId, leaseEpoch, onDispatch = () => {} } = {}) {
    const record = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      mutation: false,
    });
    if (record.incognito === true)
      throw new ProtocolError(
        "incognito_not_supported",
        "incognito pages are not enrolled",
      );
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
    let rootAttached = false;
    let rootProvisioned = false;
    try {
      onDispatch();
      await chromeCall(
        attachFn.bind(this.chrome.debugger),
        { tabId: record.rawTabId },
        REQUIRED_DEBUGGER_PROTOCOL_VERSION,
      );
      rootAttached = true;

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

      // Publish the root mapping before Target.setAutoAttach: Chrome may emit
      // Target.attachedToTarget synchronously while that command is in flight.
      this.frames.bindTab({
        tabId: record.rawTabId,
        spaceId,
        pageId,
        origin: pageOrigin(record.url),
        targetGeneration: record.targetGeneration,
        navigationGeneration: record.navigationGeneration,
        documentGeneration: record.documentGeneration,
      });
      rootProvisioned = true;
      this.attached.set(record.rawTabId, attachment);
      await this.configureRelatedTargets(record.rawTabId);

      const stillCurrent = this.tabs.assertPageDispatch({
        spaceId,
        pageId,
        leaseEpoch,
        expectedTargetGeneration: record.targetGeneration,
        mutation: false,
      });
      if (stillCurrent !== record)
        throw new ProtocolError(
          "stale_generation",
          "page changed during attachment",
        );
    } catch (error) {
      if (rootProvisioned) this.clearAttachmentState(record.rawTabId);
      if (rootAttached) {
        try {
          if (typeof this.chrome?.debugger?.detach !== "function")
            throw new ProtocolError(
              "capability_unavailable",
              "Chrome debugger.detach is unavailable",
            );
          await chromeCall(
            this.chrome.debugger.detach.bind(this.chrome.debugger),
            { tabId: record.rawTabId },
          );
        } catch (cleanupError) {
          const classified = chromeProtocolError(
            cleanupError,
            "debugger.detach",
            {
              url: record.url,
              incognito: record.incognito === true,
            },
          );
          if (classified) throw classified;
          throw unknownDispatch(
            "debugger attachment rollback was not confirmed",
            { chrome_error: "unknown" },
          );
        }
      }
      if (error instanceof ProtocolError) throw error;
      const classified = chromeProtocolError(error, "debugger.attach", {
        url: record.url,
        incognito: record.incognito === true,
      });
      if (classified) throw classified;
      throw unknownDispatch("debugger attachment result was not confirmed", {
        chrome_error: "unknown",
      });
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
      const classified = chromeProtocolError(error, "debugger.detach", {
        url: record.url,
        incognito: record.incognito === true,
      });
      if (classified) throw classified;
      throw unknownDispatch("debugger detach result was lost", {
        chrome_error: "unknown",
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
    frameScope,
    commandId,
    capability,
    approval,
    allowLargeResult = false,
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
    if (record.incognito === true)
      throw new ProtocolError(
        "incognito_not_supported",
        "incognito pages are not enrolled",
      );
    if (isRestrictedUrl(record.url))
      throw new ProtocolError(
        "restricted_url",
        "page cannot accept debugger commands",
      );
    const artifactApproval = isArtifactDebuggerCommand(method)
      ? this.validateArtifactApproval(approval, {
          record,
          spaceId,
          pageId,
          leaseEpoch,
          method,
        })
      : undefined;
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
    let runtimeApproval;
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
      runtimeApproval = this.validateRuntimeEvaluationApproval(approval, {
        record,
        spaceId,
        pageId,
        leaseEpoch,
      });
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
        typeof approval?.script_hash !== "string" ||
        !/^(?:sha256:)?[a-f0-9]{64}$/i.test(approval.script_hash)
      )
        throw new ProtocolError(
          "user_confirmation_required",
          "runtime evaluation approval is incomplete",
        );
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
      this.usedEvaluationApprovals.set(
        runtimeApproval.approvalId,
        runtimeApproval.expiresAt,
      );
    }
    if (artifactApproval) {
      this.pruneArtifactApprovals();
      if (this.usedArtifactApprovals.has(artifactApproval.approvalId))
        throw new ProtocolError(
          "replay_rejected",
          "artifact approval was already consumed",
        );
      if (this.usedArtifactApprovals.size >= MAX_USED_ARTIFACT_APPROVALS)
        throw new ProtocolError(
          "resource_exhausted",
          "artifact approval cache is full",
        );
      this.usedArtifactApprovals.set(
        artifactApproval.approvalId,
        artifactApproval.expiresAt,
      );
    }

    const debuggerTarget = { tabId: record.rawTabId };
    if (frameScope !== undefined) {
      const resolvedFrame = this.frames.resolveFrameScope({
        tabId: record.rawTabId,
        frameScope,
      });
      if (resolvedFrame.sessionId)
        debuggerTarget.sessionId = resolvedFrame.sessionId;
    }
    let dispatched = false;
    try {
      onDispatch();
      dispatched = true;
      const result = await chromeCall(
        sendCommandFn.bind(this.chrome.debugger),
        debuggerTarget,
        method,
        params,
      );
      const safeResult = redactBrowserIdentifiers(result ?? {});
      const serialized = JSON.stringify(safeResult);
      if (
        !allowLargeResult &&
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
      if (error instanceof ProtocolError) throw error;
      const classified = chromeProtocolError(error, method, {
        url: record.url,
        incognito: record.incognito === true,
      });
      if (classified) throw classified;
      if (dispatched && (mutation || isArtifactDebuggerCommand(method))) {
        throw unknownDispatch(
          isArtifactDebuggerCommand(method)
            ? "debugger artifact dispatch result was lost"
            : "debugger mutation dispatch result was lost",
          {
            ...(commandId !== undefined ? { command_id: commandId } : {}),
            chrome_error: "unknown",
          },
        );
      }
      throw new ProtocolError(
        "debugger_command_failed",
        "debugger command failed",
        { chrome_error: "unknown" },
      );
    }
  }

  handleEvent(source = {}, method, params = {}) {
    if (!Number.isInteger(source.tabId) || typeof method !== "string")
      return null;
    if (method === "Target.attachedToTarget") {
      this.handleRelatedTargetAttached(source, params);
      return null;
    }
    if (method === "Target.detachedFromTarget") {
      this.handleRelatedTargetDetached(source, params);
      return null;
    }
    if (!isAllowedDebuggerEvent(method)) return null;
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
      const invalidated = this.frames.invalidateSession(
        source.tabId,
        source.sessionId,
        reason,
        source.targetId === undefined ? {} : { targetId: source.targetId },
      );
      if (!invalidated) return null;
      this.relatedSessions.get(source.tabId)?.delete(source.sessionId);
      if (this.relatedSessions.get(source.tabId)?.size === 0)
        this.relatedSessions.delete(source.tabId);
    } else {
      this.relatedSessions.delete(source.tabId);
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
  const chromeError = classifyChromeError(error);
  if (chromeError !== "unknown")
    return {
      code: chromeError,
      message: chromeErrorMessage(chromeError),
      retryable: false,
      details: { chrome_error: chromeError },
    };
  return {
    code: "debugger_command_failed",
    message: "debugger command failed",
    retryable: false,
    details: { chrome_error: "unknown" },
  };
}
