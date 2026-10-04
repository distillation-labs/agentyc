use std::collections::BTreeMap;

use agentyc_core::{
    ActionReceipt, CoreError, ElementRef, Envelope, EventRecord, SnapshotElement, SnapshotEnvelope,
    WaitCondition,
};
use serde::{Serialize, de::DeserializeOwned};
use serde_json::Value;

fn assert_canonical_fixture<T>(name: &str, fixture: &str)
where
    T: DeserializeOwned + Serialize,
{
    let expected_text = fixture.trim();
    let expected: Value = serde_json::from_str(expected_text)
        .unwrap_or_else(|error| panic!("{name} fixture is not JSON: {error}"));
    let parsed: T = serde_json::from_str(expected_text)
        .unwrap_or_else(|error| panic!("{name} fixture does not match its Rust contract: {error}"));
    let actual = serde_json::to_value(&parsed)
        .unwrap_or_else(|error| panic!("{name} contract cannot serialize: {error}"));
    assert_eq!(actual, expected, "{name} changed during typed round-trip");
}

#[test]
fn remaining_typed_contract_fixtures_are_executable_goldens() {
    assert_canonical_fixture::<ActionReceipt>(
        "action-unknown",
        include_str!("fixtures/action-unknown.json"),
    );
    assert_canonical_fixture::<ActionReceipt>(
        "action-reconciled",
        include_str!("fixtures/action-reconciled.json"),
    );
    assert_canonical_fixture::<CoreError>(
        "error-stale-ref",
        include_str!("fixtures/error-stale-ref.json"),
    );
    assert_canonical_fixture::<EventRecord>(
        "event-record",
        include_str!("fixtures/event-record.json"),
    );
    assert_canonical_fixture::<ElementRef>("ref", include_str!("fixtures/ref.json"));
    assert_canonical_fixture::<SnapshotElement>(
        "snapshot-element",
        include_str!("fixtures/snapshot-element.json"),
    );
    assert_canonical_fixture::<SnapshotEnvelope>(
        "snapshot-full",
        include_str!("fixtures/snapshot-full.json"),
    );
    assert_canonical_fixture::<SnapshotEnvelope>(
        "snapshot-resync",
        include_str!("fixtures/snapshot-resync.json"),
    );
    assert_canonical_fixture::<WaitCondition>(
        "wait-condition",
        include_str!("fixtures/wait-condition.json"),
    );
    assert_canonical_fixture::<Envelope<BTreeMap<String, String>>>(
        "artifact-begin",
        include_str!("fixtures/artifact-begin.json"),
    );
    assert_canonical_fixture::<Envelope<BTreeMap<String, String>>>(
        "artifact-chunk-0",
        include_str!("fixtures/artifact-chunk-0.json"),
    );
    assert_canonical_fixture::<Envelope<BTreeMap<String, String>>>(
        "artifact-chunk-1",
        include_str!("fixtures/artifact-chunk-1.json"),
    );
    assert_canonical_fixture::<Envelope<BTreeMap<String, String>>>(
        "artifact-end",
        include_str!("fixtures/artifact-end.json"),
    );
    assert_canonical_fixture::<Envelope<BTreeMap<String, String>>>(
        "wait-cancel",
        include_str!("fixtures/wait-cancel.json"),
    );
}

#[test]
fn user_control_return_fixture_preserves_opaque_ticket_shape() {
    let value: Value = serde_json::from_str(include_str!("fixtures/user-control-return.json"))
        .expect("user-control return fixture");
    let object = value.as_object().expect("user-control return object");
    assert_eq!(
        object.keys().collect::<Vec<_>>(),
        vec![
            "control_ticket",
            "fence_epoch",
            "lifecycle",
            "released_epoch",
            "space_id"
        ]
    );
    assert_eq!(
        object.get("space_id").and_then(Value::as_str),
        Some("space_demo")
    );
    assert_eq!(
        object.get("released_epoch").and_then(Value::as_u64),
        Some(3)
    );
    assert_eq!(object.get("fence_epoch").and_then(Value::as_u64), Some(4));
    assert_eq!(
        object.get("lifecycle").and_then(Value::as_str),
        Some("user_owned")
    );
    assert_eq!(
        object
            .get("control_ticket")
            .and_then(Value::as_object)
            .and_then(|ticket| ticket.get("opaque"))
            .and_then(Value::as_bool),
        Some(true)
    );
}
