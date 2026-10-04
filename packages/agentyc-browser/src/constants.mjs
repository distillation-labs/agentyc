import { AgentycError } from "./errors.mjs";

export const DEFAULT_LEASE_TTL_MS = 60_000;
export const DEFAULT_WAIT_TIMEOUT_MS = 30_000;
export const MAX_WAIT_TIMEOUT_MS = 60_000;
export const MAX_REQUEST_DEADLINE_MS = 24 * 60 * 60 * 1000;

export const PROFILE_DISCLOSURE = Object.freeze({
  profileScope: "shared_existing_profile",
  sharedStateNotice: "shared_profile_state",
  isolationClaim: false,
  profileDisclosureAcknowledged: true,
});

export function invalidArgument(message, details = undefined) {
  return new AgentycError({
    code: "invalid_argument",
    message,
    retryable: false,
    guidance: "none",
    details,
  });
}

export function normalizePositiveInteger(value, field, max = Number.MAX_SAFE_INTEGER) {
  if (
    !Number.isSafeInteger(value) ||
    value <= 0 ||
    value > max
  ) {
    throw invalidArgument(
      `${field} must be a positive safe integer no greater than ${max}`,
    );
  }
  return value;
}

export function normalizeNonNegativeInteger(
  value,
  field,
  max = Number.MAX_SAFE_INTEGER,
) {
  if (!Number.isSafeInteger(value) || value < 0 || value > max) {
    throw invalidArgument(
      `${field} must be a non-negative safe integer no greater than ${max}`,
    );
  }
  return value;
}

export function normalizeLeaseTtl(value) {
  return normalizePositiveInteger(
    value ?? DEFAULT_LEASE_TTL_MS,
    "ttl",
  );
}

export function normalizeNow(value) {
  return normalizeNonNegativeInteger(value ?? Date.now(), "now");
}

export function normalizeWaitTimeout(value) {
  return normalizePositiveInteger(
    value ?? DEFAULT_WAIT_TIMEOUT_MS,
    "timeoutMs",
    MAX_WAIT_TIMEOUT_MS,
  );
}

export function normalizeDeadline(value) {
  if (value === undefined) return undefined;
  return normalizePositiveInteger(
    value,
    "deadlineMs",
    MAX_REQUEST_DEADLINE_MS,
  );
}

export function requireLeaseEpoch(value) {
  return normalizeNonNegativeInteger(value, "leaseEpoch");
}

export function transportOptions(options = {}) {
  return {
    signal: options.signal,
    deadlineMs: options.deadlineMs,
    requestId: options.requestId,
  };
}
