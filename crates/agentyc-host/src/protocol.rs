//! Small in-process local protocol dispatcher over the core envelope/frame types.
//!
//! This is deliberately not a socket, Native Messaging, or MCP implementation.
//! Later adapters can place their transport around the same bounded dispatcher.

use std::collections::BTreeMap;

use agentyc_core::{
    ActionId, DEFAULT_MAX_FRAME_PAYLOAD_BYTES, Envelope, EventCursor, EventScope, FrameDecoder,
    HelloEnvelope, RequestEnvelope, RequestId, ResponseEnvelope, ResumeEnvelope, SpaceId,
    decode_frame, decode_utf8, encode_frame,
};

use agentyc_core::protocol::ResumeWatermark;

use crate::{
    broker::{Broker, Connection},
    error::HostError,
    events::EventQuery,
};

/// Bounded in-process protocol server state for one client connection.
pub struct ProtocolServer {
    broker: Broker,
    max_payload_bytes: usize,
    connection: Option<Connection>,
}

impl std::fmt::Debug for ProtocolServer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ProtocolServer")
            .field("max_payload_bytes", &self.max_payload_bytes)
            .field("connected", &self.connection.is_some())
            .finish_non_exhaustive()
    }
}

impl ProtocolServer {
    /// Construct a dispatcher using the core's default payload bound.
    pub fn new(broker: Broker) -> Self {
        Self::with_max_payload(broker, DEFAULT_MAX_FRAME_PAYLOAD_BYTES)
    }

    /// Construct a dispatcher with a smaller explicit payload bound.
    pub fn with_max_payload(broker: Broker, max_payload_bytes: usize) -> Self {
        Self {
            broker,
            max_payload_bytes: max_payload_bytes.min(u32::MAX as usize),
            connection: None,
        }
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

    fn handle_hello(&mut self, hello: HelloEnvelope) -> Result<Vec<Envelope>, HostError> {
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
        let connection = self.connection.as_ref().ok_or_else(|| {
            agentyc_core::CoreError::new(
                agentyc_core::ErrorCode::PermissionDenied,
                "hello is required before requests",
            )
        })?;
        match request.method.as_str() {
            "space.list" => {
                let spaces = self.broker.list_spaces(connection.authority())?;
                Ok(BTreeMap::from([(
                    "spaces".to_owned(),
                    serde_json::to_string(&spaces)?,
                )]))
            }
            "space.create" => {
                let label = required(&request.params, "label")?;
                let space = self
                    .broker
                    .create_space(connection.authority(), label.to_owned())?;
                Ok(BTreeMap::from([
                    ("space_id".to_owned(), space.space_id.to_string()),
                    ("lifecycle".to_owned(), "created".to_owned()),
                ]))
            }
            "lease.acquire" => {
                let space_id = parse_space(required(&request.params, "space_id")?)?;
                let now = parse_u64(&request.params, "now").unwrap_or(0);
                let ttl = parse_u64(&request.params, "ttl")
                    .ok_or_else(|| agentyc_core::CoreError::invalid_argument("ttl is required"))?;
                let lease = self.broker.acquire_lease(
                    &space_id,
                    connection.authority(),
                    agentyc_core::Timestamp::new(now),
                    ttl,
                )?;
                Ok(BTreeMap::from([
                    ("space_id".to_owned(), lease.space_id.to_string()),
                    (
                        "lease_epoch".to_owned(),
                        lease.lease.lease_epoch.get().to_string(),
                    ),
                ]))
            }
            "action.status" => {
                let action_id = required(&request.params, "action_id")?
                    .parse::<ActionId>()
                    .map_err(|error| {
                        agentyc_core::CoreError::invalid_argument(error.to_string())
                    })?;
                let receipt = self
                    .broker
                    .action_status(connection.authority(), &action_id)?;
                Ok(BTreeMap::from([
                    ("action_id".to_owned(), receipt.action_id.to_string()),
                    (
                        "status".to_owned(),
                        format!("{:?}", receipt.status).to_ascii_lowercase(),
                    ),
                ]))
            }
            "events.cursor" => {
                let cursor = self.broker.event_cursor(connection.authority())?;
                Ok(BTreeMap::from([
                    (
                        "broker_epoch".to_owned(),
                        cursor.broker_epoch.get().to_string(),
                    ),
                    ("sequence".to_owned(), cursor.sequence.get().to_string()),
                ]))
            }
            _ => Err(agentyc_core::CoreError::invalid_argument("unknown host method").into()),
        }
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
}

impl ProtocolClient {
    /// Construct a client with the core default payload bound.
    pub fn new() -> Self {
        Self::with_max_payload(DEFAULT_MAX_FRAME_PAYLOAD_BYTES)
    }

    /// Construct a client with an explicit payload bound.
    pub fn with_max_payload(max_payload_bytes: usize) -> Self {
        let max_payload_bytes = max_payload_bytes.min(u32::MAX as usize);
        Self {
            decoder: FrameDecoder::new(max_payload_bytes),
            max_payload_bytes,
        }
    }

    /// Encode a core envelope into one bounded frame.
    pub fn encode(&self, envelope: &Envelope) -> Result<Vec<u8>, HostError> {
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

fn required<'a>(params: &'a BTreeMap<String, String>, key: &str) -> Result<&'a str, HostError> {
    params.get(key).map(String::as_str).ok_or_else(|| {
        agentyc_core::CoreError::invalid_argument(format!("{key} is required")).into()
    })
}

fn parse_space(value: &str) -> Result<SpaceId, HostError> {
    value
        .parse::<SpaceId>()
        .map_err(|error| agentyc_core::CoreError::invalid_argument(error.to_string()).into())
}

fn parse_u64(params: &BTreeMap<String, String>, key: &str) -> Option<u64> {
    params.get(key).and_then(|value| value.parse().ok())
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
    use crate::bridge::FakeBridge;
    use agentyc_core::PROTOCOL_VERSION;
    use tempfile::tempdir;

    #[test]
    fn dispatcher_uses_core_frames_and_requires_hello() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let mut server = ProtocolServer::with_max_payload(broker, 4096);
        let mut client = ProtocolClient::with_max_payload(4096);
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
}
