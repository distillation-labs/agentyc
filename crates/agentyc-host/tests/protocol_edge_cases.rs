use std::collections::BTreeMap;

use agentyc_core::{
    Envelope, ErrorCode, PROTOCOL_VERSION, RequestId, ResponseEnvelope, encode_frame,
};
use agentyc_host::ProtocolClient;

fn response(request_id: &str, value: &str) -> Envelope {
    Envelope::Response(ResponseEnvelope::success(
        RequestId::from_suffix(request_id).expect("request identity"),
        BTreeMap::from([(String::from("value"), value.to_owned())]),
    ))
}

#[test]
fn coalesced_out_of_order_responses_keep_their_request_identity() {
    let client = ProtocolClient::with_max_payload(4096).expect("protocol client");
    let second = client
        .encode(&response("request-second", "second"))
        .expect("second frame");
    let first = client
        .encode(&response("request-first", "first"))
        .expect("first frame");
    let mut combined = second;
    combined.extend_from_slice(&first);

    let mut decoder = ProtocolClient::with_max_payload(4096).expect("decoder");
    let envelopes = decoder.feed(&combined).expect("coalesced frames");
    assert_eq!(envelopes.len(), 2);
    let Envelope::Response(second_response) = &envelopes[0] else {
        panic!("first decoded envelope was not a response");
    };
    let Envelope::Response(first_response) = &envelopes[1] else {
        panic!("second decoded envelope was not a response");
    };
    assert_eq!(second_response.request_id.as_str(), "req_request-second");
    assert_eq!(first_response.request_id.as_str(), "req_request-first");
    assert_eq!(second_response.result.as_ref().unwrap()["value"], "second");
    assert_eq!(first_response.result.as_ref().unwrap()["value"], "first");
}

#[test]
fn fragmented_frames_decode_only_after_the_complete_payload_arrives() {
    let client = ProtocolClient::with_max_payload(4096).expect("protocol client");
    let frame = client
        .encode(&response("request-fragmented", "ok"))
        .expect("frame");
    let split = frame.len() / 2;
    let mut decoder = ProtocolClient::with_max_payload(4096).expect("decoder");
    assert!(
        decoder
            .feed(&frame[..split])
            .expect("first fragment")
            .is_empty()
    );
    let envelopes = decoder.feed(&frame[split..]).expect("second fragment");
    assert_eq!(envelopes.len(), 1);
}

#[test]
fn truncated_stream_is_rejected_at_finish() {
    let client = ProtocolClient::with_max_payload(4096).expect("protocol client");
    let frame = client
        .encode(&response("request-truncated", "ok"))
        .expect("frame");
    let mut decoder = ProtocolClient::with_max_payload(4096).expect("decoder");
    decoder.feed(&frame[..3]).expect("partial prefix");
    let error = decoder.finish().expect_err("truncated frame");
    assert_eq!(error.as_core_error().code, ErrorCode::TruncatedFrame);
}

#[test]
fn local_protocol_uses_the_big_endian_length_prefix() {
    let client = ProtocolClient::with_max_payload(4096).expect("protocol client");
    let frame = client
        .encode(&response("request-endian", "ok"))
        .expect("frame");
    let declared = u32::from_be_bytes(frame[..4].try_into().expect("length prefix")) as usize;
    assert_eq!(declared, frame.len() - 4);
    assert_eq!(PROTOCOL_VERSION, 1);
    assert!(encode_frame(&frame[4..], 4096).is_ok());
}
