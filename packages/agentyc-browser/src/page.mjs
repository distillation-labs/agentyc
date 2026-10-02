import { assertLogicalId } from "./errors.mjs";

/** A logical page handle; browser target identities are intentionally absent. */
export class Page {
  constructor(space, { pageId = undefined, label = undefined, record = undefined } = {}) {
    this.space = space;
    this._id = pageId ? assertLogicalId(pageId, "page_", "page_id") : undefined;
    this._label = label;
    this.record = record;
  }

  get id() {
    return this._id;
  }

  get label() {
    return this.record?.label ?? this._label;
  }

  async create(options = {}) {
    if (this._id) return this;
    if (!this._label) throw new TypeError("a lazy page handle needs a label before creation");
    const result = await this.space.client.request("page.create", {
      space_id: this.space.id,
      lease_epoch: options.leaseEpoch ?? this.space.leaseEpoch,
      label: this._label,
      now: options.now,
    });
    const record = result?.page ?? result;
    this._id = assertLogicalId(record?.page_id ?? result?.page_id, "page_", "page_id");
    this.record = record;
    return this;
  }

  async snapshot(options = {}) {
    await this.create(options);
    return this.space.client.request("snapshot", {
      space_id: this.space.id,
      page_id: this._id,
      lease_epoch: options.leaseEpoch ?? this.space.leaseEpoch,
      now: options.now,
    });
  }

  async action(operation, payload = {}, options = {}) {
    await this.create(options);
    return this.space.client.submitAction({
      space_id: this.space.id,
      page_id: this._id,
      lease_epoch: options.leaseEpoch ?? this.space.leaseEpoch,
      operation,
      payload,
      idempotency_key: options.idempotencyKey,
      postcondition: options.postcondition,
    });
  }

  async close(options = {}) {
    await this.create(options);
    return this.space.client.request("page.close", {
      space_id: this.space.id,
      page_id: this._id,
      lease_epoch: options.leaseEpoch ?? this.space.leaseEpoch,
      now: options.now,
    }, { mayHaveSideEffects: true });
  }

  async events(options = {}) {
    await this.create(options);
    return this.space.events({ ...options, pageId: this._id });
  }

  async waitFor(condition, options = {}) {
    return this.space.waitFor(condition, options);
  }
}
