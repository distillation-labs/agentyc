use agentyc_core::{
    ActionId, ActionOperation, ActionReceipt, ArtifactEnvelope, ArtifactId, ArtifactKind, ClientId,
    ClientMetadata, ConnectionEpoch, ConnectionNonce, ContentHash, DeltaLimits, DeltaOperation,
    ElementKey, ElementKind, ErrorCode, FrameDecoder, FrameError, HelloEnvelope, HelloOkEnvelope,
    HostMetadata, IdempotencyKey, LeaseEpoch, MAX_ARTIFACT_CHUNK_BYTES,
    MAX_CONTROL_FRAME_PAYLOAD_BYTES, PROTOCOL_VERSION, PageId, ProfileBindingId, ReconcileToken,
    RequestId, ResumeResult, SnapshotDelta, SnapshotDocument, SnapshotElement, SnapshotVersion,
    SpaceId, Timestamp,
};
use std::collections::BTreeMap;

#[test]
fn raw_identity_values_fail_validated_logical_id_deserialization() {
    assert!(SpaceId::new("target_123").is_err());
    assert!(PageId::new("page_").is_err());
    assert!(serde_json::from_str::<SpaceId>(r#""target_123""#).is_err());
    assert!(serde_json::from_str::<PageId>(r#""page_with.UPPER""#).is_err());
}

#[test]
fn framing_rejects_bounds_truncation_and_trailing_bytes() {
    assert!(matches!(
        agentyc_core::encode_frame(b"1234", 3),
        Err(FrameError::MessageTooLarge { length: 4, max: 3 })
    ));
    assert!(matches!(
        agentyc_core::decode_frame(&[0, 0, 0], 3),
        Err(FrameError::Truncated { .. })
    ));
    assert!(matches!(
        agentyc_core::decode_frame(&[0, 0, 0, 1], 3),
        Err(FrameError::Truncated { .. })
    ));
    let mut complete = agentyc_core::encode_frame(b"a", 3).expect("frame");
    complete.push(0);
    assert_eq!(
        agentyc_core::decode_frame(&complete, 3),
        Err(FrameError::TrailingBytes)
    );

    let mut decoder = FrameDecoder::new(3);
    decoder.feed(&[0, 0]).expect("fragment");
    assert!(matches!(
        decoder.finish(),
        Err(FrameError::Truncated { .. })
    ));
}

#[test]
fn normative_frame_and_artifact_bounds_reject_one_byte_over() {
    assert!(
        agentyc_core::encode_frame(
            &vec![0_u8; MAX_CONTROL_FRAME_PAYLOAD_BYTES + 1],
            MAX_CONTROL_FRAME_PAYLOAD_BYTES,
        )
        .is_err()
    );

    let over_limit = (MAX_CONTROL_FRAME_PAYLOAD_BYTES + 1) as u32;
    assert!(matches!(
        agentyc_core::decode_frame(&over_limit.to_be_bytes(), MAX_CONTROL_FRAME_PAYLOAD_BYTES),
        Err(FrameError::MessageTooLarge { length, max })
            if length == MAX_CONTROL_FRAME_PAYLOAD_BYTES + 1
                && max == MAX_CONTROL_FRAME_PAYLOAD_BYTES
    ));

    let artifact = ArtifactEnvelope {
        protocol: PROTOCOL_VERSION,
        artifact_id: ArtifactId::from_suffix("over").expect("artifact"),
        request_id: None,
        artifact_kind: ArtifactKind::Binary,
        chunk_sequence: 0,
        final_chunk: true,
        bytes: vec![0_u8; MAX_ARTIFACT_CHUNK_BYTES + 1],
    };
    assert_eq!(
        artifact.validate().expect_err("oversized artifact").code,
        ErrorCode::MessageTooLarge
    );
}

#[test]
fn invalid_utf8_and_protocol_mismatch_are_stable_errors() {
    let utf8 = agentyc_core::decode_utf8(&[0xff]).expect_err("invalid utf8");
    assert_eq!(utf8.code, ErrorCode::InvalidUtf8);
    let mismatch = agentyc_core::negotiate_version(&[2], &[1]).expect_err("mismatch");
    assert_eq!(mismatch.code, ErrorCode::ProtocolMismatch);
}

#[test]
fn handshake_requires_metadata_and_matches_nonce_and_profile() {
    let weak = HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: agentyc_core::PrincipalId::from_suffix("agent").expect("principal"),
        resume_from: None,
        client_metadata: None,
    };
    assert_eq!(
        weak.validate_handshake()
            .expect_err("missing client metadata")
            .code,
        ErrorCode::InvalidArgument
    );

    let nonce = ConnectionNonce::from_suffix("one").expect("nonce");
    let profile = ProfileBindingId::from_suffix("default").expect("profile");
    let hello = HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: agentyc_core::PrincipalId::from_suffix("agent").expect("principal"),
        resume_from: None,
        client_metadata: Some(ClientMetadata {
            client_id: Some(ClientId::from_suffix("one").expect("client")),
            client_name: Some("client".to_owned()),
            client_version: Some("1".to_owned()),
            connection_nonce: Some(nonce.clone()),
            profile_binding_id: Some(profile.clone()),
        }),
    };
    let mut hello_ok = HelloOkEnvelope {
        protocol: PROTOCOL_VERSION,
        broker_epoch: agentyc_core::BrokerEpoch::new(1),
        connection_epoch: ConnectionEpoch::new(1),
        capabilities: vec![agentyc_core::Capability::Snapshot],
        resume: ResumeResult::Accepted,
        host_metadata: Some(HostMetadata {
            host_name: Some("host".to_owned()),
            host_version: Some("1".to_owned()),
            connection_nonce: Some(nonce),
            profile_binding_id: Some(profile),
        }),
    };
    hello_ok
        .validate_against(&hello)
        .expect("matching handshake");
    hello_ok
        .host_metadata
        .as_mut()
        .expect("host metadata")
        .connection_nonce = Some(ConnectionNonce::from_suffix("two").expect("nonce"));
    assert_eq!(
        hello_ok
            .validate_against(&hello)
            .expect_err("mismatched nonce")
            .code,
        ErrorCode::InvalidArgument
    );
}

#[test]
fn stale_lease_and_ref_are_rejected_before_side_effects() {
    let mut receipt = ActionReceipt::queued(
        ActionId::from_suffix("action").expect("action"),
        RequestId::from_suffix("request").expect("request"),
        IdempotencyKey::from_suffix("key").expect("key"),
        ContentHash::from_bytes(b"request"),
        SpaceId::from_suffix("space").expect("space"),
        Some(PageId::from_suffix("page").expect("page")),
        LeaseEpoch::new(1),
        ActionOperation::Click,
        None,
        Some(Timestamp::new(1)),
    );
    assert_eq!(
        receipt
            .validate_lease(LeaseEpoch::new(2))
            .expect_err("stale")
            .code,
        ErrorCode::StaleLease
    );
    assert!(
        receipt
            .mark_unknown(
                agentyc_core::UnknownReason::LostResponse,
                ReconcileToken::from_suffix("token").expect("token"),
            )
            .is_err()
    );

    let provenance = agentyc_core::SnapshotProvenance {
        space_id: SpaceId::from_suffix("space").expect("space"),
        page_id: PageId::from_suffix("page").expect("page"),
        snapshot_version: SnapshotVersion::new(2),
        snapshot_hash: ContentHash::from_bytes(b"snapshot"),
        document_generation: agentyc_core::Generation::new(3),
        navigation_generation: agentyc_core::Generation::new(4),
        refs_epoch: agentyc_core::RefEpoch::new(2),
        coherent: true,
        coverage: agentyc_core::SnapshotCoverage::Complete,
    };
    let stale = agentyc_core::ElementRef {
        ref_id: agentyc_core::RefId::from_suffix("ref").expect("ref"),
        element_key: agentyc_core::ElementKey::from_suffix("root").expect("element key"),
        space_id: provenance.space_id.clone(),
        page_id: provenance.page_id.clone(),
        frame_id: agentyc_core::FrameId::from_suffix("main").expect("frame"),
        snapshot_version: SnapshotVersion::new(1),
        document_generation: agentyc_core::Generation::new(3),
        navigation_generation: agentyc_core::Generation::new(4),
        refs_epoch: agentyc_core::RefEpoch::new(1),
    };
    assert_eq!(
        stale
            .validate_against(&provenance)
            .expect_err("stale ref")
            .code,
        ErrorCode::StaleRef
    );
}

#[test]
fn delta_rejects_duplicate_targets_unordered_operations_and_bad_hashes() {
    let key_a = ElementKey::from_suffix("a").expect("key");
    let key_b = ElementKey::from_suffix("b").expect("key");
    let make_element = |key: ElementKey, order| SnapshotElement {
        key,
        parent: None,
        kind: ElementKind::Element,
        text: None,
        attributes: BTreeMap::new(),
        order,
    };
    let base = SnapshotDocument::new(
        SnapshotVersion::new(1),
        vec![make_element(key_a.clone(), 0)],
    )
    .expect("base");
    let delta = SnapshotDelta {
        base_snapshot_version: base.snapshot_version,
        base_hash: base.snapshot_hash.clone(),
        result_snapshot_version: SnapshotVersion::new(2),
        result_hash: ContentHash::from_bytes(b"wrong"),
        delta_sequence: agentyc_core::DeltaSequence::new(1),
        chain_depth: 1,
        operations: vec![
            DeltaOperation::Upsert {
                key: key_b.clone(),
                element: make_element(key_b.clone(), 1),
            },
            DeltaOperation::Remove { key: key_a.clone() },
            DeltaOperation::Remove { key: key_a },
        ],
    };
    assert!(matches!(
        delta.validate(DeltaLimits::default()),
        Err(agentyc_core::snapshots::DeltaError::NonCanonicalOrder)
            | Err(agentyc_core::snapshots::DeltaError::DuplicateTarget(_))
    ));
}
