import { assertLogicalId } from "./errors.mjs";
import {
  normalizeNonNegativeInteger,
  normalizeWaitTimeout,
  transportOptions,
  invalidArgument,
} from "./constants.mjs";

function firstDefined(source, keys) {
  for (const key of keys) {
    if (source[key] !== undefined) return source[key];
  }
  return undefined;
}

/**
 * Map the SDK `after` option onto the host's `after_epoch`/`after_sequence`
 * wait parameters. A bare number is an event sequence in the host's current
 * broker epoch; an object may be an event cursor or a `{ afterEpoch,
 * afterSequence }` pair.
 */
export function waitAfterParams(after) {
  if (after === undefined) return {};
  if (typeof after === "number") {
    return {
      after_sequence: normalizeNonNegativeInteger(after, "after"),
    };
  }
  if (!after || typeof after !== "object" || Array.isArray(after)) {
    throw invalidArgument(
      "after must be an event sequence number or an event cursor object",
    );
  }
  const source =
    after.cursor && typeof after.cursor === "object" ? after.cursor : after;
  const epoch = firstDefined(source, [
    "broker_epoch",
    "brokerEpoch",
    "after_epoch",
    "afterEpoch",
    "epoch",
  ]);
  const sequence = firstDefined(source, [
    "sequence",
    "after_sequence",
    "afterSequence",
  ]);
  if (epoch === undefined && sequence === undefined) {
    throw invalidArgument(
      "after must contain a broker epoch and/or an event sequence",
    );
  }
  return {
    ...(epoch !== undefined
      ? { after_epoch: normalizeNonNegativeInteger(epoch, "after epoch") }
      : {}),
    ...(sequence !== undefined
      ? {
          after_sequence: normalizeNonNegativeInteger(
            sequence,
            "after sequence",
          ),
        }
      : {}),
  };
}

export async function waitFor(client, condition, options = {}) {
  const normalizedOptions = options ?? {};
  const spaceId =
    normalizedOptions.spaceId !== undefined
      ? assertLogicalId(normalizedOptions.spaceId, "space_", "spaceId")
      : undefined;
  if (normalizedOptions.pageId !== undefined && spaceId === undefined) {
    throw invalidArgument("pageId requires spaceId");
  }
  const pageId =
    normalizedOptions.pageId !== undefined
      ? assertLogicalId(normalizedOptions.pageId, "page_", "pageId")
      : undefined;
  return client.request(
    "wait.for",
    {
      condition,
      timeout_ms: normalizeWaitTimeout(normalizedOptions.timeoutMs),
      ...waitAfterParams(normalizedOptions.after),
      ...(spaceId !== undefined ? { space_id: spaceId } : {}),
      ...(pageId !== undefined ? { page_id: pageId } : {}),
    },
    transportOptions(normalizedOptions),
  );
}
