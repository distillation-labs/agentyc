use std::collections::BTreeMap;

use agentyc_core::{
    ClientMetadata, ConnectionNonce, Envelope, ErrorCode, EventSequence, HelloEnvelope,
    PROTOCOL_VERSION, PrincipalId, RequestEnvelope, RequestId, ResponseEnvelope, ResumeEnvelope,
    ResumeWatermark, decode_frame, encode_frame,
};
use agentyc_host::{Broker, FakeBridge, ProtocolClient, ProtocolServer};
use tempfile::tempdir;

fn response(request_id: &str, value: &str) -> Envelope {
    Envelope::Response(ResponseEnvelope::success(
        RequestId::from_suffix(request_id).expect("request identity"),
        BTreeMap::from([(String::from("value"), value.to_owned())]),
    ))
}

fn hello() -> Envelope {
    Envelope::Hello(HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: PrincipalId::from_suffix("transport-test").expect("principal"),
        resume_from: None,
        client_metadata: Some(ClientMetadata {
            client_id: None,
            client_name: Some("transport-test".to_owned()),
            client_version: Some("1".to_owned()),
            connection_nonce: Some(ConnectionNonce::from_suffix("transport-test").expect("nonce")),
            profile_binding_id: None,
        }),
    })
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

#[test]
fn resume_wire_response_keeps_typed_values_out_of_legacy_string_maps() {
    let directory = tempdir().expect("temporary directory");
    let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
    let mut server = ProtocolServer::new(broker);
    let mut client = ProtocolClient::with_max_payload(4096).expect("protocol client");
    let hello_frame = client.encode(&hello()).expect("hello frame");
    let hello_output = server.handle_frame(&hello_frame).expect("hello output");
    client.feed(&hello_output).expect("hello response");

    let resume = Envelope::Resume(ResumeEnvelope {
        protocol: PROTOCOL_VERSION,
        after: ResumeWatermark {
            broker_epoch: agentyc_core::BrokerEpoch::new(1),
            sequence: EventSequence::new(0),
        },
    });
    let output = server
        .handle_frame(&client.encode(&resume).expect("resume frame"))
        .expect("resume output");
    let payload = decode_frame(&output, 4096).expect("response frame");
    let value: serde_json::Value = serde_json::from_slice(payload).expect("response json");
    let result = &value["result"];
    assert!(result["resume_result"].is_object());
    assert!(result["cursor"].is_object());
    assert!(result["events"].is_array());
    let typed = client.feed_typed(&output).expect("typed response");
    assert!(typed[0]["result"]["events"].is_array());
}

#[test]
fn strict_local_wire_validation_rejects_unknown_fields_and_conflicting_request_reuse() {
    let directory = tempdir().expect("temporary directory");
    let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
    let mut server = ProtocolServer::new(broker);
    server.dispatch(hello()).expect("hello");
    let malformed = serde_json::json!({
        "protocol": PROTOCOL_VERSION,
        "kind": "request",
        "request_id": "req_transport_unknown",
        "method": "space.list",
        "params": {},
        "unknown": true,
    });
    assert!(
        server
            .handle_payload(&serde_json::to_vec(&malformed).expect("json"))
            .is_err()
    );

    let make_request = |label: &str| {
        Envelope::Request(RequestEnvelope {
            protocol: PROTOCOL_VERSION,
            request_id: RequestId::from_suffix("duplicate-transport").expect("request"),
            method: "space.list".to_owned(),
            params: BTreeMap::from([("context".to_owned(), label.to_owned())]),
            deadline_ms: None,
            idempotency_key: None,
        })
    };
    let first = server.dispatch(make_request("one")).expect("first");
    assert!(matches!(&first[0], Envelope::Response(response) if response.ok));
    let second = server.dispatch(make_request("two")).expect("second");
    assert_eq!(
        match &second[0] {
            Envelope::Response(response) => response.error.as_ref().expect("error").code,
            _ => panic!("expected response"),
        },
        ErrorCode::InvalidArgument
    );
}
