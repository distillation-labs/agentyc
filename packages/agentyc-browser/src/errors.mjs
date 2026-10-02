const DEFAULT_GUIDANCE = "none";

/** A stable error returned by the logical local host contract. */
export class AgentycError extends Error {
  constructor({
    code,
    message,
    retryable = false,
    guidance = DEFAULT_GUIDANCE,
    details = undefined,
  }) {
    super(message);
    this.name = "AgentycError";
    this.code = code;
    this.retryable = Boolean(retryable);
    this.guidance = guidance;
    this.details = details;
  }
}

export class ExtensionNotConnectedError extends AgentycError {
  constructor(
    message = "the browser extension bridge is not connected",
    details,
  ) {
    super({
      code: "extension_not_connected",
      message,
      retryable: true,
      guidance: "retry",
      details,
    });
    this.name = "ExtensionNotConnectedError";
  }
}

export class CapabilityUnavailableError extends AgentycError {
  constructor(
    message = "the requested host capability is unavailable",
    details,
  ) {
    super({
      code: "capability_unavailable",
      message,
      retryable: false,
      guidance: "none",
      details,
    });
    this.name = "CapabilityUnavailableError";
  }
}

export class UnknownOutcomeError extends AgentycError {
  constructor(
    message = "the action crossed the execution boundary but its outcome is unknown",
    details,
  ) {
    super({
      code: "unknown_outcome",
      message,
      retryable: false,
      guidance: "reconcile",
      details: { ...details, reconciliation_required: true },
    });
    this.name = "UnknownOutcomeError";
  }
}

export class ReconciliationRequiredError extends AgentycError {
  constructor(
    message = "the action must be reconciled before another mutation",
    details,
  ) {
    super({
      code: "reconciliation_required",
      message,
      retryable: false,
      guidance: "reconcile",
      details,
    });
    this.name = "ReconciliationRequiredError";
  }
}

export class StaleLeaseError extends AgentycError {
  constructor(message = "the lease epoch is stale", details) {
    super({
      code: "stale_lease",
      message,
      retryable: false,
      guidance: "refresh_lease",
      details,
    });
    this.name = "StaleLeaseError";
  }
}

export class StaleReferenceError extends AgentycError {
  constructor(message = "the logical reference is stale", details) {
    super({
      code: "stale_ref",
      message,
      retryable: false,
      guidance: "resync",
      details,
    });
    this.name = "StaleReferenceError";
  }
}

const ERROR_TYPES = {
  extension_not_connected: ExtensionNotConnectedError,
  capability_unavailable: CapabilityUnavailableError,
  unknown_outcome: UnknownOutcomeError,
  reconciliation_required: ReconciliationRequiredError,
  stale_lease: StaleLeaseError,
  stale_ref: StaleReferenceError,
};

/** Convert a host error object into a typed SDK error. */
export function mapWireError(error) {
  if (error instanceof AgentycError) return error;
  const code =
    typeof error?.code === "string" ? error.code : "native_host_unavailable";
  const message =
    typeof error?.message === "string"
      ? error.message
      : "local host request failed";
  const Type = ERROR_TYPES[code];
  if (Type) return new Type(message, error);
  return new AgentycError({
    code,
    message,
    retryable: Boolean(error?.retryable),
    guidance:
      typeof error?.guidance === "string" ? error.guidance : DEFAULT_GUIDANCE,
    details: error,
  });
}

/** Map a transport failure without guessing whether a side effect occurred. */
export function mapTransportError(
  error,
  { mayHaveSideEffects = false, details = undefined } = {},
) {
  if (error instanceof AgentycError) return error;
  const message = error instanceof Error ? error.message : String(error);
  if (mayHaveSideEffects)
    return new UnknownOutcomeError(
      "local transport disconnected after a side-effecting request",
      { ...details, cause: message },
    );
  return new AgentycError({
    code: "native_host_unavailable",
    message: `local host transport unavailable: ${message}`,
    retryable: true,
    guidance: "retry",
    details: { cause: message },
  });
}

export function isAgentycError(error) {
  return error instanceof AgentycError;
}

export function assertLogicalId(value, prefix, field) {
  if (
    typeof value !== "string" ||
    !value.startsWith(prefix) ||
    value.length <= prefix.length
  ) {
    throw new AgentycError({
      code: "invalid_argument",
      message: `${field} must be a logical ${prefix.replace("_", "")} identity`,
      retryable: false,
      guidance: "none",
    });
  }
  return value;
}
