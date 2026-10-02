const PROTOCOL_VERSION = 1;
const NATIVE_HOST = "com.agentyc.p0_probe";
const FIXTURE_TITLE = "agentyc P0 probe fixture";
const MAX_NATIVE_BYTES = 64 * 1024;

function extensionOrigin() {
  return `chrome-extension://${chrome.runtime.id}`;
}

function safeResult(result) {
  return {
    ok: Boolean(result.ok),
    extension_version: chrome.runtime.getManifest().version,
    permissions: chrome.runtime.getManifest().permissions ?? [],
    debugger_command: result.debugger_command ?? "not_run",
    debugger_event: result.debugger_event ?? "not_observed",
    tab_group: result.tab_group ?? "not_run",
    native_messaging: result.native_messaging ?? "not_run",
    native_error: result.native_error ?? null,
    limitation: result.limitation ?? null,
  };
}

async function findFixture(tabId) {
  const tabs = await chrome.tabs.query({});
  const tab = tabs.find((candidate) => candidate.id === tabId);
  if (!tab || tab.title !== FIXTURE_TITLE || !tab.url?.startsWith("file://")) {
    throw new Error("refusing to mutate a non-probe tab");
  }
  return tab;
}

function waitForDebuggerEvent(tabId, method, timeoutMs = 1500) {
  return new Promise((resolve) => {
    let finished = false;
    const finish = (value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      chrome.debugger.onEvent.removeListener(onEvent);
      resolve(value);
    };
    const onEvent = (source, eventMethod) => {
      if (source.tabId === tabId && eventMethod === method) finish(true);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    chrome.debugger.onEvent.addListener(onEvent);
  });
}

async function runDebuggerProbe(tab) {
  const target = { tabId: tab.id };
  let attached = false;
  try {
    await chrome.debugger.attach(target, "1.3");
    attached = true;
    await chrome.debugger.sendCommand(target, "Runtime.enable");
    const evaluation = await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
      expression: "document.title",
      returnByValue: true,
    });
    if (evaluation?.result?.result?.value !== FIXTURE_TITLE) {
      throw new Error("fixture title did not round-trip");
    }
    const eventPromise = waitForDebuggerEvent(tab.id, "Page.loadEventFired");
    await chrome.debugger.sendCommand(target, "Page.enable");
    await chrome.debugger.sendCommand(target, "Page.reload", { ignoreCache: true });
    const eventSeen = await eventPromise;
    return {
      debugger_command: "Runtime.evaluate",
      debugger_event: eventSeen ? "Page.loadEventFired" : "not_observed",
    };
  } finally {
    if (attached) {
      try {
        await chrome.debugger.detach(target);
      } catch (_) {
        // Detach failure is reported by the outer probe result, not hidden.
      }
    }
  }
}

async function runTabGroupProbe(tab) {
  const groupId = await chrome.tabs.group({ tabIds: [tab.id] });
  await chrome.tabGroups.update(groupId, { title: "agentyc P0 probe" });
  return "created_and_named";
}

function sendNativeEnvelope(tab) {
  return new Promise((resolve) => {
    let port;
    try {
      port = chrome.runtime.connectNative(NATIVE_HOST);
    } catch (error) {
      resolve({ status: "unavailable", error: String(error) });
      return;
    }
    const nonce = crypto.randomUUID();
    const envelope = {
      version: PROTOCOL_VERSION,
      origin: extensionOrigin(),
      message_id: crypto.randomUUID(),
      nonce,
      kind: "probe",
      payload: { fixture: FIXTURE_TITLE },
    };
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      try { port.disconnect(); } catch (_) {}
      resolve(value);
    };
    const timer = setTimeout(() => finish({ status: "timeout" }), 1500);
    port.onMessage.addListener((message) => {
      clearTimeout(timer);
      if (message?.accepted === true && message?.message_id === envelope.message_id) {
        finish({ status: "accepted" });
      } else {
        finish({ status: "rejected" });
      }
    });
    port.onDisconnect.addListener(() => {
      clearTimeout(timer);
      const error = chrome.runtime.lastError?.message ?? "host disconnected";
      finish({ status: "unavailable", error });
    });
    const encodedSize = new TextEncoder().encode(JSON.stringify(envelope)).byteLength;
    if (encodedSize > MAX_NATIVE_BYTES) {
      finish({ status: "rejected", error: "probe envelope exceeds local bound" });
      return;
    }
    port.postMessage(envelope);
  });
}

async function runProbe(tabId) {
  const result = { ok: false };
  try {
    const tab = await findFixture(tabId);
    const debuggerResult = await runDebuggerProbe(tab);
    result.debugger_command = debuggerResult.debugger_command;
    result.debugger_event = debuggerResult.debugger_event;
    result.tab_group = await runTabGroupProbe(tab);
    const nativeResult = await sendNativeEnvelope(tab);
    result.native_messaging = nativeResult.status;
    result.native_error = nativeResult.error ?? null;
    result.ok = result.debugger_event !== "not_observed" && result.tab_group === "created_and_named" && result.native_messaging === "accepted";
    if (result.native_messaging !== "accepted") {
      result.limitation = "Native host is not installed or did not accept the exact extension origin.";
    }
  } catch (error) {
    result.native_error = String(error);
    result.limitation = "Live probe failed closed before reporting success.";
  }
  const output = safeResult(result);
  await chrome.storage.local.set({ last_probe: output });
  return output;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || message?.type !== "run-probe") return false;
  runProbe(message.tab_id).then(sendResponse).catch((error) => sendResponse({
    ok: false,
    limitation: String(error),
  }));
  return true;
});
