//! Bounded local protocol dispatcher over the core envelope/frame types.
//!
//! The owner-only Unix socket transport uses this dispatcher for agent and MCP
//! clients. Native Messaging remains a separate little-endian transport.

use std::{
    collections::BTreeMap,
    time::{Duration, Instant},
};

use agentyc_core::{
    ActionId, ActionOperation, ActionRequest, ArtifactBeginEnvelope, ArtifactChunkEnvelope,
    ArtifactEndEnvelope, ArtifactEnvelope, ArtifactTransferBudget, BrokerEpoch, ContentHash,
    DEFAULT_MAX_FRAME_PAYLOAD_BYTES, Envelope, EventCursor, EventKind, EventScope, EventSequence,
    FrameDecoder, GenerationWatermark, HelloEnvelope, IdempotencyKey, LeaseEpoch,
    MAX_ARTIFACT_CHUNK_BYTES, MAX_CONTROL_FRAME_PAYLOAD_BYTES, PageId, Postcondition,
    ProfileDisclosure, ReconcileToken, RequestEnvelope, RequestId, ResponseEnvelope,
    ResumeEnvelope, SpaceId, Timestamp, decode_frame, decode_utf8, encode_frame,
};

use agentyc_core::protocol::ResumeWatermark;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::{
    broker::{Broker, Connection, canonical_action_hash},
    error::HostError,
    events::EventQuery,
    leases::ControlTicket,
};

/// Bounded in-process protocol server state for one client connection.
pub struct ProtocolServer {
    broker: Broker,
    max_payload_bytes: usize,
    max_artifact_chunk_bytes: usize,
    connection: Option<Connection>,
    request_states: BTreeMap<RequestId, RequestRecord>,
    artifact_transfers: BTreeMap<String, LocalArtifactTransfer>,
    artifact_budget: ArtifactTransferBudget,
}

impl std::fmt::Debug for ProtocolServer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ProtocolServer")
            .field("max_payload_bytes", &self.max_payload_bytes)
            .field("max_artifact_chunk_bytes", &self.max_artifact_chunk_bytes)
            .field("connected", &self.connection.is_some())
            .finish_non_exhaustive()
    }
}

impl ProtocolServer {
    /// Construct a dispatcher using the core's default payload bound.
    pub fn new(broker: Broker) -> Self {
        Self::with_max_payload(broker, DEFAULT_MAX_FRAME_PAYLOAD_BYTES)
            .expect("core default frame bound is valid")
    }

    /// Construct a dispatcher with a smaller explicit payload bound.
    pub fn with_max_payload(broker: Broker, max_payload_bytes: usize) -> Result<Self, HostError> {
        Self::with_limits(broker, max_payload_bytes, MAX_ARTIFACT_CHUNK_BYTES)
    }

    /// Construct a dispatcher with checked frame and artifact bounds.
    pub fn with_limits(
        broker: Broker,
        max_payload_bytes: usize,
        max_artifact_chunk_bytes: usize,
    ) -> Result<Self, HostError> {
        validate_payload_limit(max_payload_bytes)?;
        validate_artifact_limit(max_artifact_chunk_bytes)?;
        Ok(Self {
            broker,
            max_payload_bytes,
            max_artifact_chunk_bytes,
            connection: None,
            request_states: BTreeMap::new(),
            artifact_transfers: BTreeMap::new(),
            artifact_budget: ArtifactTransferBudget::default(),
        })
    }

    /// Return the broker behind this protocol session.
    pub fn broker(&self) -> &Broker {
        &self.broker
    }

    /// Return the host-assigned connection after a successful hello.
    pub fn connection(&self) -> Option<&Connection> {
        self.connection.as_ref()
    }

    /// Dispatch one decoded core envelope.
    pub fn dispatch(&mut self, envelope: Envelope) -> Result<Vec<Envelope>, HostError> {
        validate_typed_envelope(&envelope)?;
        match envelope {
            Envelope::Hello(hello) => self.handle_hello(hello),
            Envelope::Request(request) => Ok(vec![self.handle_request(request)]),
            Envelope::Resume(resume) => self.handle_resume(resume),
            Envelope::Cancel(cancel) => {
                if let Err(error) = cancel.validate() {
                    return Ok(vec![Envelope::Response(ResponseEnvelope::failure(
                        cancel.request_id,
                        error,
                    ))]);
                }
                Ok(vec![self.handle_cancel(cancel)])
            }
            Envelope::ArtifactBegin(begin) => self.handle_artifact_begin(begin),
            Envelope::ArtifactChunk(chunk) => self.handle_artifact_chunk(chunk),
            Envelope::ArtifactEnd(end) => self.handle_artifact_end(end),
            Envelope::HelloOk(_)
            | Envelope::Response(_)
            | Envelope::Event(_)
            | Envelope::Artifact(_) => Err(agentyc_core::CoreError::invalid_argument(
                "envelope kind is not client-admissible",
            )
            .into()),
        }
    }

    /// Dispatch an envelope and return structured JSON wire values.
    ///
    /// The legacy [`Self::dispatch`] method remains available for callers that
    /// use the generic string-map envelope. This method is the typed transport
    /// path for resume and other structured results.
    pub fn dispatch_typed(&mut self, envelope: Envelope) -> Result<Vec<Value>, HostError> {
        self.dispatch(envelope)?
            .iter()
            .map(response_to_wire_value)
            .collect()
    }

    /// Decode one exact length-delimited frame, dispatch it, and encode responses.
    pub fn handle_frame(&mut self, frame: &[u8]) -> Result<Vec<u8>, HostError> {
        let payload = decode_frame(frame, self.max_payload_bytes)?;
        self.handle_payload(payload)
    }

    /// Dispatch one already-decoded payload from a streaming transport.
    pub fn handle_payload(&mut self, payload: &[u8]) -> Result<Vec<u8>, HostError> {
        let text = decode_utf8(payload)?;
        let value: Value = serde_json::from_str(text)?;
        validate_wire_envelope(&value)?;
        let envelope: Envelope = serde_json::from_value(value)?;
        let responses = self.dispatch_typed(envelope)?;
        let mut output = Vec::new();
        for value in responses {
            let json = serde_json::to_vec(&value)?;
            let encoded = encode_frame(&json, self.max_payload_bytes)?;
            output.extend_from_slice(&encoded);
        }
        Ok(output)
    }

    /// Close this protocol session and revoke only its authority ticket.
    pub fn close(&mut self) -> Result<(), HostError> {
        if let Some(connection) = self.connection.take() {
            self.broker.disconnect(connection.authority())?;
        }
        Ok(())
    }

    fn handle_hello(&mut self, hello: HelloEnvelope) -> Result<Vec<Envelope>, HostError> {
        if self.connection.is_some() {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::PermissionDenied,
                "duplicate hello on an active local connection",
            )
            .into());
        }
        let connection = self.broker.hello(&hello)?;
        let response = Envelope::HelloOk(connection.hello_ok());
        self.connection = Some(connection);
        Ok(vec![response])
    }

    fn handle_request(&mut self, request: RequestEnvelope) -> Envelope {
        let request_id = request.request_id.clone();
        if let Err(error) = validate_request(&request) {
            return Envelope::Response(ResponseEnvelope::failure(request_id, error));
        }
        let fingerprint = match request_fingerprint(&request) {
            Ok(fingerprint) => fingerprint,
            Err(error) => return Envelope::Response(ResponseEnvelope::failure(request_id, error)),
        };
        if let Some(record) = self.request_states.get(&request_id) {
            let error = match record.state {
                RequestState::CancelledQueued => agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::Cancelled,
                    "request was cancelled before dispatch",
                ),
                RequestState::Dispatched | RequestState::Completed
                    if record.fingerprint != fingerprint =>
                {
                    agentyc_core::CoreError::invalid_argument(
                        "request_id was reused with a different request hash or logical context",
                    )
                }
                RequestState::Dispatched | RequestState::Completed => agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::InvalidArgument,
                    "duplicate request_id; dispatched requests are never replayed",
                ),
            };
            return Envelope::Response(ResponseEnvelope::failure(request_id, error));
        }
        if self.request_states.len() >= MAX_TRACKED_REQUESTS {
            return Envelope::Response(ResponseEnvelope::failure(
                request_id,
                agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::MessageTooLarge,
                    "connection request tracking limit reached",
                ),
            ));
        }
        self.request_states.insert(
            request_id.clone(),
            RequestRecord {
                state: RequestState::Dispatched,
                fingerprint,
            },
        );
        let started = Instant::now();
        let deadline = request
            .deadline_ms
            .and_then(|milliseconds| started.checked_add(Duration::from_millis(milliseconds)));
        let result = if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            Err(HostError::Core(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::Timeout,
                "request deadline elapsed before dispatch",
            )))
        } else {
            self.execute_request(request, deadline)
        };
        if let Some(record) = self.request_states.get_mut(&request_id) {
            record.state = RequestState::Completed;
        }
        match result {
            Ok(result) => Envelope::Response(ResponseEnvelope::success(request_id, result)),
            Err(error) => {
                Envelope::Response(ResponseEnvelope::failure(request_id, error.as_core_error()))
            }
        }
    }

    fn handle_cancel(&mut self, cancel: agentyc_core::CancelEnvelope) -> Envelope {
        let request_id = cancel.request_id;
        let result = match self.request_states.get(&request_id) {
            Some(record) if record.state == RequestState::CancelledQueued => Ok("true"),
            Some(record) if record.state == RequestState::Dispatched => {
                Err(agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::UnknownOutcome,
                    "request has already been dispatched and cannot be safely cancelled",
                ))
            }
            Some(record) if record.state == RequestState::Completed => {
                Err(agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::InvalidArgument,
                    "request has already completed",
                ))
            }
            None if self.request_states.len() < MAX_TRACKED_REQUESTS => {
                let fingerprint = cancelled_request_fingerprint(&request_id);
                self.request_states.insert(
                    request_id.clone(),
                    RequestRecord {
                        state: RequestState::CancelledQueued,
                        fingerprint,
                    },
                );
                Ok("true")
            }
            None => Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "connection request tracking limit reached",
            )),
            Some(_) => Err(agentyc_core::CoreError::invalid_argument(
                "request state is invalid",
            )),
        };
        Envelope::Response(match result {
            Ok(cancelled) => ResponseEnvelope::success(
                request_id,
                BTreeMap::from([("cancelled".to_owned(), cancelled.to_owned())]),
            ),
            Err(error) => ResponseEnvelope::failure(request_id, error),
        })
    }

    fn handle_artifact_begin(
        &mut self,
        begin: ArtifactBeginEnvelope,
    ) -> Result<Vec<Envelope>, HostError> {
        let connection_epoch = self
            .connection
            .as_ref()
            .ok_or_else(|| {
                agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::PermissionDenied,
                    "hello is required before artifact transfer",
                )
            })?
            .connection_epoch;
        let key = begin.artifact_id.to_string();
        if self.artifact_transfers.contains_key(&key) {
            return Err(agentyc_core::CoreError::invalid_argument(
                "artifact transfer was declared more than once",
            )
            .into());
        }
        if self.artifact_transfers.len() >= MAX_ARTIFACT_TRANSFERS {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "too many concurrent artifact transfers",
            )
            .into());
        }
        if u64::from(begin.chunk_size) > self.max_artifact_chunk_bytes as u64 {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "artifact declaration exceeds the configured host bound",
            )
            .into());
        }
        let progress = agentyc_core::ArtifactTransferProgress::begin(&begin, connection_epoch)?;
        self.artifact_transfers.insert(
            key,
            LocalArtifactTransfer {
                progress,
                begin,
                bytes: Vec::new(),
            },
        );
        Ok(Vec::new())
    }

    fn handle_artifact_chunk(
        &mut self,
        chunk: ArtifactChunkEnvelope,
    ) -> Result<Vec<Envelope>, HostError> {
        let key = chunk.artifact_id.to_string();
        let size = chunk.bytes.len();
        if size > self.max_artifact_chunk_bytes {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "artifact chunk exceeds the configured host bound",
            )
            .into());
        }
        self.artifact_budget.receive(size)?;
        let Some(transfer) = self.artifact_transfers.get_mut(&key) else {
            let _ = self.artifact_budget.release(size);
            return Err(agentyc_core::CoreError::invalid_argument(
                "artifact chunk has no active begin",
            )
            .into());
        };
        if let Err(error) = transfer.progress.accept_chunk(&chunk) {
            let _ = self.artifact_budget.release(size);
            return Err(error.into());
        }
        transfer.bytes.extend_from_slice(&chunk.bytes);
        Ok(Vec::new())
    }

    fn handle_artifact_end(
        &mut self,
        end: ArtifactEndEnvelope,
    ) -> Result<Vec<Envelope>, HostError> {
        let key = end.artifact_id.to_string();
        let transfer = self.artifact_transfers.remove(&key).ok_or_else(|| {
            agentyc_core::CoreError::invalid_argument("artifact end has no active begin")
        })?;
        let result = (|| {
            transfer.progress.validate_complete()?;
            end.validate_against(&transfer.begin, &transfer.bytes)?;
            Ok::<(), agentyc_core::CoreError>(())
        })();
        let release = self.artifact_budget.release(transfer.bytes.len());
        result.and(release)?;
        Ok(Vec::new())
    }

    fn execute_request(
        &self,
        request: RequestEnvelope,
        deadline: Option<Instant>,
    ) -> Result<BTreeMap<String, String>, HostError> {
        if !request.has_valid_method() {
            return Err(agentyc_core::CoreError::invalid_argument("invalid request method").into());
        }
        validate_params(&request.params)?;
        let connection = self.connection.as_ref().ok_or_else(|| {
            agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::PermissionDenied,
                "hello is required before requests",
            )
        })?;
        let authority = connection.authority();
        let mut result = BTreeMap::new();

        match request.method.as_str() {
            "space.list" => {
                let spaces = self.broker.list_spaces(authority)?;
                put_json(&mut result, "spaces", &spaces)?;
            }
            "space.create" => {
                let label = required(&request.params, "label")?;
                let disclosure = profile_disclosure(&request.params)?;
                let space = self.broker.create_space_with_disclosure(
                    authority,
                    label.to_owned(),
                    disclosure,
                )?;
                put_json(&mut result, "space", &space)?;
                put_json(&mut result, "space_id", &space.space_id)?;
                put_json(&mut result, "lifecycle", &space.lifecycle)?;
            }
            "space.prune" => {
                let max_count = parse_u64(&request.params, "max_count")?.unwrap_or(0) as usize;
                let pruned = self.broker.prune_released_spaces(authority, max_count)?;
                put_json(&mut result, "pruned", &pruned)?;
            }
            "lease.acquire" | "space.claim" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let ttl = required_u64(&request.params, "ttl")?;
                let grant = self.broker.acquire_lease(&space_id, authority, now, ttl)?;
                put_json(&mut result, "space_id", &grant.space_id)?;
                put_json(&mut result, "lease", &grant.lease)?;
                put_json(
                    &mut result,
                    "lifecycle",
                    &agentyc_core::SpaceLifecycle::AgentOwned,
                )?;
            }
            "lease.renew" | "space.renew" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let ttl = required_u64(&request.params, "ttl")?;
                let grant = self
                    .broker
                    .renew_lease(&space_id, authority, lease_epoch, now, ttl)?;
                put_json(&mut result, "space_id", &grant.space_id)?;
                put_json(&mut result, "lease", &grant.lease)?;
                put_json(
                    &mut result,
                    "lifecycle",
                    &agentyc_core::SpaceLifecycle::AgentOwned,
                )?;
            }
            "space.takeover" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let ttl = required_u64(&request.params, "ttl")?;
                let takeover = self.broker.takeover(&space_id, authority, now, ttl)?;
                put_json(&mut result, "space_id", &takeover.space_id)?;
                put_json(&mut result, "lease_epoch", &takeover.lease_epoch)?;
                put_json(
                    &mut result,
                    "fence_acknowledged",
                    &takeover.fence_acknowledged,
                )?;
                put_json(&mut result, "lifecycle", &takeover.lifecycle)?;
            }
            "space.pause" | "space.handoff" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let ttl = required_u64(&request.params, "ttl")?;
                let space = if request.method == "space.pause" {
                    self.broker.pause_space(&space_id, authority, now, ttl)?
                } else {
                    self.broker.handoff_space(&space_id, authority, now, ttl)?
                };
                put_json(&mut result, "space", &space)?;
                put_json(&mut result, "space_id", &space.space_id)?;
                put_json(&mut result, "lifecycle", &space.lifecycle)?;
            }
            "space.return" | "space.return_control" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let returned =
                    self.broker
                        .return_control(&space_id, authority, lease_epoch, now)?;
                let control_ticket = serde_json::json!({
                    "space_id": returned.control_ticket.space_id(),
                    "broker_epoch": returned.control_ticket.broker_epoch(),
                    "fence_epoch": returned.control_ticket.fence_epoch(),
                    "token": returned.control_ticket.token().to_string(),
                });
                put_json(&mut result, "space_id", &returned.space_id)?;
                put_json(&mut result, "released_epoch", &returned.released_epoch)?;
                put_json(&mut result, "fence_epoch", &returned.fence_epoch)?;
                put_json(&mut result, "lifecycle", &returned.lifecycle)?;
                put_json(&mut result, "control_ticket", &control_ticket)?;
            }
            "space.takeover_with_control_ticket" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let control_ticket = parse_control_ticket(&request.params)?;
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let ttl = required_u64(&request.params, "ttl")?;
                let takeover = self.broker.takeover_with_control_ticket(
                    &space_id,
                    authority,
                    &control_ticket,
                    now,
                    ttl,
                )?;
                put_json(&mut result, "space_id", &takeover.space_id)?;
                put_json(&mut result, "lease_epoch", &takeover.lease_epoch)?;
                put_json(
                    &mut result,
                    "fence_acknowledged",
                    &takeover.fence_acknowledged,
                )?;
                put_json(&mut result, "lifecycle", &takeover.lifecycle)?;
            }
            "space.finish" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let space = self
                    .broker
                    .finish_space(&space_id, authority, lease_epoch, now)?;
                put_json(&mut result, "space", &space)?;
                put_json(&mut result, "space_id", &space.space_id)?;
                put_json(&mut result, "lifecycle", &space.lifecycle)?;
            }
            "space.release" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let space = self
                    .broker
                    .release_space(&space_id, authority, lease_epoch, now)?;
                put_json(&mut result, "space", &space)?;
                put_json(&mut result, "space_id", &space.space_id)?;
                put_json(&mut result, "lifecycle", &space.lifecycle)?;
            }
            "page.create" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let label = required(&request.params, "label")?;
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let page = self.broker.create_page_at(
                    &space_id,
                    authority,
                    lease_epoch,
                    label.to_owned(),
                    now,
                )?;
                put_json(&mut result, "page", &page)?;
                put_json(&mut result, "page_id", &page.page_id)?;
                put_json(&mut result, "space_id", &page.space_id)?;
            }
            "page.create_managed" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let label = required(&request.params, "label")?;
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let page = self.broker.create_managed_page(
                    &space_id,
                    authority,
                    lease_epoch,
                    label.to_owned(),
                    now,
                    request.params.get("url").map(String::as_str),
                    request.params.get("title").map(String::as_str),
                )?;
                put_json(&mut result, "page", &page)?;
                put_json(&mut result, "page_id", &page.page_id)?;
                put_json(&mut result, "space_id", &page.space_id)?;
            }
            "page.close" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let page_id = parse_page(required(&request.params, "page_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let page =
                    self.broker
                        .close_page(&space_id, &page_id, authority, lease_epoch, now)?;
                put_json(&mut result, "page", &page)?;
                put_json(&mut result, "page_id", &page.page_id)?;
                put_json(&mut result, "space_id", &page.space_id)?;
            }
            "page.list" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let space = self.broker.describe_space(authority, &space_id)?;
                put_json(&mut result, "space_id", &space.space_id)?;
                put_json(&mut result, "pages", &space.pages)?;
            }
            "page.inventory" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let inventory = self.broker.page_inventory(authority, &space_id)?;
                put_json(&mut result, "space_id", &space_id)?;
                put_json(&mut result, "pages", &inventory.pages)?;
                put_json(&mut result, "groups", &inventory.groups)?;
                put_json(&mut result, "safety", &inventory.safety)?;
                put_json(
                    &mut result,
                    "recovery_observed",
                    &inventory.recovery_observed,
                )?;
            }
            "action.execute" => {
                let action_request = action_request_from_params(&request.params)?;
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let action = self.broker.execute_action(action_request, authority, now)?;
                put_json(&mut result, "action_id", &action.receipt.action_id)?;
                put_json(&mut result, "receipt", &action.receipt)?;
            }
            "snapshot" | "snapshot.read" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let page_id = parse_page(required(&request.params, "page_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let snapshot =
                    self.broker
                        .read_snapshot(&space_id, &page_id, authority, lease_epoch, now)?;
                put_json(&mut result, "space_id", &space_id)?;
                put_json(&mut result, "page_id", &page_id)?;
                put_json(&mut result, "snapshot", &snapshot.envelope)?;
                put_json(&mut result, "cache_state", &snapshot.cache_state)?;
                put_json(&mut result, "scan_performed", &snapshot.scan_performed)?;
            }
            "action.status" => {
                let action_id = parse_action(required(&request.params, "action_id")?)?;
                let receipt = self.broker.action_status(authority, &action_id)?;
                put_json(&mut result, "action_id", &receipt.action_id)?;
                put_json(&mut result, "receipt", &receipt)?;
            }
            "action.reconcile" => {
                let action_id = parse_action(required(&request.params, "action_id")?)?;
                let lease_epoch =
                    agentyc_core::LeaseEpoch::new(required_u64(&request.params, "lease_epoch")?);
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let reconciled =
                    self.broker
                        .reconcile_action(&action_id, authority, lease_epoch, now)?;
                put_json(&mut result, "action_id", &reconciled.receipt.action_id)?;
                put_json(&mut result, "receipt", &reconciled.receipt)?;
            }
            "events.cursor" => {
                let cursor = self.broker.event_cursor(authority)?;
                put_json(&mut result, "broker_epoch", &cursor.broker_epoch)?;
                put_json(&mut result, "sequence", &cursor.sequence)?;
                put_json(&mut result, "cursor", &cursor)?;
            }
            "events.read" | "events.resume" => {
                let current = self.broker.event_cursor(authority)?;
                let after = EventCursor {
                    broker_epoch: agentyc_core::BrokerEpoch::new(
                        parse_u64(&request.params, "after_epoch")?
                            .unwrap_or_else(|| current.broker_epoch.get()),
                    ),
                    sequence: EventSequence::new(
                        parse_u64(&request.params, "after_sequence")?.unwrap_or(0),
                    ),
                };
                let scope = request_scope(&request.params)?;
                let batch = self
                    .broker
                    .resume_events(authority, EventQuery { after, scope })?;
                let limit = parse_limit(&request.params)?;
                let events: Vec<_> = batch.events.into_iter().take(limit).collect();
                put_json(&mut result, "broker_epoch", &batch.broker_epoch)?;
                put_json(&mut result, "cursor", &batch.cursor)?;
                put_json(
                    &mut result,
                    "resume",
                    &match batch.result {
                        agentyc_core::ResumeResult::Accepted => "accepted",
                        agentyc_core::ResumeResult::ResyncRequired => "resync_required",
                    },
                )?;
                put_json(&mut result, "events", &events)?;
            }
            "wait.for" => {
                let condition = parse_wait_condition(required(&request.params, "condition")?)?;
                let timeout_ms = required_u64(&request.params, "timeout_ms")?;
                if timeout_ms == 0 || timeout_ms > MAX_WAIT_TIMEOUT_MS {
                    return Err(agentyc_core::CoreError::invalid_argument(format!(
                        "timeout_ms must be between 1 and {MAX_WAIT_TIMEOUT_MS}"
                    ))
                    .into());
                }
                let scope = request_scope(&request.params)?;
                let authority = connection.authority();
                let started = Instant::now();
                let operation_deadline = started + Duration::from_millis(timeout_ms);
                let deadline = deadline.map_or(operation_deadline, |request_deadline| {
                    request_deadline.min(operation_deadline)
                });
                let cursor = self.broker.event_cursor(authority)?;
                let mut router =
                    crate::EventRouter::new_at(cursor, crate::RouterLimits::new(MAX_EVENT_LIMIT));
                let effective_timeout_ms = deadline
                    .checked_duration_since(started)
                    .map_or(0, |duration| {
                        duration.as_millis().min(u128::from(u64::MAX)) as u64
                    });
                let mut engine = crate::WaitEngine::new(ProtocolClock { started });
                let mut registration = engine.register_scoped(
                    &router,
                    condition,
                    scope.clone(),
                    Timestamp::new(effective_timeout_ms.min(timeout_ms)),
                    crate::CancellationToken::new(),
                );
                loop {
                    let notification_generation = self.broker.event_notification_generation();
                    let batch = self.broker.resume_events(
                        authority,
                        EventQuery {
                            after: registration.cursor,
                            scope: scope.clone(),
                        },
                    )?;
                    router.ingest_batch(batch)?;
                    match engine.poll(&mut registration, &router) {
                        crate::WaitOutcome::Matched { event, cursor } => {
                            put_json(&mut result, "wait", &"matched")?;
                            put_json(&mut result, "event", &event)?;
                            put_json(&mut result, "cursor", &cursor)?;
                            break;
                        }
                        crate::WaitOutcome::Pending { .. } => {
                            self.broker
                                .wait_for_event_change_until(notification_generation, deadline);
                        }
                        outcome => {
                            return Err(crate::WaitEngine::<ProtocolClock>::outcome_error(
                                &outcome,
                            )
                            .expect("terminal wait outcomes have errors")
                            .into());
                        }
                    }
                }
            }
            "host.status" => {
                let lifecycle = self.broker.lifecycle()?;
                let broker_epoch = self.broker.broker_epoch()?;
                let capabilities = self.broker.capabilities()?;
                let bridge_status = self.broker.bridge_status()?;
                let profile_instance_id = bridge_status
                    .as_ref()
                    .and_then(|status| status.profile_instance_id.clone())
                    .or_else(|| {
                        connection
                            .authority()
                            .profile_binding_id()
                            .map(ToString::to_string)
                    });
                put_json(&mut result, "broker_epoch", &broker_epoch)?;
                put_json(
                    &mut result,
                    "connection_epoch",
                    &connection.connection_epoch,
                )?;
                put_json(&mut result, "lifecycle", &host_lifecycle_name(lifecycle))?;
                put_json(&mut result, "capabilities", &capabilities)?;
                put_json(&mut result, "profile_scope", &"existing_user_profile")?;
                put_json(&mut result, "profile_bound", &profile_instance_id.is_some())?;
                put_optional_json(
                    &mut result,
                    "profile_instance_id",
                    profile_instance_id.as_ref(),
                )?;
                if let Some(status) = bridge_status {
                    put_optional_json(
                        &mut result,
                        "extension_version",
                        status.extension_version.as_ref(),
                    )?;
                    put_optional_json(
                        &mut result,
                        "worker_instance_epoch",
                        status.worker_instance_epoch.as_ref(),
                    )?;
                    put_optional_json(
                        &mut result,
                        "browser_session_epoch",
                        status.browser_session_epoch.as_ref(),
                    )?;
                }
            }
            _ => {
                return Err(
                    agentyc_core::CoreError::invalid_argument("unknown host method").into(),
                );
            }
        }
        if deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::Timeout,
                "request deadline elapsed before a definitive response",
            )
            .into());
        }
        Ok(result)
    }

    fn handle_resume(&mut self, resume: ResumeEnvelope) -> Result<Vec<Envelope>, HostError> {
        let connection = self.connection.as_ref().ok_or_else(|| {
            agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::PermissionDenied,
                "hello is required before resume",
            )
        })?;
        let batch = self.broker.resume_events(
            connection.authority(),
            EventQuery::all(EventCursor {
                broker_epoch: resume.after.broker_epoch,
                sequence: resume.after.sequence,
            }),
        )?;
        let request_id = RequestId::from_suffix(format!(
            "resume-{}-{}",
            batch.cursor.broker_epoch.get(),
            batch.cursor.sequence.get()
        ))
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
        let result = BTreeMap::from([
            (
                "resume_result".to_owned(),
                serde_json::to_string(&batch.result)?,
            ),
            ("cursor".to_owned(), serde_json::to_string(&batch.cursor)?),
            ("events".to_owned(), serde_json::to_string(&batch.events)?),
        ]);
        Ok(vec![Envelope::Response(ResponseEnvelope::success(
            request_id, result,
        ))])
    }
}

/// In-process protocol client helper for later CLI/MCP transports.
#[derive(Debug)]
pub struct ProtocolClient {
    decoder: FrameDecoder,
    max_payload_bytes: usize,
    max_artifact_chunk_bytes: usize,
}

impl ProtocolClient {
    /// Construct a client with the core default payload bound.
    pub fn new() -> Self {
        Self::with_max_payload(DEFAULT_MAX_FRAME_PAYLOAD_BYTES)
            .expect("core default frame bound is valid")
    }

    /// Construct a client with an explicit payload bound.
    pub fn with_max_payload(max_payload_bytes: usize) -> Result<Self, HostError> {
        Self::with_limits(max_payload_bytes, MAX_ARTIFACT_CHUNK_BYTES)
    }

    /// Construct a client with checked frame and artifact bounds.
    pub fn with_limits(
        max_payload_bytes: usize,
        max_artifact_chunk_bytes: usize,
    ) -> Result<Self, HostError> {
        validate_payload_limit(max_payload_bytes)?;
        validate_artifact_limit(max_artifact_chunk_bytes)?;
        Ok(Self {
            decoder: FrameDecoder::new(max_payload_bytes),
            max_payload_bytes,
            max_artifact_chunk_bytes,
        })
    }

    /// Encode a core envelope into one bounded frame.
    pub fn encode(&self, envelope: &Envelope) -> Result<Vec<u8>, HostError> {
        validate_core_envelope(envelope)?;
        match envelope {
            Envelope::Artifact(artifact) => {
                validate_artifact_envelope(artifact, self.max_artifact_chunk_bytes)?;
            }
            Envelope::ArtifactBegin(begin)
                if u64::from(begin.chunk_size) > self.max_artifact_chunk_bytes as u64 =>
            {
                return Err(agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::MessageTooLarge,
                    "artifact declaration exceeds the configured host bound",
                )
                .into());
            }
            Envelope::ArtifactChunk(chunk) if chunk.bytes.len() > self.max_artifact_chunk_bytes => {
                return Err(agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::MessageTooLarge,
                    "artifact chunk exceeds the configured host bound",
                )
                .into());
            }
            _ => {}
        }
        let value = serde_json::to_value(envelope)?;
        validate_wire_envelope(&value)?;
        let json = serde_json::to_vec(&value)?;
        Ok(encode_frame(&json, self.max_payload_bytes)?)
    }

    /// Feed fragmented or coalesced frames and decode every complete envelope.
    ///
    /// This retains the original string-map API. Structured result values are
    /// represented as JSON strings here for callers that depend on the legacy
    /// generic envelope type; [`Self::feed_typed`] exposes the wire values.
    pub fn feed(&mut self, bytes: &[u8]) -> Result<Vec<Envelope>, HostError> {
        self.feed_values(bytes)?
            .into_iter()
            .map(|value| serde_json::from_value(legacy_wire_value(value)?).map_err(HostError::Json))
            .collect()
    }

    /// Feed frames and retain structured JSON result/payload values exactly as
    /// they appeared on the wire.
    pub fn feed_typed(&mut self, bytes: &[u8]) -> Result<Vec<Value>, HostError> {
        self.feed_values(bytes)
    }

    fn feed_values(&mut self, bytes: &[u8]) -> Result<Vec<Value>, HostError> {
        let frames = self.decoder.feed(bytes)?;
        frames
            .into_iter()
            .map(|frame| {
                let text = decode_utf8(&frame)?;
                let value: Value = serde_json::from_str(text)?;
                validate_wire_envelope(&value)?;
                Ok(value)
            })
            .collect()
    }

    /// Reject a partial frame at stream end.
    pub fn finish(&self) -> Result<(), HostError> {
        Ok(self.decoder.finish()?)
    }
}

impl Default for ProtocolClient {
    fn default() -> Self {
        Self::new()
    }
}

/// Names used by later local IPC adapters.
pub type LocalProtocolServer = ProtocolServer;
/// Names used by later local IPC adapters.
pub type LocalProtocolClient = ProtocolClient;

fn validate_payload_limit(max_payload_bytes: usize) -> Result<(), HostError> {
    if max_payload_bytes == 0 || max_payload_bytes > MAX_CONTROL_FRAME_PAYLOAD_BYTES {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::MessageTooLarge,
            format!(
                "control frame payload bound must be between 1 and {MAX_CONTROL_FRAME_PAYLOAD_BYTES}"
            ),
        )
        .into());
    }
    Ok(())
}

fn validate_artifact_limit(max_artifact_chunk_bytes: usize) -> Result<(), HostError> {
    if max_artifact_chunk_bytes == 0 || max_artifact_chunk_bytes > MAX_ARTIFACT_CHUNK_BYTES {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::MessageTooLarge,
            format!("artifact chunk bound must be between 1 and {MAX_ARTIFACT_CHUNK_BYTES}"),
        )
        .into());
    }
    Ok(())
}

fn validate_artifact_envelope(
    artifact: &ArtifactEnvelope,
    max_artifact_chunk_bytes: usize,
) -> Result<(), HostError> {
    artifact.validate()?;
    if artifact.bytes.len() > max_artifact_chunk_bytes {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::MessageTooLarge,
            "artifact chunk exceeds the configured host bound",
        )
        .into());
    }
    Ok(())
}

fn validate_core_envelope(envelope: &Envelope) -> Result<(), HostError> {
    validate_typed_envelope(envelope)
}

pub(crate) fn validate_typed_envelope(envelope: &Envelope) -> Result<(), HostError> {
    envelope.validate()?;
    match envelope {
        Envelope::Hello(hello) => hello.validate_handshake()?,
        Envelope::HelloOk(hello_ok) => hello_ok.validate_handshake()?,
        Envelope::Request(request) => validate_request(request)?,
        Envelope::Response(response) => validate_response(response)?,
        Envelope::Event(event) => event.validate_scope()?,
        Envelope::ArtifactBegin(begin) => begin.validate()?,
        Envelope::ArtifactChunk(chunk) => chunk.validate()?,
        Envelope::ArtifactEnd(end) => end.validate()?,
        Envelope::Artifact(artifact) => artifact.validate()?,
        Envelope::Cancel(cancel) => cancel.validate()?,
        Envelope::Resume(_) => {}
    }
    Ok(())
}

fn validate_request(request: &RequestEnvelope) -> Result<(), agentyc_core::CoreError> {
    if request.protocol != agentyc_core::PROTOCOL_VERSION {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::ProtocolMismatch,
            "request protocol is unsupported",
        ));
    }
    if !request.has_valid_method() {
        return Err(agentyc_core::CoreError::invalid_argument(
            "request method is invalid",
        ));
    }
    if let Some(deadline_ms) = request.deadline_ms
        && (deadline_ms == 0 || deadline_ms > MAX_REQUEST_DEADLINE_MS)
    {
        return Err(agentyc_core::CoreError::invalid_argument(format!(
            "deadline_ms must be between 1 and {MAX_REQUEST_DEADLINE_MS}"
        )));
    }
    validate_params(&request.params).map_err(|error| error.as_core_error())
}

fn validate_response(response: &ResponseEnvelope) -> Result<(), agentyc_core::CoreError> {
    if response.protocol != agentyc_core::PROTOCOL_VERSION {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::ProtocolMismatch,
            "response protocol is unsupported",
        ));
    }
    if response.warnings.len() > MAX_RESPONSE_WARNINGS
        || response
            .warnings
            .iter()
            .any(|warning| warning.is_empty() || warning.len() > MAX_WARNING_BYTES)
    {
        return Err(agentyc_core::CoreError::invalid_argument(
            "response warnings are empty or exceed their bound",
        ));
    }
    match (
        response.ok,
        response.result.is_some(),
        response.error.is_some(),
    ) {
        (true, true, false) | (false, false, true) => Ok(()),
        _ => Err(agentyc_core::CoreError::invalid_argument(
            "response result and error fields do not match ok",
        )),
    }
}

fn request_fingerprint(
    request: &RequestEnvelope,
) -> Result<RequestFingerprint, agentyc_core::CoreError> {
    let bytes = serde_json::to_vec(request).map_err(|error| {
        agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::InvalidJson,
            format!("request cannot be fingerprinted: {error}"),
        )
    })?;
    let context = [
        request.method.as_str(),
        request
            .params
            .get("space_id")
            .map(String::as_str)
            .unwrap_or(""),
        request
            .params
            .get("page_id")
            .map(String::as_str)
            .unwrap_or(""),
        request
            .params
            .get("lease_epoch")
            .map(String::as_str)
            .unwrap_or(""),
        request
            .params
            .get("action_id")
            .map(String::as_str)
            .unwrap_or(""),
        request
            .params
            .get("idempotency_key")
            .map(String::as_str)
            .unwrap_or(""),
        request
            .params
            .get("request_hash")
            .map(String::as_str)
            .unwrap_or(""),
        request
            .params
            .get("context")
            .map(String::as_str)
            .unwrap_or(""),
    ]
    .join("\\u{1f}");
    Ok(RequestFingerprint {
        hash: ContentHash::from_bytes(&bytes),
        method: request.method.clone(),
        context,
    })
}

fn cancelled_request_fingerprint(request_id: &RequestId) -> RequestFingerprint {
    RequestFingerprint {
        hash: ContentHash::from_bytes(request_id.as_str().as_bytes()),
        method: "cancel".to_owned(),
        context: request_id.to_string(),
    }
}

fn response_to_wire_value(envelope: &Envelope) -> Result<Value, HostError> {
    let mut value = serde_json::to_value(envelope)?;
    if value.get("kind").and_then(Value::as_str) == Some("response")
        && let Some(result) = value.get_mut("result").and_then(Value::as_object_mut)
    {
        for key in ["resume_result", "cursor", "events"] {
            let Some(Value::String(encoded)) = result.get(key).cloned() else {
                continue;
            };
            if let Ok(decoded) = serde_json::from_str::<Value>(&encoded) {
                result.insert(key.to_owned(), decoded);
            }
        }
    }
    validate_wire_envelope(&value)?;
    Ok(value)
}

pub(crate) fn legacy_wire_value(mut value: Value) -> Result<Value, HostError> {
    validate_wire_envelope(&value)?;
    if value.get("kind").and_then(Value::as_str) == Some("response")
        && let Some(result) = value.get_mut("result").and_then(Value::as_object_mut)
    {
        for child in result.values_mut() {
            if !child.is_string() {
                *child = Value::String(serde_json::to_string(child)?);
            }
        }
    }
    Ok(value)
}

pub(crate) fn validate_wire_envelope(value: &Value) -> Result<(), HostError> {
    let object = value.as_object().ok_or_else(|| {
        agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::InvalidJson,
            "local protocol envelope must be an object",
        )
    })?;
    let protocol = wire_u64(object, "protocol")?;
    if protocol != u64::from(agentyc_core::PROTOCOL_VERSION) {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::ProtocolMismatch,
            "local protocol version is unsupported",
        )
        .into());
    }
    let kind = wire_string(object, "kind", 32)?;
    let common = ["protocol", "kind"];
    let allowed: &[&str] = match kind.as_str() {
        "hello" => &[
            "protocol",
            "kind",
            "supported_protocols",
            "principal_id",
            "resume_from",
            "client_metadata",
        ],
        "hello_ok" => &[
            "protocol",
            "kind",
            "broker_epoch",
            "connection_epoch",
            "capabilities",
            "resume",
            "host_metadata",
        ],
        "request" => &[
            "protocol",
            "kind",
            "request_id",
            "method",
            "params",
            "deadline_ms",
            "idempotency_key",
        ],
        "response" => &[
            "protocol",
            "kind",
            "request_id",
            "ok",
            "result",
            "error",
            "warnings",
        ],
        "event" => &[
            "protocol",
            "kind",
            "event_id",
            "broker_epoch",
            "sequence",
            "scope",
            "event",
            "generation",
            "dirty_reason",
            "coalesced",
            "resync_required",
            "payload",
        ],
        "artifact" => &[
            "protocol",
            "kind",
            "artifact_id",
            "request_id",
            "artifact_kind",
            "chunk_sequence",
            "final_chunk",
            "bytes",
        ],
        "artifact_begin" => &[
            "protocol",
            "kind",
            "artifact_id",
            "request_id",
            "artifact_kind",
            "total_bytes",
            "chunk_size",
            "chunk_count",
            "digest_algorithm",
            "digest",
            "redacted",
        ],
        "artifact_chunk" => &[
            "protocol",
            "kind",
            "artifact_id",
            "connection_epoch",
            "chunk_sequence",
            "bytes",
        ],
        "artifact_end" => &[
            "protocol",
            "kind",
            "artifact_id",
            "total_bytes",
            "chunk_count",
            "digest_algorithm",
            "digest",
        ],
        "cancel" => &["protocol", "kind", "request_id", "reason"],
        "resume" => &["protocol", "kind", "after"],
        _ => {
            return Err(agentyc_core::CoreError::invalid_argument(
                "unknown local protocol envelope kind",
            )
            .into());
        }
    };
    let _ = common;
    wire_reject_unknown(object, allowed)?;
    match kind.as_str() {
        "hello" => {
            wire_array(object, "supported_protocols", MAX_SUPPORTED_PROTOCOLS)?;
            wire_string(object, "principal_id", MAX_ID_BYTES)?;
            if let Some(resume) = wire_optional_object(object, "resume_from")? {
                validate_resume_watermark_wire(resume)?;
            }
            if let Some(metadata) = wire_optional_object(object, "client_metadata")? {
                validate_client_metadata_wire(metadata)?;
            }
        }
        "hello_ok" => {
            wire_positive_u64(object, "broker_epoch")?;
            wire_positive_u64(object, "connection_epoch")?;
            wire_string_array(object, "capabilities", MAX_CAPABILITIES)?;
            let resume = wire_object(object, "resume")?;
            validate_resume_result_wire(resume)?;
            if let Some(metadata) = wire_optional_object(object, "host_metadata")? {
                validate_host_metadata_wire(metadata)?;
            }
        }
        "request" => {
            wire_string(object, "request_id", MAX_ID_BYTES)?;
            let method = wire_string(object, "method", 128)?;
            if !valid_wire_method(&method) {
                return Err(
                    agentyc_core::CoreError::invalid_argument("request method is invalid").into(),
                );
            }
            let params = wire_object(object, "params")?;
            if params.len() > MAX_LOGICAL_PARAMS || params.values().any(|value| !value.is_string())
            {
                return Err(agentyc_core::CoreError::invalid_argument(
                    "request params must be a bounded string map",
                )
                .into());
            }
            if let Some(deadline) = object.get("deadline_ms")
                && !deadline.is_null()
                && (!deadline.is_u64()
                    || deadline.as_u64() == Some(0)
                    || deadline.as_u64().unwrap_or(0) > MAX_REQUEST_DEADLINE_MS)
            {
                return Err(agentyc_core::CoreError::invalid_argument(
                    "request deadline_ms is outside its bound",
                )
                .into());
            }
            wire_optional_string_or_null(object, "idempotency_key", MAX_ID_BYTES)?;
        }
        "response" => {
            wire_string(object, "request_id", MAX_ID_BYTES)?;
            let ok = object.get("ok").and_then(Value::as_bool).ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument("response ok is required")
            })?;
            let result = object.get("result").ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument("response result is required")
            })?;
            let error = object.get("error").ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument("response error is required")
            })?;
            if !error.is_null() {
                let error = error.as_object().ok_or_else(|| {
                    agentyc_core::CoreError::invalid_argument(
                        "response error must be an object or null",
                    )
                })?;
                wire_reject_unknown(error, &["code", "retryable", "guidance", "message"])?;
                wire_string(error, "code", 64)?;
                wire_bool(error, "retryable")?;
                wire_string(error, "guidance", 64)?;
                wire_string(error, "message", MAX_WARNING_BYTES)?;
            }
            if (ok && result.is_null())
                || (!ok && !result.is_null())
                || (ok && !error.is_null())
                || (!ok && error.is_null())
            {
                return Err(agentyc_core::CoreError::invalid_argument(
                    "response result and error do not match ok",
                )
                .into());
            }
            let warnings = wire_array(object, "warnings", MAX_RESPONSE_WARNINGS)?;
            if warnings.iter().any(|warning| {
                warning
                    .as_str()
                    .is_none_or(|text| text.is_empty() || text.len() > MAX_WARNING_BYTES)
            }) {
                return Err(agentyc_core::CoreError::invalid_argument(
                    "response warnings are invalid",
                )
                .into());
            }
        }
        "event" => {
            wire_string(object, "event_id", MAX_ID_BYTES)?;
            wire_positive_u64(object, "broker_epoch")?;
            wire_positive_u64(object, "sequence")?;
            let scope = wire_object(object, "scope")?;
            wire_reject_unknown(scope, &["space_id", "page_id"])?;
            if let Some(space_id) = scope.get("space_id")
                && !space_id.is_null()
            {
                if space_id.as_str().is_none_or(|value| value.is_empty()) {
                    return Err(agentyc_core::CoreError::invalid_argument(
                        "event scope space_id is invalid",
                    )
                    .into());
                }
            }
            if let Some(page_id) = scope.get("page_id")
                && !page_id.is_null()
            {
                if scope.get("space_id").and_then(Value::as_str).is_none() {
                    return Err(agentyc_core::CoreError::invalid_argument(
                        "event scope page_id requires space_id",
                    )
                    .into());
                }
                if page_id.as_str().is_none_or(|value| value.is_empty()) {
                    return Err(agentyc_core::CoreError::invalid_argument(
                        "event scope page_id is invalid",
                    )
                    .into());
                }
            }
            wire_string(object, "event", 64)?;
            let generation = wire_object(object, "generation")?;
            wire_reject_unknown(
                generation,
                &[
                    "space_generation",
                    "page_generation",
                    "navigation_generation",
                    "document_generation",
                    "snapshot_version",
                ],
            )?;
            wire_bool(object, "coalesced")?;
            wire_bool(object, "resync_required")?;
            wire_object(object, "payload")?;
        }
        "artifact" => {
            wire_string(object, "artifact_id", MAX_ID_BYTES)?;
            wire_optional_string_or_null(object, "request_id", MAX_ID_BYTES)?;
            wire_string(object, "artifact_kind", 32)?;
            wire_u64(object, "chunk_sequence")?;
            wire_bool(object, "final_chunk")?;
            wire_bytes(object, "bytes", MAX_ARTIFACT_CHUNK_BYTES)?;
        }
        "artifact_begin" => {
            wire_string(object, "artifact_id", MAX_ID_BYTES)?;
            wire_optional_string_or_null(object, "request_id", MAX_ID_BYTES)?;
            wire_string(object, "artifact_kind", 32)?;
            wire_u64(object, "total_bytes")?;
            wire_u64(object, "chunk_size")?;
            wire_u64(object, "chunk_count")?;
            wire_string(object, "digest_algorithm", 32)?;
            wire_string(object, "digest", 64)?;
            wire_bool(object, "redacted")?;
        }
        "artifact_chunk" => {
            wire_string(object, "artifact_id", MAX_ID_BYTES)?;
            wire_positive_u64(object, "connection_epoch")?;
            wire_u64(object, "chunk_sequence")?;
            wire_bytes(object, "bytes", MAX_ARTIFACT_CHUNK_BYTES)?;
        }
        "artifact_end" => {
            wire_string(object, "artifact_id", MAX_ID_BYTES)?;
            wire_u64(object, "total_bytes")?;
            wire_u64(object, "chunk_count")?;
            wire_string(object, "digest_algorithm", 32)?;
            wire_string(object, "digest", 64)?;
        }
        "cancel" => {
            wire_string(object, "request_id", MAX_ID_BYTES)?;
            wire_optional_string_or_null(object, "reason", MAX_CANCEL_REASON_BYTES)?;
        }
        "resume" => {
            wire_object(object, "after")?;
        }
        _ => unreachable!(),
    }
    Ok(())
}

fn wire_reject_unknown(object: &Map<String, Value>, allowed: &[&str]) -> Result<(), HostError> {
    if let Some(key) = object.keys().find(|key| !allowed.contains(&key.as_str())) {
        return Err(agentyc_core::CoreError::invalid_argument(format!(
            "unknown local protocol envelope field {key}"
        ))
        .into());
    }
    Ok(())
}

fn wire_string(object: &Map<String, Value>, key: &str, max: usize) -> Result<String, HostError> {
    object
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= max)
        .map(str::to_owned)
        .ok_or_else(|| {
            agentyc_core::CoreError::invalid_argument(format!("{key} is invalid")).into()
        })
}

fn wire_optional_string_or_null(
    object: &Map<String, Value>,
    key: &str,
    max: usize,
) -> Result<(), HostError> {
    if let Some(value) = object.get(key)
        && !value.is_null()
        && (value
            .as_str()
            .is_none_or(|text| text.is_empty() || text.len() > max))
    {
        return Err(agentyc_core::CoreError::invalid_argument(format!("{key} is invalid")).into());
    }
    Ok(())
}

fn wire_u64(object: &Map<String, Value>, key: &str) -> Result<u64, HostError> {
    object.get(key).and_then(Value::as_u64).ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(format!("{key} is invalid")).into()
    })
}

fn wire_positive_u64(object: &Map<String, Value>, key: &str) -> Result<u64, HostError> {
    let value = wire_u64(object, key)?;
    if value == 0 {
        return Err(
            agentyc_core::CoreError::invalid_argument(format!("{key} must be positive")).into(),
        );
    }
    Ok(value)
}

fn wire_bool(object: &Map<String, Value>, key: &str) -> Result<bool, HostError> {
    object.get(key).and_then(Value::as_bool).ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(format!("{key} is invalid")).into()
    })
}

fn wire_object<'a>(
    object: &'a Map<String, Value>,
    key: &str,
) -> Result<&'a Map<String, Value>, HostError> {
    object.get(key).and_then(Value::as_object).ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(format!("{key} must be an object")).into()
    })
}

fn wire_optional_object<'a>(
    object: &'a Map<String, Value>,
    key: &str,
) -> Result<Option<&'a Map<String, Value>>, HostError> {
    let Some(value) = object.get(key) else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    value.as_object().map(Some).ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(format!("{key} must be an object or null")).into()
    })
}

fn validate_resume_watermark_wire(object: &Map<String, Value>) -> Result<(), HostError> {
    wire_reject_unknown(object, &["broker_epoch", "sequence"])?;
    wire_positive_u64(object, "broker_epoch")?;
    wire_u64(object, "sequence")?;
    Ok(())
}

fn validate_resume_result_wire(object: &Map<String, Value>) -> Result<(), HostError> {
    wire_reject_unknown(object, &["kind"])?;
    let kind = wire_string(object, "kind", 32)?;
    if kind != "accepted" && kind != "resync_required" {
        return Err(
            agentyc_core::CoreError::invalid_argument("resume result kind is invalid").into(),
        );
    }
    Ok(())
}

fn validate_client_metadata_wire(object: &Map<String, Value>) -> Result<(), HostError> {
    wire_reject_unknown(
        object,
        &[
            "client_id",
            "client_name",
            "client_version",
            "connection_nonce",
            "profile_binding_id",
        ],
    )?;
    if let Some(value) = object.get("client_id")
        && !value.is_null()
    {
        wire_string(object, "client_id", MAX_ID_BYTES)?;
    }
    if let Some(value) = object.get("client_name")
        && !value.is_null()
    {
        wire_string(object, "client_name", 128)?;
    }
    if let Some(value) = object.get("client_version")
        && !value.is_null()
    {
        wire_string(object, "client_version", 128)?;
    }
    if let Some(value) = object.get("connection_nonce")
        && !value.is_null()
    {
        wire_string(object, "connection_nonce", MAX_ID_BYTES)?;
    }
    if let Some(value) = object.get("profile_binding_id")
        && !value.is_null()
    {
        wire_string(object, "profile_binding_id", MAX_ID_BYTES)?;
    }
    Ok(())
}

fn validate_host_metadata_wire(object: &Map<String, Value>) -> Result<(), HostError> {
    wire_reject_unknown(
        object,
        &[
            "host_name",
            "host_version",
            "connection_nonce",
            "profile_binding_id",
        ],
    )?;
    if let Some(value) = object.get("host_name")
        && !value.is_null()
    {
        wire_string(object, "host_name", 128)?;
    }
    if let Some(value) = object.get("host_version")
        && !value.is_null()
    {
        wire_string(object, "host_version", 128)?;
    }
    if let Some(value) = object.get("connection_nonce")
        && !value.is_null()
    {
        wire_string(object, "connection_nonce", MAX_ID_BYTES)?;
    }
    if let Some(value) = object.get("profile_binding_id")
        && !value.is_null()
    {
        wire_string(object, "profile_binding_id", MAX_ID_BYTES)?;
    }
    Ok(())
}

fn wire_array<'a>(
    object: &'a Map<String, Value>,
    key: &str,
    max: usize,
) -> Result<&'a Vec<Value>, HostError> {
    let values = object.get(key).and_then(Value::as_array).ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(format!("{key} must be an array"))
    })?;
    if values.len() > max {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::MessageTooLarge,
            format!("{key} exceeds its bound"),
        )
        .into());
    }
    Ok(values)
}

fn wire_string_array(object: &Map<String, Value>, key: &str, max: usize) -> Result<(), HostError> {
    let values = wire_array(object, key, max)?;
    if values
        .iter()
        .any(|value| value.as_str().is_none_or(str::is_empty))
    {
        return Err(agentyc_core::CoreError::invalid_argument(format!(
            "{key} contains an invalid string"
        ))
        .into());
    }
    Ok(())
}

fn wire_bytes(object: &Map<String, Value>, key: &str, max: usize) -> Result<(), HostError> {
    let values = wire_array(object, key, max)?;
    if values
        .iter()
        .any(|value| value.as_u64().is_none_or(|byte| byte > u64::from(u8::MAX)))
    {
        return Err(agentyc_core::CoreError::invalid_argument(format!(
            "{key} contains an invalid byte"
        ))
        .into());
    }
    Ok(())
}

fn valid_wire_method(method: &str) -> bool {
    let mut parts = method.split('.');
    !method.is_empty()
        && method.len() <= 128
        && parts.all(|part| {
            !part.is_empty()
                && part.bytes().all(|byte| {
                    byte.is_ascii_lowercase()
                        || byte.is_ascii_digit()
                        || byte == b'_'
                        || byte == b'-'
                })
        })
}

const MAX_LOGICAL_PARAMS: usize = 16;
const MAX_LOGICAL_PARAM_NAME_BYTES: usize = 64;
const MAX_REQUEST_DEADLINE_MS: u64 = 24 * 60 * 60 * 1000;
const MAX_RESPONSE_WARNINGS: usize = 64;
const MAX_WARNING_BYTES: usize = 4 * 1024;
const MAX_SUPPORTED_PROTOCOLS: usize = 8;
const MAX_CAPABILITIES: usize = 32;
const MAX_ID_BYTES: usize = 256;
const MAX_ARTIFACT_TRANSFERS: usize = 16;
const MAX_CANCEL_REASON_BYTES: usize = 256;
const MAX_LOGICAL_PARAM_VALUE_BYTES: usize = 4 * 1024;
const MAX_EVENT_LIMIT: usize = 1_024;
const MAX_WAIT_TIMEOUT_MS: u64 = 60_000;
const MAX_WAIT_CONDITION_DEPTH: usize = 8;
const MAX_WAIT_CONDITION_NODES: usize = 32;
const MAX_TRACKED_REQUESTS: usize = 4_096;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RequestState {
    CancelledQueued,
    Dispatched,
    Completed,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RequestFingerprint {
    hash: ContentHash,
    method: String,
    context: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RequestRecord {
    state: RequestState,
    fingerprint: RequestFingerprint,
}

#[derive(Debug)]
struct LocalArtifactTransfer {
    progress: agentyc_core::ArtifactTransferProgress,
    begin: ArtifactBeginEnvelope,
    bytes: Vec<u8>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum WireWaitCondition {
    EventKind {
        event: EventKind,
    },
    Event {
        event: Option<EventKind>,
        #[serde(default)]
        payload: BTreeMap<String, String>,
    },
    Payload {
        key: String,
        value: String,
    },
    GenerationAtLeast {
        generation: GenerationWatermark,
    },
    Any {
        conditions: Vec<WireWaitCondition>,
    },
    All {
        conditions: Vec<WireWaitCondition>,
    },
}

struct ProtocolClock {
    started: Instant,
}

impl crate::Clock for ProtocolClock {
    fn now(&self) -> Timestamp {
        Timestamp::new(self.started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64)
    }
}

fn parse_wait_condition(encoded: &str) -> Result<crate::WaitCondition, HostError> {
    let wire: WireWaitCondition = serde_json::from_str(encoded).map_err(|error| {
        agentyc_core::CoreError::invalid_argument(format!("invalid wait condition: {error}"))
    })?;
    let mut nodes = 0;
    wire.into_condition(0, &mut nodes)
}

impl WireWaitCondition {
    fn into_condition(
        self,
        depth: usize,
        nodes: &mut usize,
    ) -> Result<crate::WaitCondition, HostError> {
        *nodes += 1;
        if depth > MAX_WAIT_CONDITION_DEPTH || *nodes > MAX_WAIT_CONDITION_NODES {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                "wait condition exceeds its depth or node bound",
            )
            .into());
        }
        let bounded_key =
            |value: &str| !value.is_empty() && value.len() <= MAX_LOGICAL_PARAM_NAME_BYTES;
        let bounded_value =
            |value: &str| !value.is_empty() && value.len() <= MAX_LOGICAL_PARAM_VALUE_BYTES;
        Ok(match self {
            Self::EventKind { event } => crate::WaitCondition::EventKind(event),
            Self::Event { event, payload } => {
                if payload.len() > MAX_LOGICAL_PARAMS
                    || payload
                        .iter()
                        .any(|(key, value)| !bounded_key(key) || !bounded_value(value))
                {
                    return Err(agentyc_core::CoreError::invalid_argument(
                        "wait event payload keys and values must be non-empty and bounded",
                    )
                    .into());
                }
                crate::WaitCondition::Event {
                    kind: event,
                    payload,
                }
            }
            Self::Payload { key, value } => {
                if !bounded_key(&key) || !bounded_value(&value) {
                    return Err(agentyc_core::CoreError::invalid_argument(
                        "wait payload key and value must be non-empty and bounded",
                    )
                    .into());
                }
                crate::WaitCondition::Payload { key, value }
            }
            Self::GenerationAtLeast { generation } => {
                crate::WaitCondition::GenerationAtLeast(generation)
            }
            Self::Any { conditions } => {
                crate::WaitCondition::Any(convert_wait_children(conditions, depth, nodes)?)
            }
            Self::All { conditions } => {
                crate::WaitCondition::All(convert_wait_children(conditions, depth, nodes)?)
            }
        })
    }
}

fn convert_wait_children(
    conditions: Vec<WireWaitCondition>,
    depth: usize,
    nodes: &mut usize,
) -> Result<Vec<crate::WaitCondition>, HostError> {
    if conditions.is_empty() || conditions.len() > MAX_WAIT_CONDITION_NODES {
        return Err(agentyc_core::CoreError::invalid_argument(
            "wait composite condition must contain 1 to 32 children",
        )
        .into());
    }
    conditions
        .into_iter()
        .map(|condition| condition.into_condition(depth + 1, nodes))
        .collect()
}

fn validate_params(params: &BTreeMap<String, String>) -> Result<(), HostError> {
    if params.len() > MAX_LOGICAL_PARAMS {
        return Err(agentyc_core::CoreError::new(
            agentyc_core::ErrorCode::MessageTooLarge,
            format!("too many logical request parameters; maximum is {MAX_LOGICAL_PARAMS}"),
        )
        .into());
    }
    for (name, value) in params {
        if name.is_empty() || name.len() > MAX_LOGICAL_PARAM_NAME_BYTES {
            return Err(agentyc_core::CoreError::invalid_argument(
                "logical request parameter name is invalid",
            )
            .into());
        }
        if value.len() > MAX_LOGICAL_PARAM_VALUE_BYTES {
            return Err(agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::MessageTooLarge,
                format!(
                    "logical request parameter {name} exceeds the {MAX_LOGICAL_PARAM_VALUE_BYTES}-byte limit"
                ),
            )
            .into());
        }
    }
    Ok(())
}

fn required<'a>(params: &'a BTreeMap<String, String>, key: &str) -> Result<&'a str, HostError> {
    let value = params
        .get(key)
        .map(String::as_str)
        .ok_or_else(|| agentyc_core::CoreError::invalid_argument(format!("{key} is required")))?;
    if value.is_empty() {
        return Err(
            agentyc_core::CoreError::invalid_argument(format!("{key} must not be empty")).into(),
        );
    }
    Ok(value)
}

fn profile_disclosure(params: &BTreeMap<String, String>) -> Result<ProfileDisclosure, HostError> {
    let disclosure_field = |key: &str| {
        params
            .get(key)
            .filter(|value| !value.is_empty())
            .cloned()
            .ok_or_else(|| {
                agentyc_core::CoreError::new(
                    agentyc_core::ErrorCode::PermissionDenied,
                    "explicit shared-profile disclosure acknowledgement is required",
                )
            })
    };
    let disclosure = ProfileDisclosure {
        profile_scope: disclosure_field("profile_scope")?,
        shared_state_notice: disclosure_field("shared_state_notice")?,
        isolation_claim: required(params, "isolation_claim")?
            .parse::<bool>()
            .map_err(|_| {
                agentyc_core::CoreError::invalid_argument("isolation_claim must be true or false")
            })?,
        acknowledged: required(params, "profile_disclosure_acknowledged")?
            .parse::<bool>()
            .map_err(|_| {
                agentyc_core::CoreError::invalid_argument(
                    "profile_disclosure_acknowledged must be true or false",
                )
            })?,
    };
    disclosure.validate()?;
    Ok(disclosure)
}

fn parse_control_ticket(params: &BTreeMap<String, String>) -> Result<ControlTicket, HostError> {
    let encoded = required(params, "control_ticket")?;
    let value: serde_json::Value = serde_json::from_str(encoded).map_err(|error| {
        agentyc_core::CoreError::invalid_argument(format!("invalid control_ticket: {error}"))
    })?;
    let object = value.as_object().ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument("control_ticket must be a JSON object")
    })?;
    let space_id = parse_space(
        object
            .get("space_id")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument("control_ticket space_id is required")
            })?,
    )?;
    let broker_epoch = BrokerEpoch::new(
        object
            .get("broker_epoch")
            .and_then(serde_json::Value::as_u64)
            .ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument("control_ticket broker_epoch is required")
            })?,
    );
    let fence_epoch = LeaseEpoch::new(
        object
            .get("fence_epoch")
            .and_then(serde_json::Value::as_u64)
            .ok_or_else(|| {
                agentyc_core::CoreError::invalid_argument("control_ticket fence_epoch is required")
            })?,
    );
    let token = object
        .get("token")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| {
            agentyc_core::CoreError::invalid_argument("control_ticket token is required")
        })?
        .parse::<ReconcileToken>()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
    Ok(ControlTicket::new(
        space_id,
        broker_epoch,
        fence_epoch,
        token,
    ))
}

fn parse_space(value: &str) -> Result<SpaceId, HostError> {
    value
        .parse::<SpaceId>()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()).into())
}

fn parse_page(value: &str) -> Result<PageId, HostError> {
    value
        .parse::<PageId>()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()).into())
}

fn parse_action(value: &str) -> Result<ActionId, HostError> {
    value
        .parse::<ActionId>()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()).into())
}

fn parse_u64(params: &BTreeMap<String, String>, key: &str) -> Result<Option<u64>, HostError> {
    params
        .get(key)
        .map(|value| {
            value.parse::<u64>().map_err(|error| {
                agentyc_core::CoreError::invalid_argument(format!("invalid {key}: {error}")).into()
            })
        })
        .transpose()
}

fn required_u64(params: &BTreeMap<String, String>, key: &str) -> Result<u64, HostError> {
    parse_u64(params, key)?.ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(format!("{key} is required")).into()
    })
}

fn parse_limit(params: &BTreeMap<String, String>) -> Result<usize, HostError> {
    let Some(value) = parse_u64(params, "limit")? else {
        return Ok(MAX_EVENT_LIMIT);
    };
    Ok(usize::try_from(value)
        .unwrap_or(MAX_EVENT_LIMIT)
        .min(MAX_EVENT_LIMIT))
}

fn request_scope(params: &BTreeMap<String, String>) -> Result<Option<EventScope>, HostError> {
    let space_id = params
        .get("space_id")
        .map(|value| parse_space(value))
        .transpose()?;
    let page_id = params
        .get("page_id")
        .map(|value| parse_page(value))
        .transpose()?;
    if page_id.is_some() && space_id.is_none() {
        return Err(agentyc_core::CoreError::invalid_argument("page_id requires space_id").into());
    }
    Ok(match (space_id, page_id) {
        (Some(space_id), page_id) => Some(EventScope {
            space_id: Some(space_id),
            page_id,
        }),
        (None, None) => None,
        (None, Some(_)) => unreachable!("page_id without space_id was rejected above"),
    })
}

fn action_request_from_params(
    params: &BTreeMap<String, String>,
) -> Result<ActionRequest<BTreeMap<String, String>>, HostError> {
    let request_id = required(params, "request_id")?
        .parse::<RequestId>()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
    let action_id = parse_action(required(params, "action_id")?)?;
    let idempotency_key = required(params, "idempotency_key")?
        .parse::<IdempotencyKey>()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
    let space_id = parse_space(required(params, "space_id")?)?;
    let page_id = params
        .get("page_id")
        .map(|value| parse_page(value))
        .transpose()?;
    let lease_epoch = agentyc_core::LeaseEpoch::new(required_u64(params, "lease_epoch")?);
    let operation = serde_json::from_value::<ActionOperation>(serde_json::Value::String(
        required(params, "operation")?.to_owned(),
    ))
    .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
    let payload = params
        .get("payload")
        .map(|value| serde_json::from_str::<BTreeMap<String, String>>(value))
        .transpose()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?
        .unwrap_or_default();
    let postcondition = params
        .get("postcondition")
        .map(|value| serde_json::from_str::<Postcondition>(value))
        .transpose()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))?;
    let supplied_hash = params
        .get("request_hash")
        .map(|value| {
            value
                .parse::<ContentHash>()
                .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()))
        })
        .transpose()?;
    let has_supplied_hash = supplied_hash.is_some();
    let mut request = ActionRequest {
        request_id,
        action_id,
        idempotency_key,
        request_hash: supplied_hash
            .unwrap_or_else(|| ContentHash::from_bytes(b"local-protocol-action")),
        space_id,
        page_id,
        lease_epoch,
        operation,
        payload,
        postcondition,
    };
    let expected_hash = canonical_action_hash(&request)?;
    if !has_supplied_hash {
        request.request_hash = expected_hash;
    } else if request.request_hash != expected_hash {
        return Err(agentyc_core::CoreError::invalid_argument(
            "request hash does not match the complete canonical request context",
        )
        .into());
    }
    Ok(request)
}

fn put_json<T: Serialize>(
    result: &mut BTreeMap<String, String>,
    key: &str,
    value: &T,
) -> Result<(), HostError> {
    result.insert(key.to_owned(), serde_json::to_string(value)?);
    Ok(())
}

fn put_optional_json<T: Serialize>(
    result: &mut BTreeMap<String, String>,
    key: &str,
    value: Option<&T>,
) -> Result<(), HostError> {
    if let Some(value) = value {
        put_json(result, key, value)?;
    }
    Ok(())
}

fn host_lifecycle_name(lifecycle: crate::HostLifecycle) -> &'static str {
    match lifecycle {
        crate::HostLifecycle::Starting => "starting",
        crate::HostLifecycle::WaitingForExtension => "waiting_for_extension",
        crate::HostLifecycle::Ready => "ready",
        crate::HostLifecycle::Draining => "draining",
        crate::HostLifecycle::Degraded(_) => "degraded",
        crate::HostLifecycle::Recovering => "recovering",
        crate::HostLifecycle::Orphaned => "orphaned",
        crate::HostLifecycle::Stopped => "stopped",
    }
}

#[allow(dead_code)]
fn _keep_protocol_types_visible(
    _request_id: RequestId,
    _scope: EventScope,
    _watermark: ResumeWatermark,
    _protocol: u16,
) {
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        bridge::{BridgeStatus, FakeBridge, ObservationSnapshot},
        ledger::Ledger,
    };
    use agentyc_core::{ClientMetadata, ConnectionNonce, PROTOCOL_VERSION, PrincipalId};
    use serde_json::json;
    use std::sync::Arc;
    use tempfile::tempdir;

    fn hello(suffix: &str) -> Envelope {
        Envelope::Hello(HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix(suffix).expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("protocol-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(ConnectionNonce::from_suffix(suffix).expect("nonce")),
                profile_binding_id: None,
            }),
        })
    }

    fn request_envelope(id: &str, method: &str, params: BTreeMap<String, String>) -> Envelope {
        Envelope::Request(RequestEnvelope {
            protocol: PROTOCOL_VERSION,
            request_id: RequestId::from_suffix(id).expect("request id"),
            method: method.to_owned(),
            params,
            deadline_ms: None,
            idempotency_key: None,
        })
    }

    #[test]
    fn wait_condition_wire_adapter_accepts_bounded_recursive_conditions() {
        let condition = parse_wait_condition(
            r#"{"kind":"all","conditions":[{"kind":"event_kind","event":"page.changed"},{"kind":"payload","key":"reason","value":"navigation"}]}"#,
        )
        .expect("condition");
        assert!(matches!(condition, crate::WaitCondition::All(children) if children.len() == 2));
        assert!(parse_wait_condition(
            r#"{"kind":"any","conditions":[{"kind":"event_kind","event":"page.changed"}],"unexpected":true}"#
        )
        .is_err());
        let deep = format!(
            "{{\"kind\":\"any\",\"conditions\":[{}]}}",
            (0..MAX_WAIT_CONDITION_DEPTH + 1).fold(
                r#"{"kind":"event_kind","event":"page.changed"}"#.to_owned(),
                |condition, _| format!("{{\"kind\":\"any\",\"conditions\":[{condition}]}}"),
            )
        );
        assert!(parse_wait_condition(&deep).is_err());
    }

    #[test]
    fn wait_for_matches_a_broker_event_after_registration() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let mut server = ProtocolServer::new(broker.clone());
        server.dispatch(hello("wait-match")).expect("hello");
        let authority = server.connection().expect("connection").authority().clone();
        let event_broker = broker.clone();
        let event_thread = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(20));
            event_broker
                .publish_event(
                    &authority,
                    EventScope {
                        space_id: None,
                        page_id: None,
                    },
                    EventKind::ConnectionChanged,
                    BTreeMap::from([("state".to_owned(), "loading".to_owned())]),
                )
                .expect("publish unrelated event");
            event_broker
                .publish_event(
                    &authority,
                    EventScope {
                        space_id: None,
                        page_id: None,
                    },
                    EventKind::ConnectionChanged,
                    BTreeMap::from([("state".to_owned(), "ready".to_owned())]),
                )
                .expect("publish matching event");
        });
        let Envelope::Response(response) = server
            .dispatch(request_envelope(
                "wait-match-request",
                "wait.for",
                BTreeMap::from([
                    (
                        "condition".to_owned(),
                        r#"{"kind":"payload","key":"state","value":"ready"}"#.to_owned(),
                    ),
                    ("timeout_ms".to_owned(), "1000".to_owned()),
                ]),
            ))
            .expect("wait dispatch")[0]
            .clone()
        else {
            panic!("expected response");
        };
        event_thread.join().expect("event thread");
        assert!(response.ok, "{:?}", response.error);
        let result = response.result.expect("result");
        assert_eq!(result.get("wait").map(String::as_str), Some("\"matched\""));
        let event: agentyc_core::EventRecord =
            serde_json::from_str(result.get("event").expect("event")).expect("event json");
        assert_eq!(event.event, EventKind::ConnectionChanged);
        assert_eq!(
            event.payload.get("state").map(String::as_str),
            Some("ready")
        );
    }

    #[test]
    fn wait_for_timeout_is_bounded_and_returns_timeout_error() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let mut server = ProtocolServer::new(broker);
        server.dispatch(hello("wait-timeout")).expect("hello");
        let Envelope::Response(response) = server
            .dispatch(request_envelope(
                "wait-timeout-request",
                "wait.for",
                BTreeMap::from([
                    (
                        "condition".to_owned(),
                        r#"{"kind":"event_kind","event":"page.changed"}"#.to_owned(),
                    ),
                    ("timeout_ms".to_owned(), "1".to_owned()),
                ]),
            ))
            .expect("wait dispatch")[0]
            .clone()
        else {
            panic!("expected response");
        };
        assert!(!response.ok);
        assert_eq!(
            response.error.expect("timeout error").code,
            agentyc_core::ErrorCode::Timeout
        );

        let Envelope::Response(invalid) = server
            .dispatch(request_envelope(
                "wait-too-long",
                "wait.for",
                BTreeMap::from([
                    (
                        "condition".to_owned(),
                        r#"{"kind":"event_kind","event":"page.changed"}"#.to_owned(),
                    ),
                    (
                        "timeout_ms".to_owned(),
                        (MAX_WAIT_TIMEOUT_MS + 1).to_string(),
                    ),
                ]),
            ))
            .expect("invalid wait dispatch")[0]
            .clone()
        else {
            panic!("expected response");
        };
        assert_eq!(
            invalid.error.expect("invalid timeout").code,
            agentyc_core::ErrorCode::InvalidArgument
        );
    }

    #[test]
    fn cancel_before_dispatch_prevents_request_and_duplicate_mutation_replay() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let mut server = ProtocolServer::new(broker);
        server.dispatch(hello("cancel-queued")).expect("hello");
        let request_id = RequestId::from_suffix("queued-space-create").expect("request id");
        let Envelope::Response(cancel_response) = server
            .dispatch(Envelope::Cancel(agentyc_core::CancelEnvelope {
                protocol: PROTOCOL_VERSION,
                request_id: request_id.clone(),
                reason: None,
            }))
            .expect("cancel dispatch")[0]
            .clone()
        else {
            panic!("expected cancel response");
        };
        assert!(cancel_response.ok);
        let Envelope::Response(cancelled_request) = server
            .dispatch(Envelope::Request(RequestEnvelope {
                protocol: PROTOCOL_VERSION,
                request_id,
                method: "space.create".to_owned(),
                params: BTreeMap::from([("label".to_owned(), "must not exist".to_owned())]),
                deadline_ms: None,
                idempotency_key: None,
            }))
            .expect("cancelled request dispatch")[0]
            .clone()
        else {
            panic!("expected cancelled request response");
        };
        assert_eq!(
            cancelled_request.error.expect("cancel error").code,
            agentyc_core::ErrorCode::Cancelled
        );

        let mutation = request_envelope(
            "once-only-mutation",
            "space.create",
            BTreeMap::from([
                ("label".to_owned(), "created once".to_owned()),
                (
                    "profile_scope".to_owned(),
                    "shared_existing_profile".to_owned(),
                ),
                (
                    "shared_state_notice".to_owned(),
                    "shared_profile_state".to_owned(),
                ),
                ("isolation_claim".to_owned(), "false".to_owned()),
                (
                    "profile_disclosure_acknowledged".to_owned(),
                    "true".to_owned(),
                ),
            ]),
        );
        let first = server.dispatch(mutation.clone()).expect("first mutation");
        let second = server.dispatch(mutation).expect("duplicate mutation");
        assert!(matches!(&first[0], Envelope::Response(response) if response.ok));
        assert!(matches!(&second[0], Envelope::Response(response) if !response.ok));
        let Envelope::Response(cancel_after_dispatch) = server
            .dispatch(Envelope::Cancel(agentyc_core::CancelEnvelope {
                protocol: PROTOCOL_VERSION,
                request_id: RequestId::from_suffix("once-only-mutation").expect("request id"),
                reason: Some("too late".to_owned()),
            }))
            .expect("late cancel dispatch")[0]
            .clone()
        else {
            panic!("expected cancel response");
        };
        assert_eq!(
            cancel_after_dispatch.error.expect("late cancel error").code,
            agentyc_core::ErrorCode::InvalidArgument
        );
        let Envelope::Response(spaces) = server
            .dispatch(request_envelope(
                "list-after-cancel",
                "space.list",
                BTreeMap::new(),
            ))
            .expect("list dispatch")[0]
            .clone()
        else {
            panic!("expected list response");
        };
        let listed: Vec<serde_json::Value> = serde_json::from_str(
            spaces
                .result
                .expect("list result")
                .get("spaces")
                .expect("spaces field"),
        )
        .expect("spaces json");
        assert_eq!(listed.len(), 1);
    }

    #[test]
    fn duplicate_hello_does_not_replace_or_leak_connection_authority() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let mut server = ProtocolServer::new(broker.clone());
        let hello = Envelope::Hello(HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix("client").expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("protocol-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(ConnectionNonce::from_suffix("nonce").expect("nonce")),
                profile_binding_id: None,
            }),
        });
        server.dispatch(hello.clone()).expect("first hello");
        assert!(server.dispatch(hello).is_err());
        server.close().expect("close");
    }

    #[test]
    fn dispatcher_uses_core_frames_and_requires_hello() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let mut server =
            ProtocolServer::with_max_payload(broker, 4096).expect("server configuration");
        let mut client = ProtocolClient::with_max_payload(4096).expect("client configuration");
        let request = Envelope::Request(RequestEnvelope {
            protocol: PROTOCOL_VERSION,
            request_id: RequestId::from_suffix("request").expect("request"),
            method: "events.cursor".to_owned(),
            params: BTreeMap::new(),
            deadline_ms: None,
            idempotency_key: None,
        });
        let output = server
            .handle_frame(&client.encode(&request).expect("encode"))
            .expect("response");
        let envelopes = client.feed(&output).expect("decode");
        let Envelope::Response(response) = &envelopes[0] else {
            panic!("expected response");
        };
        assert!(!response.ok);
        assert_eq!(
            response.error.as_ref().expect("error").code,
            agentyc_core::ErrorCode::PermissionDenied
        );
    }

    #[test]
    fn page_inventory_is_live_space_scoped_and_page_list_stays_ledger_backed() {
        let directory = tempdir().expect("tempdir");
        let bridge = Arc::new(FakeBridge::new());
        let broker = Broker::with_shared_bridge(
            Ledger::open(directory.path()).expect("ledger"),
            bridge.clone(),
        );
        let mut server = ProtocolServer::new(broker);
        let hello = Envelope::Hello(HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix("inventory-test").expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("protocol-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(
                    ConnectionNonce::from_suffix("inventory-test").expect("nonce"),
                ),
                profile_binding_id: None,
            }),
        });
        server.dispatch(hello).expect("hello");

        let mut request_number = 0_u64;
        let mut request = |method: &str, params: BTreeMap<String, String>| {
            request_number += 1;
            let request = Envelope::Request(RequestEnvelope {
                protocol: PROTOCOL_VERSION,
                request_id: RequestId::from_suffix(format!("inventory-{request_number}"))
                    .expect("request"),
                method: method.to_owned(),
                params,
                deadline_ms: None,
                idempotency_key: None,
            });
            let Envelope::Response(response) =
                server.dispatch(request).expect("dispatch")[0].clone()
            else {
                panic!("expected response");
            };
            assert!(
                response.ok,
                "{}",
                response
                    .error
                    .map_or_else(String::new, |error| error.message)
            );
            response.result.expect("result")
        };
        let json_field = |result: &BTreeMap<String, String>, key: &str| {
            serde_json::from_str::<serde_json::Value>(result.get(key).expect("field"))
                .expect("json result field")
        };

        let first_space = request(
            "space.create",
            BTreeMap::from([
                ("label".to_owned(), "first".to_owned()),
                (
                    "profile_scope".to_owned(),
                    "shared_existing_profile".to_owned(),
                ),
                (
                    "shared_state_notice".to_owned(),
                    "shared_profile_state".to_owned(),
                ),
                ("isolation_claim".to_owned(), "false".to_owned()),
                (
                    "profile_disclosure_acknowledged".to_owned(),
                    "true".to_owned(),
                ),
            ]),
        );
        let first_space_id = json_field(&first_space, "space_id")
            .as_str()
            .expect("first space id")
            .to_owned();
        let second_space = request(
            "space.create",
            BTreeMap::from([
                ("label".to_owned(), "second".to_owned()),
                (
                    "profile_scope".to_owned(),
                    "shared_existing_profile".to_owned(),
                ),
                (
                    "shared_state_notice".to_owned(),
                    "shared_profile_state".to_owned(),
                ),
                ("isolation_claim".to_owned(), "false".to_owned()),
                (
                    "profile_disclosure_acknowledged".to_owned(),
                    "true".to_owned(),
                ),
            ]),
        );
        let second_space_id = json_field(&second_space, "space_id")
            .as_str()
            .expect("second space id")
            .to_owned();

        bridge.set_observation_snapshot(ObservationSnapshot {
            pages: vec![
                json!({
                    "space_id": first_space_id,
                    "page_id": "page_live",
                    "ownership": "agent",
                    "lifecycle": "managed",
                    "binding_state": "bound",
                    "target_generation": 2,
                    "url": "https://example.test/one",
                    "tab_hint": "hint_one",
                    "tab_id": 7,
                    "path": "/private/profile",
                    "unknown": "discarded"
                }),
                json!({
                    "space_id": second_space_id,
                    "page_id": "page_foreign",
                    "ownership": "agent",
                    "lifecycle": "managed",
                    "binding_state": "bound",
                    "target_generation": 3
                }),
                json!({
                    "ownership": "unmanaged",
                    "lifecycle": "unmanaged",
                    "binding_state": "unbound",
                    "active": true,
                    "space_id": null,
                    "page_id": null,
                    "url": "https://example.test/user",
                    "title": "User tab",
                    "tab_hint": "user_hint"
                }),
                json!({
                    "ownership": "unmanaged",
                    "lifecycle": "unmanaged",
                    "binding_state": "unbound",
                    "active": true,
                    "tab_hint": "user_missing_ids"
                }),
                json!({
                    "ownership": "unmanaged",
                    "lifecycle": "unmanaged",
                    "binding_state": "unbound",
                    "active": false,
                    "tab_hint": "inactive_hint"
                }),
                json!({
                    "ownership": "unmanaged",
                    "lifecycle": "unmanaged",
                    "binding_state": "unbound",
                    "active": true,
                    "space_id": second_space_id,
                    "tab_hint": "foreign_user_hint"
                }),
            ],
            groups: vec![
                json!({
                    "space_id": first_space_id,
                    "hint": "group_one",
                    "title": "Research",
                    "present": true,
                    "drift": false,
                    "member_count": 1,
                    "group_id": 7,
                    "unknown": "discarded"
                }),
                json!({
                    "space_id": second_space_id,
                    "hint": "group_two",
                    "present": true,
                    "drift": false,
                    "member_count": 1
                }),
            ],
            ..ObservationSnapshot::default()
        });

        let live = request(
            "page.inventory",
            BTreeMap::from([("space_id".to_owned(), first_space_id.clone())]),
        );
        let live_pages_value = json_field(&live, "pages");
        let live_pages = live_pages_value.as_array().expect("live pages");
        assert_eq!(live_pages.len(), 3);
        assert_eq!(live_pages[0]["space_id"], json!(first_space_id));
        assert!(live_pages[0].get("tab_id").is_none());
        assert!(live_pages[0].get("path").is_none());
        assert!(live_pages[0].get("unknown").is_none());
        assert!(live_pages.iter().any(|page| {
            page["ownership"] == json!("unmanaged") && page["active"] == json!(true)
        }));
        assert!(!live_pages.iter().any(|page| {
            page.get("tab_hint") == Some(&json!("inactive_hint"))
                || page.get("tab_hint") == Some(&json!("foreign_user_hint"))
        }));
        let live_groups = json_field(&live, "groups");
        let live_groups = live_groups.as_array().expect("live groups");
        assert_eq!(live_groups.len(), 1);
        assert_eq!(live_groups[0]["space_id"], json!(first_space_id));
        assert!(live_groups[0].get("group_id").is_none());
        assert!(live_groups[0].get("unknown").is_none());

        let listed = request(
            "page.list",
            BTreeMap::from([("space_id".to_owned(), first_space_id)]),
        );
        assert!(
            json_field(&listed, "pages")
                .as_array()
                .expect("ledger pages")
                .is_empty()
        );
    }

    #[test]
    fn external_space_create_requires_profile_disclosure_before_commit() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let mut server = ProtocolServer::new(broker);
        server.dispatch(hello("profile-disclosure")).expect("hello");
        let Envelope::Response(response) = server
            .dispatch(request_envelope(
                "missing-profile-disclosure",
                "space.create",
                BTreeMap::from([("label".to_owned(), "must reject".to_owned())]),
            ))
            .expect("dispatch")[0]
            .clone()
        else {
            panic!("expected response");
        };
        assert!(!response.ok);
        assert_eq!(
            response.error.expect("disclosure error").code,
            agentyc_core::ErrorCode::PermissionDenied
        );
    }

    #[test]
    fn direct_methods_dispatch_bounded_json_results() {
        let directory = tempdir().expect("tempdir");
        let bridge = FakeBridge::new();
        bridge.set_bridge_status(BridgeStatus {
            profile_instance_id: Some("profile_live".to_owned()),
            extension_version: Some("2.0.0".to_owned()),
            worker_instance_epoch: Some(4),
            browser_session_epoch: Some(5),
        });
        let broker = Broker::open(directory.path(), bridge).expect("broker");
        let mut server = ProtocolServer::new(broker);
        let hello = Envelope::Hello(HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix("direct-methods").expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("protocol-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(
                    ConnectionNonce::from_suffix("direct-methods").expect("nonce"),
                ),
                profile_binding_id: None,
            }),
        });
        server.dispatch(hello).expect("hello");

        let mut request_number = 0_u64;
        let mut request = |method: &str, params: BTreeMap<String, String>| {
            request_number += 1;
            let request = Envelope::Request(RequestEnvelope {
                protocol: PROTOCOL_VERSION,
                request_id: RequestId::from_suffix(format!("direct-{request_number}"))
                    .expect("request"),
                method: method.to_owned(),
                params,
                deadline_ms: None,
                idempotency_key: None,
            });
            let Envelope::Response(response) =
                server.dispatch(request).expect("dispatch")[0].clone()
            else {
                panic!("expected response");
            };
            assert!(
                response.ok,
                "{}",
                response
                    .error
                    .map_or_else(String::new, |error| error.message)
            );
            response.result.expect("result")
        };
        let json_field = |result: &BTreeMap<String, String>, key: &str| {
            serde_json::from_str::<serde_json::Value>(result.get(key).expect("field"))
                .expect("json result field")
        };

        let created = request(
            "space.create",
            BTreeMap::from([
                ("label".to_owned(), "protocol space".to_owned()),
                (
                    "profile_scope".to_owned(),
                    "shared_existing_profile".to_owned(),
                ),
                (
                    "shared_state_notice".to_owned(),
                    "shared_profile_state".to_owned(),
                ),
                ("isolation_claim".to_owned(), "false".to_owned()),
                (
                    "profile_disclosure_acknowledged".to_owned(),
                    "true".to_owned(),
                ),
            ]),
        );
        assert!(json_field(&created, "space").is_object());
        let space_id = json_field(&created, "space_id")
            .as_str()
            .expect("space id")
            .to_owned();

        let claimed = request(
            "space.claim",
            BTreeMap::from([
                ("space_id".to_owned(), space_id.clone()),
                ("ttl".to_owned(), "1000".to_owned()),
                ("now".to_owned(), "1".to_owned()),
            ]),
        );
        assert!(json_field(&claimed, "lease").is_object());
        assert_eq!(json_field(&claimed, "lifecycle"), "agent_owned");
        let lease_epoch = json_field(&claimed, "lease")["lease_epoch"]
            .as_u64()
            .expect("lease epoch");

        let renewed = request(
            "lease.renew",
            BTreeMap::from([
                ("space_id".to_owned(), space_id.clone()),
                ("lease_epoch".to_owned(), lease_epoch.to_string()),
                ("ttl".to_owned(), "1000".to_owned()),
                ("now".to_owned(), "2".to_owned()),
            ]),
        );
        assert!(json_field(&renewed, "lease").is_object());

        let page = request(
            "page.create",
            BTreeMap::from([
                ("space_id".to_owned(), space_id.clone()),
                ("lease_epoch".to_owned(), lease_epoch.to_string()),
                ("label".to_owned(), "main".to_owned()),
                ("now".to_owned(), "3".to_owned()),
            ]),
        );
        assert!(json_field(&page, "page").is_object());
        let listed = request(
            "page.list",
            BTreeMap::from([("space_id".to_owned(), space_id.clone())]),
        );
        assert_eq!(
            json_field(&listed, "pages")
                .as_array()
                .expect("pages")
                .len(),
            1
        );

        let cursor = request("events.cursor", BTreeMap::new());
        assert!(json_field(&cursor, "cursor").is_object());
        let resumed = request(
            "events.resume",
            BTreeMap::from([
                ("after_epoch".to_owned(), "1".to_owned()),
                ("after_sequence".to_owned(), "0".to_owned()),
                ("space_id".to_owned(), space_id.clone()),
                ("limit".to_owned(), "16".to_owned()),
            ]),
        );
        assert!(json_field(&resumed, "events").is_array());

        let status = request("host.status", BTreeMap::new());
        assert_eq!(json_field(&status, "lifecycle"), "ready");
        assert!(json_field(&status, "capabilities").is_array());
        assert_eq!(json_field(&status, "profile_instance_id"), "profile_live");
        assert_eq!(json_field(&status, "extension_version"), "2.0.0");
        assert_eq!(json_field(&status, "worker_instance_epoch"), 4);
        assert_eq!(json_field(&status, "browser_session_epoch"), 5);
        assert_eq!(json_field(&status, "connection_epoch"), 1);

        let takeover = request(
            "space.takeover",
            BTreeMap::from([
                ("space_id".to_owned(), space_id.clone()),
                ("ttl".to_owned(), "1000".to_owned()),
                ("now".to_owned(), "4".to_owned()),
            ]),
        );
        let takeover_epoch = json_field(&takeover, "lease_epoch")
            .as_u64()
            .expect("takeover epoch");
        assert_eq!(json_field(&takeover, "fence_acknowledged"), true);

        let finished = request(
            "space.finish",
            BTreeMap::from([
                ("space_id".to_owned(), space_id.clone()),
                ("lease_epoch".to_owned(), takeover_epoch.to_string()),
                ("now".to_owned(), "5".to_owned()),
            ]),
        );
        assert_eq!(json_field(&finished, "lifecycle"), "finished");
        let released = request(
            "space.release",
            BTreeMap::from([
                ("space_id".to_owned(), space_id),
                ("lease_epoch".to_owned(), takeover_epoch.to_string()),
                ("now".to_owned(), "6".to_owned()),
            ]),
        );
        assert_eq!(json_field(&released, "lifecycle"), "released");
    }
}
