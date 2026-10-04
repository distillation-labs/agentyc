use std::{collections::BTreeMap, sync::Arc};

use agentyc_core::{
    ActionId, ActionOperation, ActionRequest, ActionStatus, ClientMetadata, ConnectionNonce,
    ContentHash, ErrorCode, FrameId, FrameVersion, HelloEnvelope, IdempotencyKey, LeaseEpoch,
    PROTOCOL_VERSION, PageId, PrincipalId, ProfileBindingId, RequestId, SpaceId, Timestamp,
};
use agentyc_host::{
    ActionabilityEvidence, AuthorityTicket, Broker, FakeBridge, Ledger, SnapshotRead,
    canonical_action_hash, empty_snapshot,
};
use tempfile::tempdir;

fn authority(broker: &Broker, suffix: &str) -> AuthorityTicket {
    broker
        .hello(&HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix(suffix).expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("phase5-broker-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(
                    ConnectionNonce::from_suffix(format!("nonce-{suffix}")).expect("nonce"),
                ),
                profile_binding_id: Some(
                    ProfileBindingId::from_suffix("phase5-profile").expect("profile"),
                ),
            }),
        })
        .expect("hello")
        .authority()
        .clone()
}

fn managed_page(
    broker: &Broker,
    authority: &AuthorityTicket,
    space_id: &SpaceId,
    lease_epoch: LeaseEpoch,
) -> PageId {
    let page = broker
        .create_page_at(space_id, authority, lease_epoch, "main", Timestamp::new(1))
        .expect("create page");
    broker
        .bind_page(
            space_id,
            &page.page_id,
            authority,
            lease_epoch,
            Timestamp::new(1),
            Some("https://example.test/".to_owned()),
            Some("Example".to_owned()),
            1,
        )
        .expect("bind page");
    page.page_id
}

fn action_request(
    suffix: &str,
    space_id: SpaceId,
    page_id: PageId,
    lease_epoch: LeaseEpoch,
    payload: BTreeMap<String, String>,
) -> ActionRequest<BTreeMap<String, String>> {
    let mut request = ActionRequest {
        request_id: RequestId::from_suffix(format!("request-{suffix}")).expect("request"),
        action_id: ActionId::from_suffix(format!("action-{suffix}")).expect("action"),
        idempotency_key: IdempotencyKey::from_suffix(format!("key-{suffix}")).expect("key"),
        request_hash: ContentHash::from_bytes(b"placeholder"),
        space_id,
        page_id: Some(page_id),
        lease_epoch,
        operation: ActionOperation::Click,
        payload,
        postcondition: None,
    };
    request.request_hash = canonical_action_hash(&request).expect("request hash");
    request
}

#[test]
fn production_action_path_rejects_missing_element_proof_before_bridge_dispatch() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = Broker::with_shared_bridge(
        Ledger::open(directory.path()).expect("ledger"),
        bridge.clone(),
    );
    let authority = authority(&broker, "missing-proof");
    let space = broker.create_space(&authority, "space").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &authority, Timestamp::new(0), 100)
        .expect("lease");
    let page_id = managed_page(
        &broker,
        &authority,
        &space.space_id,
        lease.lease.lease_epoch,
    );

    let mut request = action_request(
        "missing-proof",
        space.space_id,
        page_id,
        lease.lease.lease_epoch,
        BTreeMap::new(),
    );
    request.operation = ActionOperation::Click;
    request.request_hash = canonical_action_hash(&request).expect("request hash");
    let error = broker
        .execute_action(request, &authority, Timestamp::new(2))
        .expect_err("missing proof must fail closed");
    assert!(matches!(
        error,
        agentyc_host::HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::InvalidArgument,
            ..
        })
    ));
    assert_eq!(bridge.dispatch_count(), 0);
}

#[test]
fn production_ref_resolution_is_required_and_mutations_invalidate_the_ref() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = Broker::with_shared_bridge(
        Ledger::open(directory.path()).expect("ledger"),
        bridge.clone(),
    );
    let authority = authority(&broker, "ref-path");
    let space = broker.create_space(&authority, "space").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &authority, Timestamp::new(0), 100)
        .expect("lease");
    let page_id = managed_page(
        &broker,
        &authority,
        &space.space_id,
        lease.lease.lease_epoch,
    );
    let frame_id = FrameId::from_suffix("main").expect("frame");
    let mut snapshot = empty_snapshot(space.space_id.clone(), page_id.clone());
    snapshot
        .frame_versions
        .insert(frame_id.clone(), FrameVersion::new(1));
    broker
        .put_snapshot(
            &authority,
            snapshot.clone(),
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("snapshot");
    let element_ref = broker
        .issue_ref(
            &space.space_id,
            &page_id,
            frame_id.clone(),
            &authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("ref");
    let page = broker
        .describe_space(&authority, &space.space_id)
        .expect("space")
        .page(&page_id)
        .expect("page")
        .clone();
    let evidence = ActionabilityEvidence::proven_interactive().with_generations(
        Some(page.target_generation),
        page.navigation_generation,
        page.document_generation,
        Some(snapshot.snapshot_hash.clone()),
    );
    let payload = BTreeMap::from([
        (
            "element_ref".to_owned(),
            serde_json::to_string(&element_ref).expect("ref json"),
        ),
        (
            "provenance".to_owned(),
            serde_json::to_string(&snapshot.provenance()).expect("provenance json"),
        ),
        (
            "actionability_evidence".to_owned(),
            serde_json::to_string(&evidence).expect("evidence json"),
        ),
        ("frame_scope".to_owned(), frame_id.to_string()),
    ]);
    let request = action_request(
        "valid-ref",
        space.space_id.clone(),
        page_id.clone(),
        lease.lease.lease_epoch,
        payload.clone(),
    );
    let result = broker
        .execute_action(request, &authority, Timestamp::new(3))
        .expect("proven action");
    assert_eq!(result.receipt.status, ActionStatus::Succeeded);
    assert_eq!(bridge.dispatch_count(), 1);

    let stale = action_request(
        "stale-ref",
        space.space_id,
        page_id,
        lease.lease.lease_epoch,
        payload,
    );
    let error = broker
        .execute_action(stale, &authority, Timestamp::new(4))
        .expect_err("mutation must invalidate the old ref");
    assert!(matches!(
        error,
        agentyc_host::HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::StaleRef | ErrorCode::TargetReplaced,
            ..
        })
    ));
    assert_eq!(bridge.dispatch_count(), 1);
}

#[test]
fn clean_snapshot_hit_does_not_observe_live_inventory() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = Broker::with_shared_bridge(
        Ledger::open(directory.path()).expect("ledger"),
        bridge.clone(),
    );
    let authority = authority(&broker, "clean-path");
    let space = broker.create_space(&authority, "space").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &authority, Timestamp::new(0), 100)
        .expect("lease");
    let page_id = managed_page(
        &broker,
        &authority,
        &space.space_id,
        lease.lease.lease_epoch,
    );
    let first = broker
        .read_snapshot(
            &space.space_id,
            &page_id,
            &authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("first snapshot");
    assert!(first.scan_performed);
    let second: SnapshotRead = broker
        .read_snapshot(
            &space.space_id,
            &page_id,
            &authority,
            lease.lease.lease_epoch,
            Timestamp::new(3),
        )
        .expect("clean snapshot");
    assert!(!second.scan_performed);
    assert_eq!(bridge.observe_count(), 1);
}
