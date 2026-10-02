use std::collections::BTreeMap;

use agentyc_core::{
    ActionOperation, ActionReceipt, ActionStatus, ArtifactEnvelope, ArtifactKind, BrokerEpoch,
    CompletionSource, ContentHash, DeltaLimits, DeltaOperation, ElementKey, ElementKind, Envelope,
    ErrorCode, EventId, EventKind, EventRecord, EventScope, FrameDecoder, Generation,
    GenerationWatermark, IdempotencyKey, LeaseEpoch, PageBindingState, PageDescriptor, PageId,
    PageLifecycle, PageOwnership, ReconcileToken, RefEpoch, RequestEnvelope, RequestId,
    ResponseEnvelope, SnapshotBody, SnapshotCoverage, SnapshotDecision, SnapshotDocument,
    SnapshotElement, SnapshotEnvelope, SnapshotMode, SnapshotProvenance, SnapshotVersion, SpaceId,
    Timestamp, TopologyVersion, choose_snapshot_decision, encode_frame,
};

fn element(suffix: &str, kind: ElementKind, text: &str, order: u32) -> SnapshotElement {
    SnapshotElement {
        key: ElementKey::from_suffix(suffix).expect("valid element key"),
        parent: None,
        kind,
        text: Some(text.to_owned()),
        attributes: BTreeMap::new(),
        order,
    }
}

#[test]
fn request_envelope_matches_golden_wire_order() {
    let params = BTreeMap::from([
        ("space_id".to_owned(), "space_demo".to_owned()),
        ("mode".to_owned(), "compact".to_owned()),
    ]);
    let envelope = Envelope::Request(RequestEnvelope {
        protocol: 1,
        request_id: RequestId::from_suffix("demo").expect("request"),
        method: "space.describe".to_owned(),
        params,
        deadline_ms: Some(30_000),
        idempotency_key: None,
    });
    let actual = serde_json::to_string(&envelope).expect("serialize envelope");
    let expected = include_str!("fixtures/request-envelope.json").trim();
    assert_eq!(actual, expected);
    assert_eq!(
        serde_json::from_str::<Envelope>(&actual).expect("deserialize envelope"),
        envelope
    );
}

#[test]
fn snapshot_element_matches_golden_json_and_sorted_attributes() {
    let element = SnapshotElement {
        key: ElementKey::from_suffix("root").expect("key"),
        parent: None,
        kind: ElementKind::Control,
        text: Some("Submit".to_owned()),
        attributes: BTreeMap::from([
            ("role".to_owned(), "button".to_owned()),
            ("aria_label".to_owned(), "Submit".to_owned()),
        ]),
        order: 0,
    };
    let actual = serde_json::to_string(&element).expect("serialize element");
    let expected = include_str!("fixtures/snapshot-element.json").trim();
    assert_eq!(actual, expected);
}

#[test]
fn snapshot_delta_hashes_and_order_are_deterministic() {
    let base = SnapshotDocument::new(
        SnapshotVersion::new(1),
        vec![
            element("a", ElementKind::Element, "A", 0),
            element("b", ElementKind::Text, "B", 1),
        ],
    )
    .expect("base");
    let result = SnapshotDocument::new(
        SnapshotVersion::new(2),
        vec![
            element("a", ElementKind::Element, "A", 0),
            element("c", ElementKind::Text, "C", 1),
        ],
    )
    .expect("result");
    let delta = agentyc_core::SnapshotDelta {
        base_snapshot_version: base.snapshot_version,
        base_hash: base.snapshot_hash.clone(),
        result_snapshot_version: result.snapshot_version,
        result_hash: result.snapshot_hash.clone(),
        delta_sequence: agentyc_core::DeltaSequence::new(1),
        chain_depth: 1,
        operations: vec![
            DeltaOperation::Remove {
                key: ElementKey::from_suffix("b").expect("key"),
            },
            DeltaOperation::Upsert {
                key: ElementKey::from_suffix("c").expect("key"),
                element: element("c", ElementKind::Text, "C", 1),
            },
        ],
    };
    delta
        .validate(DeltaLimits::default())
        .expect("canonical delta");
    assert_eq!(
        delta.apply(&base, DeltaLimits::default()).expect("apply"),
        result
    );
    assert_eq!(
        serde_json::to_string(&delta).expect("serialize delta"),
        include_str!("fixtures/snapshot-delta.json").trim()
    );

    let mut out_of_order = delta.clone();
    out_of_order.operations.reverse();
    assert!(matches!(
        out_of_order.validate(DeltaLimits::default()),
        Err(agentyc_core::snapshots::DeltaError::NonCanonicalOrder)
    ));
    assert_eq!(
        choose_snapshot_decision(true, true, true, 10, 100, 1, DeltaLimits::default()),
        SnapshotDecision::Delta
    );
}

#[test]
fn event_response_artifact_and_frame_contracts_round_trip() {
    let request_id = RequestId::from_suffix("one").expect("request");
    let response = ResponseEnvelope::success(
        request_id.clone(),
        BTreeMap::from([("ok".to_owned(), "yes".to_owned())]),
    );
    let encoded = serde_json::to_vec(&response).expect("response");
    assert!(serde_json::from_slice::<ResponseEnvelope>(&encoded).is_ok());

    let event: EventRecord = EventRecord {
        protocol: 1,
        event_id: EventId::from_suffix("one").expect("event"),
        broker_epoch: BrokerEpoch::new(1),
        sequence: agentyc_core::EventSequence::new(1),
        scope: EventScope::space(SpaceId::from_suffix("one").expect("space")),
        event: EventKind::SnapshotChanged,
        generation: GenerationWatermark::default(),
        dirty_reason: None,
        coalesced: false,
        resync_required: false,
        payload: BTreeMap::from([("changed".to_owned(), "true".to_owned())]),
    };
    assert!(
        serde_json::to_string(&event)
            .expect("event")
            .contains("snapshot.changed")
    );

    let artifact = ArtifactEnvelope {
        protocol: 1,
        artifact_id: agentyc_core::ArtifactId::from_suffix("one").expect("artifact"),
        request_id: Some(request_id),
        artifact_kind: ArtifactKind::Text,
        chunk_sequence: 0,
        final_chunk: true,
        bytes: b"hello".to_vec(),
    };
    artifact.validate().expect("bounded artifact");

    let frame = encode_frame(&encoded, 4096).expect("frame");
    let mut decoder = FrameDecoder::new(4096);
    let frames = decoder.feed(&frame).expect("decode");
    assert_eq!(frames, vec![encoded]);
    decoder.finish().expect("clean eof");
}

#[test]
fn no_raw_browser_identity_keys_are_serialized() {
    let page = PageDescriptor {
        page_id: PageId::from_suffix("page").expect("page"),
        space_id: SpaceId::from_suffix("space").expect("space"),
        label: "Main".to_owned(),
        lifecycle: PageLifecycle::Managed,
        ownership: PageOwnership::Agent,
        binding: PageBindingState::Bound,
        url: Some("https://example.test/".to_owned()),
        title: Some("Example".to_owned()),
        target_generation: Generation::new(2),
        navigation_generation: Generation::new(3),
        document_generation: Generation::new(4),
        frame_count: 1,
        retained: true,
    };
    let serialized = serde_json::to_string(&page).expect("serialize page");
    for forbidden in [
        "targetId",
        "sessionId",
        "tabId",
        "target_id",
        "session_id",
        "tab_id",
    ] {
        assert!(
            !serialized.contains(forbidden),
            "leaked browser identity key: {forbidden}"
        );
    }
    assert!(serialized.contains("page_id"));
    assert!(serialized.contains("target_generation"));
}

#[test]
fn receipt_unknown_state_is_not_retryable_until_reconciled() {
    let mut receipt = ActionReceipt::queued(
        agentyc_core::ActionId::from_suffix("one").expect("action"),
        RequestId::from_suffix("one").expect("request"),
        IdempotencyKey::from_suffix("one").expect("idempotency"),
        ContentHash::from_bytes(b"request"),
        SpaceId::from_suffix("one").expect("space"),
        None,
        LeaseEpoch::new(7),
        ActionOperation::Navigate,
        None,
        Some(Timestamp::new(10)),
    );
    receipt.mark_dispatched().expect("dispatch");
    receipt
        .mark_unknown(
            agentyc_core::UnknownReason::TimeoutAfterDispatch,
            ReconcileToken::from_suffix("one").expect("token"),
        )
        .expect("unknown");
    assert_eq!(receipt.status, ActionStatus::Unknown);
    assert!(!receipt.retryable);
    assert_eq!(receipt.error_code, Some(ErrorCode::UnknownOutcome));
    receipt
        .begin_reconciliation()
        .expect("begin reconciliation");
    receipt
        .reconcile_succeeded(Some(Timestamp::new(12)))
        .expect("reconciled");
    assert_eq!(receipt.completion_source, CompletionSource::Reconciliation);
}

#[test]
fn snapshot_provenance_and_envelope_are_distinct_from_transport() {
    let space = SpaceId::from_suffix("space").expect("space");
    let page = PageId::from_suffix("page").expect("page");
    let hash = ContentHash::from_bytes(b"[]");
    let provenance = SnapshotProvenance {
        space_id: space.clone(),
        page_id: page.clone(),
        snapshot_version: SnapshotVersion::new(4),
        snapshot_hash: hash.clone(),
        document_generation: Generation::new(5),
        navigation_generation: Generation::new(6),
        refs_epoch: RefEpoch::new(2),
        coherent: true,
        coverage: SnapshotCoverage::Complete,
    };
    assert!(provenance.can_issue_refs());
    let envelope = SnapshotEnvelope {
        schema_version: 1,
        space_id: space,
        page_id: page,
        snapshot_version: SnapshotVersion::new(4),
        snapshot_hash: hash.clone(),
        base_snapshot_version: None,
        base_hash: None,
        result_hash: hash,
        delta_sequence: None,
        topology_version: TopologyVersion::new(1),
        navigation_generation: Generation::new(6),
        document_generation: Generation::new(5),
        frame_versions: BTreeMap::new(),
        changed: Vec::new(),
        delta_or_elements: SnapshotBody::Elements {
            elements: Vec::new(),
        },
        mode: SnapshotMode::Full,
        operation_count: 0,
        coherent: true,
        coverage: SnapshotCoverage::Complete,
        dirty_reasons: Vec::new(),
        cache_state: agentyc_core::CacheState::Fresh,
        resync_reason: None,
        transport_bytes: 0,
        utf8_bytes: 0,
        serialized_tokens: 0,
        model_context_tokens: 0,
        tokenizer: None,
        budget: None,
        omitted: Vec::new(),
        truncated: false,
        resync_required: false,
        refs_epoch: RefEpoch::new(2),
    };
    envelope.validate().expect("valid full envelope");
    assert!(envelope.can_issue_refs());
}
