import {
  MAX_CONTROL_BYTES,
  ProtocolError,
  SequenceValidator,
  assertBoundedEnvelope,
  createLogicalId,
  createNonce,
  errorResult,
  makeEnvelope,
  publicError,
  validateEnvelope,
} from "./protocol.mjs";

const DEFAULT_HOST_NAME = "com.agentyc.host";
const DEFAULT_RECONNECT_MS = 1000;
const MAX_RECONNECT_MS = 30_000;

function chromeApiOrGlobal(chromeApi) {
  return chromeApi ?? globalThis.chrome;
}

function addListener(event, listener) {
  if (event?.addListener) event.addListener(listener);
  return () => event?.removeListener?.(listener);
}

export class NativeMessagingClient {
  constructor({
    chromeApi,
    hostName = DEFAULT_HOST_NAME,
    profileInstanceId,
    workerInstanceEpoch,
    browserSessionEpoch,
    extensionVersion = "0.1.0",
    autoReconnect = true,
    reconnectDelayMs = DEFAULT_RECONNECT_MS,
    setTimeoutFn = globalThis.setTimeout,
    clearTimeoutFn = globalThis.clearTimeout,
    onMessage = () => {},
    onStateChange = () => {},
    onUnknownActions = () => {},
  } = {}) {
    this.chrome = chromeApiOrGlobal(chromeApi);
    this.hostName = hostName;
    this.profileInstanceId = profileInstanceId;
    this.workerInstanceEpoch = workerInstanceEpoch;
    this.browserSessionEpoch = browserSessionEpoch;
    this.extensionVersion = extensionVersion;
    this.autoReconnect = autoReconnect;
    this.reconnectDelayMs = reconnectDelayMs;
    this.setTimeoutFn = setTimeoutFn;
    this.clearTimeoutFn = clearTimeoutFn;
    this.onMessage = onMessage;
    this.onStateChange = onStateChange;
    this.onUnknownActions = onUnknownActions;

    this.state = "disconnected";
    this.port = null;
    this.nonce = null;
    this.brokerEpoch = undefined;
    this.connectionEpoch = undefined;
    this.outboundSequence = 1;
    this.inboundSequence = new SequenceValidator(1);
    this.pendingMutations = new Set();
    this.reconnectTimer = null;
    this.reconnectAttempt = 0;
    this.stopped = false;
    this.disconnecting = false;
    this.removePortListeners = [];
    this.connectPromise = null;
  }

  get connected() {
    return this.state === "connected";
  }

  get connectionInfo() {
    return {
      state: this.state,
      brokerEpoch: this.brokerEpoch,
      connectionEpoch: this.connectionEpoch,
      workerInstanceEpoch: this.workerInstanceEpoch,
      browserSessionEpoch: this.browserSessionEpoch,
      hasPort: this.port !== null,
    };
  }

  async connect() {
    if (this.connected || this.state === "handshaking")
      return this.connectionInfo;
    if (this.connectPromise) return this.connectPromise;
    if (!this.chrome?.runtime?.connectNative) {
      const error = new ProtocolError(
        "native_host_unavailable",
        "Chrome Native Messaging is unavailable",
      );
      this.transition("disconnected", error);
      throw error;
    }

    this.stopped = false;
    this.clearReconnectTimer();
    this.connectPromise = Promise.resolve()
      .then(() => {
        this.cleanupPortListeners();
        this.state = "handshaking";
        this.nonce = createNonce();
        this.brokerEpoch = undefined;
        this.connectionEpoch = undefined;
        this.outboundSequence = 1;
        this.inboundSequence.reset(1);
        this.pendingMutations.clear();

        let port;
        try {
          port = this.chrome.runtime.connectNative(this.hostName);
        } catch (error) {
          this.handleDisconnect(error);
          throw new ProtocolError(
            "native_host_unavailable",
            "Native Messaging connection failed",
            {
              cause: error instanceof Error ? error.message : String(error),
            },
          );
        }
        this.port = port;
        this.removePortListeners = [
          addListener(port?.onMessage, (message) =>
            this.handleIncoming(message),
          ),
          addListener(port?.onDisconnect, () => {
            const runtimeError = this.chrome?.runtime?.lastError;
            this.handleDisconnect(
              runtimeError ?? new Error("Native Messaging port disconnected"),
            );
          }),
        ];

        this.postHello();
        this.transition("handshaking");
        return this.connectionInfo;
      })
      .finally(() => {
        this.connectPromise = null;
      });
    return this.connectPromise;
  }

  postHello() {
    this.postEnvelope(
      makeEnvelope("hello", {
        nonce: this.nonce,
        sequence: this.outboundSequence++,
        worker_instance_epoch: this.workerInstanceEpoch,
        browser_session_epoch: this.browserSessionEpoch,
        profile_instance_id: this.profileInstanceId,
        extension_version: this.extensionVersion,
        capabilities: [
          "debugger_allowlist",
          "logical_tabs",
          "visual_groups",
          "frame_events",
          "side_panel",
        ],
      }),
    );
  }

  handleIncoming(message) {
    try {
      assertBoundedEnvelope(message, { maxBytes: MAX_CONTROL_BYTES });
      if (Object.prototype.hasOwnProperty.call(message, "origin")) {
        throw new ProtocolError(
          "origin_invalid",
          "origin claims are transport metadata, not JSON fields",
        );
      }
      validateEnvelope(message, {
        expectedNonce: this.nonce,
        expectedBrokerEpoch: this.brokerEpoch,
        expectedConnectionEpoch: this.connectionEpoch,
        expectedWorkerInstanceEpoch: this.workerInstanceEpoch,
        expectedBrowserSessionEpoch: this.browserSessionEpoch,
        requireEpochs: true,
      });
      this.inboundSequence.accept(message.sequence);

      if (this.state === "handshaking") {
        this.acceptHelloOk(message);
        return;
      }
      if (!this.connected) {
        throw new ProtocolError(
          "connection_not_ready",
          "message received outside a live connection",
        );
      }
      this.onMessage(message);
    } catch (error) {
      this.failProtocol(error);
    }
  }

  acceptHelloOk(message) {
    if (message.kind !== "hello_ok") {
      throw new ProtocolError(
        "handshake_required",
        "first host message must be hello_ok",
      );
    }
    if (
      !Number.isSafeInteger(message.broker_epoch) ||
      message.broker_epoch < 1
    ) {
      throw new ProtocolError(
        "handshake_invalid",
        "hello_ok requires broker_epoch",
      );
    }
    if (
      !Number.isSafeInteger(message.connection_epoch) ||
      message.connection_epoch < 1
    ) {
      throw new ProtocolError(
        "handshake_invalid",
        "hello_ok requires connection_epoch",
      );
    }
    if (
      message.worker_instance_epoch !== undefined &&
      message.worker_instance_epoch !== this.workerInstanceEpoch
    ) {
      throw new ProtocolError(
        "stale_epoch",
        "hello_ok targets another worker instance",
      );
    }
    if (
      message.browser_session_epoch !== undefined &&
      message.browser_session_epoch !== this.browserSessionEpoch
    ) {
      throw new ProtocolError(
        "stale_epoch",
        "hello_ok targets another browser session",
      );
    }
    this.brokerEpoch = message.broker_epoch;
    this.connectionEpoch = message.connection_epoch;
    this.reconnectAttempt = 0;
    this.transition("connected", message);
    this.onMessage(message);
  }

  send(kind, fields = {}, { mutation = false, actionId } = {}) {
    if (!this.connected) {
      throw new ProtocolError(
        "native_host_unavailable",
        "Native Messaging is not connected",
      );
    }
    if (mutation && actionId) this.pendingMutations.add(actionId);
    const envelope = makeEnvelope(kind, {
      ...fields,
      nonce: this.nonce,
      sequence: this.outboundSequence++,
      broker_epoch: this.brokerEpoch,
      connection_epoch: this.connectionEpoch,
      worker_instance_epoch: this.workerInstanceEpoch,
      browser_session_epoch: this.browserSessionEpoch,
    });
    try {
      this.postEnvelope(envelope);
    } catch (error) {
      if (mutation && actionId) this.pendingMutations.delete(actionId);
      this.handleDisconnect(error);
      throw error;
    }
    return envelope;
  }

  sendRequest({
    method,
    params = {},
    requestId = createLogicalId("req"),
    actionId,
    mutation = false,
    ...fields
  }) {
    return this.send(
      "request",
      {
        request_id: requestId,
        ...(actionId ? { action_id: actionId } : {}),
        method,
        params,
        ...fields,
      },
      { mutation, actionId },
    );
  }

  sendResponse({
    requestId,
    actionId,
    ok,
    result,
    error,
    mutation = false,
  } = {}) {
    if (actionId) this.pendingMutations.delete(actionId);
    return this.send(
      "response",
      {
        request_id: requestId,
        ...(actionId ? { action_id: actionId } : {}),
        ok: Boolean(ok),
        ...(ok
          ? { result: result ?? {} }
          : {
              error: error ?? errorResult("extension_error", "request failed"),
            }),
      },
      { mutation },
    );
  }

  sendEvent(event, payload = {}, scope = {}) {
    return this.send("event", {
      event,
      payload,
      ...scope,
    });
  }

  markActionComplete(actionId) {
    if (actionId) this.pendingMutations.delete(actionId);
  }

  postEnvelope(envelope) {
    if (!this.port?.postMessage) {
      throw new ProtocolError(
        "native_host_unavailable",
        "Native Messaging port is unavailable",
      );
    }
    assertBoundedEnvelope(envelope, { maxBytes: MAX_CONTROL_BYTES });
    this.port.postMessage(envelope);
  }

  failProtocol(error) {
    const protocolError =
      error instanceof ProtocolError
        ? error
        : new ProtocolError(
            "protocol_invalid",
            error instanceof Error ? error.message : String(error),
          );
    this.transition("rejected", protocolError);
    const unknownActions = [...this.pendingMutations];
    this.pendingMutations.clear();
    if (unknownActions.length > 0)
      this.onUnknownActions(unknownActions, protocolError);
    this.disconnectPort();
    this.scheduleReconnect();
  }

  handleDisconnect(reason) {
    if (this.disconnecting) return;
    const wasLive = this.state === "connected" || this.state === "handshaking";
    const unknownActions = [...this.pendingMutations];
    this.pendingMutations.clear();
    this.cleanupPortListeners();
    this.port = null;
    this.brokerEpoch = undefined;
    this.connectionEpoch = undefined;
    this.state = "disconnected";
    if (wasLive || unknownActions.length > 0) {
      this.onUnknownActions(unknownActions, reason);
    }
    this.onStateChange(this.state, reason);
    this.scheduleReconnect();
  }

  disconnectPort() {
    const port = this.port;
    if (!port) return;
    this.disconnecting = true;
    try {
      port.disconnect?.();
    } catch {
      // The port is already unusable. State is cleared below.
    } finally {
      this.disconnecting = false;
      this.cleanupPortListeners();
      this.port = null;
    }
  }

  async reconnect() {
    this.stop({ reconnect: false });
    this.stopped = false;
    return this.connect();
  }

  stop({ reconnect = false } = {}) {
    this.stopped = !reconnect;
    this.clearReconnectTimer();
    this.pendingMutations.clear();
    this.disconnectPort();
    this.state = "disconnected";
    this.onStateChange(
      this.state,
      new Error("Native Messaging client stopped"),
    );
  }

  transition(state, detail = undefined) {
    this.state = state;
    this.onStateChange(state, detail);
  }

  scheduleReconnect() {
    if (
      !this.autoReconnect ||
      this.stopped ||
      this.reconnectTimer ||
      !this.setTimeoutFn
    )
      return;
    const delay = Math.min(
      MAX_RECONNECT_MS,
      Math.max(0, this.reconnectDelayMs) *
        2 ** Math.min(this.reconnectAttempt, 5),
    );
    this.reconnectAttempt += 1;
    this.reconnectTimer = this.setTimeoutFn(() => {
      this.reconnectTimer = null;
      void this.connect().catch(() => {});
    }, delay);
    this.reconnectTimer?.unref?.();
  }

  clearReconnectTimer() {
    if (this.reconnectTimer && this.clearTimeoutFn)
      this.clearTimeoutFn(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  cleanupPortListeners() {
    for (const remove of this.removePortListeners.splice(0)) remove();
  }
}

export function nativeMessagingError(
  error,
  fallback = "native_host_unavailable",
) {
  return publicError(error, fallback);
}

export { DEFAULT_HOST_NAME };
