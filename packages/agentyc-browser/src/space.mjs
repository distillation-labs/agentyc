import { Page } from "./page.mjs";
import { assertLogicalId } from "./errors.mjs";

function logicalSpace(result) {
  const record = result?.space ?? result;
  return {
    id: assertLogicalId(
      record?.space_id ?? result?.space_id,
      "space_",
      "space_id",
    ),
    record,
  };
}

/** A logical task-space handle; it never contains a browser handle. */
export class TaskSpace {
  constructor(client, spaceId, record = undefined) {
    this.client = client;
    this._id = assertLogicalId(spaceId, "space_", "space_id");
    this.record = record;
    this.leaseEpoch = record?.lease?.lease_epoch;
  }

  get id() {
    return this._id;
  }

  get label() {
    return this.record?.label;
  }

  page(labelOrId) {
    if (typeof labelOrId !== "string" || labelOrId.length === 0)
      throw new TypeError("page label or logical page id is required");
    const pageId = labelOrId.startsWith("page_") ? labelOrId : undefined;
    return new Page(this, { pageId, label: pageId ? undefined : labelOrId });
  }

  async newPage(label, options = {}) {
    const result = await this.client.request(
      "page.create",
      {
        space_id: this.id,
        lease_epoch: options.leaseEpoch ?? this.leaseEpoch,
        label,
        now: options.now,
      },
      { signal: options.signal },
    );
    return this._pageFromResult(result, label);
  }

  async listPages(options = {}) {
    const result = await this.client.request(
      "page.list",
      { space_id: this.id },
      { signal: options.signal },
    );
    return (result?.pages ?? []).map(
      (page) =>
        new Page(this, {
          pageId: page.page_id,
          label: page.label,
          record: page,
        }),
    );
  }

  async claim(options = {}) {
    const result = await this.client.request(
      "space.claim",
      {
        space_id: this.id,
        ttl: options.ttl,
        now: options.now,
      },
      { signal: options.signal },
    );
    this.leaseEpoch =
      result?.lease?.lease_epoch ?? result?.lease_epoch ?? this.leaseEpoch;
    this.record = result?.space ?? this.record;
    return result;
  }

  async renew(options = {}) {
    const result = await this.client.request(
      "space.renew",
      {
        space_id: this.id,
        lease_epoch: options.leaseEpoch ?? this.leaseEpoch,
        ttl: options.ttl,
        now: options.now,
      },
      { signal: options.signal },
    );
    this.leaseEpoch = result?.lease?.lease_epoch ?? this.leaseEpoch;
    return result;
  }

  async takeover(options = {}) {
    const result = await this.client.request(
      "space.takeover",
      {
        space_id: this.id,
        ttl: options.ttl,
        now: options.now,
      },
      { signal: options.signal },
    );
    this.leaseEpoch = result?.lease_epoch ?? this.leaseEpoch;
    return result;
  }

  async returnControl(options = {}) {
    return this.client.request(
      "space.return",
      {
        space_id: this.id,
        lease_epoch: options.leaseEpoch ?? this.leaseEpoch,
        now: options.now,
      },
      { signal: options.signal },
    );
  }

  async finish(options = {}) {
    return this.client.request(
      "space.finish",
      {
        space_id: this.id,
        lease_epoch: options.leaseEpoch ?? this.leaseEpoch,
        now: options.now,
      },
      { signal: options.signal },
    );
  }

  async release(options = {}) {
    return this.client.request(
      "space.release",
      {
        space_id: this.id,
        lease_epoch: options.leaseEpoch ?? this.leaseEpoch,
        now: options.now,
      },
      { signal: options.signal },
    );
  }

  async actionStatus(actionId, options = {}) {
    return this.client.actionStatus(actionId, options);
  }

  async reconcileAction(actionId, options = {}) {
    return this.client.reconcileAction(
      actionId,
      options.leaseEpoch ?? this.leaseEpoch,
      options.now,
      options,
    );
  }

  async events(options = {}) {
    return this.client.events({ ...options, spaceId: this.id });
  }

  async waitFor(condition, options = {}) {
    return this.client.waitFor(condition, options);
  }

  _pageFromResult(result, fallbackLabel) {
    const record = result?.page ?? result;
    const pageId = assertLogicalId(
      record?.page_id ?? result?.page_id,
      "page_",
      "page_id",
    );
    return new Page(this, {
      pageId,
      label: record?.label ?? fallbackLabel,
      record,
    });
  }
}

export { logicalSpace };
