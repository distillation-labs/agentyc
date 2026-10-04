import { TaskSpace } from "./space.mjs";
import { actionStatus, reconcileAction, submitAction } from "./actions.mjs";
import { readEvents } from "./events.mjs";
import { waitFor } from "./waits.mjs";
import {
  createLocalProtocolTransport,
  createLocalTransport,
  makeBatch,
  makeRequest,
} from "./transport.mjs";
import {
  AgentycError,
  BatchError,
  CapabilityUnavailableError,
  CancelledError,
  UnknownOutcomeError,
  assertLogicalId,
  mapTransportError,
  mapWireError,
  withRequestIdentity,
} from "./errors.mjs";
import {
  PROFILE_DISCLOSURE,
  invalidArgument,
  transportOptions,
} from "./constants.mjs";
import { methodMayHaveSideEffects as registryMethodMayHaveSideEffects } from "./operations.mjs";

function responseItems(response) {
  if (Array.isArray(response)) return response;
  if (Array.isArray(response?.responses)) return response.responses;
  if (Array.isArray(response?.results)) return response.results;
  return [response];
}

function protocolError(message, details = undefined) {
  return new AgentycError({
    code: "invalid_json",
    message,
    retryable: false,
    guidance: "none",
    details,
    transportFailure: true,
  });
}

function correlateResponses(response, requestIds) {
  if (new Set(requestIds).size !== requestIds.length) {
    throw protocolError("request IDs must be unique before dispatch", {
      request_ids: requestIds,
    });
  }
  const expected = new Set(requestIds);
  const byId = new Map();
  for (const item of responseItems(response)) {
    if (
      !item ||
      typeof item !== "object" ||
      typeof item.request_id !== "string"
    ) {
      throw protocolError("host response is missing request_id");
    }
    if (!expected.has(item.request_id)) {
      throw protocolError("host response contains an unexpected request_id", {
        request_id: item.request_id,
      });
    }
    if (byId.has(item.request_id)) {
      throw protocolError("host response contains a duplicate request_id", {
        request_id: item.request_id,
      });
    }
    byId.set(item.request_id, item);
  }
  const missing = requestIds.filter((requestId) => !byId.has(requestId));
  if (missing.length > 0) {
    throw protocolError("host response is missing request IDs", {
      request_ids: missing,
    });
  }
  return requestIds.map((requestId) => byId.get(requestId));
}

function requestIdentity(entry) {
  const params = entry.request.params ?? {};
  return {
    request_id: entry.request.request_id,
    ...(typeof params.action_id === "string"
      ? { action_id: params.action_id }
      : {}),
    ...(typeof entry.request.idempotency_key === "string"
      ? { idempotency_key: entry.request.idempotency_key }
      : {}),
  };
}

function unwrap(response) {
  if (!response || typeof response !== "object") {
    throw protocolError("host response must be an object");
  }
  if (response.kind && response.kind !== "response") {
    throw protocolError("host response has an invalid envelope kind", {
      kind: response.kind,
    });
  }
  if (response.protocol !== undefined && response.protocol !== 1) {
    throw protocolError("host response has an unsupported protocol version", {
      protocol: response.protocol,
    });
  }
  if (response.ok === false) throw mapWireError(response.error);
  if (response.ok === true) return response.result;
  if (response.error) throw mapWireError(response.error);
  return response.result === undefined ? response : response.result;
}

function sideEffectDetails(entries) {
  const requests = entries.map(requestIdentity);
  return {
    requests,
    ...(requests.length === 1 ? requests[0] : {}),
  };
}

function isAbortError(error) {
  return Boolean(error?.cancelled) || error?.name === "AbortError";
}

function dispatchSignal(entries, signal) {
  const signals = [signal, ...entries.map((entry) => entry.signal)].filter(
    Boolean,
  );
  if (signals.length <= 1) return signals[0];
  if (
    typeof AbortSignal !== "undefined" &&
    typeof AbortSignal.any === "function"
  ) {
    return AbortSignal.any(signals);
  }
  const controller = new AbortController();
  const abort = (event) => controller.abort(event.target?.reason);
  for (const candidate of signals) {
    if (candidate.aborted) {
      controller.abort(candidate.reason);
      break;
    }
    candidate.addEventListener("abort", abort, { once: true });
  }
  return controller.signal;
}

function bindSubscription(unsubscribe, signal) {
  if (!signal) return unsubscribe;
  if (signal.aborted) {
    unsubscribe();
    throw new CancelledError("the event subscription was cancelled");
  }
  const abort = () => unsubscribe();
  signal.addEventListener("abort", abort, { once: true });
  return () => {
    signal.removeEventListener("abort", abort);
    return unsubscribe();
  };
}

function abortable(promise, signal, onAbort, isDispatched = () => true) {
  if (!signal) return promise;
  if (signal.aborted) {
    onAbort?.();
    return Promise.reject(
      Object.assign(new Error("the request was cancelled"), {
        name: "AbortError",
        cancelled: true,
        dispatched: isDispatched(),
      }),
    );
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => signal.removeEventListener("abort", abort);
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      callback(value);
    };
    const abort = () => {
      onAbort?.();
      finish(
        reject,
        Object.assign(new Error("the request was cancelled"), {
          name: "AbortError",
          cancelled: true,
          dispatched: isDispatched(),
        }),
      );
    };
    signal.addEventListener("abort", abort, { once: true });
    promise.then(
      (value) => finish(resolve, value),
      (error) => finish(reject, error),
    );
  });
}

/** Return whether a method is conservatively treated as side-effecting. */
export function methodMayHaveSideEffects(method, requested = false) {
  return registryMethodMayHaveSideEffects(method, requested);
}

/** Typed client over one generic local transport. */
export class BrowserClient {
  constructor({ transport, reconnect = true, maxReconnects = 1 } = {}) {
    if (!transport || typeof transport.request !== "function") {
      throw new TypeError("connect requires a local transport");
    }
    this.transport = transport;
    this.reconnectEnabled = reconnect;
    this.maxReconnects = Math.max(0, maxReconnects);
    this.connected = transport.connected !== false;
  }

  taskSpace(spaceId) {
    return new TaskSpace(this, assertLogicalId(spaceId, "space_", "spaceId"));
  }

  async createSpace(label, options = {}) {
    const normalizedOptions = options ?? {};
    if (normalizedOptions.acceptSharedProfileDisclosure !== true) {
      throw new AgentycError({
        code: "permission_denied",
        message:
          "explicit shared-profile disclosure acknowledgement is required before space creation",
        retryable: false,
        guidance: "none",
      });
    }
    const result = await this.request(
      "space.create",
      {
        label,
        ...(normalizedOptions.retention !== undefined
          ? { retention: normalizedOptions.retention }
          : {}),
        profile_scope: PROFILE_DISCLOSURE.profileScope,
        shared_state_notice: PROFILE_DISCLOSURE.sharedStateNotice,
        isolation_claim: PROFILE_DISCLOSURE.isolationClaim,
        profile_disclosure_acknowledged:
          PROFILE_DISCLOSURE.profileDisclosureAcknowledged,
      },
      transportOptions(normalizedOptions),
    );
    const record = result?.space ?? result;
    return new TaskSpace(
      this,
      assertLogicalId(
        record?.space_id ?? result?.space_id,
        "space_",
        "space_id",
      ),
      record,
    );
  }

  async hostStatus(options = {}) {
    return this.request("host.status", {}, transportOptions(options));
  }

  async listSpaces(options = {}) {
    const result = await this.request(
      "space.list",
      {},
      transportOptions(options),
    );
    return (result?.spaces ?? []).map(
      (record) =>
        new TaskSpace(
          this,
          assertLogicalId(record?.space_id, "space_", "space_id"),
          record,
        ),
    );
  }

  async pruneSpaces(maxCount = 8, options = {}) {
    if (!Number.isSafeInteger(maxCount) || maxCount < 0) {
      throw invalidArgument("maxCount must be a non-negative safe integer");
    }
    return this.request(
      "space.prune",
      { max_count: maxCount },
      transportOptions(options),
    );
  }

  async events(options = {}) {
    return readEvents(this, options);
  }

  async resumeEvents(options = {}) {
    if (options.signal?.aborted) {
      throw new CancelledError("the event resume was cancelled");
    }
    if (typeof this.transport.resume !== "function") {
      throw new CapabilityUnavailableError(
        "event resume requires a local protocol transport",
        { capability: "resume" },
      );
    }
    try {
      return await this.transport.resume(options);
    } catch (error) {
      if (isAbortError(error)) {
        throw new CancelledError("the event resume was cancelled", {
          cause: error.message,
        });
      }
      throw error;
    }
  }

  async subscribeEvents(listener, options = {}) {
    if (options.signal?.aborted) {
      throw new CancelledError("the event subscription was cancelled");
    }
    if (typeof this.transport.subscribe === "function") {
      const unsubscribe = await this.transport.subscribe(listener, options);
      return bindSubscription(unsubscribe, options.signal);
    }
    if (typeof this.transport.onEvent === "function") {
      const unsubscribe = this.transport.onEvent(listener);
      if (
        options.afterEpoch !== undefined ||
        options.afterSequence !== undefined
      ) {
        try {
          await this.resumeEvents(options);
        } catch (error) {
          unsubscribe();
          throw error;
        }
      }
      return bindSubscription(unsubscribe, options.signal);
    }
    throw new CapabilityUnavailableError(
      "event subscription requires a local protocol transport",
      { capability: "events" },
    );
  }

  async actionStatus(actionId, options = {}) {
    return actionStatus(this, actionId, options);
  }

  async reconcileAction(actionId, leaseEpoch, now, options = {}) {
    return reconcileAction(this, actionId, leaseEpoch, now, options);
  }

  async submitAction(request) {
    return submitAction(this, request);
  }

  async waitFor(condition, options = {}) {
    return waitFor(this, condition, options);
  }

  async request(method, params = {}, options = {}) {
    const [result] = await this._send(
      [
        {
          request: makeRequest(method, params, options),
          mayHaveSideEffects: methodMayHaveSideEffects(
            method,
            options.mayHaveSideEffects,
          ),
          signal: options.signal,
        },
      ],
      options.signal,
    );
    return result;
  }

  async batch(requests, options = {}) {
    if (!Array.isArray(requests) || requests.length === 0) return [];
    const entries = requests.map((entry) => ({
      request: makeRequest(entry.method, entry.params ?? {}, entry),
      mayHaveSideEffects: methodMayHaveSideEffects(
        entry.method,
        entry.mayHaveSideEffects,
      ),
      signal: entry.signal ?? options.signal,
    }));
    return this._send(entries, options.signal);
  }

  async cancel(requestId, reason) {
    if (typeof this.transport.cancel !== "function") {
      throw new CapabilityUnavailableError(
        "request cancellation requires a local protocol transport",
        { capability: "cancel" },
      );
    }
    return this.transport.cancel(
      Array.isArray(requestId) ? requestId : [requestId],
      reason,
    );
  }

  async reconnect() {
    if (typeof this.transport.reconnect !== "function") {
      this.connected = false;
      throw new AgentycError({
        code: "native_host_unavailable",
        message: "the local transport does not provide reconnect()",
        retryable: true,
        guidance: "retry",
      });
    }
    await this.transport.reconnect();
    this.connected = true;
  }

  async close() {
    if (typeof this.transport.close === "function")
      await this.transport.close();
    this.connected = false;
  }

  async _cancelTransport(entries, reason) {
    if (typeof this.transport.cancel !== "function") return;
    try {
      await this.transport.cancel(
        entries.map((entry) => entry.request.request_id),
        reason,
      );
    } catch {
      // Cancellation is advisory once dispatch has crossed the transport boundary.
    }
  }

  async _send(entries, signal) {
    const requestIds = entries.map((entry) => entry.request.request_id);
    if (new Set(requestIds).size !== requestIds.length) {
      throw protocolError("request IDs must be unique before dispatch", {
        request_ids: requestIds,
      });
    }
    const payload = makeBatch(entries.map((entry) => entry.request));
    const hasSideEffects = entries.some((entry) => entry.mayHaveSideEffects);
    const identities = entries.map(requestIdentity);
    const requestSignal = dispatchSignal(entries, signal);
    if (requestSignal?.aborted) {
      throw new CancelledError("the request was cancelled before dispatch", {
        requests: identities,
      });
    }
    let reconnects = 0;
    while (true) {
      let dispatched = this.transport.dispatchAware !== true;
      try {
        const responsePromise = this.transport.request(payload, {
          signal: requestSignal,
          onDispatch: () => {
            dispatched = true;
          },
        });
        const response = await abortable(
          responsePromise,
          requestSignal,
          () => this._cancelTransport(entries, requestSignal?.reason?.message),
          () => dispatched,
        );
        this.connected = true;
        const correlated = correlateResponses(response, requestIds);
        const results = new Array(entries.length).fill(undefined);
        const failures = [];
        for (let index = 0; index < entries.length; index += 1) {
          try {
            results[index] = unwrap(correlated[index]);
          } catch (error) {
            const identified = withRequestIdentity(error, identities[index]);
            failures.push({
              index,
              request_id: identities[index].request_id,
              action_id: identities[index].action_id,
              error: identified,
            });
          }
        }
        if (failures.length > 0) {
          if (entries.length === 1) throw failures[0].error;
          throw new BatchError({ failures, results });
        }
        return results;
      } catch (error) {
        if (error instanceof BatchError) throw error;
        if (isAbortError(error) || signal?.aborted) {
          this.connected = this.transport.connected !== false;
          const wasDispatched = error?.dispatched ?? dispatched;
          if (hasSideEffects && wasDispatched) {
            throw new UnknownOutcomeError(
              "a side-effecting request was cancelled after dispatch",
              { ...sideEffectDetails(entries), cause: error.message },
            );
          }
          throw new CancelledError("the request was cancelled", {
            requests: identities,
            cause: error.message,
          });
        }

        const knownResponseError =
          error instanceof AgentycError && !error.transportFailure;
        const protocolFailure =
          error instanceof AgentycError &&
          ["invalid_json", "protocol_mismatch", "message_too_large"].includes(
            error.code,
          );
        const transportFailure =
          !knownResponseError &&
          !protocolFailure &&
          (error instanceof AgentycError ? error.transportFailure : true);
        if (
          transportFailure &&
          this.reconnectEnabled &&
          !hasSideEffects &&
          reconnects < this.maxReconnects &&
          typeof this.transport.reconnect === "function"
        ) {
          reconnects += 1;
          try {
            await this.transport.reconnect();
            this.connected = true;
            continue;
          } catch (reconnectError) {
            this.connected = false;
            throw mapTransportError(reconnectError, {
              mayHaveSideEffects: false,
              details: sideEffectDetails(entries),
              transportFailure: true,
            });
          }
        }
        if (knownResponseError) throw error;
        if (protocolFailure && !hasSideEffects) throw error;
        this.connected = this.transport.connected !== false;
        throw mapTransportError(error, {
          mayHaveSideEffects: hasSideEffects,
          details: sideEffectDetails(entries),
          transportFailure: true,
        });
      }
    }
  }
}

/** Connect to an injected transport or to the bounded local host protocol. */
export async function connect(options = {}) {
  const transport = options.transport ?? options.handler;
  const normalized = transport
    ? createLocalTransport(transport)
    : createLocalProtocolTransport(options);
  if (typeof normalized.connect === "function") await normalized.connect();
  return new BrowserClient({
    transport: normalized,
    reconnect: options.reconnect ?? true,
    maxReconnects: options.maxReconnects ?? 1,
  });
}
