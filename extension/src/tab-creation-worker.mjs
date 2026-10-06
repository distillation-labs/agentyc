import { createLogicalId } from "./protocol.mjs";
import { NativeMessagingClient } from "./native-messaging.mjs";

const METADATA_KEY = "agentyc_extension_metadata";
const BOOTSTRAP_URL =
  /^about:blank#agentyc-tab=(page_[a-z0-9][a-z0-9._:-]{0,126})-([1-9][0-9]*)$/;

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

export async function handleTabCreationRequest(
  message,
  { chromeApi = globalThis.chrome, nativeClient },
) {
  if (message?.kind !== "request") return;
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
    await tabs.create.call(tabs, {
      url: params.bootstrap_url,
      active: false,
    });
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
    this.native ??= new NativeMessagingClient({
      chromeApi: this.chrome,
      profileInstanceId: this.identity.profileInstanceId,
      workerInstanceEpoch: this.identity.workerInstanceEpoch,
      browserSessionEpoch: this.identity.browserSessionEpoch,
      extensionVersion:
        this.chrome?.runtime?.getManifest?.().version ?? "0.1.0",
      requestedCapabilities: [],
      autoReconnect: this.autoReconnect,
      onMessage: (message) => {
        void handleTabCreationRequest(message, {
          chromeApi: this.chrome,
          nativeClient: this.native,
        }).catch((error) => {
          console.error("agentyc tab request handling failed", error);
        });
      },
    });
    this.native.profileInstanceId = this.identity.profileInstanceId;
    this.native.workerInstanceEpoch = this.identity.workerInstanceEpoch;
    this.native.browserSessionEpoch = this.identity.browserSessionEpoch;
    this.native.requestedCapabilities = [];
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
    this.startPromise = undefined;
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
