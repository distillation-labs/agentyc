const PROTOCOL_VERSION = 1;
const NATIVE_HOST = "com.agentyc.p0_probe";
const FIXTURE_TITLE = "agentyc P0 probe fixture";
const FIXTURE_MARKER = "agentyc_p0_probe";
const FIXTURE_TEXT =
  "This local fixture contains no user data and is the only tab the live probe may mutate.";
const MAX_NATIVE_BYTES = 64 * 1024;

function extensionOrigin() {
  return `chrome-extension://${chrome.runtime.id}`;
}

function safeResult(result) {
  return {
    ok: Boolean(result.ok),
    extension_loaded: true,
    request_id: result.request_id ?? null,
    debugger_command_passed: result.debugger_command_passed === true,
    debugger_event_received: result.debugger_event_received === true,
    tab_group_created: result.tab_group_created === true,
    native_messaging_passed: result.native_messaging_passed === true,
    cleanup_passed: result.cleanup_passed === true,
    extension_version: chrome.runtime.getManifest().version,
    permissions: chrome.runtime.getManifest().permissions ?? [],
    debugger_command: result.debugger_command ?? "not_run",
    debugger_event: result.debugger_event ?? "not_observed",
    tab_group: result.tab_group ?? "not_run",
    native_messaging: result.native_messaging ?? "not_run",
    native_error: result.native_error ?? null,
    handshake_transcript: Array.isArray(result.handshake_transcript)
      ? result.handshake_transcript.slice(0, 8)
      : [],
    metadata: {
      extension_origin: "redacted",
      chrome_launch: "never",
      chrome_download: "never",
      secrets_logged: false,
    },
    limitation: result.limitation ?? null,
  };
}

async function findFixture(tabId) {
  const tabs = await chrome.tabs.query({});
  const tab =
    tabs.find((candidate) => candidate.id === tabId) ??
    tabs.find((candidate) => {
      try {
        const url = new URL(candidate.url ?? "");
        return (
          candidate.title === FIXTURE_TITLE &&
          url.protocol === "file:" &&
          url.searchParams.get(FIXTURE_MARKER) === "1"
        );
      } catch (_) {
        return false;
      }
    });
  let isFixture = false;
  try {
    const url = new URL(tab?.url ?? "");
    isFixture =
      url.protocol === "file:" &&
      url.pathname.endsWith("/fixture.html") &&
      url.searchParams.get(FIXTURE_MARKER) === "1";
  } catch (_) {
    isFixture = false;
  }
  if (!tab || tab.title !== FIXTURE_TITLE || !isFixture) {
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
    const evaluation = await chrome.debugger.sendCommand(
      target,
      "Runtime.evaluate",
      {
        expression: "document.title",
        returnByValue: true,
      },
    );
    if (evaluation?.result?.result?.value !== FIXTURE_TITLE) {
      throw new Error("fixture title did not round-trip");
    }
    const identity = await chrome.debugger.sendCommand(
      target,
      "Runtime.evaluate",
      {
        expression: `({title: document.title, heading: document.querySelector("h1")?.textContent, body: document.querySelector("p")?.textContent})`,
        returnByValue: true,
      },
    );
    const identityValue = identity?.result?.result?.value;
    if (
      identityValue?.title !== FIXTURE_TITLE ||
      identityValue?.heading !== FIXTURE_TITLE ||
      identityValue?.body !== FIXTURE_TEXT
    ) {
      throw new Error("fixture identity did not round-trip");
    }
    const eventPromise = waitForDebuggerEvent(tab.id, "Page.loadEventFired");
    await chrome.debugger.sendCommand(target, "Page.enable");
    await chrome.debugger.sendCommand(target, "Page.reload", {
      ignoreCache: true,
    });
    const eventSeen = await eventPromise;
    if (!eventSeen) throw new Error("debugger event was not observed");
    return {
      debugger_command: "Runtime.evaluate",
      debugger_command_passed: true,
      debugger_event: "Page.loadEventFired",
      debugger_event_received: true,
    };
  } finally {
    if (attached) {
      try {
        await chrome.debugger.detach(target);
      } catch (_) {
        throw new Error("debugger detach failed");
      }
    }
  }
}

async function runTabGroupProbe(tab) {
  const originalGroupId = typeof tab.groupId === "number" ? tab.groupId : -1;
  const groupId = await chrome.tabs.group({ tabIds: [tab.id] });
  await chrome.tabGroups.update(groupId, { title: "agentyc P0 probe" });
  let cleaned = false;
  try {
    await chrome.tabs.ungroup(tab.id);
    if (originalGroupId >= 0)
      await chrome.tabs.group({ groupId: originalGroupId, tabIds: [tab.id] });
    cleaned = true;
  } catch (_) {
    throw new Error("tab-group cleanup failed");
  }
  return {
    status: cleaned ? "created_and_cleaned" : "cleanup_failed",
    created: true,
    cleaned,
  };
}

function sendNativeEnvelope() {
  return new Promise((resolve) => {
    let port;
    try {
      port = chrome.runtime.connectNative(NATIVE_HOST);
    } catch (_) {
      resolve({ status: "unavailable", error: "connect_failed" });
      return;
    }
    const nonce = crypto.randomUUID();
    const hello = {
      version: PROTOCOL_VERSION,
      origin: extensionOrigin(),
      message_id: crypto.randomUUID(),
      nonce,
      kind: "hello",
      payload: { fixture: FIXTURE_TITLE },
    };
    const probe = {
      version: PROTOCOL_VERSION,
      origin: extensionOrigin(),
      message_id: crypto.randomUUID(),
      nonce,
      kind: "probe",
      payload: { fixture: FIXTURE_TITLE },
    };
    let phase = "hello";
    const transcript = [];
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        port.disconnect();
      } catch (_) {}
      resolve(value);
    };
    const timer = setTimeout(
      () => finish({ status: "timeout", error: "handshake_timeout" }),
      1500,
    );
    port.onMessage.addListener((message) => {
      const expected = phase === "hello" ? hello : probe;
      const expectedPhase = phase;
      if (
        message?.accepted !== true ||
        message?.message_id !== expected.message_id ||
        message?.nonce !== expected.nonce ||
        message?.version !== expected.version ||
        message?.phase !== expectedPhase
      ) {
        finish({ status: "rejected", error: "handshake_rejected" });
        return;
      }
      if (phase === "hello") {
        transcript.push("hello_accepted");
        phase = "probe";
        try {
          port.postMessage(probe);
        } catch (_) {
          finish({ status: "unavailable", error: "probe_send_failed" });
        }
      } else {
        transcript.push("probe_accepted");
        finish({ status: "accepted", transcript });
      }
    });
    port.onDisconnect.addListener(() => {
      if (settled) return;
      void chrome.runtime.lastError;
      finish({ status: "unavailable", error: "host_disconnected" });
    });
    const encodedSize =
      new TextEncoder().encode(JSON.stringify(hello)).byteLength +
      new TextEncoder().encode(JSON.stringify(probe)).byteLength;
    if (encodedSize > MAX_NATIVE_BYTES) {
      finish({
        status: "rejected",
        error: "probe_envelope_exceeds_local_bound",
      });
      return;
    }
    try {
      port.postMessage(hello);
    } catch (_) {
      finish({ status: "unavailable", error: "hello_send_failed" });
    }
  });
}

async function runProbe(tabId, requestId = null) {
  const result = { ok: false, request_id: requestId };
  try {
    const tab = await findFixture(tabId);
    const debuggerResult = await runDebuggerProbe(tab);
    result.debugger_command = debuggerResult.debugger_command;
    result.debugger_command_passed =
      debuggerResult.debugger_command_passed === true;
    result.debugger_event = debuggerResult.debugger_event;
    result.debugger_event_received =
      debuggerResult.debugger_event_received === true;
    const tabGroupResult = await runTabGroupProbe(tab);
    result.tab_group = tabGroupResult.status;
    result.tab_group_created = tabGroupResult.created === true;
    result.cleanup_passed = tabGroupResult.cleaned === true;
    const nativeResult = await sendNativeEnvelope();
    result.native_messaging = nativeResult.status;
    result.native_messaging_passed = nativeResult.status === "accepted";
    result.handshake_transcript = nativeResult.transcript ?? [];
    result.native_error = nativeResult.error ?? null;
    result.ok =
      result.debugger_command_passed &&
      result.debugger_event_received &&
      result.tab_group_created &&
      result.cleanup_passed &&
      result.native_messaging_passed;
    if (result.native_messaging !== "accepted") {
      result.limitation =
        "Native host is not installed or did not accept the exact extension origin.";
    }
  } catch (_) {
    result.native_error = "probe_failed";
    result.limitation = "Live probe failed closed before reporting success.";
  }
  const output = safeResult(result);
  await chrome.storage.local.set({ last_probe: output });
  return output;
}

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes.run_probe?.newValue) return;
  void runProbe(
    changes.run_probe.newValue.tab_id ?? null,
    changes.run_probe.newValue.request_id ?? null,
  );
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || message?.type !== "run-probe")
    return false;
  runProbe(message.tab_id)
    .then(sendResponse)
    .catch(() =>
      sendResponse({
        ok: false,
        limitation: "Live probe failed closed before reporting success.",
      }),
    );
  return true;
});
