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
    // Chrome's timer functions are Web IDL methods and must be invoked with
    // their global receiver. Keep dependency injection available for tests,
    // but never store the unbound platform methods as defaults.
    setTimeoutFn = (...args) => globalThis.setTimeout(...args),
    clearTimeoutFn = (...args) => globalThis.clearTimeout(...args),
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
    this.connectionGeneration = 0;
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
        this.notifyUnknownMutations(
          new Error("Native Messaging connection was replaced"),
        );
        this.state = "handshaking";
        this.nonce = createNonce();
        this.brokerEpoch = undefined;
        this.connectionEpoch = undefined;
        this.outboundSequence = 1;
        this.inboundSequence.reset(1);
        const connectionGeneration = ++this.connectionGeneration;

        let port;
        try {
          port = this.chrome.runtime.connectNative(this.hostName);
        } catch (error) {
          this.handleDisconnect(error, { scheduleReconnect: false });
          this.scheduleReconnect();
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
            this.handleIncoming(message, connectionGeneration, port),
          ),
          addListener(port?.onDisconnect, () => {
            const runtimeError = this.chrome?.runtime?.lastError;
            this.handleDisconnect(
              runtimeError ?? new Error("Native Messaging port disconnected"),
              {
                immediateReconnect: true,
                connectionGeneration,
                port,
              },
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
          "snapshot",
          "reconcile",
          "side_panel",
        ],
      }),
    );
  }

  handleIncoming(
    message,
    connectionGeneration = this.connectionGeneration,
    port = this.port,
  ) {
    if (
      connectionGeneration !== this.connectionGeneration ||
      port !== this.port
    )
      return;
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
      this.failProtocol(error, { connectionGeneration, port });
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
    let envelope;
    const sequence = this.outboundSequence;
    try {
      if (
        !Number.isSafeInteger(sequence) ||
        sequence < 1 ||
        sequence === Number.MAX_SAFE_INTEGER
      )
        throw new ProtocolError(
          "sequence_invalid",
          "Native Messaging outbound sequence is exhausted",
        );
      envelope = makeEnvelope(kind, {
        ...fields,
        nonce: this.nonce,
        sequence,
        broker_epoch: this.brokerEpoch,
        connection_epoch: this.connectionEpoch,
        worker_instance_epoch: this.workerInstanceEpoch,
        browser_session_epoch: this.browserSessionEpoch,
      });
      this.outboundSequence = sequence + 1;
    } catch (error) {
      if (mutation && actionId) this.pendingMutations.delete(actionId);
      throw error;
    }
    try {
      this.postEnvelope(envelope);
    } catch (error) {
      this.handleDisconnect(error, {
        connectionGeneration: this.connectionGeneration,
        port: this.port,
      });
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
    const envelope = this.send(
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
      { mutation, actionId },
    );
    if (actionId) this.pendingMutations.delete(actionId);
    return envelope;
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

  failProtocol(error, { connectionGeneration, port } = {}) {
    if (
      connectionGeneration !== undefined &&
      (connectionGeneration !== this.connectionGeneration || port !== this.port)
    )
      return;
    const protocolError =
      error instanceof ProtocolError
        ? error
        : new ProtocolError(
            "protocol_invalid",
            error instanceof Error ? error.message : String(error),
          );
    this.transition("rejected", protocolError);
    this.notifyUnknownMutations(protocolError);
    this.disconnectPort(port);
    this.scheduleReconnect();
  }

  handleDisconnect(
    reason,
    {
      scheduleReconnect = true,
      immediateReconnect = false,
      connectionGeneration,
      port,
    } = {},
  ) {
    if (
      (connectionGeneration !== undefined &&
        connectionGeneration !== this.connectionGeneration) ||
      (port !== undefined && port !== this.port)
    )
      return;
    if (this.disconnecting) return;
    const wasLive = this.state === "connected" || this.state === "handshaking";
    this.notifyUnknownMutations(reason);
    this.cleanupPortListeners();
    this.port = null;
    this.brokerEpoch = undefined;
    this.connectionEpoch = undefined;
    this.state = "disconnected";
    if (wasLive) this.onUnknownActions([], reason);
    this.onStateChange(this.state, reason);
    if (scheduleReconnect) this.scheduleReconnect(immediateReconnect);
  }

  notifyUnknownMutations(reason) {
    const unknownActions = [...this.pendingMutations];
    this.pendingMutations.clear();
    if (unknownActions.length > 0)
      this.onUnknownActions(unknownActions, reason);
    return unknownActions;
  }

  disconnectPort(expectedPort = this.port) {
    const port = this.port;
    if (!port || (expectedPort !== undefined && port !== expectedPort)) return;
    this.disconnecting = true;
    this.port = null;
    try {
      port.disconnect?.();
    } catch {
      // The port is already unusable. State is cleared below.
    } finally {
      this.disconnecting = false;
      this.cleanupPortListeners();
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
    const reason = new Error("Native Messaging client stopped");
    this.notifyUnknownMutations(reason);
    this.disconnectPort();
    this.state = "disconnected";
    this.onStateChange(this.state, reason);
  }

  transition(state, detail = undefined) {
    this.state = state;
    this.onStateChange(state, detail);
  }

  scheduleReconnect(immediate = false) {
    if (
      !this.autoReconnect ||
      this.stopped ||
      this.reconnectTimer ||
      !this.setTimeoutFn
    )
      return;
    const delay = immediate
      ? 0
      : Math.min(
          MAX_RECONNECT_MS,
          Math.max(0, this.reconnectDelayMs) *
            2 ** Math.min(this.reconnectAttempt, 5),
        );
    if (!immediate) this.reconnectAttempt += 1;
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
