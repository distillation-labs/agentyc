import { assertLogicalId } from "./errors.mjs";
import {
  normalizeWaitTimeout,
  transportOptions,
  invalidArgument,
} from "./constants.mjs";

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
      ...(normalizedOptions.after !== undefined
        ? { after: normalizedOptions.after }
        : {}),
      ...(spaceId !== undefined ? { space_id: spaceId } : {}),
      ...(pageId !== undefined ? { page_id: pageId } : {}),
    },
    transportOptions(normalizedOptions),
  );
}
