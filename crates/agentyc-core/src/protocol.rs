//! Versioned envelopes and bounded four-byte big-endian framing.

use std::{collections::BTreeMap, mem};

use serde::{Deserialize, Serialize};

use crate::{
    errors::{CoreError, ErrorCode, FrameError},
    events::EventRecord,
    ids::{
        ArtifactId, BrokerEpoch, ClientId, ConnectionEpoch, ConnectionNonce, EventSequence,
        IdempotencyKey, PrincipalId, ProfileBindingId, RequestId,
    },
    states::Capability,
};

/// Current local protocol version.
pub const PROTOCOL_VERSION: u16 = 1;
/// Normative maximum payload for one control frame.
pub const MAX_CONTROL_FRAME_PAYLOAD_BYTES: usize = 1024 * 1024;
/// Default maximum payload accepted by the local frame codec.
pub const DEFAULT_MAX_FRAME_PAYLOAD_BYTES: usize = MAX_CONTROL_FRAME_PAYLOAD_BYTES;
/// Native length-prefix width used by the local protocol.
pub const FRAME_PREFIX_BYTES: usize = 4;
/// Maximum artifact chunk accepted in one artifact envelope.
pub const MAX_ARTIFACT_CHUNK_BYTES: usize = 256 * 1024;

/// Resume position supplied during a handshake or reconnect.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResumeWatermark {
    /// Broker epoch that issued the sequence.
    pub broker_epoch: BrokerEpoch,
    /// Last sequence fully processed by the client.
    pub sequence: EventSequence,
}

/// Result of protocol version negotiation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct NegotiatedProtocol {
    /// Version selected for this connection.
    pub protocol: u16,
}

/// Bounded client metadata carried inside a handshake.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClientMetadata {
    /// Optional logical client instance identity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub client_id: Option<ClientId>,
    /// Human-readable client name.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub client_name: Option<String>,
    /// Client implementation version.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub client_version: Option<String>,
    /// Fresh connection nonce for this handshake.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub connection_nonce: Option<ConnectionNonce>,
    /// Logical profile binding selected by the client, when enrolled.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile_binding_id: Option<ProfileBindingId>,
}

impl ClientMetadata {
    /// Validate bounded metadata without requiring an enrolled profile.
    pub fn validate(&self) -> Result<(), CoreError> {
        validate_optional_metadata_text("client_name", self.client_name.as_deref())?;
        validate_optional_metadata_text("client_version", self.client_version.as_deref())
    }

    fn validate_required(&self) -> Result<(), CoreError> {
        self.validate()?;
        if self.connection_nonce.is_none() {
            return Err(CoreError::invalid_argument(
                "handshake client metadata requires a connection nonce",
            ));
        }
        if self.client_name.is_none() || self.client_version.is_none() {
            return Err(CoreError::invalid_argument(
                "handshake client metadata requires client name and version",
            ));
        }
        Ok(())
    }
}

/// Bounded host metadata returned by a handshake.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct HostMetadata {
    /// Human-readable host name.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub host_name: Option<String>,
    /// Host implementation version.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub host_version: Option<String>,
    /// Fresh connection nonce echoed by the host.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub connection_nonce: Option<ConnectionNonce>,
    /// Logical profile binding selected by the host, when enrolled.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile_binding_id: Option<ProfileBindingId>,
}

impl HostMetadata {
    /// Validate bounded host metadata.
    pub fn validate(&self) -> Result<(), CoreError> {
        validate_optional_metadata_text("host_name", self.host_name.as_deref())?;
        validate_optional_metadata_text("host_version", self.host_version.as_deref())
    }

    fn validate_required(&self) -> Result<(), CoreError> {
        self.validate()?;
        if self.connection_nonce.is_none() {
            return Err(CoreError::invalid_argument(
                "handshake host metadata requires a connection nonce",
            ));
        }
        if self.host_name.is_none() || self.host_version.is_none() {
            return Err(CoreError::invalid_argument(
                "handshake host metadata requires host name and version",
            ));
        }
        Ok(())
    }
}

fn validate_optional_metadata_text(field: &str, value: Option<&str>) -> Result<(), CoreError> {
    if value.is_some_and(|value| value.is_empty() || value.len() > 128) {
        return Err(CoreError::invalid_argument(format!(
            "handshake {field} must be between 1 and 128 bytes"
        )));
    }
    Ok(())
}

/// Client-to-host handshake envelope.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HelloEnvelope {
    /// Protocol version requested by the client.
    pub protocol: u16,
    /// Versions the client can speak.
    pub supported_protocols: Vec<u16>,
    /// Logical principal metadata; local transport authentication is separate.
    pub principal_id: PrincipalId,
    /// Optional event resume cursor.
    pub resume_from: Option<ResumeWatermark>,
    /// Optional metadata used to bind a client nonce and profile context.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub client_metadata: Option<ClientMetadata>,
}

impl HelloEnvelope {
    /// Validate the version list and any supplied metadata.
    pub fn validate(&self) -> Result<(), CoreError> {
        if self.supported_protocols.is_empty() || !self.supported_protocols.contains(&self.protocol)
        {
            return Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                "hello protocol is not present in supported_protocols",
            ));
        }
        if let Some(metadata) = &self.client_metadata {
            metadata.validate()?;
        }
        Ok(())
    }

    /// Validate the stronger handshake form used by an authenticated host.
    pub fn validate_handshake(&self) -> Result<(), CoreError> {
        self.validate()?;
        self.client_metadata.as_ref().map_or_else(
            || {
                Err(CoreError::invalid_argument(
                    "handshake requires client metadata",
                ))
            },
            ClientMetadata::validate_required,
        )
    }
}

/// Host-to-client handshake acknowledgement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HelloOkEnvelope {
    /// Version selected for this connection.
    pub protocol: u16,
    /// Broker lifecycle epoch.
    pub broker_epoch: BrokerEpoch,
    /// Connection epoch for this client connection.
    pub connection_epoch: ConnectionEpoch,
    /// Capabilities available through the negotiated protocol.
    pub capabilities: Vec<Capability>,
    /// Whether the requested resume cursor is still available.
    pub resume: ResumeResult,
    /// Optional metadata proving the host-side nonce and profile context.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub host_metadata: Option<HostMetadata>,
}

impl HelloOkEnvelope {
    /// Validate the selected version and any supplied host metadata.
    pub fn validate(&self) -> Result<(), CoreError> {
        if self.protocol != PROTOCOL_VERSION {
            return Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                format!("unsupported protocol version {}", self.protocol),
            ));
        }
        if let Some(metadata) = &self.host_metadata {
            metadata.validate()?;
        }
        Ok(())
    }

    /// Validate the stronger host acknowledgement form.
    pub fn validate_handshake(&self) -> Result<(), CoreError> {
        self.validate()?;
        self.host_metadata.as_ref().map_or_else(
            || {
                Err(CoreError::invalid_argument(
                    "hello_ok requires host metadata",
                ))
            },
            HostMetadata::validate_required,
        )
    }

    /// Validate this acknowledgement against the client's stronger handshake.
    pub fn validate_against(&self, hello: &HelloEnvelope) -> Result<(), CoreError> {
        hello.validate_handshake()?;
        self.validate_handshake()?;
        if self.protocol != hello.protocol {
            return Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                "hello and hello_ok protocol versions differ",
            ));
        }

        let client_metadata = hello
            .client_metadata
            .as_ref()
            .ok_or_else(|| CoreError::invalid_argument("handshake requires client metadata"))?;
        let host_metadata = self
            .host_metadata
            .as_ref()
            .ok_or_else(|| CoreError::invalid_argument("hello_ok requires host metadata"))?;
        if host_metadata.connection_nonce != client_metadata.connection_nonce {
            return Err(CoreError::invalid_argument(
                "hello_ok does not echo the client connection nonce",
            ));
        }
        if host_metadata.profile_binding_id != client_metadata.profile_binding_id {
            return Err(CoreError::invalid_argument(
                "hello_ok does not echo the client profile binding",
            ));
        }
        Ok(())
    }
}

/// Result of an event-stream resume request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum ResumeResult {
    /// Events after the cursor can be replayed.
    Accepted,
    /// The cursor is too old and a fresh snapshot is needed.
    ResyncRequired,
}

/// A request envelope. The generic payload keeps the core independent of JSON-value types.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RequestEnvelope<P = BTreeMap<String, String>> {
    /// Protocol version for this request.
    pub protocol: u16,
    /// Request identity used to match out-of-order responses.
    pub request_id: RequestId,
    /// Transport-neutral operation name.
    pub method: String,
    /// Typed operation parameters chosen by the caller.
    pub params: P,
    /// Optional deadline in milliseconds from the client's clock.
    pub deadline_ms: Option<u64>,
    /// Optional idempotency identity for mutations.
    pub idempotency_key: Option<IdempotencyKey>,
}

impl<P> RequestEnvelope<P> {
    /// Return whether the method is a bounded dotted operation name.
    pub fn has_valid_method(&self) -> bool {
        let mut parts = self.method.split('.');
        !self.method.is_empty()
            && self.method.len() <= 128
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
}

/// A response envelope. The result payload is generic for the same reason as request parameters.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResponseEnvelope<P = BTreeMap<String, String>> {
    /// Protocol version for this response.
    pub protocol: u16,
    /// Request identity being completed.
    pub request_id: RequestId,
    /// Whether the request succeeded.
    pub ok: bool,
    /// Typed result when `ok` is true.
    pub result: Option<P>,
    /// Structured error when `ok` is false.
    pub error: Option<CoreError>,
    /// Bounded warnings that do not change the result.
    pub warnings: Vec<String>,
}

impl<P> ResponseEnvelope<P> {
    /// Construct a successful response.
    pub fn success(request_id: RequestId, result: P) -> Self {
        Self {
            protocol: PROTOCOL_VERSION,
            request_id,
            ok: true,
            result: Some(result),
            error: None,
            warnings: Vec::new(),
        }
    }

    /// Construct an error response.
    pub fn failure(request_id: RequestId, error: CoreError) -> Self {
        Self {
            protocol: PROTOCOL_VERSION,
            request_id,
            ok: false,
            result: None,
            error: Some(error),
            warnings: Vec::new(),
        }
    }
}

/// An event envelope with a generic payload.
pub type EventEnvelope<P = BTreeMap<String, String>> = EventRecord<P>;

/// Kind of bounded artifact chunk.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactKind {
    /// Human-readable text.
    Text,
    /// Image data.
    Image,
    /// PDF data.
    Pdf,
    /// Other bounded binary data.
    Binary,
}

/// A bounded artifact chunk envelope.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ArtifactEnvelope {
    /// Protocol version for this envelope.
    pub protocol: u16,
    /// Artifact identity.
    pub artifact_id: ArtifactId,
    /// Optional request that produced the artifact.
    pub request_id: Option<RequestId>,
    /// Artifact media category.
    pub artifact_kind: ArtifactKind,
    /// Zero-based chunk sequence.
    pub chunk_sequence: u32,
    /// Whether this chunk completes the artifact.
    pub final_chunk: bool,
    /// Bounded chunk bytes.
    pub bytes: Vec<u8>,
}

impl ArtifactEnvelope {
    /// Validate the protocol version and chunk bound before transport encoding.
    pub fn validate(&self) -> Result<(), CoreError> {
        if self.protocol != PROTOCOL_VERSION {
            return Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                format!("unsupported protocol version {}", self.protocol),
            ));
        }
        if self.bytes.len() > MAX_ARTIFACT_CHUNK_BYTES {
            return Err(CoreError::new(
                ErrorCode::MessageTooLarge,
                format!("artifact chunk exceeds {} bytes", MAX_ARTIFACT_CHUNK_BYTES),
            ));
        }
        Ok(())
    }
}

/// Envelope used to cancel a request that has not completed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CancelEnvelope {
    /// Protocol version for this envelope.
    pub protocol: u16,
    /// Request being cancelled.
    pub request_id: RequestId,
    /// Optional bounded reason.
    pub reason: Option<String>,
}

/// Envelope used to request event replay after a cursor.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResumeEnvelope {
    /// Protocol version for this envelope.
    pub protocol: u16,
    /// Event cursor to resume after.
    pub after: ResumeWatermark,
}

/// All local protocol message variants.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Envelope<P = BTreeMap<String, String>> {
    /// Initial client handshake.
    Hello(HelloEnvelope),
    /// Host handshake acknowledgement.
    HelloOk(HelloOkEnvelope),
    /// Client request.
    Request(RequestEnvelope<P>),
    /// Host response.
    Response(ResponseEnvelope<P>),
    /// Broker-sequenced event.
    Event(EventEnvelope<P>),
    /// Bounded artifact chunk.
    Artifact(ArtifactEnvelope),
    /// Request cancellation.
    Cancel(CancelEnvelope),
    /// Event resume request.
    Resume(ResumeEnvelope),
}

impl<P> Envelope<P> {
    /// Return the protocol version carried by this envelope.
    pub fn protocol(&self) -> u16 {
        match self {
            Self::Hello(value) => value.protocol,
            Self::HelloOk(value) => value.protocol,
            Self::Request(value) => value.protocol,
            Self::Response(value) => value.protocol,
            Self::Event(value) => value.protocol,
            Self::Artifact(value) => value.protocol,
            Self::Cancel(value) => value.protocol,
            Self::Resume(value) => value.protocol,
        }
    }

    /// Validate the version field against the current protocol.
    pub fn validate_protocol(&self) -> Result<(), CoreError> {
        if self.protocol() == PROTOCOL_VERSION {
            Ok(())
        } else {
            Err(CoreError::new(
                ErrorCode::ProtocolMismatch,
                format!("unsupported protocol version {}", self.protocol()),
            ))
        }
    }
}

/// Negotiate the highest version supported by both peers.
pub fn negotiate_version(client: &[u16], server: &[u16]) -> Result<NegotiatedProtocol, CoreError> {
    let selected = client
        .iter()
        .copied()
        .filter(|version| server.contains(version))
        .max()
        .ok_or_else(|| {
            CoreError::new(
                ErrorCode::ProtocolMismatch,
                "client and host have no common protocol version",
            )
        })?;
    Ok(NegotiatedProtocol { protocol: selected })
}

/// Encode one bounded payload with a four-byte big-endian length prefix.
pub fn encode_frame(payload: &[u8], max_payload_bytes: usize) -> Result<Vec<u8>, FrameError> {
    if max_payload_bytes > u32::MAX as usize {
        return Err(FrameError::InvalidLimit);
    }
    if payload.len() > max_payload_bytes || payload.len() > u32::MAX as usize {
        return Err(FrameError::MessageTooLarge {
            length: payload.len(),
            max: max_payload_bytes.min(u32::MAX as usize),
        });
    }
    let length = u32::try_from(payload.len()).map_err(|_| FrameError::MessageTooLarge {
        length: payload.len(),
        max: max_payload_bytes,
    })?;
    let mut frame = Vec::with_capacity(FRAME_PREFIX_BYTES + payload.len());
    frame.extend_from_slice(&length.to_be_bytes());
    frame.extend_from_slice(payload);
    Ok(frame)
}

/// Decode exactly one complete frame without allocating the payload.
pub fn decode_frame(frame: &[u8], max_payload_bytes: usize) -> Result<&[u8], FrameError> {
    if max_payload_bytes > u32::MAX as usize {
        return Err(FrameError::InvalidLimit);
    }
    if frame.len() < FRAME_PREFIX_BYTES {
        return Err(FrameError::Truncated {
            expected: FRAME_PREFIX_BYTES,
            actual: frame.len(),
        });
    }
    let length = u32::from_be_bytes([frame[0], frame[1], frame[2], frame[3]]) as usize;
    if length > max_payload_bytes {
        return Err(FrameError::MessageTooLarge {
            length,
            max: max_payload_bytes,
        });
    }
    let expected = FRAME_PREFIX_BYTES
        .checked_add(length)
        .ok_or(FrameError::InvalidLimit)?;
    if frame.len() < expected {
        return Err(FrameError::Truncated {
            expected,
            actual: frame.len(),
        });
    }
    if frame.len() > expected {
        return Err(FrameError::TrailingBytes);
    }
    Ok(&frame[FRAME_PREFIX_BYTES..expected])
}

/// Incremental frame decoder for fragmented or coalesced input.
#[derive(Debug)]
pub struct FrameDecoder {
    max_payload_bytes: usize,
    prefix: [u8; FRAME_PREFIX_BYTES],
    prefix_len: usize,
    expected_payload: Option<usize>,
    payload: Vec<u8>,
    poisoned: bool,
}

impl FrameDecoder {
    /// Construct a decoder with a bounded payload size.
    pub fn new(max_payload_bytes: usize) -> Self {
        Self {
            max_payload_bytes: max_payload_bytes.min(u32::MAX as usize),
            prefix: [0; FRAME_PREFIX_BYTES],
            prefix_len: 0,
            expected_payload: None,
            payload: Vec::new(),
            poisoned: false,
        }
    }

    /// Construct a decoder using the default bound.
    pub fn default_bound() -> Self {
        Self::new(DEFAULT_MAX_FRAME_PAYLOAD_BYTES)
    }

    /// Return the configured maximum payload size.
    pub const fn max_payload_bytes(&self) -> usize {
        self.max_payload_bytes
    }

    /// Feed bytes and return every complete payload observed in this chunk.
    pub fn feed(&mut self, bytes: &[u8]) -> Result<Vec<Vec<u8>>, FrameError> {
        if self.poisoned {
            return Err(FrameError::DecoderPoisoned);
        }
        let mut frames = Vec::new();
        for byte in bytes {
            if let Some(expected) = self.expected_payload {
                self.payload.push(*byte);
                if self.payload.len() == expected {
                    self.expected_payload = None;
                    frames.push(mem::take(&mut self.payload));
                }
                continue;
            }

            self.prefix[self.prefix_len] = *byte;
            self.prefix_len += 1;
            if self.prefix_len != FRAME_PREFIX_BYTES {
                continue;
            }

            let length = u32::from_be_bytes(self.prefix) as usize;
            self.prefix_len = 0;
            if length > self.max_payload_bytes {
                self.poisoned = true;
                return Err(FrameError::MessageTooLarge {
                    length,
                    max: self.max_payload_bytes,
                });
            }
            if length == 0 {
                frames.push(Vec::new());
            } else {
                self.expected_payload = Some(length);
                self.payload = Vec::with_capacity(length);
            }
        }
        Ok(frames)
    }

    /// Finish decoding and reject a partial prefix or payload.
    pub fn finish(&self) -> Result<(), FrameError> {
        if self.poisoned {
            return Err(FrameError::DecoderPoisoned);
        }
        if self.prefix_len != 0 {
            return Err(FrameError::Truncated {
                expected: FRAME_PREFIX_BYTES,
                actual: self.prefix_len,
            });
        }
        if let Some(expected) = self.expected_payload {
            return Err(FrameError::Truncated {
                expected,
                actual: self.payload.len(),
            });
        }
        Ok(())
    }
}

/// Decode a UTF-8 frame payload without tying the core to a JSON implementation.
pub fn decode_utf8(payload: &[u8]) -> Result<&str, CoreError> {
    std::str::from_utf8(payload)
        .map_err(|_| CoreError::new(ErrorCode::InvalidUtf8, "frame payload is not UTF-8"))
}

/// Convert a serde deserialization failure at an adapter boundary into the stable code.
pub fn invalid_json_error(message: impl Into<String>) -> CoreError {
    CoreError::new(ErrorCode::InvalidJson, message)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        events::GenerationWatermark,
        ids::{
            ArtifactId, ClientId, ConnectionEpoch, ConnectionNonce, EventId, ProfileBindingId,
            SpaceId,
        },
        states::Capability,
    };

    #[test]
    fn framing_uses_big_endian_and_supports_fragmentation_and_coalescing() {
        let first = encode_frame(b"one", 32).expect("frame");
        let second = encode_frame(b"two", 32).expect("frame");
        let mut decoder = FrameDecoder::new(32);
        assert!(decoder.feed(&first[..2]).expect("partial").is_empty());
        assert_eq!(
            decoder.feed(&[first[2], first[3]]).expect("prefix"),
            Vec::<Vec<u8>>::new()
        );
        let mut joined = first[4..].to_vec();
        joined.extend_from_slice(&second);
        assert_eq!(
            decoder.feed(&joined).expect("coalesced"),
            vec![b"one".to_vec(), b"two".to_vec()]
        );
        decoder.finish().expect("clean eof");
        assert_eq!(first[..4], [0, 0, 0, 3]);
    }

    #[test]
    fn oversized_prefix_is_rejected_before_payload_allocation() {
        let mut decoder = FrameDecoder::new(3);
        let error = decoder.feed(&[0, 0, 0, 4]).expect_err("oversized");
        assert_eq!(error.code(), ErrorCode::MessageTooLarge);
        assert!(matches!(
            decoder.feed(&[]),
            Err(FrameError::DecoderPoisoned)
        ));
    }

    #[test]
    fn normative_limits_accept_exact_boundaries_and_reject_overages() {
        assert_eq!(MAX_CONTROL_FRAME_PAYLOAD_BYTES, 1024 * 1024);
        assert_eq!(DEFAULT_MAX_FRAME_PAYLOAD_BYTES, 1024 * 1024);
        assert_eq!(MAX_ARTIFACT_CHUNK_BYTES, 256 * 1024);

        let control = vec![0_u8; MAX_CONTROL_FRAME_PAYLOAD_BYTES];
        let frame = encode_frame(&control, MAX_CONTROL_FRAME_PAYLOAD_BYTES).expect("exact frame");
        assert_eq!(
            decode_frame(&frame, MAX_CONTROL_FRAME_PAYLOAD_BYTES)
                .expect("decode")
                .len(),
            control.len()
        );
        assert_eq!(
            FrameDecoder::default_bound().max_payload_bytes(),
            MAX_CONTROL_FRAME_PAYLOAD_BYTES
        );
        let mut over_control = control;
        over_control.push(0);
        assert!(matches!(
            encode_frame(&over_control, MAX_CONTROL_FRAME_PAYLOAD_BYTES),
            Err(FrameError::MessageTooLarge { length, max })
                if length == MAX_CONTROL_FRAME_PAYLOAD_BYTES + 1
                    && max == MAX_CONTROL_FRAME_PAYLOAD_BYTES
        ));

        let artifact = ArtifactEnvelope {
            protocol: PROTOCOL_VERSION,
            artifact_id: ArtifactId::from_suffix("boundary").expect("artifact"),
            request_id: None,
            artifact_kind: ArtifactKind::Binary,
            chunk_sequence: 0,
            final_chunk: true,
            bytes: vec![0_u8; MAX_ARTIFACT_CHUNK_BYTES],
        };
        artifact.validate().expect("exact artifact chunk");
        let mut over_artifact = artifact;
        over_artifact.bytes.push(0);
        assert!(matches!(
            over_artifact.validate(),
            Err(CoreError {
                code: ErrorCode::MessageTooLarge,
                ..
            })
        ));
    }

    #[test]
    fn handshake_metadata_is_validated_and_echoed() {
        let nonce = ConnectionNonce::from_suffix("fresh").expect("nonce");
        let profile = ProfileBindingId::from_suffix("default").expect("profile");
        let hello = HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: crate::ids::PrincipalId::from_suffix("agent").expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: Some(ClientId::from_suffix("one").expect("client")),
                client_name: Some("test-client".to_owned()),
                client_version: Some("1.0".to_owned()),
                connection_nonce: Some(nonce.clone()),
                profile_binding_id: Some(profile.clone()),
            }),
        };
        let hello_ok = HelloOkEnvelope {
            protocol: PROTOCOL_VERSION,
            broker_epoch: BrokerEpoch::new(1),
            connection_epoch: ConnectionEpoch::new(1),
            capabilities: vec![Capability::Snapshot],
            resume: ResumeResult::Accepted,
            host_metadata: Some(HostMetadata {
                host_name: Some("test-host".to_owned()),
                host_version: Some("1.0".to_owned()),
                connection_nonce: Some(nonce),
                profile_binding_id: Some(profile),
            }),
        };
        hello.validate_handshake().expect("strong hello");
        hello_ok
            .validate_against(&hello)
            .expect("matching handshake");
    }

    #[test]
    fn envelope_versions_and_event_payloads_are_transport_neutral() {
        let space = SpaceId::from_suffix("one").expect("space");
        let event = EventRecord {
            protocol: PROTOCOL_VERSION,
            event_id: EventId::from_suffix("one").expect("event"),
            broker_epoch: BrokerEpoch::new(1),
            sequence: EventSequence::new(1),
            scope: crate::events::EventScope::space(space),
            event: crate::events::EventKind::PageChanged,
            generation: GenerationWatermark::default(),
            dirty_reason: None,
            coalesced: false,
            resync_required: false,
            payload: BTreeMap::<String, String>::new(),
        };
        let envelope = Envelope::Event(event);
        assert_eq!(envelope.protocol(), PROTOCOL_VERSION);
        envelope.validate_protocol().expect("current protocol");
    }
}
