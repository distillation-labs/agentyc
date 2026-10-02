const PROTOCOL_VERSION = 1;
const NATIVE_HOST = "com.agentyc.p0_probe";
const FIXTURE_TITLE = "agentyc P0 probe fixture";
const FIXTURE_PATH_SUFFIX = "/extension/probes/fixture.html";
const FIXTURE_TEXT =
  "This local fixture contains no user data and is the only tab the live probe may mutate.";
const MAX_NATIVE_BYTES = 64 * 1024;
const MAX_RESULT_BYTES = 16 * 1024;
const MAX_SCREENSHOT_BASE64_CHARS = 4 * 1024 * 1024;
const MAX_TRANSCRIPT_ENTRIES = 8;
const MAX_PERMISSION_ENTRIES = 16;
const MAX_PERMISSION_LENGTH = 128;
const REQUEST_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[0-9a-f]{64}$/i;
const PROBE_BINDING_FILE = "probe_binding.json";

let activeProbe = null;
let pendingProbe = null;
let probeSequence = 0;
let latestProbeSequence = 0;

function extensionOrigin() {
  return `chrome-extension://${chrome.runtime.id}`;
}

function isRequestId(value) {
  return typeof value === "string" && REQUEST_ID_PATTERN.test(value);
}

function requestIdOrNew(value) {
  return isRequestId(value) ? value : crypto.randomUUID();
}

function boundedString(value, fallback = null) {
  if (typeof value !== "string") return fallback;
  return value.length <= 256 ? value : value.slice(0, 256);
}

function extensionManifest() {
  try {
    const manifest = chrome.runtime.getManifest();
    return manifest && typeof manifest === "object" ? manifest : {};
  } catch (_) {
    return {};
  }
}

function boundedManifestVersion() {
  return boundedString(extensionManifest().version, "unknown") ?? "unknown";
}

function boundedPermissions() {
  const permissions = extensionManifest().permissions;
  if (!Array.isArray(permissions)) return [];
  return permissions
    .filter((permission) => typeof permission === "string")
    .slice(0, MAX_PERMISSION_ENTRIES)
    .map((permission) => permission.slice(0, MAX_PERMISSION_LENGTH));
}

async function readProbeBinding() {
  try {
    if (typeof fetch !== "function") return null;
    const response = await fetch(chrome.runtime.getURL(PROBE_BINDING_FILE), {
      cache: "no-store",
    });
    if (!response.ok) return null;
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength > 1024) return null;
    const value = JSON.parse(new TextDecoder().decode(bytes));
    if (
      !value ||
      !isRequestId(value.nonce) ||
      typeof value.source_tree_sha256 !== "string" ||
      !SHA256_PATTERN.test(value.source_tree_sha256)
    ) {
      return null;
    }
    return {
      nonce: value.nonce,
      source_tree_sha256: value.source_tree_sha256.toLowerCase(),
    };
  } catch (_) {
    return null;
  }
}

function minimalResult(requestId, limitation) {
  return {
    ok: false,
    extension_loaded: true,
    request_id: isRequestId(requestId) ? requestId : null,
    binding_nonce: null,
    binding_source_tree_sha256: null,
    extension_build_binding_passed: false,
    fixture_identity_passed: false,
    debugger_command_passed: false,
    debugger_event_received: false,
    tab_group_created: false,
    native_messaging_passed: false,
    debugger_cleanup_passed: false,
    cleanup_passed: false,
    screenshot_captured: false,
    extension_version: boundedManifestVersion(),
    permissions: boundedPermissions(),
    debugger_command: "not_run",
    debugger_event: "not_observed",
    tab_group: "not_run",
    native_messaging: "not_run",
    native_error: "probe_failed",
    handshake_transcript: [],
    metadata: {
      extension_origin: "redacted",
      chrome_launch: "never",
      chrome_download: "never",
      secrets_logged: false,
    },
    limitation: boundedString(limitation, "Live probe failed closed."),
  };
}

function safeResult(result) {
  const transcript = Array.isArray(result.handshake_transcript)
    ? result.handshake_transcript
        .filter(
          (entry) => entry === "hello_accepted" || entry === "probe_accepted",
        )
        .slice(0, MAX_TRANSCRIPT_ENTRIES)
    : [];
  const output = {
    ok: result.ok === true,
    extension_loaded: result.extension_loaded === true,
    request_id: isRequestId(result.request_id) ? result.request_id : null,
    binding_nonce: isRequestId(result.binding_nonce)
      ? result.binding_nonce
      : null,
    binding_source_tree_sha256:
      typeof result.binding_source_tree_sha256 === "string" &&
      SHA256_PATTERN.test(result.binding_source_tree_sha256)
        ? result.binding_source_tree_sha256.toLowerCase()
        : null,
    extension_build_binding_passed:
      result.extension_build_binding_passed === true,
    fixture_identity_passed: result.fixture_identity_passed === true,
    debugger_command_passed: result.debugger_command_passed === true,
    debugger_event_received: result.debugger_event_received === true,
    tab_group_created: result.tab_group_created === true,
    native_messaging_passed: result.native_messaging_passed === true,
    debugger_cleanup_passed: result.debugger_cleanup_passed === true,
    cleanup_passed: result.cleanup_passed === true,
    screenshot_captured: result.screenshot_captured === true,
    extension_version: boundedManifestVersion(),
    permissions: boundedPermissions(),
    debugger_command: boundedString(result.debugger_command, "not_run"),
    debugger_event: boundedString(result.debugger_event, "not_observed"),
    tab_group: boundedString(result.tab_group, "not_run"),
    native_messaging: boundedString(result.native_messaging, "not_run"),
    native_error: boundedString(result.native_error),
    handshake_transcript: transcript,
    metadata: {
      extension_origin: "redacted",
      chrome_launch: "never",
      chrome_download: "never",
      secrets_logged: false,
    },
    limitation: boundedString(result.limitation),
  };
  try {
    if (
      new TextEncoder().encode(JSON.stringify(output)).byteLength <=
      MAX_RESULT_BYTES
    ) {
      return output;
    }
  } catch (_) {
    // Return the bounded failure below.
  }
  return minimalResult(
    output.request_id,
    "Probe result exceeded the local handoff bound.",
  );
}

function isCanonicalFixture(tab, expectedUrl = null) {
  if (!tab || !Number.isInteger(tab.id) || tab.title !== FIXTURE_TITLE)
    return false;
  if (typeof expectedUrl !== "string") return false;
  try {
    const expected = new URL(expectedUrl);
    return (
      expected.protocol === "file:" &&
      expected.hostname === "" &&
      expected.search === "" &&
      expected.hash === "" &&
      expected.pathname.endsWith(FIXTURE_PATH_SUFFIX) &&
      tab.url === expectedUrl
    );
  } catch (_) {
    return false;
  }
}

async function findFixture(expectedUrl = null) {
  const tabs = await chrome.tabs.query({});
  const fixtures = tabs.filter((tab) => isCanonicalFixture(tab, expectedUrl));
  if (fixtures.length !== 1) {
    throw new Error(
      fixtures.length === 0
        ? "canonical probe fixture was not observed"
        : "canonical probe fixture is ambiguous",
    );
  }
  return fixtures[0];
}

function waitForDebuggerEvent(tabId, method, timeoutMs = 1500) {
  return new Promise((resolve) => {
    let finished = false;
    let timer = null;
    const finish = (value) => {
      if (finished) return;
      finished = true;
      if (timer !== null) clearTimeout(timer);
      try {
        chrome.debugger.onEvent.removeListener(onEvent);
      } catch (_) {}
      resolve(value);
    };
    const onEvent = (source, eventMethod) => {
      if (source?.tabId === tabId && eventMethod === method) finish(true);
    };
    chrome.debugger.onEvent.addListener(onEvent);
    timer = setTimeout(() => finish(false), timeoutMs);
  });
}

function evaluatedValue(response) {
  if (!response || response.exceptionDetails) {
    throw new Error("debugger evaluation failed");
  }
  const remote = response.result;
  if (!remote || !Object.prototype.hasOwnProperty.call(remote, "value")) {
    throw new Error("debugger evaluation returned no value");
  }
  return remote.value;
}

async function runDebuggerProbe(tab, expectedUrl = null) {
  const target = { tabId: tab.id };
  let attached = false;
  let operationError = null;
  let detachError = false;
  let output = null;
  try {
    await chrome.debugger.attach(target, "1.3");
    attached = true;
    await chrome.debugger.sendCommand(target, "Runtime.enable");
    const identity = evaluatedValue(
      await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
        expression:
          "(() => ({ url: location.href, protocol: location.protocol, pathname: location.pathname, search: location.search, hash: location.hash, title: document.title, heading: document.querySelector('h1')?.textContent, body: document.querySelector('p')?.textContent, body_child_count: document.body?.children.length }))()",
        returnByValue: true,
      }),
    );
    if (
      (expectedUrl
        ? identity?.url !== expectedUrl
        : identity?.protocol !== "file:") ||
      (!expectedUrl && !identity.pathname?.endsWith(FIXTURE_PATH_SUFFIX)) ||
      identity.search !== "" ||
      identity.hash !== "" ||
      identity.title !== FIXTURE_TITLE ||
      identity.heading !== FIXTURE_TITLE ||
      identity.body !== FIXTURE_TEXT ||
      identity.body_child_count !== 2
    ) {
      throw new Error("canonical fixture identity did not round-trip");
    }
    const eventPromise = waitForDebuggerEvent(tab.id, "Page.loadEventFired");
    await chrome.debugger.sendCommand(target, "Page.enable");
    await chrome.debugger.sendCommand(target, "Page.reload", {
      ignoreCache: true,
    });
    const eventSeen = await eventPromise;
    if (!eventSeen) throw new Error("debugger event was not observed");
    const revalidated = await findFixture(expectedUrl);
    if (revalidated.id !== tab.id) {
      throw new Error("fixture tab identity changed during debugger probe");
    }
    const screenshot = await chrome.debugger.sendCommand(
      target,
      "Page.captureScreenshot",
      { format: "png" },
    );
    if (
      typeof screenshot?.data !== "string" ||
      screenshot.data.length === 0 ||
      screenshot.data.length > MAX_SCREENSHOT_BASE64_CHARS
    ) {
      throw new Error("bounded screenshot capture failed");
    }
    output = {
      debugger_command: "Runtime.evaluate",
      debugger_command_passed: true,
      debugger_event: "Page.loadEventFired",
      debugger_event_received: true,
      fixture_identity_passed: true,
      debugger_cleanup_passed: true,
      screenshot_captured: true,
    };
  } catch (error) {
    operationError = error;
  } finally {
    if (attached) {
      try {
        await chrome.debugger.detach(target);
      } catch (_) {
        detachError = true;
      }
    }
  }
  if (detachError) throw new Error("debugger detach failed");
  if (operationError) throw operationError;
  if (!output) throw new Error("debugger probe returned no result");
  return output;
}

async function runTabGroupProbe(tab, expectedUrl = null) {
  const before = await findFixture(expectedUrl);
  if (before.id !== tab.id) {
    throw new Error("fixture tab identity changed before tab-group mutation");
  }
  const originalGroupId =
    Number.isInteger(before.groupId) && before.groupId >= 0
      ? before.groupId
      : -1;
  let tabChanged = false;
  const onUpdated = (updatedTabId, changeInfo) => {
    if (
      updatedTabId === tab.id &&
      (typeof changeInfo?.url === "string" || changeInfo?.status === "loading")
    ) {
      tabChanged = true;
    }
  };
  chrome.tabs.onUpdated.addListener(onUpdated);
  const assertStableFixture = async () => {
    if (tabChanged)
      throw new Error("fixture tab changed during tab-group probe");
    const current = await findFixture(expectedUrl);
    if (current.id !== tab.id)
      throw new Error("fixture tab identity changed during tab-group probe");
    return current;
  };
  let groupId = null;
  let grouped = false;
  let ungrouped = false;
  let restored = originalGroupId < 0;
  let operationError = null;
  let cleanupError = null;
  try {
    await assertStableFixture();
    groupId = await chrome.tabs.group({ tabIds: [tab.id] });
    grouped = true;
    if (!Number.isInteger(groupId) || groupId < 0) {
      throw new Error("tab-group creation returned an invalid group");
    }
    await chrome.tabGroups.update(groupId, { title: "agentyc P0 probe" });
    const groupedTab = await assertStableFixture();
    if (groupedTab.groupId !== groupId) {
      throw new Error("fixture tab group changed before cleanup");
    }
  } catch (error) {
    operationError = error;
  } finally {
    if (grouped) {
      try {
        const groupedTab = await assertStableFixture();
        if (groupedTab.groupId !== groupId) {
          throw new Error("fixture tab group ownership changed before cleanup");
        }
        await chrome.tabs.ungroup(tab.id);
        ungrouped = true;
      } catch (error) {
        cleanupError = error;
      }
      if (originalGroupId >= 0) {
        try {
          const ungroupedTab = await assertStableFixture();
          if (ungroupedTab.groupId !== -1) {
            throw new Error("fixture tab was regrouped by another actor");
          }
          await chrome.tabs.group({
            groupId: originalGroupId,
            tabIds: [tab.id],
          });
          restored = true;
        } catch (error) {
          cleanupError = cleanupError ?? error;
        }
      } else {
        restored = ungrouped;
      }
      try {
        const after = await assertStableFixture();
        if (after.id !== tab.id) {
          cleanupError =
            cleanupError ??
            new Error("fixture tab identity changed during tab-group cleanup");
        }
      } catch (error) {
        cleanupError = cleanupError ?? error;
      }
    }
  }
  try {
    chrome.tabs.onUpdated.removeListener(onUpdated);
  } catch (error) {
    cleanupError = cleanupError ?? error;
  }
  if (operationError || cleanupError || !restored) {
    throw new Error("tab-group probe or restoration failed closed");
  }
  return {
    status: "created_and_cleaned",
    created: true,
    cleaned: true,
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
    let timer = null;
    let onMessage;
    let onDisconnect;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      try {
        if (onMessage) port.onMessage.removeListener(onMessage);
        if (onDisconnect) port.onDisconnect.removeListener(onDisconnect);
        port.disconnect();
      } catch (_) {}
      resolve(value);
    };
    const expectedMessage = () => (phase === "hello" ? hello : probe);
    onMessage = (message) => {
      const expected = expectedMessage();
      const expectedPhase = phase;
      const responseKeys = [
        "accepted",
        "kind",
        "message_id",
        "phase",
        "nonce",
        "version",
      ];
      const actualKeys =
        message && typeof message === "object"
          ? Object.keys(message).sort()
          : [];
      if (
        actualKeys.join("|") !== responseKeys.slice().sort().join("|") ||
        message?.accepted !== true ||
        message?.kind !== "ack" ||
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
    };
    onDisconnect = () => {
      if (settled) return;
      void chrome.runtime.lastError;
      finish({ status: "unavailable", error: "host_disconnected" });
    };
    try {
      port.onMessage.addListener(onMessage);
      port.onDisconnect.addListener(onDisconnect);
      const helloBytes = new TextEncoder().encode(
        JSON.stringify(hello),
      ).byteLength;
      const probeBytes = new TextEncoder().encode(
        JSON.stringify(probe),
      ).byteLength;
      if (
        helloBytes > MAX_NATIVE_BYTES ||
        probeBytes > MAX_NATIVE_BYTES ||
        helloBytes + probeBytes > MAX_NATIVE_BYTES
      ) {
        finish({
          status: "rejected",
          error: "probe_envelope_exceeds_local_bound",
        });
        return;
      }
      timer = setTimeout(
        () => finish({ status: "timeout", error: "handshake_timeout" }),
        1500,
      );
      port.postMessage(hello);
    } catch (_) {
      finish({ status: "unavailable", error: "hello_send_failed" });
    }
  });
}

async function runProbe(requestId, expectedUrl = null) {
  const result = {
    ok: false,
    extension_loaded: true,
    request_id: requestId,
  };
  try {
    const binding = await readProbeBinding();
    result.binding_nonce = binding?.nonce ?? null;
    result.binding_source_tree_sha256 = binding?.source_tree_sha256 ?? null;
    const tab = await findFixture(expectedUrl);
    const debuggerResult = await runDebuggerProbe(tab, expectedUrl);
    result.debugger_command = debuggerResult.debugger_command;
    result.debugger_command_passed =
      debuggerResult.debugger_command_passed === true;
    result.debugger_event = debuggerResult.debugger_event;
    result.debugger_event_received =
      debuggerResult.debugger_event_received === true;
    result.fixture_identity_passed =
      debuggerResult.fixture_identity_passed === true;
    result.debugger_cleanup_passed =
      debuggerResult.debugger_cleanup_passed === true;
    result.screenshot_captured = debuggerResult.screenshot_captured === true;
    if (!result.debugger_cleanup_passed) {
      throw new Error("debugger cleanup failed");
    }

    const tabGroupResult = await runTabGroupProbe(tab, expectedUrl);
    result.tab_group = tabGroupResult.status;
    result.tab_group_created = tabGroupResult.created === true;
    result.cleanup_passed = tabGroupResult.cleaned === true;

    const nativeResult = await sendNativeEnvelope();
    result.native_messaging = nativeResult.status;
    result.native_messaging_passed = nativeResult.status === "accepted";
    result.handshake_transcript = nativeResult.transcript ?? [];
    result.native_error = nativeResult.error ?? null;
    result.ok =
      result.fixture_identity_passed &&
      result.debugger_command_passed &&
      result.debugger_event_received &&
      result.tab_group_created &&
      result.cleanup_passed &&
      result.debugger_cleanup_passed &&
      result.screenshot_captured &&
      result.native_messaging_passed;
    if (result.native_messaging !== "accepted") {
      result.limitation =
        "Native host is not installed or did not accept the exact extension origin.";
    }
  } catch (_) {
    result.native_error = result.native_error ?? "probe_failed";
    result.limitation = "Live probe failed closed before reporting success.";
  }
  return safeResult(result);
}

function failedResult(requestId, limitation) {
  return safeResult({
    ok: false,
    extension_loaded: true,
    request_id: requestId,
    limitation,
  });
}

async function persistResult(output) {
  let boundedOutput = output;
  try {
    const encoded = new TextEncoder().encode(JSON.stringify(output));
    if (encoded.byteLength > MAX_RESULT_BYTES) {
      boundedOutput = failedResult(
        output.request_id,
        "Probe result exceeded the local handoff bound.",
      );
    }
    await chrome.storage.local.set({ last_probe: boundedOutput });
    return boundedOutput;
  } catch (_) {
    return failedResult(
      output.request_id,
      "Probe result handoff failed closed.",
    );
  }
}

function makeProbeEntry(requestId, fixtureUrl = null) {
  let resolve;
  const promise = new Promise((finish) => {
    resolve = finish;
  });
  return {
    requestId,
    fixtureUrl,
    sequence: ++probeSequence,
    promise,
    resolve,
  };
}

async function executeProbeEntry(entry) {
  activeProbe = entry;
  let output;
  try {
    output = await runProbe(entry.requestId, entry.fixtureUrl);
    if (entry.sequence === latestProbeSequence) {
      output = await persistResult(output);
    }
  } catch (_) {
    output = failedResult(
      entry.requestId,
      "Live probe failed closed before reporting success.",
    );
    if (entry.sequence === latestProbeSequence) {
      output = await persistResult(output);
    }
  }
  entry.resolve(output);
  if (activeProbe === entry) activeProbe = null;
  if (pendingProbe) {
    const next = pendingProbe;
    pendingProbe = null;
    void executeProbeEntry(next);
  }
}

function scheduleProbe(requestId, fixtureUrl = null) {
  const normalizedRequestId = requestIdOrNew(requestId);
  if (activeProbe?.requestId === normalizedRequestId)
    return activeProbe.promise;
  if (pendingProbe?.requestId === normalizedRequestId)
    return pendingProbe.promise;
  if (activeProbe && pendingProbe) {
    return Promise.resolve(
      failedResult(
        normalizedRequestId,
        "Another probe is already queued; refusing an unbounded trigger.",
      ),
    );
  }
  const entry = makeProbeEntry(normalizedRequestId, fixtureUrl);
  latestProbeSequence = entry.sequence;
  if (!activeProbe) {
    void executeProbeEntry(entry);
    return entry.promise;
  }
  pendingProbe = entry;
  return entry.promise;
}

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes.run_probe?.newValue) return;
  const trigger = changes.run_probe.newValue;
  if (!trigger || typeof trigger !== "object") return;
  void scheduleProbe(trigger.request_id, trigger.fixture_url);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender?.id !== chrome.runtime.id || message?.type !== "run-probe")
    return false;
  void scheduleProbe(message.request_id, message.fixture_url).then(
    sendResponse,
    () =>
      sendResponse(
        failedResult(
          message.request_id,
          "Live probe failed closed before reporting success.",
        ),
      ),
  );
  return true;
});
