import {
  ProtocolError,
  assertLogicalScope,
  assertNoRawBrowserIdentifiers,
  createLogicalId,
  errorResult,
  isFenceMethod,
  isMutationMethod,
  publicError,
} from "./protocol.mjs";
import { NativeMessagingClient } from "./native-messaging.mjs";
import { DebuggerBridge, debuggerOperationError } from "./debugger-bridge.mjs";
import { TabsRegistry, unknownDispatch } from "./tabs-registry.mjs";
import { GroupsRegistry } from "./groups.mjs";
import { FramesRegistry } from "./frames.mjs";

const METADATA_KEY = "agentyc_extension_metadata";
const VERSION = "0.1.0";

function chromeApiOrGlobal(chromeApi) {
  return chromeApi ?? globalThis.chrome;
}

async function storageGet(area, key) {
  if (!area?.get) return {};
  const result = area.get(key);
  return result?.then ? result : {};
}

async function storageSet(area, value) {
  if (!area?.set) return;
  const result = area.set(value);
  if (result?.then) await result;
}

function positiveEpoch(value, field) {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new ProtocolError(
      "schema_invalid",
      `${field} must be a positive integer`,
    );
  }
  return value;
}

function normalizedParams(message) {
  const params =
    message.params && typeof message.params === "object" ? message.params : {};
  assertNoRawBrowserIdentifiers(params);
  return params;
}

function valueOf(message, params, snake, camel = undefined) {
  return (
    message[snake] ??
    (camel ? message[camel] : undefined) ??
    params[snake] ??
    (camel ? params[camel] : undefined)
  );
}

/**
 * MV3 service-worker adapter. It owns only live routing and browser handles;
 * no lease, action journal, logical identity, or cleanup decision is persisted.
 */
export class ServiceWorkerController {
  constructor({
    chromeApi,
    nativeClient,
    storageArea,
    hostName,
    profileInstanceId,
    workerInstanceEpoch,
    browserSessionEpoch,
    autoReconnect = true,
    now = () => Date.now(),
  } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.storage = storageArea ?? this.chrome?.storage?.local;
    this.hostName = hostName;
    this.now = now;
    this.metadata = {
      profileInstanceId,
      workerInstanceEpoch,
      browserSessionEpoch,
    };
    this.started = false;
    this.pending = new Map();
    this.inflight = new Map();
    this.fences = new Map();
    this.contentPending = new Map();
    this.sidePanelListeners = [];

    const hintSalt = `worker:${workerInstanceEpoch ?? 0}`;
    this.groups = new GroupsRegistry({
      chromeApi: this.chrome,
      hintSalt,
      onEvent: (event, payload) => this.handleExtensionEvent(event, payload),
    });
    this.tabs = new TabsRegistry({
      chromeApi: this.chrome,
      groups: this.groups,
      hintSalt,
      now,
      onEvent: (event, payload) => this.handleExtensionEvent(event, payload),
    });
    this.frames = new FramesRegistry({
      hintSalt,
      onEvent: (payload) => this.handleDebuggerEvent(payload),
      onRoute: () => {},
    });
    this.debugger = new DebuggerBridge({
      chromeApi: this.chrome,
      tabs: this.tabs,
      frames: this.frames,
      onEvent: (payload) => this.handleDebuggerEvent(payload),
      onStateChange: (state, payload) =>
        this.handleExtensionEvent(`debugger.${state}`, payload),
    });
    this.native =
      nativeClient ??
      new NativeMessagingClient({
        chromeApi: this.chrome,
        hostName,
        profileInstanceId,
        workerInstanceEpoch,
        browserSessionEpoch,
        extensionVersion: VERSION,
        autoReconnect,
        onMessage: (message) => void this.handleHostEnvelope(message),
        onStateChange: (state, detail) => this.handleNativeState(state, detail),
        onUnknownActions: (actionIds, reason) =>
          this.handleLostDispatch(actionIds, reason),
      });
  }

  async start() {
    if (this.started) return this;
    await this.loadMetadata();
    this.started = true;
    this.groups.start();
    await this.tabs.start();
    this.debugger.start();
    this.installRuntimeListener();
    try {
      await this.native.connect();
    } catch {
      // Reconnect is handled by NativeMessagingClient; pages remain retained.
    }
    return this;
  }

  async loadMetadata() {
    const stored =
      (await storageGet(this.storage, METADATA_KEY))[METADATA_KEY] ?? {};
    const profileInstanceId =
      this.metadata.profileInstanceId ??
      (typeof stored.profile_instance_id === "string"
        ? stored.profile_instance_id
        : createLogicalId("profile"));
    const previousWorker = Number.isSafeInteger(stored.worker_instance_epoch)
      ? stored.worker_instance_epoch
      : 0;
    const workerInstanceEpoch =
      this.metadata.workerInstanceEpoch ?? Math.max(1, previousWorker + 1);
    const browserSessionEpoch =
      this.metadata.browserSessionEpoch ??
      (Number.isSafeInteger(stored.browser_session_epoch)
        ? stored.browser_session_epoch
        : 1);
    this.metadata = {
      profileInstanceId,
      workerInstanceEpoch,
      browserSessionEpoch,
    };
    if (this.native) {
      this.native.profileInstanceId = profileInstanceId;
      this.native.workerInstanceEpoch = workerInstanceEpoch;
      this.native.browserSessionEpoch = browserSessionEpoch;
    }
    await storageSet(this.storage, {
      [METADATA_KEY]: {
        profile_instance_id: profileInstanceId,
        worker_instance_epoch: workerInstanceEpoch,
        browser_session_epoch: browserSessionEpoch,
        ui_version: VERSION,
      },
    });
  }

  installRuntimeListener() {
    const event = this.chrome?.runtime?.onMessage;
    if (!event?.addListener) return;
    const listener = (message, sender) =>
      this.handleRuntimeMessage(message, sender);
    event.addListener(listener);
    this.sidePanelListeners.push(() => event.removeListener?.(listener));
  }

  stop() {
    for (const remove of this.sidePanelListeners.splice(0)) remove();
    this.debugger.stop();
    this.tabs.stop();
    this.groups.stop();
    this.native.stop();
    this.pending.clear();
    this.inflight.clear();
    this.started = false;
  }

  handleNativeState(state, detail) {
    this.handleExtensionEvent(`native.${state}`, {
      state,
      ...(detail?.code ? { code: detail.code } : {}),
    });
  }

  handleLostDispatch(actionIds, reason) {
    for (const actionId of actionIds) {
      const pending = this.inflight.get(actionId);
      this.inflight.delete(actionId);
      this.handleExtensionEvent("action.unknown", {
        action_id: actionId,
        outcome: "unknown",
        code: "unknown_outcome",
        reason: reason instanceof Error ? reason.message : String(reason),
      });
      if (pending?.requestId) this.pending.delete(pending.requestId);
    }
  }

  async sendInventory() {
    if (!this.native.connected) return;
    try {
      this.native.send("inventory", {
        payload: {
          profile_instance_id: this.metadata.profileInstanceId,
          pages: this.tabs.inventory(),
          groups: this.groups.listHints(),
        },
      });
    } catch {
      // A disconnect between state notification and post is handled as a loss.
    }
  }

  async handleHostEnvelope(message) {
    try {
      if (message.kind === "hello_ok") {
        await this.sendInventory();
        return { ok: true };
      }
      if (message.kind === "event") {
        this.forwardHostEvent(message);
        return { ok: true };
      }
      if (message.kind === "request" || message.kind === "fence") {
        return await this.handleHostRequest(message);
      }
      if (message.kind === "response" || message.kind === "action_result") {
        this.native.markActionComplete(message.action_id);
        this.forwardHostEvent({
          event: "host.response",
          payload: message,
        });
        return { ok: true };
      }
      throw new ProtocolError(
        "schema_invalid",
        "host envelope kind is not executable",
      );
    } catch (error) {
      const safeError = publicError(error);
      this.handleExtensionEvent("host.message_rejected", {
        code: safeError.code,
      });
      if (
        (message?.kind === "request" || message?.kind === "fence") &&
        typeof message.request_id === "string"
      ) {
        this.reply({
          requestId: message.request_id,
          actionId: message.action_id,
          ok: false,
          error: safeError,
          mutation: isMutationMethod(message.method),
        });
      }
      return { ok: false, error: safeError };
    }
  }

  async handleHostRequest(message) {
    const params = normalizedParams(message);
    const method =
      message.method ?? (message.kind === "fence" ? "fence" : undefined);
    if (typeof method !== "string" || method.length === 0)
      throw new ProtocolError(
        "schema_invalid",
        "host request method is required",
      );
    const requestId = message.request_id ?? createLogicalId("req");
    const actionId = message.action_id;
    const mutation = isMutationMethod(method);
    const spaceId = valueOf(message, params, "space_id", "spaceId");
    const pageId = valueOf(message, params, "page_id", "pageId");
    const leaseEpoch = valueOf(message, params, "lease_epoch", "leaseEpoch");
    if (isFenceMethod(method)) {
      return this.handleFence({
        message,
        params,
        requestId,
        actionId,
        spaceId,
        leaseEpoch,
      });
    }
    if (mutation) {
      assertLogicalScope(
        { spaceId, pageId },
        {
          pageRequired:
            method !== "space.pause" &&
            method !== "space.finish" &&
            method !== "space.release",
        },
      );
      positiveEpoch(leaseEpoch, "lease_epoch");
      this.assertFence(spaceId, leaseEpoch);
    }

    if (actionId)
      this.inflight.set(actionId, {
        requestId,
        spaceId,
        method,
        dispatched: false,
      });
    try {
      const result = await this.executeHostMethod({
        method,
        params,
        spaceId,
        pageId,
        leaseEpoch,
        requestId,
        actionId,
        message,
      });
      if (actionId) this.inflight.delete(actionId);
      this.reply({ requestId, actionId, ok: true, result, mutation });
      return { ok: true, result };
    } catch (error) {
      if (actionId) this.inflight.delete(actionId);
      const safeError = publicError(error);
      this.reply({
        requestId,
        actionId,
        ok: false,
        error: safeError,
        mutation,
      });
      return { ok: false, error: safeError };
    }
  }

  async executeHostMethod({
    method,
    params,
    spaceId,
    pageId,
    leaseEpoch,
    requestId,
    actionId,
  }) {
    switch (method) {
      case "tab.inventory":
      case "page.list":
        return {
          pages: this.tabs.inventory(),
          groups: this.groups.listHints(),
        };
      case "page.create":
        return this.tabs.createAgentPage({
          spaceId,
          pageId,
          leaseEpoch,
          url: params.url,
          title: params.title,
          ownershipProof: params.ownership_proof,
          windowHint: params.window_hint,
        });
      case "page.adopt":
        return this.tabs.adoptExistingTab({
          tabHint: params.tab_hint,
          spaceId,
          pageId,
          leaseEpoch,
          ownershipProof: params.ownership_proof,
          intentTicket: params.intent_ticket,
        });
      case "page.close":
        return this.tabs.closeManagedPage({
          spaceId,
          pageId,
          leaseEpoch,
          expectedGeneration: params.expected_generation,
          cleanupProof: params.cleanup_proof,
        });
      case "debugger.attach":
        return this.debugger.attach({ spaceId, pageId, leaseEpoch });
      case "debugger.detach":
        return this.debugger.detach({
          spaceId,
          pageId,
          leaseEpoch,
          reason: params.reason,
        });
      case "debugger.command": {
        const debuggerMethod = params.method;
        const pending = actionId ? this.inflight.get(actionId) : undefined;
        if (pending) pending.dispatched = true;
        return this.debugger.sendCommand({
          spaceId,
          pageId,
          leaseEpoch,
          method: debuggerMethod,
          params: params.params ?? {},
          expectedGeneration: params.expected_generation,
          commandId: params.command_id ?? actionId ?? requestId,
          capability: params.capability,
          approval: params.approval,
        });
      }
      case "group.present": {
        const record = this.tabs.assertPageDispatch({
          spaceId,
          pageId,
          leaseEpoch,
          mutation: false,
        });
        return this.groups.presentSpace({
          spaceId,
          tabId: record.rawTabId,
          title: params.title,
        });
      }
      case "content.request":
        return this.sendContentRequest({
          spaceId,
          pageId,
          leaseEpoch,
          params,
          requestId,
        });
      default:
        throw new ProtocolError(
          "capability_unavailable",
          `extension method is not implemented: ${method}`,
        );
    }
  }

  async sendContentRequest({ spaceId, pageId, leaseEpoch, params, requestId }) {
    const record = this.tabs.assertPageDispatch({
      spaceId,
      pageId,
      leaseEpoch,
      mutation: true,
    });
    if (!this.chrome?.tabs?.sendMessage)
      throw new ProtocolError(
        "capability_unavailable",
        "content messaging is unavailable",
      );
    const message = {
      type: "agentyc.content.request",
      version: 1,
      nonce: params.nonce,
      document_id: params.document_id,
      request_id: requestId,
      operation: params.operation,
      payload: params.payload ?? {},
    };
    this.contentPending.set(requestId, { spaceId, pageId });
    try {
      await this.chrome.tabs.sendMessage(record.rawTabId, message);
    } catch (error) {
      this.contentPending.delete(requestId);
      throw unknownDispatch("content bridge dispatch result was lost", {
        cause: error instanceof Error ? error.message : String(error),
      });
    }
    return { accepted: true, request_id: requestId };
  }

  handleFence({ message, params, requestId, actionId, spaceId, leaseEpoch }) {
    assertLogicalScope({ spaceId });
    positiveEpoch(leaseEpoch, "lease_epoch");
    const fenceEpoch = positiveEpoch(
      params.fence_epoch ?? message.fence_epoch ?? leaseEpoch,
      "fence_epoch",
    );
    const current = this.fences.get(spaceId) ?? 0;
    if (fenceEpoch <= current) {
      const error = errorResult(
        "stale_fence",
        "fence barrier is older than or equal to the live barrier",
      );
      this.reply({ requestId, actionId, ok: false, error, mutation: false });
      return { ok: false, error };
    }
    const drain = this.tabs.beginFence(spaceId, fenceEpoch);
    const unknownActionIds = [];
    for (const [id, pending] of this.inflight) {
      if (pending.spaceId === spaceId && pending.dispatched) {
        unknownActionIds.push(id);
        this.inflight.delete(id);
      }
    }
    this.fences.set(spaceId, fenceEpoch);
    const result = {
      space_id: spaceId,
      fence_epoch: fenceEpoch,
      drained_request_ids: [],
      unknown_action_ids: unknownActionIds,
      affected_page_count: drain.affected_page_ids.length,
    };
    try {
      this.native.send("fence_ack", {
        request_id: requestId,
        action_id: actionId,
        space_id: spaceId,
        fence_epoch: fenceEpoch,
        result,
        ok: true,
      });
    } catch {
      // Host reconciliation sees the missing acknowledgement and keeps the space paused.
    }
    return { ok: true, result };
  }

  assertFence(spaceId, leaseEpoch) {
    const fence = this.fences.get(spaceId) ?? 0;
    if (leaseEpoch < fence)
      throw new ProtocolError(
        "stale_lease",
        "command is below the current fence barrier",
      );
  }

  reply({ requestId, actionId, ok, result, error, mutation }) {
    try {
      this.native.sendResponse({
        requestId,
        actionId,
        ok,
        result,
        error,
        mutation,
      });
    } catch (sendError) {
      if (actionId && mutation) {
        this.handleLostDispatch([actionId], sendError);
      }
    }
  }

  handleExtensionEvent(event, payload = {}) {
    const safePayload =
      payload && typeof payload === "object" ? payload : { value: payload };
    try {
      assertNoRawBrowserIdentifiers(safePayload);
    } catch {
      return;
    }
    this.forwardToSidePanel({
      type: "agentyc.event",
      event,
      payload: safePayload,
    });
    if (this.native.connected) {
      try {
        this.native.sendEvent(event, safePayload, {
          space_id: safePayload.space_id,
          page_id: safePayload.page_id,
        });
      } catch {
        // Transport loss is handled by NativeMessagingClient without replay.
      }
    }
  }

  handleDebuggerEvent(payload) {
    if (!payload) return;
    this.handleExtensionEvent(payload.event ?? "debugger.event", payload);
  }

  forwardHostEvent(message) {
    const payload = message.payload ?? message;
    try {
      assertNoRawBrowserIdentifiers(payload);
    } catch {
      return;
    }
    this.forwardToSidePanel({
      type: "agentyc.host_event",
      event: message.event ?? "host.event",
      payload,
    });
  }

  forwardToSidePanel(message) {
    try {
      this.chrome?.runtime?.sendMessage?.(message);
    } catch {
      // The side panel is optional and may not be open.
    }
  }

  async handleRuntimeMessage(message, sender) {
    if (!message || typeof message !== "object") return undefined;
    if (
      sender?.id &&
      this.chrome?.runtime?.id &&
      sender.id !== this.chrome.runtime.id
    )
      return undefined;
    if (message.type === "agentyc.content.result")
      return this.handleContentResult(message);
    if (message.type === "agentyc.sidepanel.request")
      return this.handleSidePanelRequest(message);
    return undefined;
  }

  handleContentResult(message) {
    const pending = this.contentPending.get(message.request_id);
    if (!pending)
      return {
        ok: false,
        error: errorResult("stale_request", "content result is not pending"),
      };
    this.contentPending.delete(message.request_id);
    this.handleExtensionEvent("content.result", {
      space_id: pending.spaceId,
      page_id: pending.pageId,
      request_id: message.request_id,
      ok: Boolean(message.ok),
      ...(message.ok
        ? { result: message.result ?? {} }
        : { error: message.error }),
    });
    return { ok: true };
  }

  handleSidePanelRequest(message) {
    const action = message.action;
    const allowed = new Set([
      "create",
      "pause",
      "stop",
      "takeover",
      "return_control",
      "handoff",
      "finish",
      "retain",
      "release",
    ]);
    if (!allowed.has(action))
      return {
        ok: false,
        error: errorResult(
          "capability_unavailable",
          "side-panel action is not allowlisted",
        ),
      };
    const params =
      message.params && typeof message.params === "object"
        ? message.params
        : {};
    try {
      assertNoRawBrowserIdentifiers(params);
      const requestId = createLogicalId("req");
      const method = `space.${action}`;
      const actionId = createLogicalId("action");
      this.native.sendRequest({
        method,
        params,
        requestId,
        actionId,
        mutation: true,
      });
      return { ok: true, request_id: requestId };
    } catch (error) {
      return { ok: false, error: publicError(error) };
    }
  }
}

export function createServiceWorker(options = {}) {
  return new ServiceWorkerController(options);
}

export async function installServiceWorker(chromeApi = globalThis.chrome) {
  const controller = createServiceWorker({ chromeApi });
  await controller.start();
  return controller;
}

// Chrome loads this module as the MV3 service worker. Node tests import the
// factory instead; the protocol guard avoids side effects in those tests.
if (
  globalThis.location?.protocol === "chrome-extension:" &&
  globalThis.chrome?.runtime?.connectNative
) {
  const controller = createServiceWorker({ chromeApi: globalThis.chrome });
  globalThis.__agentycServiceWorker = controller;
  void controller.start();
}
