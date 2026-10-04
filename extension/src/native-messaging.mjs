import {
  MAX_ARTIFACT_BYTES,
  MAX_ARTIFACT_CHUNK_BYTES,
  MAX_ARTIFACT_CHUNKS,
  MAX_ARTIFACT_TRANSFERS,
  MAX_CUMULATIVE_ARTIFACT_BYTES,
  MAX_IN_FLIGHT_ARTIFACT_BYTES,
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

function defaultNativeLimits() {
  return {
    max_control_bytes: MAX_CONTROL_BYTES,
    max_artifact_chunk_bytes: MAX_ARTIFACT_CHUNK_BYTES,
    max_artifact_bytes: MAX_ARTIFACT_BYTES,
    max_artifact_chunks: MAX_ARTIFACT_CHUNKS,
    max_in_flight_artifact_bytes: MAX_IN_FLIGHT_ARTIFACT_BYTES,
    max_cumulative_artifact_bytes: MAX_CUMULATIVE_ARTIFACT_BYTES,
  };
}

const RESUME_STATUSES = new Set(["accepted", "resync_required"]);

function nativeResumeCursor(value, field = "resume cursor") {
  if (value === undefined || value === null) return undefined;
  if (
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.getPrototypeOf(value) !== Object.prototype
  ) {
    throw new ProtocolError("schema_invalid", `${field} must be an object`);
  }
  const keys = Object.keys(value);
  if (
    keys.length !== 2 ||
    !keys.includes("broker_epoch") ||
    !keys.includes("sequence")
  ) {
    throw new ProtocolError("schema_invalid", `${field} has unknown fields`);
  }
  if (
    !Number.isSafeInteger(value.broker_epoch) ||
    value.broker_epoch < 1 ||
    value.broker_epoch > Number.MAX_SAFE_INTEGER
  ) {
    throw new ProtocolError(
      "schema_invalid",
      `${field}.broker_epoch is invalid`,
    );
  }
  if (
    !Number.isSafeInteger(value.sequence) ||
    value.sequence < 0 ||
    value.sequence > Number.MAX_SAFE_INTEGER
  ) {
    throw new ProtocolError("schema_invalid", `${field}.sequence is invalid`);
  }
  return {
    broker_epoch: value.broker_epoch,
    sequence: value.sequence,
  };
}

function nativeResumeStatus(value) {
  if (value === undefined || value === null) return undefined;
  const status =
    typeof value === "string"
      ? value
      : value &&
          typeof value === "object" &&
          !Array.isArray(value) &&
          Object.getPrototypeOf(value) === Object.prototype &&
          Object.keys(value).length === 1 &&
          typeof value.kind === "string"
        ? value.kind
        : undefined;
  if (!RESUME_STATUSES.has(status)) {
    throw new ProtocolError("handshake_invalid", "resume status is invalid");
  }
  return status;
}

function validateNativeResumeFields(envelope) {
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope))
    return;
  if (envelope.kind === "hello") {
    if (
      envelope.resume_from !== undefined &&
      envelope.resume_cursor !== undefined
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "hello cannot contain both resume_from and resume_cursor",
      );
    }
    nativeResumeCursor(
      envelope.resume_from ?? envelope.resume_cursor,
      "resume_from",
    );
  } else if (envelope.kind === "hello_ok") {
    nativeResumeStatus(envelope.resume);
    nativeResumeCursor(envelope.cursor, "hello_ok cursor");
  } else if (envelope.kind === "event") {
    nativeResumeCursor(envelope.cursor, "event cursor");
  }
}

function nativeEnvelopeForValidation(envelope) {
  if (!envelope || typeof envelope !== "object" || Array.isArray(envelope))
    return envelope;
  const copy = { ...envelope };
  if (envelope.kind === "hello") {
    delete copy.resume_from;
    delete copy.resume_cursor;
  } else if (envelope.kind === "hello_ok") {
    delete copy.resume;
    delete copy.cursor;
  } else if (envelope.kind === "event") {
    delete copy.cursor;
  }
  return copy;
}

function appendArtifactBytes(existing, chunk) {
  const output = new Uint8Array(existing.length + chunk.length);
  output.set(existing, 0);
  output.set(chunk, existing.length);
  return output;
}

function artifactDigest(bytes) {
  let hash = 0xcbf29ce484222325n;
  for (const byte of bytes) {
    hash ^= BigInt(byte);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return `fnv1a64:${hash.toString(16).padStart(16, "0")}`;
}

function bytesFromWire(bytes) {
  if (
    !Array.isArray(bytes) ||
    bytes.some((byte) => !Number.isInteger(byte) || byte < 0 || byte > 255)
  ) {
    throw new ProtocolError("schema_invalid", "artifact bytes are invalid");
  }
  return bytes;
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
    this.resumeCursor = undefined;
    this.resumeStatus = undefined;
    this.outboundSequence = 1;
    this.eventSequence = 0;
    this.inboundSequence = new SequenceValidator(1);
    this.pendingMutations = new Set();
    this.reconnectTimer = null;
    this.reconnectAttempt = 0;
    this.stopped = false;
    this.disconnecting = false;
    this.removePortListeners = [];
    this.connectionGeneration = 0;
    this.connectPromise = null;
    this.requestedCapabilities = [
      "debugger_allowlist",
      "logical_tabs",
      "visual_groups",
      "frame_events",
      "snapshot",
      "evaluate",
      "reconcile",
      "side_panel",
      "artifact_transfer",
    ];
    this.negotiatedCapabilities = [];
    this.limits = defaultNativeLimits();
    this.profileState = profileInstanceId ? "bound" : "unbound";
    this.artifactTransfers = new Map();
    this.artifactInFlightBytes = 0;
    this.artifactCumulativeBytes = 0;
  }

  get connected() {
    return this.state === "connected";
  }

  get connectionInfo() {
    return {
      state: this.state,
      brokerEpoch: this.brokerEpoch,
      connectionEpoch: this.connectionEpoch,
      resumeCursor: this.resumeCursor ? { ...this.resumeCursor } : undefined,
      resumeStatus: this.resumeStatus,
      workerInstanceEpoch: this.workerInstanceEpoch,
      browserSessionEpoch: this.browserSessionEpoch,
      profileInstanceId: this.profileInstanceId,
      profileState: this.profileState,
      capabilities: [...this.negotiatedCapabilities],
      limits: { ...this.limits },
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
        this.resumeStatus = undefined;
        this.outboundSequence = 1;
        this.eventSequence = 0;
        this.inboundSequence.reset(1);
        this.negotiatedCapabilities = [];
        this.limits = defaultNativeLimits();
        this.profileState = this.profileInstanceId ? "bound" : "unbound";
        this.artifactTransfers.clear();
        this.artifactInFlightBytes = 0;
        this.artifactCumulativeBytes = 0;
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
    const envelope = makeEnvelope("hello", {
      nonce: this.nonce,
      sequence: this.outboundSequence++,
      worker_instance_epoch: this.workerInstanceEpoch,
      browser_session_epoch: this.browserSessionEpoch,
      profile_instance_id: this.profileInstanceId,
      extension_version: this.extensionVersion,
      capabilities: this.requestedCapabilities,
      ...(this.profileInstanceId ? { profile_state: "bound" } : {}),
      limits: defaultNativeLimits(),
    });
    if (this.resumeCursor) envelope.resume_from = { ...this.resumeCursor };
    this.postEnvelope(envelope);
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
      assertBoundedEnvelope(nativeEnvelopeForValidation(message), {
        maxBytes:
          this.state === "connected"
            ? this.limits.max_control_bytes
            : MAX_CONTROL_BYTES,
      });
      if (Object.prototype.hasOwnProperty.call(message, "origin")) {
        throw new ProtocolError(
          "origin_invalid",
          "origin claims are transport metadata, not JSON fields",
        );
      }
      validateNativeResumeFields(message);
      validateEnvelope(nativeEnvelopeForValidation(message), {
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
      if (message.kind === "artifact_begin") this.acceptArtifactBegin(message);
      else if (message.kind === "artifact_chunk")
        this.acceptArtifactChunk(message);
      else if (message.kind === "artifact_end") this.acceptArtifactEnd(message);
      else if (message.kind === "event" && message.cursor !== undefined)
        this.acceptBrokerCursor(message.cursor);
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
    if (message.profile_state !== "bound") {
      throw new ProtocolError(
        "handshake_invalid",
        "hello_ok requires a bound profile state",
      );
    }
    if (
      this.profileInstanceId !== undefined &&
      message.profile_instance_id !== this.profileInstanceId
    ) {
      throw new ProtocolError(
        "stale_epoch",
        "hello_ok targets another profile binding",
      );
    }
    const requestedCursor = this.resumeCursor
      ? { ...this.resumeCursor }
      : undefined;
    const resumeStatusFromHost = nativeResumeStatus(message.resume);
    const hostCursor = nativeResumeCursor(message.cursor, "hello_ok cursor");
    let resumeStatus = resumeStatusFromHost ?? "accepted";
    if (
      requestedCursor &&
      requestedCursor.broker_epoch !== message.broker_epoch
    ) {
      if (resumeStatusFromHost === "accepted") {
        throw new ProtocolError(
          "stale_epoch",
          "hello_ok accepted a cursor from another broker epoch",
        );
      }
      if (resumeStatusFromHost === undefined) resumeStatus = "resync_required";
    }
    if (hostCursor && hostCursor.broker_epoch !== message.broker_epoch) {
      throw new ProtocolError(
        "stale_epoch",
        "hello_ok cursor does not match broker_epoch",
      );
    }
    if (
      resumeStatus === "accepted" &&
      requestedCursor &&
      hostCursor &&
      hostCursor.sequence < requestedCursor.sequence
    ) {
      throw new ProtocolError(
        "handshake_invalid",
        "hello_ok cursor moved behind the requested resume cursor",
      );
    }
    if (resumeStatus === "resync_required") {
      this.resumeCursor = undefined;
    } else if (hostCursor) {
      this.resumeCursor = hostCursor;
    }
    this.resumeStatus = resumeStatus;

    const capabilities = Array.isArray(message.capabilities)
      ? message.capabilities
      : [];
    if (
      new Set(capabilities).size !== capabilities.length ||
      capabilities.some(
        (capability) => !this.requestedCapabilities.includes(capability),
      )
    ) {
      throw new ProtocolError(
        "handshake_invalid",
        "hello_ok capabilities are not an intersection with the hello",
      );
    }
    this.negotiatedCapabilities = [...capabilities];
    this.limits = { ...message.limits };
    this.profileState = "bound";
    this.brokerEpoch = message.broker_epoch;
    this.connectionEpoch = message.connection_epoch;
    this.reconnectAttempt = 0;
    this.transition("connected", message);
    this.onMessage(message);
  }

  acceptBrokerCursor(value) {
    const cursor = nativeResumeCursor(value, "event cursor");
    if (!cursor) return;
    if (
      this.brokerEpoch === undefined ||
      cursor.broker_epoch !== this.brokerEpoch
    ) {
      throw new ProtocolError(
        "stale_epoch",
        "event cursor does not match the live broker epoch",
      );
    }
    if (
      this.resumeCursor &&
      this.resumeCursor.broker_epoch === cursor.broker_epoch &&
      cursor.sequence < this.resumeCursor.sequence
    ) {
      throw new ProtocolError(
        "sequence_replayed",
        "event cursor moved behind the last accepted cursor",
      );
    }
    this.resumeCursor = cursor;
    this.resumeStatus = "accepted";
  }

  sendArtifactBegin({
    artifactId,
    requestId,
    artifactKind,
    totalBytes,
    chunkSize,
    chunkCount,
    digestAlgorithm = "fnv1a64",
    digest,
    redacted = false,
  } = {}) {
    return this.send("artifact_begin", {
      artifact_id: artifactId,
      ...(requestId ? { request_id: requestId } : {}),
      artifact_kind: artifactKind,
      total_bytes: totalBytes,
      chunk_size: chunkSize,
      chunk_count: chunkCount,
      digest_algorithm: digestAlgorithm,
      digest,
      redacted,
    });
  }

  sendArtifactChunk({ artifactId, chunkSequence, bytes } = {}) {
    return this.send("artifact_chunk", {
      artifact_id: artifactId,
      chunk_sequence: chunkSequence,
      bytes: bytesFromWire(bytes),
    });
  }

  sendArtifactEnd({
    artifactId,
    totalBytes,
    chunkCount,
    digestAlgorithm = "fnv1a64",
    digest,
  } = {}) {
    return this.send("artifact_end", {
      artifact_id: artifactId,
      total_bytes: totalBytes,
      chunk_count: chunkCount,
      digest_algorithm: digestAlgorithm,
      digest,
    });
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
      this.validateOutboundArtifact(envelope);
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
    deadlineMs = 30_000,
    ...fields
  }) {
    return this.send(
      "request",
      {
        request_id: requestId,
        ...(actionId ? { action_id: actionId } : {}),
        method,
        params,
        deadline_ms: fields.deadline_ms ?? deadlineMs,
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
    const eventSequence = ++this.eventSequence;
    return this.send("event", {
      event,
      payload: {
        ...payload,
        event_id: createLogicalId("event"),
        event_sequence: eventSequence,
        event_source: "extension",
        ...(event === "heartbeat" ? { heartbeat: true } : {}),
      },
      ...scope,
    });
  }

  validateOutboundArtifact(envelope) {
    if (!envelope.kind.startsWith("artifact_")) return;
    if (envelope.kind === "artifact_begin") {
      if (
        this.artifactTransfers.size >= MAX_ARTIFACT_TRANSFERS ||
        envelope.total_bytes > this.limits.max_artifact_bytes ||
        envelope.chunk_size > this.limits.max_artifact_chunk_bytes ||
        envelope.chunk_count > this.limits.max_artifact_chunks
      ) {
        throw new ProtocolError(
          "message_too_large",
          "artifact declaration exceeds negotiated limits",
        );
      }
      if (this.artifactTransfers.has(envelope.artifact_id)) {
        throw new ProtocolError(
          "schema_invalid",
          "artifact transfer already exists",
        );
      }
      this.artifactTransfers.set(envelope.artifact_id, {
        begin: { ...envelope },
        bytes: new Uint8Array(0),
        nextChunk: 0,
      });
      return;
    }
    const transfer = this.artifactTransfers.get(envelope.artifact_id);
    if (!transfer) {
      throw new ProtocolError(
        "schema_invalid",
        "artifact transfer has no begin",
      );
    }
    if (envelope.kind === "artifact_chunk") {
      if (
        envelope.connection_epoch !== this.connectionEpoch ||
        envelope.chunk_sequence !== transfer.nextChunk
      ) {
        throw new ProtocolError(
          "schema_invalid",
          "artifact chunk is out of order or stale",
        );
      }
      const bytes = bytesFromWire(envelope.bytes);
      const begin = transfer.begin;
      const expected =
        envelope.chunk_sequence + 1 === begin.chunk_count
          ? begin.total_bytes - envelope.chunk_sequence * begin.chunk_size
          : begin.chunk_size;
      if (bytes.length !== expected) {
        throw new ProtocolError(
          "schema_invalid",
          "artifact chunk length does not match declaration",
        );
      }
      if (
        this.artifactInFlightBytes + bytes.length >
          this.limits.max_in_flight_artifact_bytes ||
        this.artifactCumulativeBytes + bytes.length >
          this.limits.max_cumulative_artifact_bytes
      ) {
        throw new ProtocolError(
          "message_too_large",
          "artifact transfer budget exceeded",
        );
      }
      this.artifactInFlightBytes += bytes.length;
      this.artifactCumulativeBytes += bytes.length;
      transfer.bytes = appendArtifactBytes(transfer.bytes, bytes);
      transfer.nextChunk += 1;
      return;
    }
    if (
      envelope.total_bytes !== transfer.begin.total_bytes ||
      envelope.chunk_count !== transfer.begin.chunk_count ||
      envelope.digest !== transfer.begin.digest ||
      envelope.digest_algorithm !== transfer.begin.digest_algorithm ||
      transfer.nextChunk !== transfer.begin.chunk_count ||
      transfer.bytes.length !== transfer.begin.total_bytes ||
      artifactDigest(transfer.bytes) !== transfer.begin.digest
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "artifact completion does not match declaration",
      );
    }
    this.artifactInFlightBytes -= transfer.bytes.length;
    this.artifactTransfers.delete(envelope.artifact_id);
  }

  acceptArtifactBegin(envelope) {
    if (this.artifactTransfers.size >= MAX_ARTIFACT_TRANSFERS) {
      throw new ProtocolError(
        "message_too_large",
        "too many concurrent artifact transfers",
      );
    }
    if (this.artifactTransfers.has(envelope.artifact_id)) {
      throw new ProtocolError(
        "schema_invalid",
        "artifact transfer already exists",
      );
    }
    if (
      envelope.total_bytes > this.limits.max_artifact_bytes ||
      envelope.chunk_size > this.limits.max_artifact_chunk_bytes ||
      envelope.chunk_count > this.limits.max_artifact_chunks
    ) {
      throw new ProtocolError(
        "message_too_large",
        "artifact declaration exceeds negotiated limits",
      );
    }
    this.artifactTransfers.set(envelope.artifact_id, {
      begin: { ...envelope },
      bytes: new Uint8Array(0),
      nextChunk: 0,
    });
  }

  acceptArtifactChunk(envelope) {
    const transfer = this.artifactTransfers.get(envelope.artifact_id);
    if (!transfer)
      throw new ProtocolError("schema_invalid", "artifact chunk has no begin");
    if (
      envelope.connection_epoch !== this.connectionEpoch ||
      envelope.chunk_sequence !== transfer.nextChunk
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "artifact chunk is out of order or stale",
      );
    }
    const bytes = bytesFromWire(envelope.bytes);
    const begin = transfer.begin;
    const expected =
      envelope.chunk_sequence + 1 === begin.chunk_count
        ? begin.total_bytes - envelope.chunk_sequence * begin.chunk_size
        : begin.chunk_size;
    if (bytes.length !== expected) {
      throw new ProtocolError(
        "schema_invalid",
        "artifact chunk length does not match declaration",
      );
    }
    if (
      this.artifactInFlightBytes + bytes.length >
        this.limits.max_in_flight_artifact_bytes ||
      this.artifactCumulativeBytes + bytes.length >
        this.limits.max_cumulative_artifact_bytes
    ) {
      throw new ProtocolError(
        "message_too_large",
        "artifact transfer budget exceeded",
      );
    }
    this.artifactInFlightBytes += bytes.length;
    this.artifactCumulativeBytes += bytes.length;
    transfer.bytes = appendArtifactBytes(transfer.bytes, bytes);
    transfer.nextChunk += 1;
  }

  acceptArtifactEnd(envelope) {
    const transfer = this.artifactTransfers.get(envelope.artifact_id);
    if (!transfer)
      throw new ProtocolError("schema_invalid", "artifact end has no begin");
    if (
      envelope.total_bytes !== transfer.begin.total_bytes ||
      envelope.chunk_count !== transfer.begin.chunk_count ||
      envelope.digest !== transfer.begin.digest ||
      envelope.digest_algorithm !== transfer.begin.digest_algorithm ||
      transfer.nextChunk !== transfer.begin.chunk_count ||
      transfer.bytes.length !== transfer.begin.total_bytes ||
      artifactDigest(transfer.bytes) !== transfer.begin.digest
    ) {
      throw new ProtocolError(
        "schema_invalid",
        "artifact completion does not match declaration",
      );
    }
    this.artifactInFlightBytes -= transfer.bytes.length;
    this.artifactTransfers.delete(envelope.artifact_id);
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
    validateNativeResumeFields(envelope);
    assertBoundedEnvelope(nativeEnvelopeForValidation(envelope), {
      maxBytes: this.connected
        ? this.limits.max_control_bytes
        : MAX_CONTROL_BYTES,
    });
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
    this.resumeStatus = undefined;
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
