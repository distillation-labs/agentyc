//! Bounded local protocol dispatcher over the core envelope/frame types.
//!
//! The owner-only Unix socket transport uses this dispatcher for agent and MCP
//! clients. Native Messaging remains a separate little-endian transport.

use std::collections::BTreeMap;

use agentyc_core::{
    ActionId, ActionOperation, ActionRequest, ArtifactEnvelope, ContentHash,
    DEFAULT_MAX_FRAME_PAYLOAD_BYTES, Envelope, EventCursor, EventScope, EventSequence,
    FrameDecoder, HelloEnvelope, IdempotencyKey, MAX_ARTIFACT_CHUNK_BYTES,
    MAX_CONTROL_FRAME_PAYLOAD_BYTES, PageId, Postcondition, RequestEnvelope, RequestId,
    ResponseEnvelope, ResumeEnvelope, SpaceId, Timestamp, decode_frame, decode_utf8, encode_frame,
};

use agentyc_core::protocol::ResumeWatermark;
use serde::Serialize;

use crate::{
    broker::{Broker, Connection, canonical_action_hash},
    error::HostError,
    events::EventQuery,
};

/// Bounded in-process protocol server state for one client connection.
pub struct ProtocolServer {
    broker: Broker,
    max_payload_bytes: usize,
    max_artifact_chunk_bytes: usize,
    connection: Option<Connection>,
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
        envelope.validate_protocol()?;
        match envelope {
            Envelope::Hello(hello) => self.handle_hello(hello),
            Envelope::Request(request) => Ok(vec![self.handle_request(request)]),
            Envelope::Resume(resume) => self.handle_resume(resume),
            Envelope::Cancel(cancel) => {
                let request_id = cancel.request_id;
                Ok(vec![Envelope::Response(ResponseEnvelope::failure(
                    request_id,
                    agentyc_core::CoreError::new(
                        agentyc_core::ErrorCode::InvalidArgument,
                        "cancellation is reserved for the transport adapter",
                    ),
                ))])
            }
            Envelope::HelloOk(_)
            | Envelope::Response(_)
            | Envelope::Event(_)
            | Envelope::Artifact(_) => Err(agentyc_core::CoreError::invalid_argument(
                "envelope kind is not client-admissible",
            )
            .into()),
        }
    }

    /// Decode one exact length-delimited frame, dispatch it, and encode responses.
    pub fn handle_frame(&mut self, frame: &[u8]) -> Result<Vec<u8>, HostError> {
        let payload = decode_frame(frame, self.max_payload_bytes)?;
        self.handle_payload(payload)
    }

    /// Dispatch one already-decoded payload from a streaming transport.
    pub fn handle_payload(&mut self, payload: &[u8]) -> Result<Vec<u8>, HostError> {
        let text = decode_utf8(payload)?;
        let envelope: Envelope = serde_json::from_str(text)?;
        let responses = self.dispatch(envelope)?;
        let mut output = Vec::new();
        for response in responses {
            let json = serde_json::to_vec(&response)?;
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
        let result = self.execute_request(request);
        match result {
            Ok(result) => Envelope::Response(ResponseEnvelope::success(request_id, result)),
            Err(error) => {
                Envelope::Response(ResponseEnvelope::failure(request_id, error.as_core_error()))
            }
        }
    }

    fn execute_request(
        &self,
        request: RequestEnvelope,
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
                let space = self.broker.create_space(authority, label.to_owned())?;
                put_json(&mut result, "space", &space)?;
                put_json(&mut result, "space_id", &space.space_id)?;
                put_json(&mut result, "lifecycle", &space.lifecycle)?;
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
                    "opaque": true,
                });
                put_json(&mut result, "space_id", &returned.space_id)?;
                put_json(&mut result, "released_epoch", &returned.released_epoch)?;
                put_json(&mut result, "fence_epoch", &returned.fence_epoch)?;
                put_json(&mut result, "lifecycle", &returned.lifecycle)?;
                put_json(&mut result, "control_ticket", &control_ticket)?;
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
                let pages = self.broker.page_inventory(authority, &space_id)?;
                put_json(&mut result, "space_id", &space_id)?;
                put_json(&mut result, "pages", &pages)?;
            }
            "action.execute" => {
                let action_request = action_request_from_params(&request.params)?;
                let now = Timestamp::new(parse_u64(&request.params, "now")?.unwrap_or(0));
                let action = self.broker.execute_action(action_request, authority, now)?;
                put_json(&mut result, "action_id", &action.receipt.action_id)?;
                put_json(&mut result, "receipt", &action.receipt)?;
            }
            "snapshot.read" => {
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
            "events.resume" => {
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
            "host.status" => {
                let lifecycle = self.broker.lifecycle()?;
                let broker_epoch = self.broker.broker_epoch()?;
                let capabilities = self.broker.capabilities()?;
                put_json(&mut result, "broker_epoch", &broker_epoch)?;
                put_json(&mut result, "lifecycle", &host_lifecycle_name(lifecycle))?;
                put_json(&mut result, "capabilities", &capabilities)?;
            }
            _ => {
                return Err(
                    agentyc_core::CoreError::invalid_argument("unknown host method").into(),
                );
            }
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
        if let Envelope::Artifact(artifact) = envelope {
            validate_artifact_envelope(artifact, self.max_artifact_chunk_bytes)?;
        }
        let json = serde_json::to_vec(envelope)?;
        Ok(encode_frame(&json, self.max_payload_bytes)?)
    }

    /// Feed fragmented or coalesced frames and decode every complete envelope.
    pub fn feed(&mut self, bytes: &[u8]) -> Result<Vec<Envelope>, HostError> {
        let frames = self.decoder.feed(bytes)?;
        frames
            .into_iter()
            .map(|frame| {
                let text = decode_utf8(&frame)?;
                serde_json::from_str(text).map_err(HostError::Json)
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

const MAX_LOGICAL_PARAMS: usize = 16;
const MAX_LOGICAL_PARAM_NAME_BYTES: usize = 64;
const MAX_LOGICAL_PARAM_VALUE_BYTES: usize = 4 * 1024;
const MAX_EVENT_LIMIT: usize = 1_024;

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
    let mut request = ActionRequest {
        request_id,
        action_id,
        idempotency_key,
        request_hash: ContentHash::from_bytes(b"local-protocol-action"),
        space_id,
        page_id,
        lease_epoch,
        operation,
        payload,
        postcondition,
    };
    request.request_hash = canonical_action_hash(&request)?;
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

fn host_lifecycle_name(lifecycle: crate::HostLifecycle) -> &'static str {
    match lifecycle {
        crate::HostLifecycle::Ready => "ready",
        crate::HostLifecycle::Draining => "draining",
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
    use crate::{bridge::FakeBridge, ledger::Ledger};
    use agentyc_core::{ClientMetadata, ConnectionNonce, PROTOCOL_VERSION, PrincipalId};
    use serde_json::json;
    use std::sync::Arc;
    use tempfile::tempdir;

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
            BTreeMap::from([("label".to_owned(), "first".to_owned())]),
        );
        let first_space_id = json_field(&first_space, "space_id")
            .as_str()
            .expect("first space id")
            .to_owned();
        let second_space = request(
            "space.create",
            BTreeMap::from([("label".to_owned(), "second".to_owned())]),
        );
        let second_space_id = json_field(&second_space, "space_id")
            .as_str()
            .expect("second space id")
            .to_owned();

        bridge.set_observation(vec![
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
        ]);

        let live = request(
            "page.inventory",
            BTreeMap::from([("space_id".to_owned(), first_space_id.clone())]),
        );
        let live_pages_value = json_field(&live, "pages");
        let live_pages = live_pages_value.as_array().expect("live pages");
        assert_eq!(live_pages.len(), 1);
        assert_eq!(live_pages[0]["space_id"], json!(first_space_id));
        assert!(live_pages[0].get("tab_id").is_none());
        assert!(live_pages[0].get("path").is_none());
        assert!(live_pages[0].get("unknown").is_none());

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
    fn direct_methods_dispatch_bounded_json_results() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
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
            BTreeMap::from([("label".to_owned(), "protocol space".to_owned())]),
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
