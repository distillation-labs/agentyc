export type Guidance =
  | "none"
  | "retry"
  | "reconcile"
  | "refresh_lease"
  | "resync"
  | "claim"
  | "await_user_control";
export type ErrorCode = string;

export class AgentycError extends Error {
  readonly code: ErrorCode;
  readonly retryable: boolean;
  readonly guidance: Guidance | string;
  readonly details?: unknown;
  readonly transportFailure: boolean;
  constructor(input: {
    code: ErrorCode;
    message: string;
    retryable?: boolean;
    guidance?: Guidance | string;
    details?: unknown;
    transportFailure?: boolean;
  });
}
export class BatchError extends AgentycError {
  readonly failures: readonly BatchFailure[];
  readonly results: readonly unknown[];
}
export class CancelledError extends AgentycError {}
export class ExtensionNotConnectedError extends AgentycError {}
export class CapabilityUnavailableError extends AgentycError {}
export class UnknownOutcomeError extends AgentycError {}
export class ReconciliationRequiredError extends AgentycError {}
export class StaleLeaseError extends AgentycError {}
export class StaleReferenceError extends AgentycError {}
export function isAgentycError(error: unknown): error is AgentycError;
export function methodMayHaveSideEffects(
  method: string,
  requested?: boolean,
): boolean;
export function withRequestIdentity(
  error: AgentycError,
  identity: RequestIdentity,
): AgentycError;

export interface RequestIdentity {
  request_id: string;
  action_id?: string;
  idempotency_key?: string;
}
export interface BatchFailure {
  index: number;
  request_id: string;
  action_id?: string;
  error: AgentycError;
}

export type LogicalSpaceId = string & {
  readonly __logicalSpaceId: unique symbol;
};
export type LogicalPageId = string & {
  readonly __logicalPageId: unique symbol;
};
export type LogicalActionId = string & {
  readonly __logicalActionId: unique symbol;
};

export type ActionOperation =
  | "navigate"
  | "click"
  | "input"
  | "evaluate"
  | "scroll"
  | "wait"
  | "screenshot"
  | "storage_write"
  | "cookie_write"
  | "upload"
  | "close";

export interface OperationCliMapping {
  readonly command: readonly string[];
  readonly option?: string;
}
export interface OperationDefinition {
  readonly key: string;
  readonly kind: "request" | "action";
  readonly operation?: ActionOperation;
  readonly wireMethods: readonly string[];
  readonly sideEffecting: boolean;
  readonly supported: boolean;
  readonly sdk?: string;
  readonly cli?: OperationCliMapping;
  readonly unsupportedReason?: string;
}
export const OPERATION_REGISTRY: readonly OperationDefinition[];
export function actionOperationNames(): ActionOperation[];
export function operationForAction(
  operation: string,
): OperationDefinition | undefined;
export function operationForMethod(
  method: string,
): OperationDefinition | undefined;

export const DEFAULT_LEASE_TTL_MS: number;
export const DEFAULT_WAIT_TIMEOUT_MS: number;
export const MAX_WAIT_TIMEOUT_MS: number;
export const MAX_REQUEST_DEADLINE_MS: number;
export const PROFILE_DISCLOSURE: Readonly<{
  profileScope: "shared_existing_profile";
  sharedStateNotice: "shared_profile_state";
  isolationClaim: false;
  profileDisclosureAcknowledged: true;
}>;

export interface WireRequest {
  protocol: number;
  requests: Array<{
    request_id: string;
    method: string;
    params: Record<string, unknown>;
    deadline_ms?: number;
    idempotency_key?: string;
  }>;
}
export interface WireResponse {
  kind?: "response";
  protocol?: number;
  request_id?: string;
  ok?: boolean;
  result?: unknown;
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
    guidance?: string;
    [key: string]: unknown;
  };
  responses?: Array<{
    request_id?: string;
    ok?: boolean;
    result?: unknown;
    error?: WireResponse["error"];
  }>;
  results?: Array<{
    request_id?: string;
    ok?: boolean;
    result?: unknown;
    error?: WireResponse["error"];
  }>;
  warnings?: string[];
}
export interface TransportRequestOptions {
  signal?: AbortSignal;
  /**
   * Called once, after the first request frame has been written. Never called
   * when encoding or the first write fails.
   */
  onDispatch?: () => void;
}
export interface LocalTransport<R = WireRequest, S = WireResponse> {
  readonly connected?: boolean;
  /** True when the transport reports dispatch through `onDispatch`. */
  readonly dispatchAware?: boolean;
  request(payload: R, options?: TransportRequestOptions): Promise<S>;
  reconnect?(): Promise<unknown>;
  close?(): Promise<void>;
  cancel?(requestIds: string[] | string, reason?: string): Promise<void>;
  resume?(options?: EventsOptions): Promise<unknown>;
  subscribe?(
    listener: (event: unknown) => void,
    options?: EventsOptions,
  ): Promise<() => boolean | void>;
  onEvent?(listener: (event: unknown) => void): () => boolean | void;
  rememberCursor?(cursor: unknown): unknown;
}
export interface LocalProtocolOptions {
  socketPath?: string;
  profile?: string;
  principal?: string;
  clientId?: string;
  clientName?: string;
  clientVersion?: string;
  maxPayloadBytes?: number;
}
export class LocalProtocolTransport implements LocalTransport<
  WireRequest,
  WireResponse
> {
  constructor(options?: LocalProtocolOptions);
  readonly connected: boolean;
  readonly closed: boolean;
  readonly dispatchAware: true;
  readonly helloOk?: unknown;
  connect(): Promise<void>;
  request(
    payload: WireRequest,
    options?: TransportRequestOptions,
  ): Promise<WireResponse>;
  cancel(requestIds: string[] | string, reason?: string): Promise<void>;
  resume(options?: EventsOptions): Promise<unknown>;
  onEvent(listener: (event: unknown) => void): () => boolean;
  rememberCursor(cursor: unknown): unknown;
  subscribe(
    listener: (event: unknown) => void,
    options?: EventsOptions,
  ): Promise<() => boolean>;
  reconnect(): Promise<unknown>;
  close(): Promise<void>;
}
export function createLocalTransport<R = WireRequest, S = WireResponse>(
  handler:
    | LocalTransport<R, S>
    | ((payload: R, options?: TransportRequestOptions) => Promise<S>),
): LocalTransport<R, S>;
export function createLocalProtocolTransport(
  options?: LocalProtocolOptions,
): LocalProtocolTransport;
export const PROTOCOL_VERSION: number;
export const DEFAULT_MAX_PAYLOAD_BYTES: number;

export interface ConnectOptions extends LocalProtocolOptions {
  transport?: LocalTransport;
  handler?: LocalTransport["request"];
  reconnect?: boolean;
  maxReconnects?: number;
}
export function connect(options?: ConnectOptions): Promise<BrowserClient>;

export interface SpaceRecord {
  space_id: LogicalSpaceId;
  label: string;
  lifecycle?: string;
  lease?: LeaseRecord;
  pages?: PageRecord[];
  [key: string]: unknown;
}
export interface PageRecord {
  page_id: LogicalPageId;
  space_id: LogicalSpaceId;
  label: string;
  lifecycle?: string;
  [key: string]: unknown;
}
export interface LeaseRecord {
  lease_epoch: number;
  expires_at?: number;
  renew_by?: number;
  state?: string;
  [key: string]: unknown;
}
export interface ActionReceipt {
  action_id: LogicalActionId;
  space_id: LogicalSpaceId;
  page_id?: LogicalPageId;
  status: string;
  reconciliation_state?: string;
  next_action?: string;
  [key: string]: unknown;
}
export interface RequestOptions {
  signal?: AbortSignal;
  requestId?: string;
  deadlineMs?: number;
}
export interface PageOptions extends RequestOptions {
  leaseEpoch?: number;
  now?: number;
}
export interface LeaseOptions extends RequestOptions {
  ttl?: number;
  now?: number;
  leaseEpoch?: number;
}
export interface SpaceTransitionOptions extends RequestOptions {
  leaseEpoch?: number;
  now?: number;
}
export interface ControlTransitionOptions extends RequestOptions {
  ttl?: number;
  now?: number;
}
export interface ActionOptions extends PageOptions {
  actionId?: LogicalActionId;
  idempotencyKey?: string;
  now?: number;
  postcondition?: unknown;
}
export interface ManagedPageOptions extends PageOptions {
  url?: string;
  title?: string;
}
export interface CreateSpaceOptions extends RequestOptions {
  retention?: unknown;
  acceptSharedProfileDisclosure: true;
}
export interface ReclaimOptions extends LeaseOptions {
  controlTicket: unknown;
}
export interface SubmitActionRequest {
  request_id?: string;
  requestId?: string;
  action_id?: LogicalActionId;
  actionId?: LogicalActionId;
  idempotency_key?: string;
  idempotencyKey?: string;
  space_id: LogicalSpaceId;
  page_id?: LogicalPageId;
  lease_epoch?: number;
  operation: ActionOperation;
  payload?: ActionPayload;
  postcondition?: unknown;
  now?: number;
  deadline_ms?: number;
  deadlineMs?: number;
  signal?: AbortSignal;
}

export type ActionPayload = Record<string, string>;
export type ActionFieldValue = string | number | boolean;
/** Element reference object returned by a snapshot; sent as a JSON-encoded field. */
export type ElementRefObject = Record<string, unknown>;
export interface ActionTargetFields {
  selector?: string;
  elementRef?: ElementRefObject | string;
  element_ref?: ElementRefObject | string;
  ref?: ElementRefObject | string;
  evidence?: ElementRefObject | string;
  actionabilityEvidence?: ElementRefObject | string;
  actionability_evidence?: ElementRefObject | string;
  provenance?: ElementRefObject | string;
  [field: string]: ActionFieldValue | ElementRefObject | undefined;
}
/** A string is a selector; an object is forwarded field by field. */
export type ActionTarget = string | ActionTargetFields;
export type ScrollDelta = ActionTargetFields & {
  x?: ActionFieldValue;
  y?: ActionFieldValue;
  deltaX?: ActionFieldValue;
  deltaY?: ActionFieldValue;
};
export type PageHelperName =
  "goto" | "click" | "type" | "fill" | "scroll" | "evaluate";
export const PAGE_HELPER_OPERATIONS: Readonly<
  Record<PageHelperName, ActionOperation>
>;
export type SnapshotMode =
  "auto" | "full" | "min" | "compact" | "focus" | "delta";
/** Options forwarded to the host's `snapshot.read` context builder. */
export interface SnapshotOptions extends PageOptions {
  mode?: SnapshotMode;
  focus?: string | Record<string, unknown>;
  focusRef?: string | Record<string, unknown>;
  focusElement?: string | Record<string, unknown>;
  frameId?: string;
  elementKey?: string;
  maxSerializedBytes?: number;
  tokenBudget?: Record<string, unknown>;
  base?: Record<string, unknown>;
  tokenizer?: "unicode_scalars";
  metadataOnly?: boolean;
  sinceHash?: string;
}
export interface PressOptions extends ActionOptions {
  target?: ActionTarget;
}
export type UrlMatcher =
  | string
  | { exact: string }
  | { contains: string }
  | { prefix: string }
  | { suffix: string };

export class Page {
  private constructor();
  readonly space: TaskSpace;
  readonly id?: LogicalPageId;
  readonly label?: string;
  readonly record?: PageRecord;
  create(options?: PageOptions): Promise<this>;
  resolve(options?: RequestOptions): Promise<this>;
  snapshot(options?: SnapshotOptions): Promise<unknown>;
  action(
    operation: ActionOperation,
    payload?: ActionPayload,
    options?: ActionOptions,
  ): Promise<ActionReceipt | unknown>;
  goto(url: string, options?: ActionOptions): Promise<ActionReceipt | unknown>;
  click(
    target?: ActionTarget,
    options?: ActionOptions,
  ): Promise<ActionReceipt | unknown>;
  type(
    target: ActionTarget | null | undefined,
    text: string,
    options?: ActionOptions,
  ): Promise<ActionReceipt | unknown>;
  fill(
    target: ActionTarget | null | undefined,
    text: string,
    options?: ActionOptions,
  ): Promise<ActionReceipt | unknown>;
  press(key: string, options?: PressOptions): Promise<never>;
  scroll(
    delta?: ScrollDelta,
    options?: ActionOptions,
  ): Promise<ActionReceipt | unknown>;
  select(
    target: ActionTarget | null | undefined,
    value: string,
    options?: ActionOptions,
  ): Promise<never>;
  upload(
    target: ActionTarget | null | undefined,
    fields?: ActionTargetFields,
    options?: ActionOptions,
  ): Promise<never>;
  evaluate(expression: string, options?: ActionOptions): Promise<never>;
  waitForURL(url: UrlMatcher, options?: WaitOptions): Promise<unknown>;
  close(options?: PageOptions): Promise<unknown>;
  events(options?: EventsOptions): Promise<unknown>;
  waitFor(condition: unknown, options?: WaitOptions): Promise<unknown>;
}

export interface EventsOptions extends RequestOptions {
  afterEpoch?: number;
  afterSequence?: number;
  spaceId?: LogicalSpaceId;
  pageId?: LogicalPageId;
  limit?: number;
}
/**
 * Event cursor for `after`. A bare number is an event sequence in the host's
 * current broker epoch. Mapped to the host's `after_epoch`/`after_sequence`.
 */
export type WaitAfter =
  | number
  | { broker_epoch?: number; sequence?: number }
  | { brokerEpoch?: number; sequence?: number }
  | { afterEpoch?: number; afterSequence?: number }
  | { after_epoch?: number; after_sequence?: number }
  | { cursor: { broker_epoch?: number; sequence?: number } };
export interface WaitOptions extends RequestOptions {
  timeoutMs?: number;
  after?: WaitAfter;
  spaceId?: LogicalSpaceId;
  pageId?: LogicalPageId;
}
export function waitAfterParams(after?: WaitAfter): {
  after_epoch?: number;
  after_sequence?: number;
};
export class TaskSpace {
  private constructor();
  readonly id: LogicalSpaceId;
  readonly label?: string;
  readonly record?: SpaceRecord;
  readonly leaseEpoch?: number;
  page(labelOrId: string): Page;
  newPage(label: string, options?: PageOptions): Promise<Page>;
  newManagedPage(label: string, options?: ManagedPageOptions): Promise<Page>;
  listPages(options?: RequestOptions): Promise<Page[]>;
  inventory(options?: RequestOptions): Promise<unknown>;
  claim(options?: LeaseOptions): Promise<unknown>;
  renew(options?: LeaseOptions): Promise<unknown>;
  takeover(options?: LeaseOptions): Promise<unknown>;
  reclaim(options: ReclaimOptions): Promise<unknown>;
  returnControl(options?: LeaseOptions): Promise<unknown>;
  pause(options: ControlTransitionOptions): Promise<unknown>;
  handoff(options: ControlTransitionOptions): Promise<unknown>;
  finish(options?: SpaceTransitionOptions): Promise<unknown>;
  release(options?: SpaceTransitionOptions): Promise<unknown>;
  actionStatus(
    actionId: LogicalActionId,
    options?: RequestOptions,
  ): Promise<ActionReceipt | unknown>;
  reconcileAction(
    actionId: LogicalActionId,
    options?: PageOptions,
  ): Promise<ActionReceipt | unknown>;
  events(options?: Omit<EventsOptions, "spaceId">): Promise<unknown>;
  waitFor(condition: unknown, options?: WaitOptions): Promise<unknown>;
}

export class BrowserClient {
  readonly transport: LocalTransport;
  readonly connected: boolean;
  /** True after close(); requests fail until reconnect() is called. */
  readonly closed: boolean;
  taskSpace(spaceId: LogicalSpaceId): TaskSpace;
  taskSpace(label: string, options: CreateSpaceOptions): Promise<TaskSpace>;
  createSpace(label: string, options: CreateSpaceOptions): Promise<TaskSpace>;
  listSpaces(options?: RequestOptions): Promise<TaskSpace[]>;
  pruneSpaces(maxCount?: number, options?: RequestOptions): Promise<unknown>;
  hostStatus(options?: RequestOptions): Promise<unknown>;
  events(options?: EventsOptions): Promise<unknown>;
  resumeEvents(options?: EventsOptions): Promise<unknown>;
  subscribeEvents(
    listener: (event: unknown) => void,
    options?: EventsOptions,
  ): Promise<() => boolean | void>;
  actionStatus(
    actionId: LogicalActionId,
    options?: RequestOptions,
  ): Promise<ActionReceipt | unknown>;
  reconcileAction(
    actionId: LogicalActionId,
    leaseEpoch: number,
    now?: number,
    options?: RequestOptions,
  ): Promise<ActionReceipt | unknown>;
  submitAction(request: SubmitActionRequest): Promise<ActionReceipt | unknown>;
  waitFor(condition: unknown, options?: WaitOptions): Promise<unknown>;
  request<T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    options?: RequestOptions & {
      mayHaveSideEffects?: boolean;
      requestId?: string;
      deadlineMs?: number;
      idempotencyKey?: string;
    },
  ): Promise<T>;
  batch<T = unknown>(
    requests: Array<{
      method: string;
      params?: Record<string, unknown>;
      mayHaveSideEffects?: boolean;
      requestId?: string;
      deadlineMs?: number;
      idempotencyKey?: string;
      signal?: AbortSignal;
    }>,
    options?: RequestOptions,
  ): Promise<T[]>;
  cancel(requestId: string | string[], reason?: string): Promise<void>;
  reconnect(): Promise<unknown>;
  close(): Promise<void>;
}

export function actionStatus(
  client: BrowserClient,
  actionId: LogicalActionId,
  options?: RequestOptions,
): Promise<ActionReceipt | unknown>;
export function reconcileAction(
  client: BrowserClient,
  actionId: LogicalActionId,
  leaseEpoch: number,
  now?: number,
  options?: RequestOptions,
): Promise<ActionReceipt | unknown>;
export function submitAction(
  client: BrowserClient,
  request: SubmitActionRequest,
): Promise<ActionReceipt | unknown>;
export function readEvents(
  client: BrowserClient,
  options?: EventsOptions,
): Promise<unknown>;
export function resumeEvents(
  client: BrowserClient,
  options?: EventsOptions,
): Promise<unknown>;
export function subscribeEvents(
  client: BrowserClient,
  listener: (event: unknown) => void,
  options?: EventsOptions,
): Promise<() => boolean | void>;
export function eventCursor(result: unknown): {
  broker_epoch: number;
  sequence: number;
};
export function waitFor(
  client: BrowserClient,
  condition: unknown,
  options?: WaitOptions,
): Promise<unknown>;
