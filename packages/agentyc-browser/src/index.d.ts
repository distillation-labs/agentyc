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
}
export interface LocalTransport<R = WireRequest, S = WireResponse> {
  readonly connected?: boolean;
  request(payload: R, options?: TransportRequestOptions): Promise<S>;
  reconnect?(): Promise<void>;
  close?(): Promise<void>;
  cancel?(requestIds: string[] | string, reason?: string): Promise<void>;
  resume?(options?: EventsOptions): Promise<unknown>;
  subscribe?(
    listener: (event: unknown) => void,
    options?: EventsOptions,
  ): Promise<() => boolean | void>;
  onEvent?(listener: (event: unknown) => void): () => boolean | void;
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
  connect(): Promise<void>;
  request(
    payload: WireRequest,
    options?: TransportRequestOptions,
  ): Promise<WireResponse>;
  cancel(requestIds: string[] | string, reason?: string): Promise<void>;
  resume(options?: EventsOptions): Promise<unknown>;
  onEvent(listener: (event: unknown) => void): () => boolean;
  subscribe(
    listener: (event: unknown) => void,
    options?: EventsOptions,
  ): Promise<() => boolean>;
  reconnect(): Promise<void>;
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
export interface ActionOptions extends PageOptions {
  idempotencyKey?: string;
  postcondition?: unknown;
}

export class Page {
  private constructor();
  readonly space: TaskSpace;
  readonly id?: LogicalPageId;
  readonly label?: string;
  readonly record?: PageRecord;
  create(options?: PageOptions): Promise<this>;
  snapshot(options?: PageOptions): Promise<unknown>;
  action(
    operation: string,
    payload?: Record<string, unknown>,
    options?: ActionOptions,
  ): Promise<ActionReceipt | unknown>;
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
export interface WaitOptions extends RequestOptions {
  timeoutMs?: number;
  after?: unknown;
}
export class TaskSpace {
  private constructor();
  readonly id: LogicalSpaceId;
  readonly label?: string;
  readonly record?: SpaceRecord;
  readonly leaseEpoch?: number;
  page(labelOrId: string): Page;
  newPage(label: string, options?: PageOptions): Promise<Page>;
  listPages(options?: RequestOptions): Promise<Page[]>;
  claim(options?: LeaseOptions): Promise<unknown>;
  renew(options?: LeaseOptions): Promise<unknown>;
  takeover(options?: LeaseOptions): Promise<unknown>;
  returnControl(options?: LeaseOptions): Promise<unknown>;
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
  taskSpace(spaceId: LogicalSpaceId): TaskSpace;
  createSpace(
    label: string,
    options?: { retention?: unknown; signal?: AbortSignal },
  ): Promise<TaskSpace>;
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
  submitAction(
    request: Record<string, unknown>,
  ): Promise<ActionReceipt | unknown>;
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
  reconnect(): Promise<void>;
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
  request: Record<string, unknown>,
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
