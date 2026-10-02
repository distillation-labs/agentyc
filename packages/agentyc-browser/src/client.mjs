import { TaskSpace } from "./space.mjs";
import { actionStatus, reconcileAction, submitAction } from "./actions.mjs";
import { readEvents } from "./events.mjs";
import { waitFor } from "./waits.mjs";
import { makeBatch, makeRequest } from "./transport.mjs";
import {
  AgentycError,
  assertLogicalId,
  mapTransportError,
  mapWireError,
} from "./errors.mjs";

function responseItems(response) {
  if (Array.isArray(response)) return response;
  if (Array.isArray(response?.responses)) return response.responses;
  if (Array.isArray(response?.results)) return response.results;
  return [response];
}

function responseFor(response, requestId, expectedCount) {
  const items = responseItems(response);
  if (items.length === 1 && expectedCount === 1 && !items[0]?.request_id)
    return items[0];
  return items.find((item) => item?.request_id === requestId) ?? items[0];
}

function unwrap(response) {
  if (!response || typeof response !== "object") return response;
  if (response.ok === false) throw mapWireError(response.error);
  if (response.ok === true) return response.result;
  if (response.error) throw mapWireError(response.error);
  return response.result === undefined ? response : response.result;
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
    this.connected = true;
  }

  taskSpace(spaceId) {
    return new TaskSpace(this, assertLogicalId(spaceId, "space_", "spaceId"));
  }

  async createSpace(label, options = {}) {
    const result = await this.request("space.create", {
      label,
      retention: options.retention,
    });
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

  async hostStatus() {
    return this.request("host.status", {});
  }

  async events(options = {}) {
    return readEvents(this, options);
  }

  async actionStatus(actionId) {
    return actionStatus(this, actionId);
  }

  async reconcileAction(actionId, leaseEpoch, now) {
    return reconcileAction(this, actionId, leaseEpoch, now);
  }

  async submitAction(request) {
    return submitAction(this, request);
  }

  async waitFor(condition, options = {}) {
    return waitFor(this, condition, options);
  }

  async request(method, params = {}, options = {}) {
    const [result] = await this._send([
      {
        request: makeRequest(method, params, options),
        mayHaveSideEffects: Boolean(options.mayHaveSideEffects),
      },
    ]);
    return result;
  }

  async batch(requests) {
    if (!Array.isArray(requests) || requests.length === 0) return [];
    const entries = requests.map((entry) => ({
      request: makeRequest(entry.method, entry.params ?? {}, entry),
      mayHaveSideEffects: Boolean(entry.mayHaveSideEffects),
    }));
    return this._send(entries);
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

  async _send(entries) {
    const payload = makeBatch(entries.map((entry) => entry.request));
    const hasSideEffects = entries.some((entry) => entry.mayHaveSideEffects);
    let reconnects = 0;
    while (true) {
      try {
        const response = await this.transport.request(payload);
        this.connected = true;
        return entries.map((entry) =>
          unwrap(
            responseFor(response, entry.request.request_id, entries.length),
          ),
        );
      } catch (error) {
        if (
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
            });
          }
        }
        this.connected = false;
        throw mapTransportError(error, {
          mayHaveSideEffects: hasSideEffects,
          details:
            hasSideEffects && entries.length === 1
              ? { action_id: entries[0].request.params?.action_id }
              : undefined,
        });
      }
    }
  }
}

/** Connect a client to an injected local transport or handler. */
export async function connect(options = {}) {
  const transport = options.transport ?? options.handler;
  if (!transport) throw new TypeError("connect requires transport or handler");
  const normalized =
    typeof transport === "function" ? { request: transport } : transport;
  return new BrowserClient({
    transport: normalized,
    reconnect: options.reconnect ?? true,
    maxReconnects: options.maxReconnects ?? 1,
  });
}
