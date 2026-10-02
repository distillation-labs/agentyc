export async function readEvents(client, options = {}) {
  return client.request("events.read", {
    after_epoch: options.afterEpoch,
    after_sequence: options.afterSequence ?? 0,
    space_id: options.spaceId,
    page_id: options.pageId,
    limit: options.limit,
  });
}

export function eventCursor(result) {
  return result?.cursor ?? { broker_epoch: result?.broker_epoch ?? 0, sequence: 0 };
}
