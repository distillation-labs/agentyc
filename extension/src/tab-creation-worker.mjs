import { createLogicalId } from "./protocol.mjs";
import { NativeMessagingClient } from "./native-messaging.mjs";
import { createGroupsRegistry } from "./groups.mjs";

const METADATA_KEY = "agentyc_extension_metadata";
const SESSION_STATE_KEY = "agentyc_tab_group_state";
const BOOTSTRAP_URL =
  /^about:blank#agentyc-tab=(page_[a-z0-9][a-z0-9_-]{0,126})-([1-9][0-9]*)$/;
const SPACE_ID = /^space_[a-z0-9][a-z0-9_-]{0,126}$/;
const PAGE_ID = /^page_[a-z0-9][a-z0-9_-]{0,126}$/;

function isSafeEpoch(value) {
  return Number.isSafeInteger(value) && value >= 1;
}

function validateBootstrapUrl(value) {
  if (typeof value !== "string" || value.length > 1024) return false;
  const match = BOOTSTRAP_URL.exec(value);
  return Boolean(match && Number.isSafeInteger(Number(match[2])));
}

function sendResponse(nativeClient, message, ok, result, error) {
  nativeClient.sendResponse({
    requestId: message.request_id,
    actionId: message.action_id,
    ok,
    result,
    error,
  });
}

function isLogicalId(value, pattern) {
  return typeof value === "string" && pattern.test(value);
}

function validGroupParams(params) {
  if (!params || typeof params !== "object" || Array.isArray(params)) return false;
  const keys = Object.keys(params).sort();
  if (keys.join(",") !== "lease_epoch,page_id,space_id,title") return false;
  return (
    isLogicalId(params.space_id, SPACE_ID) &&
    isLogicalId(params.page_id, PAGE_ID) &&
    Number.isSafeInteger(params.lease_epoch) &&
    params.lease_epoch >= 1 &&
    typeof params.title === "string" &&
    params.title.length > 0 &&
    params.title.length <= 128
  );
}

function validSessionState(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (!value.pages || typeof value.pages !== "object" || Array.isArray(value.pages))
    return false;
  if (!value.groups || typeof value.groups !== "object" || Array.isArray(value.groups))
    return false;
  return true;
}

export async function handleTabCreationRequest(
  message,
  {
    chromeApi = globalThis.chrome,
    nativeClient,
    pageTabs = new Map(),
    groups,
    onStateChange = async () => {},
  },
) {
  if (message?.kind !== "request") return;
  if (message.method === "group.present") {
    if (!validGroupParams(message.params)) {
      sendResponse(nativeClient, message, false, undefined, {
        code: "invalid_argument",
        message: "tab-group presentation request is invalid",
      });
      return;
    }
    const rawTabId = pageTabs.get(message.params.page_id);
    if (!Number.isInteger(rawTabId) || !groups) {
      sendResponse(nativeClient, message, false, undefined, {
        code: "capability_unavailable",
        message: "the managed page is not available for tab grouping",
      });
      return;
    }
    try {
      const hint = await groups.presentSpace({
        spaceId: message.params.space_id,
        rawTabId,
        title: message.params.title,
        required: true,
      });
      if (hint.present !== true) throw new Error("Chrome did not create a tab group");
      await onStateChange();
      sendResponse(nativeClient, message, true, { grouped: true });
    } catch (error) {
      sendResponse(nativeClient, message, false, undefined, {
        code: "capability_unavailable",
        message: error instanceof Error ? error.message : "tab grouping failed",
      });
    }
    return;
  }
  if (message.method !== "tab.create") {
    sendResponse(nativeClient, message, false, undefined, {
      code: "capability_unavailable",
      message: "only host-requested tab creation is supported",
    });
    return;
  }
  const params = message.params;
  if (
    !params ||
    typeof params !== "object" ||
    Array.isArray(params) ||
    Object.keys(params).length !== 1 ||
    !Object.hasOwn(params, "bootstrap_url") ||
    !validateBootstrapUrl(params.bootstrap_url)
  ) {
    sendResponse(nativeClient, message, false, undefined, {
      code: "invalid_argument",
      message: "tab creation request is invalid",
    });
    return;
  }
  const tabs = chromeApi?.tabs;
  if (typeof tabs?.create !== "function") {
    sendResponse(nativeClient, message, false, undefined, {
      code: "tabs_unavailable",
      message: "Chrome tab creation is unavailable",
    });
    return;
  }
  try {
    const created = await tabs.create.call(tabs, {
      url: params.bootstrap_url,
      active: false,
    });
    const pageId = BOOTSTRAP_URL.exec(params.bootstrap_url)?.[1];
    if (!pageId || !Number.isInteger(created?.id)) {
      throw new Error("Chrome did not return the created tab identity");
    }
    pageTabs.set(pageId, created.id);
    await onStateChange();
  } catch {
    sendResponse(nativeClient, message, false, undefined, {
      code: "unknown_outcome",
      message: "Chrome did not confirm tab creation",
    });
    return;
  }
  sendResponse(nativeClient, message, true, { created: true });
}

async function loadIdentity(chromeApi) {
  const storage = chromeApi?.storage?.local;
  if (typeof storage?.get !== "function" || typeof storage?.set !== "function") {
    throw new Error("Chrome extension local storage is unavailable");
  }
  const values = await storage.get([METADATA_KEY]);
  const stored = values?.[METADATA_KEY] ?? {};
  const profileInstanceId =
    typeof stored.profile_instance_id === "string" &&
    /^profile_[a-z0-9][a-z0-9._:-]{0,127}$/.test(stored.profile_instance_id)
      ? stored.profile_instance_id
      : createLogicalId("profile");
  const previousWorkerEpoch = Number.isSafeInteger(
    stored.worker_instance_epoch,
  )
    ? stored.worker_instance_epoch
    : 0;
  if (previousWorkerEpoch >= Number.MAX_SAFE_INTEGER) {
    throw new Error("extension worker epoch is exhausted");
  }
  const workerInstanceEpoch = Math.max(1, previousWorkerEpoch + 1);
  const browserSessionEpoch = isSafeEpoch(stored.browser_session_epoch)
    ? stored.browser_session_epoch
    : 1;
  const identity = {
    profileInstanceId,
    workerInstanceEpoch,
    browserSessionEpoch,
  };
  await storage.set({
    [METADATA_KEY]: {
      profile_instance_id: profileInstanceId,
      worker_instance_epoch: workerInstanceEpoch,
      browser_session_epoch: browserSessionEpoch,
      extension_version: chromeApi.runtime?.getManifest?.().version ?? "0.1.0",
    },
  });
  return identity;
}

export class TabCreationWorker {
  constructor({
    chromeApi = globalThis.chrome,
    nativeClient,
    autoReconnect = true,
  } = {}) {
    this.chrome = chromeApi;
    this.native = nativeClient;
    this.autoReconnect = autoReconnect;
    this.identity = undefined;
    this.startPromise = undefined;
    this.pageTabs = new Map();
    this.groups = undefined;
    this.onStartup = () => {
      void this.advanceBrowserSession().catch((error) => {
        console.error("agentyc browser-session update failed", error);
      });
    };
    this.chrome?.runtime?.onStartup?.addListener?.(this.onStartup);
  }

  start() {
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.startOnce();
    return this.startPromise;
  }

  async startOnce() {
    this.identity = await loadIdentity(this.chrome);
    this.groups = createGroupsRegistry({
      chromeApi: this.chrome,
      hintSalt: this.identity.profileInstanceId,
    });
    this.groups.start();
    await this.loadSessionState();
    this.native ??= new NativeMessagingClient({
      chromeApi: this.chrome,
      profileInstanceId: this.identity.profileInstanceId,
      workerInstanceEpoch: this.identity.workerInstanceEpoch,
      browserSessionEpoch: this.identity.browserSessionEpoch,
      extensionVersion:
        this.chrome?.runtime?.getManifest?.().version ?? "0.1.0",
      requestedCapabilities: ["visual_groups"],
      autoReconnect: this.autoReconnect,
      onMessage: (message) => {
        void handleTabCreationRequest(message, {
          chromeApi: this.chrome,
          nativeClient: this.native,
          pageTabs: this.pageTabs,
          groups: this.groups,
          onStateChange: () => this.saveSessionState(),
        }).catch((error) => {
          console.error("agentyc tab request handling failed", error);
        });
      },
    });
    this.native.profileInstanceId = this.identity.profileInstanceId;
    this.native.workerInstanceEpoch = this.identity.workerInstanceEpoch;
    this.native.browserSessionEpoch = this.identity.browserSessionEpoch;
    this.native.requestedCapabilities = ["visual_groups"];
    try {
      await this.native.connect();
    } catch (error) {
      console.warn("agentyc Native Messaging connection is retrying", error);
    }
    return this;
  }

  async advanceBrowserSession() {
    await this.start();
    const nextEpoch = this.identity.browserSessionEpoch + 1;
    if (!isSafeEpoch(nextEpoch)) {
      throw new Error("browser session epoch is exhausted");
    }
    await this.chrome.storage.local.set({
      [METADATA_KEY]: {
        profile_instance_id: this.identity.profileInstanceId,
        worker_instance_epoch: this.identity.workerInstanceEpoch,
        browser_session_epoch: nextEpoch,
        extension_version: this.chrome.runtime?.getManifest?.().version ?? "0.1.0",
      },
    });
    this.identity.browserSessionEpoch = nextEpoch;
    this.native.browserSessionEpoch = nextEpoch;
    this.pageTabs.clear();
    this.groups?.clear?.();
    await this.clearSessionState();
    this.native.stop();
    this.native.stopped = false;
    try {
      await this.native.connect();
    } catch (error) {
      console.warn("agentyc Native Messaging reconnect is retrying", error);
    }
  }

  stop() {
    this.chrome?.runtime?.onStartup?.removeListener?.(this.onStartup);
    this.native?.stop?.();
    this.groups?.stop?.();
    this.groups = undefined;
    this.startPromise = undefined;
  }

  async loadSessionState() {
    const storage = this.chrome?.storage?.session;
    if (typeof storage?.get !== "function") return;
    const values = await storage.get([SESSION_STATE_KEY]);
    const state = values?.[SESSION_STATE_KEY];
    if (!validSessionState(state)) return;
    for (const [pageId, tabId] of Object.entries(state.pages)) {
      if (PAGE_ID.test(pageId) && Number.isInteger(tabId) && tabId >= 0)
        this.pageTabs.set(pageId, tabId);
    }
    for (const [spaceId, group] of Object.entries(state.groups)) {
      if (!SPACE_ID.test(spaceId) || !group || typeof group !== "object") continue;
      this.groups?.restoreSpace({
        spaceId,
        rawGroupId: group.group_id,
        rawTabIds: Array.isArray(group.tab_ids) ? group.tab_ids : [],
        title: group.title,
      });
    }
  }

  async saveSessionState() {
    const storage = this.chrome?.storage?.session;
    if (typeof storage?.set !== "function") return;
    const groups = {};
    for (const hint of this.groups?.listHints?.() ?? []) {
      const internal = this.groups.getInternal(hint.space_id);
      if (!internal?.present || !Number.isInteger(internal.rawGroupId)) continue;
      groups[hint.space_id] = {
        group_id: internal.rawGroupId,
        tab_ids: [...internal.claimedTabIds],
        title: internal.title,
      };
    }
    await storage.set({
      [SESSION_STATE_KEY]: {
        pages: Object.fromEntries(this.pageTabs),
        groups,
      },
    });
  }

  async clearSessionState() {
    const storage = this.chrome?.storage?.session;
    if (typeof storage?.set !== "function") return;
    await storage.set({ [SESSION_STATE_KEY]: { pages: {}, groups: {} } });
  }
}

if (
  globalThis.location?.protocol === "chrome-extension:" &&
  globalThis.chrome?.runtime?.connectNative
) {
  const worker = new TabCreationWorker({ chromeApi: globalThis.chrome });
  void worker.start().catch((error) => {
    console.error("agentyc tab-creation worker failed to start", error);
  });
}
