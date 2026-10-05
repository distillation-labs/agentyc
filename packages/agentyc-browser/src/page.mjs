import {
  AgentycError,
  CapabilityUnavailableError,
  assertLogicalId,
} from "./errors.mjs";
import {
  invalidArgument,
  normalizeNonNegativeInteger,
  normalizeNow,
  requireLeaseEpoch,
  transportOptions,
} from "./constants.mjs";
import { assertSupportedAction } from "./actions.mjs";

/**
 * Page helper -> canonical action operation. Helpers never invent operations:
 * every entry must resolve through the operation registry, and they all travel
 * as `action.execute`.
 */
export const PAGE_HELPER_OPERATIONS = Object.freeze({
  goto: "navigate",
  click: "click",
  type: "input",
  fill: "input",
  scroll: "scroll",
  evaluate: "evaluate",
});

const SNAPSHOT_MODES = new Set([
  "auto",
  "full",
  "min",
  "compact",
  "focus",
  "delta",
]);
const MAX_URL_BYTES = 4_096;
// Payload fields the host accepts as JSON objects; everything else must be a
// string on the wire.
const TYPED_PAYLOAD_KEYS = new Set([
  "element_ref",
  "ref",
  "provenance",
  "actionability_evidence",
  "evidence",
]);
const PAYLOAD_KEY_ALIASES = Object.freeze({
  elementRef: "element_ref",
  actionabilityEvidence: "actionability_evidence",
});
const URL_MATCHERS = ["exact", "contains", "prefix", "suffix"];

function requireString(value, field, { allowEmpty = false, maxBytes } = {}) {
  if (typeof value !== "string" || (!allowEmpty && value.length === 0)) {
    throw invalidArgument(
      `${field} must be ${allowEmpty ? "a string" : "a non-empty string"}`,
    );
  }
  if (maxBytes !== undefined && Buffer.byteLength(value, "utf8") > maxBytes) {
    throw invalidArgument(`${field} must be at most ${maxBytes} bytes`);
  }
  return value;
}

function payloadValue(key, value) {
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return String(value);
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (TYPED_PAYLOAD_KEYS.has(key) && value && typeof value === "object") {
    return JSON.stringify(value);
  }
  throw invalidArgument(
    `action payload field ${key} must be a string, finite number, or boolean`,
  );
}

/** Build a wire payload (string values) from helper fields, dropping undefined. */
function actionPayload(...sources) {
  const payload = {};
  for (const source of sources) {
    if (source === undefined || source === null) continue;
    if (typeof source !== "object" || Array.isArray(source)) {
      throw invalidArgument("action target fields must be an object");
    }
    for (const [rawKey, value] of Object.entries(source)) {
      if (value === undefined) continue;
      const key = PAYLOAD_KEY_ALIASES[rawKey] ?? rawKey;
      payload[key] = payloadValue(key, value);
    }
  }
  return payload;
}

/** A string target is a selector; an object target is forwarded field by field. */
function targetFields(target) {
  if (target === undefined || target === null) return undefined;
  if (typeof target === "string") {
    return { selector: requireString(target, "target selector") };
  }
  return target;
}

function snapshotParams(options) {
  const params = {};
  if (options.mode !== undefined) {
    if (!SNAPSHOT_MODES.has(options.mode)) {
      throw invalidArgument(
        "mode must be auto, full, min, compact, focus, or delta",
      );
    }
    params.mode = options.mode;
  }
  for (const [option, wire] of [
    ["focus", "focus"],
    ["focusRef", "focus_ref"],
    ["focusElement", "focus_element"],
    ["frameId", "frame_id"],
    ["elementKey", "element_key"],
    ["sinceHash", "since_hash"],
  ]) {
    if (options[option] !== undefined) params[wire] = options[option];
  }
  if (options.maxSerializedBytes !== undefined) {
    params.max_serialized_bytes = normalizeNonNegativeInteger(
      options.maxSerializedBytes,
      "maxSerializedBytes",
    );
  }
  if (options.tokenBudget !== undefined)
    params.token_budget = options.tokenBudget;
  if (options.base !== undefined) params.base = options.base;
  if (options.tokenizer !== undefined) {
    if (options.tokenizer !== "unicode_scalars") {
      throw invalidArgument("tokenizer must be unicode_scalars");
    }
    params.tokenizer = options.tokenizer;
  }
  if (options.metadataOnly !== undefined) {
    if (typeof options.metadataOnly !== "boolean") {
      throw invalidArgument("metadataOnly must be a boolean");
    }
    params.metadata_only = options.metadataOnly;
  }
  return params;
}

function urlCondition(url) {
  if (url instanceof RegExp || typeof url === "function") {
    throw invalidArgument(
      "waitForURL supports exact, contains, prefix, and suffix text matchers only",
    );
  }
  let matcher;
  if (typeof url === "string") {
    matcher = { kind: "exact", value: requireString(url, "url") };
  } else if (url && typeof url === "object" && !Array.isArray(url)) {
    const kinds = URL_MATCHERS.filter((kind) => url[kind] !== undefined);
    if (kinds.length !== 1) {
      throw invalidArgument(
        "url matcher must set exactly one of exact, contains, prefix, or suffix",
      );
    }
    matcher = {
      kind: kinds[0],
      value: requireString(url[kinds[0]], `url.${kinds[0]}`),
    };
  } else {
    throw invalidArgument("url must be a string or a text matcher object");
  }
  return { kind: "url", matcher };
}

/** A logical page handle; browser target identities are intentionally absent. */
export class Page {
  constructor(
    space,
    { pageId = undefined, label = undefined, record = undefined } = {},
  ) {
    this.space = space;
    this._id = pageId ? assertLogicalId(pageId, "page_", "page_id") : undefined;
    this._label = label;
    this.record = record;
    this._createPromise = undefined;
    this._resolvePromise = undefined;
  }

  get id() {
    return this._id;
  }

  get label() {
    return this.record?.label ?? this._label;
  }

  async create(options = {}) {
    if (this._id) return this;
    if (this._resolvePromise) return this._resolvePromise;
    if (!this._label)
      throw new TypeError("a lazy page handle needs a label before creation");
    if (this._createPromise) return this._createPromise;
    this._createPromise = this._create(options).finally(() => {
      this._createPromise = undefined;
    });
    return this._createPromise;
  }

  async _create(options) {
    const leaseEpoch = requireLeaseEpoch(
      options.leaseEpoch ?? this.space.leaseEpoch,
    );
    const result = await this.space.client.request(
      "page.create",
      {
        space_id: this.space.id,
        lease_epoch: leaseEpoch,
        label: this._label,
        now: normalizeNow(options.now),
      },
      transportOptions(options),
    );
    const record = result?.page ?? result;
    this._id = assertLogicalId(
      record?.page_id ?? result?.page_id,
      "page_",
      "page_id",
    );
    this.record = record;
    return this;
  }

  /** Resolve this label to exactly one existing logical page without creating it. */
  async resolve(options = {}) {
    if (this._id) return this;
    if (this._createPromise) return this._createPromise;
    if (!this._label)
      throw new TypeError("a lazy page handle needs a label before resolution");
    if (this._resolvePromise) return this._resolvePromise;
    this._resolvePromise = this.space
      .listPages(options)
      .then((pages) => {
        const matches = pages.filter((page) => page.label === this._label);
        if (matches.length !== 1) {
          const ambiguous = matches.length > 1;
          throw new AgentycError({
            code: ambiguous ? "invalid_argument" : "page_not_found",
            message: ambiguous
              ? `page label ${this._label} is ambiguous in this space`
              : `no existing page has label ${this._label} in this space`,
            retryable: false,
            guidance: "none",
          });
        }
        this._id = matches[0].id;
        this.record = matches[0].record;
        return this;
      })
      .finally(() => {
        this._resolvePromise = undefined;
      });
    return this._resolvePromise;
  }

  async snapshot(options = {}) {
    const hostOptions = snapshotParams(options);
    const leaseEpoch = requireLeaseEpoch(
      options.leaseEpoch ?? this.space.leaseEpoch,
    );
    await this.resolve(options);
    return this.space.client.request(
      "snapshot.read",
      {
        space_id: this.space.id,
        page_id: this._id,
        lease_epoch: leaseEpoch,
        now: normalizeNow(options.now),
        ...hostOptions,
      },
      transportOptions(options),
    );
  }

  async action(operation, payload = {}, options = {}) {
    assertSupportedAction(operation, payload);
    const leaseEpoch = requireLeaseEpoch(
      options.leaseEpoch ?? this.space.leaseEpoch,
    );
    await this.resolve(options);
    return this.space.client.submitAction({
      space_id: this.space.id,
      page_id: this._id,
      lease_epoch: leaseEpoch,
      operation,
      payload,
      request_id: options.requestId,
      action_id: options.actionId,
      idempotency_key: options.idempotencyKey,
      now: options.now,
      deadline_ms: options.deadlineMs,
      postcondition: options.postcondition,
      signal: options.signal,
    });
  }

  _helperAction(helper, payload, options) {
    const operation = PAGE_HELPER_OPERATIONS[helper];
    assertSupportedAction(operation, payload);
    return this.action(operation, payload, options ?? {});
  }

  /** `navigate` action. */
  async goto(url, options = {}) {
    requireString(url, "url", { maxBytes: MAX_URL_BYTES });
    return this._helperAction("goto", actionPayload({ url }), options);
  }

  /**
   * `click` action. A string target is sent as `selector`; an object target
   * (`elementRef`, `selector`, coordinates, ...) is forwarded unchanged.
   */
  async click(target, options = {}) {
    return this._helperAction(
      "click",
      actionPayload(targetFields(target)),
      options,
    );
  }

  /** `input` action carrying `text`. Same wire shape as `fill`. */
  async type(target, text, options = {}) {
    requireString(text, "text", { allowEmpty: true });
    return this._helperAction(
      "type",
      actionPayload(targetFields(target), { text }),
      options,
    );
  }

  /** `input` action carrying `text`. Same wire shape as `type`. */
  async fill(target, text, options = {}) {
    requireString(text, "text", { allowEmpty: true });
    return this._helperAction(
      "fill",
      actionPayload(targetFields(target), { text }),
      options,
    );
  }

  /**
   * `input` action carrying `key`. The host and extension decide whether a
   * key payload is supported; the SDK does not assume native key delivery.
   */
  async press(_key, _options = {}) {
    throw new CapabilityUnavailableError(
      "key presses are not supported by the current host action contract",
      { capability: "keyboard_input" },
    );
  }

  /** `scroll` action; `delta` fields (`x`, `y`, `deltaX`, `deltaY`, ...) are forwarded. */
  async scroll(delta = {}, options = {}) {
    return this._helperAction("scroll", actionPayload(delta), options);
  }

  /**
   * `input` action carrying `value`. The host and extension decide whether a
   * select payload is supported; the SDK does not assume native selection.
   */
  async select(_target, _value, _options = {}) {
    throw new CapabilityUnavailableError(
      "select-option actions are not supported by the current host action contract",
      { capability: "select_option" },
    );
  }

  /**
   * `upload` action; `fields` are forwarded unchanged. The extension may deny
   * uploads, in which case the host error is surfaced as-is.
   */
  async upload(_target, _fields = {}, _options = {}) {
    assertSupportedAction("upload");
    throw new CapabilityUnavailableError(
      "file upload is not enabled by the current extension action policy",
      { capability: "upload" },
    );
  }

  /** `evaluate` action carrying `expression`. */
  async evaluate(expression, options = {}) {
    requireString(expression, "expression", { maxBytes: 65_536 });
    return this._helperAction(
      "evaluate",
      actionPayload({ expression }),
      options,
    );
  }

  /**
   * `wait.for` with a `url` condition. Accepts exact text or a single
   * `exact`/`contains`/`prefix`/`suffix` matcher; regular expressions are not
   * part of the host wait protocol and are rejected.
   */
  async waitForURL(url, options = {}) {
    return this.waitFor(urlCondition(url), options);
  }

  async close(options = {}) {
    const leaseEpoch = requireLeaseEpoch(
      options.leaseEpoch ?? this.space.leaseEpoch,
    );
    await this.resolve(options);
    return this.space.client.request(
      "page.close",
      {
        space_id: this.space.id,
        page_id: this._id,
        lease_epoch: leaseEpoch,
        now: normalizeNow(options.now),
      },
      transportOptions(options),
    );
  }

  async events(options = {}) {
    await this.resolve(options);
    return this.space.events({ ...options, pageId: this._id });
  }

  async waitFor(condition, options = {}) {
    await this.resolve(options);
    return this.space.client.waitFor(condition, {
      ...options,
      spaceId: this.space.id,
      pageId: this._id,
    });
  }
}
