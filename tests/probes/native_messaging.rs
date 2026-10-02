//! Standalone, dependency-free framing and policy tests for the Phase 0 probe.
//! Compile directly with `rustc --test tests/probes/native_messaging.rs`.

use std::collections::{HashMap, HashSet};

const MAX_FRAME_BYTES: usize = 1024 * 1024;
const MAX_CHUNK_BYTES: usize = 64 * 1024;
const MAX_ENVELOPE_BYTES: usize = 64 * 1024;
const MAX_CUMULATIVE_FRAME_BYTES: usize = 4 * MAX_FRAME_BYTES;
const SUPPORTED_VERSION: u64 = 1;
const EXPECTED_ORIGIN: &str = "chrome-extension://p0probe";

#[derive(Debug, PartialEq, Eq)]
enum Reject {
    Oversize,
    Truncated,
    InvalidUtf8,
    InvalidJson,
    WrongOrigin,
    Replay,
    UnsupportedVersion,
    InvalidEnvelope,
}

struct FrameDecoder {
    buffer: Vec<u8>,
    declared: Option<usize>,
    total_wire_bytes: usize,
}

impl FrameDecoder {
    fn new() -> Self {
        Self { buffer: Vec::new(), declared: None, total_wire_bytes: 0 }
    }

    fn feed(&mut self, chunk: &[u8]) -> Result<Vec<Vec<u8>>, Reject> {
        if chunk.len() > MAX_CHUNK_BYTES {
            return Err(Reject::Oversize);
        }
        self.total_wire_bytes = self.total_wire_bytes.saturating_add(chunk.len());
        if self.total_wire_bytes > MAX_CUMULATIVE_FRAME_BYTES
            || self.buffer.len().saturating_add(chunk.len()) > MAX_FRAME_BYTES + 4
        {
            return Err(Reject::Oversize);
        }
        self.buffer.extend_from_slice(chunk);
        let mut frames = Vec::new();
        loop {
            if self.declared.is_none() {
                if self.buffer.len() < 4 {
                    break;
                }
                let len = u32::from_le_bytes(self.buffer[..4].try_into().unwrap()) as usize;
                self.buffer.drain(..4);
                if len > MAX_FRAME_BYTES {
                    return Err(Reject::Oversize);
                }
                self.declared = Some(len);
            }
            let len = self.declared.unwrap();
            if self.buffer.len() < len {
                break;
            }
            frames.push(self.buffer.drain(..len).collect());
            self.declared = None;
        }
        Ok(frames)
    }

    fn finish(&self) -> Result<(), Reject> {
        if self.declared.is_some() || !self.buffer.is_empty() {
            Err(Reject::Truncated)
        } else {
            Ok(())
        }
    }
}

fn frame(payload: &[u8]) -> Result<Vec<u8>, Reject> {
    if payload.len() > MAX_FRAME_BYTES {
        return Err(Reject::Oversize);
    }
    let mut output = (payload.len() as u32).to_le_bytes().to_vec();
    output.extend_from_slice(payload);
    Ok(output)
}

fn json_string_field<'a>(json: &'a str, field: &str) -> Option<&'a str> {
    let marker = format!("\"{field}\":\"");
    let start = json.find(&marker)? + marker.len();
    let rest = &json[start..];
    let end = rest.find('"')?;
    Some(&rest[..end])
}

fn json_number_field(json: &str, field: &str) -> Option<u64> {
    let marker = format!("\"{field}\":");
    let start = json.find(&marker)? + marker.len();
    let rest = &json[start..];
    let end = rest.find(|c: char| !c.is_ascii_digit()).unwrap_or(rest.len());
    rest[..end].parse().ok()
}

fn is_basic_json_object(json: &str) -> bool {
    let bytes = json.as_bytes();
    if bytes.len() < 2 || bytes[0] != b'{' || bytes[bytes.len() - 1] != b'}' {
        return false;
    }
    let mut in_string = false;
    let mut escaped = false;
    for &byte in bytes {
        if escaped {
            escaped = false;
            continue;
        }
        if byte == b'\\' && in_string {
            escaped = true;
        } else if byte == b'"' {
            in_string = !in_string;
        } else if byte < 0x20 && in_string {
            return false;
        }
    }
    !in_string && !escaped
}

struct Session {
    seen: HashSet<String>,
    nonce: Option<String>,
}

impl Session {
    fn new() -> Self {
        Self { seen: HashSet::new(), nonce: None }
    }

    fn accept(&mut self, payload: &[u8]) -> Result<(), Reject> {
        if payload.len() > MAX_ENVELOPE_BYTES {
            return Err(Reject::Oversize);
        }
        let json = std::str::from_utf8(payload).map_err(|_| Reject::InvalidUtf8)?;
        if !is_basic_json_object(json) {
            return Err(Reject::InvalidJson);
        }
        if json_number_field(json, "version") != Some(SUPPORTED_VERSION) {
            return Err(Reject::UnsupportedVersion);
        }
        if json_string_field(json, "origin") != Some(EXPECTED_ORIGIN) {
            return Err(Reject::WrongOrigin);
        }
        let message_id = json_string_field(json, "message_id").ok_or(Reject::InvalidEnvelope)?.to_owned();
        let nonce = json_string_field(json, "nonce").ok_or(Reject::InvalidEnvelope)?.to_owned();
        if !self.seen.insert(message_id) {
            return Err(Reject::Replay);
        }
        if let Some(bound) = &self.nonce {
            if bound != &nonce {
                return Err(Reject::Replay);
            }
        } else {
            self.nonce = Some(nonce);
        }
        Ok(())
    }
}

struct BrokerRegistry {
    brokers: HashMap<String, usize>,
}

impl BrokerRegistry {
    fn new() -> Self { Self { brokers: HashMap::new() } }
    fn connect(&mut self, key: &str) -> usize {
        let next = self.brokers.len() + 1;
        *self.brokers.entry(key.to_owned()).or_insert(next)
    }
}

fn valid_message(id: &str, nonce: &str, version: u64, origin: &str) -> Vec<u8> {
    format!(
        "{{\"kind\":\"probe\",\"message_id\":\"{id}\",\"nonce\":\"{nonce}\",\"origin\":\"{origin}\",\"payload\":{{}},\"version\":{version}}}"
    ).into_bytes()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fragmented_frame_round_trips() {
        let payload = b"hello";
        let encoded = frame(payload).unwrap();
        let mut decoder = FrameDecoder::new();
        for byte in encoded {
            assert!(decoder.feed(&[byte]).unwrap().is_empty() || byte == b'o');
        }
        let mut decoder = FrameDecoder::new();
        assert!(decoder.feed(&[5, 0]).unwrap().is_empty());
        assert!(decoder.feed(b"\0\0he").unwrap().is_empty());
        assert_eq!(decoder.feed(b"llo").unwrap(), vec![payload.to_vec()]);
    }

    #[test]
    fn malformed_frames_fail_closed() {
        let mut decoder = FrameDecoder::new();
        decoder.feed(&[5, 0]).unwrap();
        assert_eq!(decoder.finish(), Err(Reject::Truncated));
        let mut decoder = FrameDecoder::new();
        assert_eq!(decoder.feed(&(MAX_FRAME_BYTES as u32 + 1).to_le_bytes()), Err(Reject::Oversize));
        let mut decoder = FrameDecoder::new();
        decoder.feed(&[8, 0, 0, 0, b'a', b'b']).unwrap();
        assert_eq!(decoder.finish(), Err(Reject::Truncated));
    }

    #[test]
    fn invalid_utf8_and_json_are_rejected() {
        let mut session = Session::new();
        assert_eq!(session.accept(&[0xff]), Err(Reject::InvalidUtf8));
        assert_eq!(session.accept(b"not-json"), Err(Reject::InvalidJson));
    }

    #[test]
    fn origin_version_and_replay_are_rejected() {
        let mut session = Session::new();
        let hello = valid_message("m1", "n1", 1, EXPECTED_ORIGIN);
        session.accept(&hello).unwrap();
        assert_eq!(session.accept(&hello), Err(Reject::Replay));
        let mut wrong_origin = Session::new();
        assert_eq!(wrong_origin.accept(&valid_message("m2", "n1", 1, "chrome-extension://wrong/")), Err(Reject::WrongOrigin));
        let mut wrong_version = Session::new();
        assert_eq!(wrong_version.accept(&valid_message("m3", "n1", 2, EXPECTED_ORIGIN)), Err(Reject::UnsupportedVersion));
    }

    #[test]
    fn reconnect_reuses_one_broker() {
        let mut registry = BrokerRegistry::new();
        assert_eq!(registry.connect("p0"), registry.connect("p0"));
        assert_eq!(registry.brokers.len(), 1);
    }
}
