use std::collections::BTreeMap;

use agentyc_core::{
    ActionOperation, ActionReceipt, ActionStatus, CacheState, Capability, CompletionSource,
    ContentHash, DeltaError, DeltaLimits, DeltaOperation, ElementKey, ElementKind, FenceProof,
    FrameId, FrameVersion, Generation, IdempotencyKey, Lease, LeaseEpoch, LeaseState, NextAction,
    PageId, ProfileBindingId, ProfileBindingState, ReconcileToken, RefEpoch, ReleaseProof,
    RequestId, RetentionPolicy, SnapshotBody, SnapshotCoverage, SnapshotDelta, SnapshotDocument,
    SnapshotEnvelope, SnapshotMode, SnapshotVersion, SpaceDescriptor, SpaceId, SpaceLifecycle,
    Timestamp, TopologyVersion, UserIntentContext, UserIntentTicket, UserIntentTicketId,
    UserIntentTicketState,
};

fn element(suffix: &str, order: u32) -> agentyc_core::SnapshotElement {
    agentyc_core::SnapshotElement {
        key: ElementKey::from_suffix(suffix).expect("element key"),
        parent: None,
        kind: ElementKind::Element,
        text: Some(suffix.to_owned()),
        attributes: BTreeMap::new(),
        order,
    }
}

fn full_snapshot(space_id: SpaceId, page_id: PageId) -> SnapshotEnvelope {
    let document =
        SnapshotDocument::new(SnapshotVersion::new(1), vec![element("root", 0)]).expect("document");
    SnapshotEnvelope {
        schema_version: 1,
        space_id,
        page_id,
        snapshot_version: document.snapshot_version,
        snapshot_hash: document.snapshot_hash.clone(),
        base_snapshot_version: None,
        base_hash: None,
        result_hash: document.snapshot_hash,
        delta_sequence: None,
        topology_version: TopologyVersion::new(1),
        navigation_generation: Generation::new(1),
        document_generation: Generation::new(1),
        frame_versions: BTreeMap::new(),
        changed: Vec::new(),
        delta_or_elements: SnapshotBody::Elements {
            elements: document.elements,
        },
        mode: SnapshotMode::Full,
        operation_count: 0,
        coherent: true,
        coverage: SnapshotCoverage::Complete,
        dirty_reasons: Vec::new(),
        cache_state: CacheState::Fresh,
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
        refs_epoch: RefEpoch::new(1),
    }
}

#[test]
fn ordinary_mutations_are_fenced_by_recovery_expiry_and_epoch() {
    let principal = agentyc_core::PrincipalId::from_suffix("agent").expect("principal");
    let space_id = SpaceId::from_suffix("one").expect("space");
    let lease = Lease::active(
        principal.clone(),
        LeaseEpoch::new(4),
        Timestamp::new(10),
        Timestamp::new(8),
    );
    let mut space = SpaceDescriptor {
        space_id,
        label: "fixture".to_owned(),
        lifecycle: SpaceLifecycle::AgentOwned,
        owner: principal.clone(),
        lease: Some(lease),
        profile_binding: ProfileBindingState::Bound,
        pages: Vec::new(),
        visual_group_hint: None,
        capabilities: vec![Capability::Action],
        warnings: Vec::new(),
        retention: RetentionPolicy::default(),
    };

    space
        .validate_mutation_admission(&principal, LeaseEpoch::new(4), Timestamp::new(9))
        .expect("current lease admits mutation");
    assert_eq!(
        space
            .validate_mutation_admission(&principal, LeaseEpoch::new(4), Timestamp::new(10))
            .expect_err("exact expiry fences mutation")
            .code,
        agentyc_core::ErrorCode::LeaseExpired
    );
    assert_eq!(
        space
            .validate_mutation_admission(&principal, LeaseEpoch::new(3), Timestamp::new(9))
            .expect_err("stale epoch fences mutation")
            .code,
        agentyc_core::ErrorCode::StaleLease
    );
    space.lifecycle = SpaceLifecycle::Recovering;
    assert_eq!(
        space
            .validate_mutation_admission(&principal, LeaseEpoch::new(4), Timestamp::new(9))
            .expect_err("recovery does not admit ordinary mutation")
            .code,
        agentyc_core::ErrorCode::UserControlRequired
    );
    assert!(!LeaseState::Expired.admits_mutations());
}

#[test]
fn ticket_identity_expiry_and_single_use_are_enforced() {
    let profile = ProfileBindingId::from_suffix("default").expect("profile");
    let space = SpaceId::from_suffix("one").expect("space");
    let page = PageId::from_suffix("main").expect("page");
    let nonce = agentyc_core::ConnectionNonce::from_suffix("panel").expect("nonce");
    let hash = ContentHash::from_bytes(b"canonical-action");
    let mut ticket = UserIntentTicket {
        ticket_id: UserIntentTicketId::from_suffix("one").expect("ticket"),
        profile_binding_id: profile.clone(),
        space_id: space.clone(),
        page_id: Some(page.clone()),
        document_generation: Some(Generation::new(2)),
        action_hash: hash.clone(),
        lease_epoch: LeaseEpoch::new(4),
        connection_epoch: agentyc_core::ConnectionEpoch::new(3),
        connection_nonce: nonce.clone(),
        expires_at: Timestamp::new(10),
        state: UserIntentTicketState::Issued,
    };
    let context = UserIntentContext {
        profile_binding_id: &profile,
        space_id: &space,
        page_id: Some(&page),
        document_generation: Some(Generation::new(2)),
        action_hash: &hash,
        lease_epoch: LeaseEpoch::new(4),
        connection_epoch: agentyc_core::ConnectionEpoch::new(3),
        connection_nonce: &nonce,
    };

    assert_eq!(
        ticket
            .validate_and_consume(&context, Timestamp::new(10))
            .expect_err("expiry is exclusive")
            .code,
        agentyc_core::ErrorCode::PermissionDenied
    );
    ticket
        .validate_and_consume(&context, Timestamp::new(9))
        .expect("single valid consumption");
    assert_eq!(ticket.state, UserIntentTicketState::Consumed);
    assert_eq!(
        ticket
            .validate_and_consume(&context, Timestamp::new(9))
            .expect_err("replay is rejected")
            .code,
        agentyc_core::ErrorCode::PermissionDenied
    );
}

#[test]
fn fence_return_release_and_cleanup_proofs_form_a_closed_transition() {
    let space = SpaceId::from_suffix("one").expect("space");
    let fence = FenceProof {
        space_id: space.clone(),
        broker_epoch: agentyc_core::BrokerEpoch::new(2),
        released_epoch: Some(LeaseEpoch::new(3)),
        fence_epoch: LeaseEpoch::new(4),
        acknowledged: true,
    };
    fence.validate().expect("acknowledged fence");
    let returned = agentyc_core::ControlReturnProof {
        space_id: space.clone(),
        released_epoch: LeaseEpoch::new(3),
        fence,
        lifecycle: SpaceLifecycle::UserOwned,
    };
    returned.validate().expect("user return");

    let release = ReleaseProof {
        space_id: space.clone(),
        released_epoch: Some(LeaseEpoch::new(4)),
        lifecycle: SpaceLifecycle::Released,
        released_at: Timestamp::new(20),
    };
    release.validate().expect("durable release");
    let cleanup = agentyc_core::CleanupProof {
        space_id: space,
        released_epoch: release.released_epoch,
        page_ids: vec![
            PageId::from_suffix("a").expect("page"),
            PageId::from_suffix("b").expect("page"),
        ],
        completed_at: Timestamp::new(21),
    };
    cleanup.validate().expect("ordered cleanup");
    let mut invalid_cleanup = cleanup.clone();
    invalid_cleanup.page_ids.reverse();
    assert!(invalid_cleanup.validate().is_err());
}

#[test]
fn snapshot_refs_and_action_receipts_fail_closed_across_state_boundaries() {
    let space = SpaceId::from_suffix("one").expect("space");
    let page = PageId::from_suffix("main").expect("page");
    let envelope = full_snapshot(space.clone(), page.clone());
    let reference = envelope
        .make_ref(
            agentyc_core::RefId::from_suffix("one").expect("ref"),
            FrameId::from_suffix("main").expect("frame"),
            agentyc_core::ElementKey::from_suffix("root").expect("element key"),
        )
        .expect("validated snapshot ref");
    reference
        .validate_against(&envelope.provenance())
        .expect("current ref");

    let base =
        SnapshotDocument::new(SnapshotVersion::new(1), vec![element("root", 0)]).expect("base");
    let reset = SnapshotDelta {
        base_snapshot_version: base.snapshot_version,
        base_hash: base.snapshot_hash.clone(),
        result_snapshot_version: SnapshotVersion::new(2),
        result_hash: base.snapshot_hash.clone(),
        delta_sequence: agentyc_core::DeltaSequence::new(1),
        chain_depth: 1,
        operations: vec![DeltaOperation::FrameReset {
            frame_id: FrameId::from_suffix("child").expect("frame"),
            frame_version: FrameVersion::new(2),
        }],
    };
    reset
        .validate(DeltaLimits::default())
        .expect("reset validates");
    assert!(matches!(
        reset.apply(&base, DeltaLimits::default()),
        Err(DeltaError::FrameResetRequiresVersionVector(_))
    ));

    let mut receipt = ActionReceipt::queued(
        agentyc_core::ActionId::from_suffix("one").expect("action"),
        RequestId::from_suffix("one").expect("request"),
        IdempotencyKey::from_suffix("one").expect("idempotency"),
        ContentHash::from_bytes(b"action"),
        space,
        Some(page),
        LeaseEpoch::new(4),
        ActionOperation::Click,
        None,
        Some(Timestamp::new(1)),
    );
    receipt.validate().expect("queued receipt");
    receipt.mark_dispatched().expect("dispatch");
    receipt
        .mark_unknown(
            agentyc_core::UnknownReason::LostResponse,
            ReconcileToken::from_suffix("one").expect("reconcile token"),
        )
        .expect("unknown outcome");
    assert_eq!(receipt.status, ActionStatus::Unknown);
    assert_eq!(receipt.next_action, NextAction::Reconcile);
    receipt.validate().expect("unknown receipt invariant");
    receipt.begin_reconciliation().expect("start reconcile");
    receipt
        .reconcile_succeeded(Some(Timestamp::new(2)))
        .expect("reconcile without replay");
    assert_eq!(receipt.completion_source, CompletionSource::Reconciliation);
    receipt.validate().expect("reconciled receipt invariant");
}
