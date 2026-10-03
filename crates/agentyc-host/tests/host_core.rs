use std::{
    collections::BTreeMap,
    fs,
    sync::{
        Arc, Mutex,
        atomic::{AtomicU64, Ordering},
    },
};

use agentyc_core::{
    ActionId, ActionOperation, ActionRequest, ActionStatus, BrokerEpoch, Capability, ClientId,
    ClientMetadata, ConnectionNonce, ContentHash, CoreError, ErrorCode, EventKind, EventScope,
    FrameId, FrameVersion, HelloEnvelope, IdempotencyKey, LeaseEpoch, MAX_ARTIFACT_CHUNK_BYTES,
    MAX_CONTROL_FRAME_PAYLOAD_BYTES, PROTOCOL_VERSION, PageId, PrincipalId, ProfileBindingId,
    RequestId, ResumeResult, RetentionPolicy, SnapshotEnvelope, SpaceId, Timestamp, UnknownReason,
};
use agentyc_host::{
    AuthorityTicket, Bridge, BridgeDispatchResult, BridgeReconcileResult, Broker, EventQuery,
    FakeBridge, HostError, Ledger, LedgerError, LedgerLimits, NullBridge, ProtocolClient,
    ProtocolServer, RefRegistry, SnapshotMetadataRead, SnapshotRead, canonical_action_hash,
    empty_snapshot,
};
use tempfile::tempdir;

static AUTHORITY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

fn principal(suffix: &str) -> PrincipalId {
    PrincipalId::from_suffix(suffix).expect("valid principal")
}

fn make_broker(path: &std::path::Path, bridge: Arc<FakeBridge>) -> Broker {
    Broker::with_shared_bridge(Ledger::open(path).expect("ledger"), bridge)
}

fn authority(broker: &Broker, suffix: &str) -> AuthorityTicket {
    authority_with_profile(broker, suffix, Some("host-test"))
}

fn authority_with_profile(
    broker: &Broker,
    suffix: &str,
    profile_suffix: Option<&str>,
) -> AuthorityTicket {
    let nonce_suffix = format!(
        "{suffix}-{}",
        AUTHORITY_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    );
    let hello = HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: principal(suffix),
        resume_from: None,
        client_metadata: Some(ClientMetadata {
            client_id: Some(
                ClientId::from_suffix(format!("client-{nonce_suffix}")).expect("client"),
            ),
            client_name: Some("host-integration-test".to_owned()),
            client_version: Some("2".to_owned()),
            connection_nonce: Some(
                ConnectionNonce::from_suffix(format!("nonce-{nonce_suffix}")).expect("nonce"),
            ),
            profile_binding_id: profile_suffix
                .map(|value| ProfileBindingId::from_suffix(value).expect("profile")),
        }),
    };
    broker.hello(&hello).expect("hello").authority().clone()
}

fn action_request(
    suffix: &str,
    space_id: SpaceId,
    page_id: Option<PageId>,
    lease_epoch: LeaseEpoch,
    operation: ActionOperation,
) -> ActionRequest<BTreeMap<String, String>> {
    let mut request = ActionRequest {
        request_id: RequestId::from_suffix(format!("request-{suffix}")).expect("request"),
        action_id: ActionId::from_suffix(format!("action-{suffix}")).expect("action"),
        idempotency_key: IdempotencyKey::from_suffix(format!("key-{suffix}")).expect("key"),
        request_hash: ContentHash::from_bytes(b"placeholder"),
        space_id,
        page_id,
        lease_epoch,
        operation,
        payload: BTreeMap::new(),
        postcondition: None,
    };
    request.request_hash = canonical_action_hash(&request).expect("canonical request hash");
    request
}

fn action_request_with_payload(
    suffix: &str,
    space_id: SpaceId,
    page_id: Option<PageId>,
    lease_epoch: LeaseEpoch,
    operation: ActionOperation,
    payload: BTreeMap<String, String>,
) -> ActionRequest<BTreeMap<String, String>> {
    let mut request = action_request(suffix, space_id, page_id, lease_epoch, operation);
    request.payload = payload;
    request.request_hash = canonical_action_hash(&request).expect("canonical request hash");
    request
}

fn managed_page(
    broker: &Broker,
    space_id: &SpaceId,
    authority: &AuthorityTicket,
    lease_epoch: LeaseEpoch,
) -> PageId {
    let page = broker
        .create_page_at(space_id, authority, lease_epoch, "main", Timestamp::new(1))
        .expect("planned page");
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
        .expect("managed page");
    page.page_id
}

type DispatchHook = Box<dyn Fn(&ActionRequest<BTreeMap<String, String>>) + Send + Sync>;
type FenceHook = Box<dyn Fn(&SpaceId, LeaseEpoch) -> Result<(), CoreError> + Send + Sync>;

struct ReentrantBridge {
    delegate: Arc<FakeBridge>,
    dispatch_hook: Mutex<Option<DispatchHook>>,
    fence_hook: Mutex<Option<FenceHook>>,
}

impl ReentrantBridge {
    fn new(delegate: Arc<FakeBridge>) -> Self {
        Self {
            delegate,
            dispatch_hook: Mutex::new(None),
            fence_hook: Mutex::new(None),
        }
    }

    fn on_dispatch<F>(&self, hook: F)
    where
        F: Fn(&ActionRequest<BTreeMap<String, String>>) + Send + Sync + 'static,
    {
        *self.dispatch_hook.lock().expect("dispatch hook") = Some(Box::new(hook));
    }

    fn on_fence<F>(&self, hook: F)
    where
        F: Fn(&SpaceId, LeaseEpoch) -> Result<(), CoreError> + Send + Sync + 'static,
    {
        *self.fence_hook.lock().expect("fence hook") = Some(Box::new(hook));
    }
}

impl Bridge for ReentrantBridge {
    fn capabilities(&self) -> Vec<Capability> {
        self.delegate.capabilities()
    }

    fn dispatch(
        &self,
        request: &ActionRequest<BTreeMap<String, String>>,
    ) -> Result<BridgeDispatchResult, CoreError> {
        if let Some(hook) = self
            .dispatch_hook
            .lock()
            .map_err(|_| CoreError::new(ErrorCode::ExtensionNotConnected, "hook poisoned"))?
            .take()
        {
            hook(request);
        }
        self.delegate.dispatch(request)
    }

    fn reconcile(
        &self,
        receipt: &agentyc_core::ActionReceipt,
    ) -> Result<BridgeReconcileResult, CoreError> {
        self.delegate.reconcile(receipt)
    }

    fn snapshot(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
    ) -> Result<SnapshotEnvelope, CoreError> {
        self.delegate.snapshot(space_id, page_id)
    }

    fn fence(
        &self,
        space_id: &SpaceId,
        old_epoch: Option<LeaseEpoch>,
        new_epoch: LeaseEpoch,
        broker_epoch: BrokerEpoch,
    ) -> Result<agentyc_host::FenceResult, CoreError> {
        let hook = self
            .fence_hook
            .lock()
            .map_err(|_| CoreError::new(ErrorCode::ExtensionNotConnected, "hook poisoned"))?
            .take();
        if let Some(hook) = hook {
            hook(space_id, new_epoch)?;
        }
        self.delegate
            .fence(space_id, old_epoch, new_epoch, broker_epoch)
    }

    fn close_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
    ) -> Result<(), CoreError> {
        self.delegate.close_page(space_id, page_id, lease_epoch)
    }
}

#[test]
fn two_spaces_stay_isolated_and_stale_epochs_fail_before_dispatch() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let one_authority = authority(&broker, "one");
    let one = broker
        .create_space(&one_authority, "one")
        .expect("space one");
    let lease_one = broker
        .acquire_lease(&one.space_id, &one_authority, Timestamp::new(0), 100)
        .expect("lease one");
    let page_one = managed_page(
        &broker,
        &one.space_id,
        &one_authority,
        lease_one.lease.lease_epoch,
    );

    let two_authority = authority(&broker, "two");
    let two = broker
        .create_space(&two_authority, "two")
        .expect("space two");
    let lease_two = broker
        .acquire_lease(&two.space_id, &two_authority, Timestamp::new(0), 100)
        .expect("lease two");

    let wrong_space = action_request(
        "wrong-space",
        one.space_id.clone(),
        Some(page_one.clone()),
        lease_two.lease.lease_epoch,
        ActionOperation::Click,
    );
    let error = broker
        .execute_action(wrong_space, &two_authority, Timestamp::new(2))
        .expect_err("cross-space action");
    assert!(matches!(
        error,
        HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::SpaceForbidden,
            ..
        })
    ));
    let one_authority = authority(&broker, "one");
    let stale = action_request(
        "stale",
        one.space_id.clone(),
        Some(page_one),
        LeaseEpoch::new(0),
        ActionOperation::Click,
    );
    let error = broker
        .execute_action(stale, &one_authority, Timestamp::new(2))
        .expect_err("stale epoch");
    assert!(matches!(
        error,
        HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::StaleLease,
            ..
        })
    ));
    assert_eq!(bridge.dispatch_count(), 0);
    assert_eq!(broker.list_spaces(&one_authority).expect("list").len(), 1);
}

#[test]
fn takeover_monotonically_fences_old_work_and_waits_for_ack() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    bridge.set_fence_acknowledged(false);
    let broker = make_broker(directory.path(), bridge.clone());
    let one_authority = authority(&broker, "one");
    let space = broker.create_space(&one_authority, "one").expect("space");
    let first = broker
        .acquire_lease(&space.space_id, &one_authority, Timestamp::new(0), 100)
        .expect("first lease");
    let pending = broker
        .takeover(&space.space_id, &one_authority, Timestamp::new(1), 100)
        .expect("pending takeover");
    assert_eq!(pending.lease_epoch.get(), first.lease.lease_epoch.get() + 1);
    assert_eq!(
        pending.lifecycle,
        agentyc_core::SpaceLifecycle::FencePending
    );
    assert!(
        broker
            .renew_lease(
                &space.space_id,
                &one_authority,
                first.lease.lease_epoch,
                Timestamp::new(2),
                100,
            )
            .is_err()
    );
    assert!(
        broker
            .create_page_at(
                &space.space_id,
                &one_authority,
                pending.lease_epoch,
                "blocked",
                Timestamp::new(2),
            )
            .is_err()
    );

    bridge.set_fence_acknowledged(true);
    let ready = broker
        .acknowledge_fence(&space.space_id, &one_authority, pending.lease_epoch)
        .expect("acknowledge");
    assert_eq!(ready.lifecycle, agentyc_core::SpaceLifecycle::AgentOwned);
    broker
        .create_page_at(
            &space.space_id,
            &one_authority,
            pending.lease_epoch,
            "allowed",
            Timestamp::new(2),
        )
        .expect("new owner admitted");
}

#[test]
fn persistence_recovery_increments_broker_epoch_and_retains_logical_records() {
    let directory = tempdir().expect("tempdir");
    let old_lease;
    let old_broker_epoch;
    {
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("first broker");
        let owner_authority = authority(&broker, "owner");
        old_broker_epoch = broker.broker_epoch().expect("epoch");
        let space = broker
            .create_space(&owner_authority, "retained")
            .expect("space");
        old_lease = broker
            .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
            .expect("lease");
        let _page = managed_page(
            &broker,
            &space.space_id,
            &owner_authority,
            old_lease.lease.lease_epoch,
        );
    }
    let bridge = Arc::new(FakeBridge::new());
    let recovered = make_broker(directory.path(), bridge);
    let owner_authority = authority(&recovered, "owner");
    assert_eq!(
        recovered.broker_epoch().expect("epoch").get(),
        old_broker_epoch.get() + 1
    );
    let spaces = recovered.list_spaces(&owner_authority).expect("spaces");
    assert_eq!(spaces.len(), 1);
    assert_eq!(spaces[0].lifecycle, agentyc_core::SpaceLifecycle::Orphaned);
    assert_eq!(
        spaces[0].lease.as_ref().expect("fenced lease").state,
        agentyc_core::states::LeaseState::Fenced
    );
    assert_eq!(spaces[0].pages.len(), 1);
    let json = recovered.ledger_json(&owner_authority).expect("json");
    let text = String::from_utf8(json).expect("utf8");
    for forbidden in [
        "targetId",
        "target_id",
        "sessionId",
        "session_id",
        "tabId",
        "tab_id",
    ] {
        assert!(
            !text.contains(forbidden),
            "public record leaked {forbidden}"
        );
    }
}

#[test]
fn unknown_outcomes_require_reconciliation_and_never_blind_replay() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    bridge.push_dispatch_result(BridgeDispatchResult::Unknown {
        reason: UnknownReason::LostResponse,
    });
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "actions")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let page = managed_page(
        &broker,
        &space.space_id,
        &owner_authority,
        lease.lease.lease_epoch,
    );
    let request = action_request(
        "unknown",
        space.space_id.clone(),
        Some(page),
        lease.lease.lease_epoch,
        ActionOperation::Click,
    );
    let unknown = broker
        .execute_action(request, &owner_authority, Timestamp::new(2))
        .expect("unknown receipt")
        .receipt;
    assert_eq!(unknown.status, ActionStatus::Unknown);
    assert!(!unknown.retryable);
    assert_eq!(bridge.dispatch_count(), 1);
    assert!(
        broker
            .dispatch_action(
                &unknown.action_id,
                &owner_authority,
                lease.lease.lease_epoch,
                Timestamp::new(3),
            )
            .is_err()
    );

    bridge.push_reconcile_result(BridgeReconcileResult::Succeeded);
    let reconciled = broker
        .reconcile_action(
            &unknown.action_id,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(4),
        )
        .expect("reconciled")
        .receipt;
    assert_eq!(reconciled.status, ActionStatus::Succeeded);
    assert_eq!(bridge.dispatch_count(), 1);
    assert_eq!(bridge.reconcile_count(), 1);
}

#[test]
fn event_resume_filters_scope_and_resyncs_lagged_or_old_epochs() {
    let directory = tempdir().expect("tempdir");
    let limits = LedgerLimits {
        max_events: 2,
        ..LedgerLimits::default()
    };
    let bridge = Arc::new(FakeBridge::new());
    let broker = Broker::with_shared_bridge(
        Ledger::open_with_limits(directory.path(), limits).expect("ledger"),
        bridge,
    );
    let owner_authority = authority(&broker, "owner");
    let space_one = broker.create_space(&owner_authority, "one").expect("one");
    let space_two = broker.create_space(&owner_authority, "two").expect("two");
    let cursor = broker.event_cursor(&owner_authority).expect("cursor");
    broker
        .publish_event(
            &owner_authority,
            EventScope::space(space_one.space_id.clone()),
            EventKind::SpaceChanged,
            BTreeMap::new(),
        )
        .expect("event one");
    broker
        .publish_event(
            &owner_authority,
            EventScope::space(space_two.space_id.clone()),
            EventKind::SpaceChanged,
            BTreeMap::new(),
        )
        .expect("event two");
    broker
        .publish_event(
            &owner_authority,
            EventScope::space(space_one.space_id.clone()),
            EventKind::LeaseChanged,
            BTreeMap::new(),
        )
        .expect("event three");
    let lagged = broker
        .resume_events(&owner_authority, EventQuery::all(cursor))
        .expect("resume result");
    assert_eq!(lagged.result, ResumeResult::ResyncRequired);

    let current = broker
        .event_cursor(&owner_authority)
        .expect("current cursor");
    broker
        .publish_event(
            &owner_authority,
            EventScope::space(space_one.space_id.clone()),
            EventKind::PageChanged,
            BTreeMap::new(),
        )
        .expect("scoped one");
    broker
        .publish_event(
            &owner_authority,
            EventScope::space(space_two.space_id.clone()),
            EventKind::PageChanged,
            BTreeMap::new(),
        )
        .expect("scoped two");
    let scoped = broker
        .resume_events(
            &owner_authority,
            EventQuery::scoped(current, EventScope::space(space_one.space_id)),
        )
        .expect("scoped resume");
    assert_eq!(scoped.result, ResumeResult::Accepted);
    assert_eq!(scoped.events.len(), 1);
    assert_eq!(scoped.events[0].event, EventKind::PageChanged);

    let old_epoch = agentyc_core::EventCursor {
        broker_epoch: BrokerEpoch::new(0),
        sequence: current.sequence,
    };
    assert_eq!(
        broker
            .resume_events(&owner_authority, EventQuery::all(old_epoch))
            .expect("old epoch")
            .result,
        ResumeResult::ResyncRequired
    );
}

#[test]
fn empty_event_history_reports_lag_after_retention_drops_every_event() {
    let directory = tempdir().expect("tempdir");
    let limits = LedgerLimits {
        max_events: 0,
        ..LedgerLimits::default()
    };
    let broker =
        Broker::open_with_limits(directory.path(), limits, FakeBridge::new()).expect("broker");
    let owner_authority = authority(&broker, "owner");
    broker
        .create_space(&owner_authority, "empty-history")
        .expect("space");
    let result = broker
        .resume_events(
            &owner_authority,
            EventQuery::all(agentyc_core::EventCursor {
                broker_epoch: broker.broker_epoch().expect("epoch"),
                sequence: agentyc_core::EventSequence::new(0),
            }),
        )
        .expect("resume");
    assert_eq!(result.result, ResumeResult::ResyncRequired);
}

#[test]
fn clean_snapshot_cache_reads_have_zero_bridge_scans() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "snapshots")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let page = managed_page(
        &broker,
        &space.space_id,
        &owner_authority,
        lease.lease.lease_epoch,
    );

    let first = broker
        .read_snapshot(
            &space.space_id,
            &page,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("first scan");
    assert!(first.scan_performed);
    assert_eq!(first.cache_state, agentyc_core::CacheState::Fresh);
    assert_eq!(bridge.snapshot_scan_count(), 1);
    let second = broker
        .read_snapshot(
            &space.space_id,
            &page,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("clean cache");
    assert!(!second.scan_performed);
    assert_eq!(second.cache_state, agentyc_core::CacheState::Cached);
    assert_eq!(bridge.snapshot_scan_count(), 1);
    broker
        .mark_snapshot_dirty(
            &owner_authority,
            &space.space_id,
            &page,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("dirty");
    let third = broker
        .read_snapshot(
            &space.space_id,
            &page,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("refresh");
    assert!(third.scan_performed);
    assert_eq!(bridge.snapshot_scan_count(), 2);
    let _: SnapshotRead = third;
}

#[test]
fn corrupt_ledger_fails_closed_and_shutdown_does_not_global_close() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    {
        let broker = make_broker(directory.path(), bridge.clone());
        broker.shutdown(Timestamp::new(10)).expect("shutdown");
        assert_eq!(bridge.close_count(), 0);
    }
    let ledger_path = directory.path().join("ledger.json");
    fs::write(&ledger_path, b"{ definitely not json").expect("corrupt");
    let error = Broker::open(directory.path(), NullBridge).expect_err("must fail closed");
    assert!(matches!(error, HostError::Ledger(LedgerError::Corrupt(_))));
    assert_eq!(
        fs::read(&ledger_path).expect("read"),
        b"{ definitely not json"
    );
    assert!(
        fs::read_dir(directory.path())
            .expect("directory")
            .flatten()
            .any(|entry| entry.file_name().to_string_lossy().contains("quarantine"))
    );
}

#[test]
fn user_return_requires_current_authority_and_ticketed_reconciliation() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge);
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "handoff")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let returned = broker
        .return_control(
            &space.space_id,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(1),
        )
        .expect("return control");
    assert_eq!(returned.lifecycle, agentyc_core::SpaceLifecycle::UserOwned);
    let claimant_authority = authority(&broker, "claimant");
    assert!(matches!(
        broker.acquire_lease(&space.space_id, &claimant_authority, Timestamp::new(2), 100),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::UserControlRequired,
            ..
        }))
    ));
    assert!(
        broker
            .takeover(&space.space_id, &claimant_authority, Timestamp::new(2), 100)
            .is_err()
    );
    let reclaimed = broker
        .takeover_with_control_ticket(
            &space.space_id,
            &claimant_authority,
            &returned.control_ticket,
            Timestamp::new(2),
            100,
        )
        .expect("ticketed reclaim");
    assert!(reclaimed.fence_acknowledged);
    assert_eq!(
        reclaimed.lifecycle,
        agentyc_core::SpaceLifecycle::AgentOwned
    );
    assert!(
        broker
            .takeover_with_control_ticket(
                &space.space_id,
                &claimant_authority,
                &returned.control_ticket,
                Timestamp::new(3),
                100,
            )
            .is_err()
    );
}

#[test]
fn failed_user_return_remains_fenced_until_explicit_acknowledgement() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    bridge.set_fence_acknowledged(false);
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "failed-return")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    assert!(
        broker
            .return_control(
                &space.space_id,
                &owner_authority,
                lease.lease.lease_epoch,
                Timestamp::new(1),
            )
            .is_err()
    );
    assert!(matches!(
        broker.acquire_lease(&space.space_id, &owner_authority, Timestamp::new(2), 100),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::PermissionDenied,
            ..
        }))
    ));
    bridge.set_fence_acknowledged(true);
    let returned = broker
        .acknowledge_return_control(&space.space_id, &owner_authority, LeaseEpoch::new(2))
        .expect("acknowledged return");
    assert_eq!(returned.lifecycle, agentyc_core::SpaceLifecycle::UserOwned);
    assert!(
        broker
            .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(3), 100)
            .is_err()
    );
}

#[test]
fn profile_and_bridge_capabilities_are_checked_before_mutation_lease() {
    let directory = tempdir().expect("tempdir");
    let broker = Broker::open(directory.path(), NullBridge).expect("broker");
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "profile")
        .expect("space");
    let wrong_profile = authority_with_profile(&broker, "owner", Some("other"));
    assert!(matches!(
        broker.acquire_lease(&space.space_id, &wrong_profile, Timestamp::new(0), 100),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::CapabilityUnavailable,
            ..
        }))
    ));
}

#[test]
fn canonical_hash_and_principal_reads_are_enforced() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge);
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "idempotency")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let mut request = action_request(
        "hash",
        space.space_id.clone(),
        None,
        lease.lease.lease_epoch,
        ActionOperation::Wait,
    );
    request.request_hash = ContentHash::from_bytes(b"wrong");
    assert!(matches!(
        broker.enqueue_action(request, &owner_authority, Timestamp::new(1)),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::InvalidArgument,
            ..
        }))
    ));
    let other_authority = authority(&broker, "other");
    assert!(
        broker
            .list_spaces(&other_authority)
            .expect("other list")
            .is_empty()
    );
    assert!(matches!(
        broker.describe_space(&other_authority, &space.space_id),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::SpaceForbidden,
            ..
        }))
    ));
}

#[test]
fn multiple_connection_authorities_coexist_and_echo_their_nonces() {
    let directory = tempdir().expect("tempdir");
    let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
    let hello = |suffix: &str| HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: principal("client"),
        resume_from: None,
        client_metadata: Some(ClientMetadata {
            client_id: None,
            client_name: Some("test-client".to_owned()),
            client_version: Some("1".to_owned()),
            connection_nonce: Some(ConnectionNonce::from_suffix(suffix).expect("nonce")),
            profile_binding_id: None,
        }),
    };
    let hello_one = hello("one");
    let first = broker.hello(&hello_one).expect("first hello");
    first
        .hello_ok()
        .validate_against(&hello_one)
        .expect("handshake echo");
    let second = broker.hello(&hello("two")).expect("second hello");
    broker
        .list_spaces(first.authority())
        .expect("first authority remains active");
    broker
        .create_space(second.authority(), "current")
        .expect("second authority admitted");
    broker
        .disconnect(first.authority())
        .expect("first authority disconnected");
    assert!(matches!(
        broker.list_spaces(first.authority()),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::PermissionDenied,
            ..
        }))
    ));
    broker
        .list_spaces(second.authority())
        .expect("second authority remains active");
}

#[test]
fn page_loss_increments_generation_and_prevents_a_stale_snapshot_scan() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "loss")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let page = managed_page(
        &broker,
        &space.space_id,
        &owner_authority,
        lease.lease.lease_epoch,
    );
    let before = broker
        .describe_space(&owner_authority, &space.space_id)
        .expect("space")
        .page(&page)
        .expect("page")
        .target_generation;
    broker
        .read_snapshot(
            &space.space_id,
            &page,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("snapshot");
    assert_eq!(bridge.snapshot_scan_count(), 1);
    let lost = broker
        .mark_page_lost(
            &space.space_id,
            &page,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(3),
        )
        .expect("loss");
    assert!(lost.target_generation.get() > before.get());
    assert!(matches!(
        broker.read_snapshot(
            &space.space_id,
            &page,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(4),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::UnmanagedPage,
            ..
        }))
    ));
    assert_eq!(bridge.snapshot_scan_count(), 1);
}

#[test]
fn close_is_page_scoped_and_revalidates_the_logical_page() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "close")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let page = managed_page(
        &broker,
        &space.space_id,
        &owner_authority,
        lease.lease.lease_epoch,
    );
    let closed = broker
        .close_page(
            &space.space_id,
            &page,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("close page");
    assert_eq!(closed.lifecycle, agentyc_core::PageLifecycle::Closed);
    assert_eq!(bridge.close_count(), 1);
    assert_eq!(bridge.closed_pages().len(), 1);
}

#[test]
fn shutdown_gates_dispatch_without_closing_pages() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "shutdown")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let request = action_request(
        "shutdown",
        space.space_id.clone(),
        None,
        lease.lease.lease_epoch,
        ActionOperation::Wait,
    );
    let receipt = broker
        .enqueue_action(request, &owner_authority, Timestamp::new(1))
        .expect("queued");
    broker.shutdown(Timestamp::new(2)).expect("shutdown");
    assert!(matches!(
        broker.dispatch_action(
            &receipt.action_id,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(3),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::HostDraining,
            ..
        }))
    ));
    assert_eq!(bridge.dispatch_count(), 0);
    assert_eq!(bridge.close_count(), 0);
}

#[test]
fn structural_raw_browser_id_fields_are_denied_before_admission() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge);
    let owner_authority = authority(&broker, "owner");
    let space = broker
        .create_space(&owner_authority, "raw-id")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let mut request = action_request(
        "raw-id",
        space.space_id,
        None,
        lease.lease.lease_epoch,
        ActionOperation::Wait,
    );
    request
        .payload
        .insert("targetId".to_owned(), "opaque-browser-value".to_owned());
    request.request_hash = canonical_action_hash(&request).expect("hash");
    assert!(matches!(
        broker.enqueue_action(request, &owner_authority, Timestamp::new(1)),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::InvalidArgument,
            ..
        }))
    ));
}

#[test]
fn incompatible_ledger_is_quarantined_without_replacement() {
    let directory = tempdir().expect("tempdir");
    {
        let broker = Broker::open(directory.path(), NullBridge).expect("broker");
        drop(broker);
    }
    let path = directory.path().join("ledger.json");
    let mut value: serde_json::Value =
        serde_json::from_slice(&fs::read(&path).expect("state")).expect("json");
    value["schema_version"] = serde_json::Value::from(999_u64);
    let bytes = serde_json::to_vec_pretty(&value).expect("json");
    fs::write(&path, &bytes).expect("write incompatible state");
    assert!(matches!(
        Broker::open(directory.path(), NullBridge),
        Err(HostError::Ledger(LedgerError::Incompatible(_)))
    ));
    assert_eq!(fs::read(&path).expect("state"), bytes);
    assert!(
        fs::read_dir(directory.path())
            .expect("directory")
            .flatten()
            .any(|entry| entry.file_name().to_string_lossy().contains("quarantine"))
    );
}

#[test]
fn user_control_ticket_is_invalidated_and_regenerated_after_restart() {
    let directory = tempdir().expect("tempdir");
    let old_ticket;
    {
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let owner_authority = authority(&broker, "owner");
        let space = broker
            .create_space(&owner_authority, "restart-control")
            .expect("space");
        let lease = broker
            .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
            .expect("lease");
        old_ticket = broker
            .return_control(
                &space.space_id,
                &owner_authority,
                lease.lease.lease_epoch,
                Timestamp::new(1),
            )
            .expect("return")
            .control_ticket;
    }
    let recovered = Broker::open(directory.path(), FakeBridge::new()).expect("recovered");
    let owner_authority = authority(&recovered, "owner");
    let space = recovered
        .list_spaces(&owner_authority)
        .expect("spaces")
        .into_iter()
        .next()
        .expect("space");
    assert!(
        recovered
            .takeover_with_control_ticket(
                &space.space_id,
                &owner_authority,
                &old_ticket,
                Timestamp::new(2),
                100,
            )
            .is_err()
    );
    let fresh_ticket = recovered
        .control_ticket(&owner_authority, &space.space_id)
        .expect("fresh ticket");
    let reclaimed = recovered
        .takeover_with_control_ticket(
            &space.space_id,
            &owner_authority,
            &fresh_ticket,
            Timestamp::new(2),
            100,
        )
        .expect("reclaim");
    assert_eq!(
        reclaimed.lifecycle,
        agentyc_core::SpaceLifecycle::AgentOwned
    );
}

#[cfg(unix)]
#[test]
fn state_and_lock_files_are_private() {
    use std::os::unix::fs::PermissionsExt;

    let directory = tempdir().expect("tempdir");
    let broker = Broker::open(directory.path(), NullBridge).expect("broker");
    let state_mode = fs::metadata(directory.path().join("ledger.json"))
        .expect("state metadata")
        .permissions()
        .mode()
        & 0o777;
    let lock_mode = fs::metadata(directory.path().join("broker.lock"))
        .expect("lock metadata")
        .permissions()
        .mode()
        & 0o777;
    assert_eq!(state_mode, 0o600);
    assert_eq!(lock_mode, 0o600);
    drop(broker);
}

#[test]
fn finish_closes_only_proven_managed_pages_and_release_never_global_closes() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "finish-owner");
    let space = broker
        .create_space(&owner_authority, "finish")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let managed = managed_page(
        &broker,
        &space.space_id,
        &owner_authority,
        lease.lease.lease_epoch,
    );
    let planned = broker
        .create_page_at(
            &space.space_id,
            &owner_authority,
            lease.lease.lease_epoch,
            "planned",
            Timestamp::new(1),
        )
        .expect("planned page");
    let lost = managed_page(
        &broker,
        &space.space_id,
        &owner_authority,
        lease.lease.lease_epoch,
    );
    broker
        .mark_page_lost(
            &space.space_id,
            &lost,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("lost page");

    let finished = broker
        .finish_space(
            &space.space_id,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(3),
        )
        .expect("finish");
    assert_eq!(finished.lifecycle, agentyc_core::SpaceLifecycle::Finished);
    assert_eq!(
        finished.lease.as_ref().expect("release lease").state,
        agentyc_core::states::LeaseState::Released
    );
    assert_eq!(bridge.close_count(), 1);
    assert_eq!(
        bridge.closed_pages(),
        vec![(space.space_id.clone(), managed.clone())]
    );
    assert_eq!(
        finished.page(&managed).expect("managed page").lifecycle,
        agentyc_core::PageLifecycle::Closed
    );
    assert_eq!(
        finished
            .page(&planned.page_id)
            .expect("planned page")
            .lifecycle,
        agentyc_core::PageLifecycle::Planned
    );
    assert_eq!(
        finished.page(&lost).expect("lost page").lifecycle,
        agentyc_core::PageLifecycle::TargetLost
    );

    let released = broker
        .release_space(
            &space.space_id,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(4),
        )
        .expect("release");
    assert_eq!(released.lifecycle, agentyc_core::SpaceLifecycle::Released);
    assert_eq!(bridge.close_count(), 1);
    let idempotent = broker
        .release_space(
            &space.space_id,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(5),
        )
        .expect("idempotent release");
    assert_eq!(idempotent.lifecycle, agentyc_core::SpaceLifecycle::Released);
    assert_eq!(bridge.close_count(), 1);
}

#[test]
fn finish_and_release_require_current_principal_and_lease_epoch() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "finish-current-owner");
    let space = broker
        .create_space(&owner_authority, "current-finish")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");

    let other_authority = authority(&broker, "finish-other-owner");
    assert!(matches!(
        broker.finish_space(
            &space.space_id,
            &other_authority,
            lease.lease.lease_epoch,
            Timestamp::new(1),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::SpaceForbidden,
            ..
        }))
    ));
    let owner_authority = authority(&broker, "finish-current-owner");
    assert!(matches!(
        broker.finish_space(
            &space.space_id,
            &owner_authority,
            LeaseEpoch::new(0),
            Timestamp::new(1),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::StaleLease,
            ..
        }))
    ));
    assert_eq!(bridge.close_count(), 0);

    broker
        .finish_space(
            &space.space_id,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("finish");
    let other_authority = authority(&broker, "finish-other-owner");
    assert!(matches!(
        broker.release_space(
            &space.space_id,
            &other_authority,
            lease.lease.lease_epoch,
            Timestamp::new(3),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::SpaceForbidden,
            ..
        }))
    ));
    let owner_authority = authority(&broker, "finish-current-owner");
    assert!(matches!(
        broker.release_space(
            &space.space_id,
            &owner_authority,
            LeaseEpoch::new(0),
            Timestamp::new(3),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::StaleLease,
            ..
        }))
    ));
}

#[test]
fn retention_policy_controls_finish_and_release_transitions() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge);

    let auto_authority = authority(&broker, "release-on-finish");
    let auto = broker
        .create_space_with_retention(
            &auto_authority,
            "auto-release",
            RetentionPolicy::ReleaseOnFinish,
        )
        .expect("auto space");
    let auto_lease = broker
        .acquire_lease(&auto.space_id, &auto_authority, Timestamp::new(0), 100)
        .expect("auto lease");
    let auto_finished = broker
        .finish_space(
            &auto.space_id,
            &auto_authority,
            auto_lease.lease.lease_epoch,
            Timestamp::new(1),
        )
        .expect("auto finish");
    assert_eq!(
        auto_finished.lifecycle,
        agentyc_core::SpaceLifecycle::Released
    );
    assert_eq!(
        broker
            .release_space(
                &auto.space_id,
                &auto_authority,
                auto_lease.lease.lease_epoch,
                Timestamp::new(2),
            )
            .expect("auto idempotent release")
            .lifecycle,
        agentyc_core::SpaceLifecycle::Released
    );

    let timed_authority = authority(&broker, "until-release");
    let timed = broker
        .create_space_with_retention(
            &timed_authority,
            "timed-release",
            RetentionPolicy::Until {
                at: Timestamp::new(10),
            },
        )
        .expect("timed space");
    let timed_lease = broker
        .acquire_lease(&timed.space_id, &timed_authority, Timestamp::new(0), 100)
        .expect("timed lease");
    assert_eq!(
        broker
            .finish_space(
                &timed.space_id,
                &timed_authority,
                timed_lease.lease.lease_epoch,
                Timestamp::new(1),
            )
            .expect("timed finish")
            .lifecycle,
        agentyc_core::SpaceLifecycle::Finished
    );
    assert!(matches!(
        broker.release_space(
            &timed.space_id,
            &timed_authority,
            timed_lease.lease.lease_epoch,
            Timestamp::new(9),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::PermissionDenied,
            ..
        }))
    ));
    assert_eq!(
        broker
            .release_space(
                &timed.space_id,
                &timed_authority,
                timed_lease.lease.lease_epoch,
                Timestamp::new(10),
            )
            .expect("timed release")
            .lifecycle,
        agentyc_core::SpaceLifecycle::Released
    );
}

#[test]
fn unknown_page_cleanup_fails_closed_without_blind_retry() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    bridge.push_close_result(Err(CoreError::new(
        ErrorCode::Timeout,
        "close response was lost",
    )));
    let broker = make_broker(directory.path(), bridge.clone());
    let owner_authority = authority(&broker, "unknown-cleanup");
    let space = broker
        .create_space(&owner_authority, "unknown-cleanup")
        .expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
        .expect("lease");
    let page = managed_page(
        &broker,
        &space.space_id,
        &owner_authority,
        lease.lease.lease_epoch,
    );

    assert!(matches!(
        broker.finish_space(
            &space.space_id,
            &owner_authority,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        ),
        Err(HostError::Bridge(CoreError {
            code: ErrorCode::Timeout,
            ..
        }))
    ));
    let failed = broker
        .describe_space(&owner_authority, &space.space_id)
        .expect("failed cleanup state");
    assert_eq!(failed.lifecycle, agentyc_core::SpaceLifecycle::Draining);
    assert_eq!(
        failed.page(&page).expect("failed page").lifecycle,
        agentyc_core::PageLifecycle::TargetLost
    );
    assert_eq!(bridge.close_count(), 1);
    assert!(
        broker
            .release_space(
                &space.space_id,
                &owner_authority,
                lease.lease.lease_epoch,
                Timestamp::new(3),
            )
            .is_err()
    );
    assert!(
        broker
            .finish_space(
                &space.space_id,
                &owner_authority,
                lease.lease.lease_epoch,
                Timestamp::new(4),
            )
            .is_err()
    );
    assert_eq!(bridge.close_count(), 1);
}

#[test]
fn finished_and_released_lifecycles_recover_durably() {
    let directory = tempdir().expect("tempdir");
    let lease_epoch;
    let space_id;
    {
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let owner_authority = authority(&broker, "recover-finish");
        let space = broker
            .create_space(&owner_authority, "recover-finish")
            .expect("space");
        let lease = broker
            .acquire_lease(&space.space_id, &owner_authority, Timestamp::new(0), 100)
            .expect("lease");
        lease_epoch = lease.lease.lease_epoch;
        space_id = space.space_id.clone();
        broker
            .finish_space(
                &space.space_id,
                &owner_authority,
                lease_epoch,
                Timestamp::new(1),
            )
            .expect("finish");
        broker
            .release_space(
                &space.space_id,
                &owner_authority,
                lease_epoch,
                Timestamp::new(2),
            )
            .expect("release");
    }

    let recovered = Broker::open(directory.path(), FakeBridge::new()).expect("recovered broker");
    let owner_authority = authority(&recovered, "recover-finish");
    let recovered_space = recovered
        .describe_space(&owner_authority, &space_id)
        .expect("recovered space");
    assert_eq!(
        recovered_space.lifecycle,
        agentyc_core::SpaceLifecycle::Released
    );
    assert_eq!(
        recovered_space
            .lease
            .as_ref()
            .expect("recovered lease")
            .lease_epoch,
        lease_epoch
    );
}

#[test]
fn sensitive_actions_require_explicit_approval_intent_and_capabilities() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner = authority(&broker, "sensitive");
    let space = broker.create_space(&owner, "sensitive").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
        .expect("lease");

    for (index, operation) in [
        ActionOperation::Evaluate,
        ActionOperation::CookieWrite,
        ActionOperation::StorageWrite,
        ActionOperation::Upload,
    ]
    .into_iter()
    .enumerate()
    {
        let request = action_request(
            &format!("missing-{index}"),
            space.space_id.clone(),
            None,
            lease.lease.lease_epoch,
            operation,
        );
        assert!(matches!(
            broker.enqueue_action(request, &owner, Timestamp::new(1)),
            Err(HostError::Core(agentyc_core::CoreError {
                code: ErrorCode::InvalidArgument,
                ..
            }))
        ));
    }

    bridge.set_capabilities(vec![
        Capability::Snapshot,
        Capability::Action,
        Capability::Wait,
        Capability::Reconcile,
    ]);
    let valid_evaluate = action_request_with_payload(
        "evaluate-capability",
        space.space_id,
        None,
        lease.lease.lease_epoch,
        ActionOperation::Evaluate,
        BTreeMap::from([
            ("approval".to_owned(), "approved".to_owned()),
            (
                "user_intent".to_owned(),
                "inspect the current page".to_owned(),
            ),
        ]),
    );
    assert!(matches!(
        broker.enqueue_action(valid_evaluate, &owner, Timestamp::new(1)),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::CapabilityUnavailable,
            ..
        }))
    ));
}

#[test]
fn action_dispatch_is_fifo_and_allows_only_one_in_flight_mutation() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner = authority(&broker, "fifo");
    let space = broker.create_space(&owner, "fifo").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
        .expect("lease");
    let first = broker
        .enqueue_action(
            action_request(
                "fifo-first",
                space.space_id.clone(),
                None,
                lease.lease.lease_epoch,
                ActionOperation::Wait,
            ),
            &owner,
            Timestamp::new(1),
        )
        .expect("first");
    let second = broker
        .enqueue_action(
            action_request(
                "fifo-second",
                space.space_id.clone(),
                None,
                lease.lease.lease_epoch,
                ActionOperation::Wait,
            ),
            &owner,
            Timestamp::new(1),
        )
        .expect("second");
    assert!(matches!(
        broker.dispatch_action(
            &second.action_id,
            &owner,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::PermissionDenied,
            ..
        }))
    ));
    broker
        .dispatch_action(
            &first.action_id,
            &owner,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("first dispatch");
    broker
        .dispatch_action(
            &second.action_id,
            &owner,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("second dispatch");

    let directory = tempdir().expect("mutation directory");
    let delegate = Arc::new(FakeBridge::new());
    let reentrant = Arc::new(ReentrantBridge::new(delegate));
    let broker = Broker::with_shared_bridge(
        Ledger::open(directory.path()).expect("ledger"),
        reentrant.clone(),
    );
    let owner = authority(&broker, "one-running");
    let space = broker.create_space(&owner, "one-running").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
        .expect("lease");
    let page = managed_page(&broker, &space.space_id, &owner, lease.lease.lease_epoch);
    let first = broker
        .enqueue_action(
            action_request(
                "mutation-first",
                space.space_id.clone(),
                Some(page.clone()),
                lease.lease.lease_epoch,
                ActionOperation::Click,
            ),
            &owner,
            Timestamp::new(1),
        )
        .expect("mutation first");
    let second = broker
        .enqueue_action(
            action_request(
                "mutation-second",
                space.space_id.clone(),
                Some(page),
                lease.lease.lease_epoch,
                ActionOperation::Click,
            ),
            &owner,
            Timestamp::new(1),
        )
        .expect("mutation second");
    let nested_error = Arc::new(Mutex::new(None));
    let nested_error_for_hook = nested_error.clone();
    let broker_for_hook = broker.clone();
    let owner_for_hook = owner.clone();
    let second_id = second.action_id.clone();
    let epoch = lease.lease.lease_epoch;
    reentrant.on_dispatch(move |_| {
        let result =
            broker_for_hook.dispatch_action(&second_id, &owner_for_hook, epoch, Timestamp::new(2));
        *nested_error_for_hook.lock().expect("nested result") = Some(result);
    });
    broker
        .dispatch_action(&first.action_id, &owner, epoch, Timestamp::new(2))
        .expect("first mutation dispatch");
    let nested_error = nested_error
        .lock()
        .expect("nested result")
        .take()
        .expect("nested dispatch result");
    assert!(matches!(
        nested_error,
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::PermissionDenied,
            ..
        }))
    ));
}

#[test]
fn fence_completion_is_compare_and_set_against_the_exact_pending_request() {
    let directory = tempdir().expect("tempdir");
    let delegate = Arc::new(FakeBridge::new());
    let bridge = Arc::new(ReentrantBridge::new(delegate));
    let broker = Broker::with_shared_bridge(
        Ledger::open(directory.path()).expect("ledger"),
        bridge.clone(),
    );
    let owner = authority(&broker, "fence-race");
    let space = broker.create_space(&owner, "fence-race").expect("space");
    broker
        .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
        .expect("lease");
    let broker_for_hook = broker.clone();
    let owner_for_hook = owner.clone();
    bridge.on_fence(move |space_id, epoch| {
        broker_for_hook
            .acknowledge_fence(space_id, &owner_for_hook, epoch)
            .map(|_| ())
            .map_err(|error| error.as_core_error())
    });

    assert!(matches!(
        broker.takeover(&space.space_id, &owner, Timestamp::new(1), 100),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::StaleLease,
            ..
        }))
    ));
    assert_eq!(
        broker
            .describe_space(&owner, &space.space_id)
            .expect("space")
            .lifecycle,
        agentyc_core::SpaceLifecycle::AgentOwned
    );
}

#[test]
fn older_epoch_reconciliation_requires_current_owner_and_takeover_proof() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    bridge.push_dispatch_result(BridgeDispatchResult::Unknown {
        reason: UnknownReason::LostResponse,
    });
    let broker = make_broker(directory.path(), bridge);
    let owner = authority(&broker, "proof-owner");
    let space = broker.create_space(&owner, "proof").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
        .expect("lease");
    let unknown = broker
        .execute_action(
            action_request(
                "proof-action",
                space.space_id.clone(),
                None,
                lease.lease.lease_epoch,
                ActionOperation::Wait,
            ),
            &owner,
            Timestamp::new(1),
        )
        .expect("unknown action")
        .receipt;
    let takeover = broker
        .takeover(&space.space_id, &owner, Timestamp::new(2), 100)
        .expect("takeover");

    let old_principal = authority(&broker, "proof-old-principal");
    assert!(matches!(
        broker.reconcile_action(
            &unknown.action_id,
            &old_principal,
            takeover.lease_epoch,
            Timestamp::new(3),
        ),
        Err(HostError::Core(agentyc_core::CoreError {
            code: ErrorCode::SpaceForbidden,
            ..
        }))
    ));

    let current_owner = authority(&broker, "proof-owner");
    let reconciled = broker
        .reconcile_action(
            &unknown.action_id,
            &current_owner,
            takeover.lease_epoch,
            Timestamp::new(4),
        )
        .expect("current owner reconciliation")
        .receipt;
    assert_eq!(reconciled.status, ActionStatus::Succeeded);
}

#[test]
fn clean_snapshot_metadata_result_contains_no_body_and_does_not_scan() {
    let directory = tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    let broker = make_broker(directory.path(), bridge.clone());
    let owner = authority(&broker, "metadata");
    let space = broker.create_space(&owner, "metadata").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
        .expect("lease");
    let page = managed_page(&broker, &space.space_id, &owner, lease.lease.lease_epoch);
    broker
        .read_snapshot(
            &space.space_id,
            &page,
            &owner,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("initial scan");
    let metadata: SnapshotMetadataRead = broker
        .read_snapshot_metadata(
            &space.space_id,
            &page,
            &owner,
            lease.lease.lease_epoch,
            Timestamp::new(2),
        )
        .expect("metadata");
    assert!(!metadata.scan_performed);
    assert_eq!(metadata.cache_state, agentyc_core::CacheState::Cached);
    assert_eq!(bridge.snapshot_scan_count(), 1);
    assert_eq!(metadata.metadata.space_id, space.space_id);
}

#[test]
fn refs_require_the_requested_frame_in_the_snapshot_vector() {
    let space = SpaceId::from_suffix("ref-space").expect("space");
    let page = PageId::from_suffix("ref-page").expect("page");
    let frame = FrameId::from_suffix("missing-frame").expect("frame");
    let mut registry = RefRegistry::default();
    let mut snapshot = empty_snapshot(space, page);
    assert!(
        registry
            .issue(&snapshot, frame.clone(), Timestamp::new(1))
            .is_err()
    );
    snapshot
        .frame_versions
        .insert(frame.clone(), FrameVersion::new(1));
    registry
        .issue(&snapshot, frame, Timestamp::new(1))
        .expect("frame in vector");
}

#[test]
fn oversized_protocol_and_artifact_configuration_is_rejected() {
    let directory = tempdir().expect("tempdir");
    let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
    assert!(
        ProtocolServer::with_max_payload(broker, MAX_CONTROL_FRAME_PAYLOAD_BYTES + 1,).is_err()
    );
    assert!(ProtocolClient::with_max_payload(MAX_CONTROL_FRAME_PAYLOAD_BYTES + 1).is_err());
    assert!(ProtocolClient::with_limits(4096, MAX_ARTIFACT_CHUNK_BYTES + 1).is_err());
    assert!(ProtocolClient::with_limits(4096, MAX_ARTIFACT_CHUNK_BYTES).is_ok());
}

#[test]
fn ledger_rejects_multiple_running_mutations_on_recovery() {
    let directory = tempdir().expect("tempdir");
    let action_ids;
    let space_id;
    {
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let owner = authority(&broker, "corrupt-running");
        let space = broker
            .create_space(&owner, "corrupt-running")
            .expect("space");
        let lease = broker
            .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
            .expect("lease");
        let first = broker
            .enqueue_action(
                action_request(
                    "corrupt-first",
                    space.space_id.clone(),
                    None,
                    lease.lease.lease_epoch,
                    ActionOperation::Click,
                ),
                &owner,
                Timestamp::new(1),
            )
            .expect("first");
        let second = broker
            .enqueue_action(
                action_request(
                    "corrupt-second",
                    space.space_id.clone(),
                    None,
                    lease.lease.lease_epoch,
                    ActionOperation::Click,
                ),
                &owner,
                Timestamp::new(1),
            )
            .expect("second");
        action_ids = [first.action_id, second.action_id];
        space_id = space.space_id;
    }
    let path = directory.path().join("ledger.json");
    let mut value: serde_json::Value =
        serde_json::from_slice(&fs::read(&path).expect("state")).expect("json");
    let actions = value["actions"].as_object_mut().expect("actions");
    for action_id in &action_ids {
        let receipt = actions.get_mut(&action_id.to_string()).expect("receipt");
        receipt["status"] = serde_json::Value::from("running");
        receipt["dispatch_state"] = serde_json::Value::from("dispatched");
    }
    value["action_queues"][space_id.to_string()] = serde_json::Value::Array(Vec::new());
    fs::write(&path, serde_json::to_vec_pretty(&value).expect("json")).expect("corrupt state");
    let recovered = Broker::open(&directory, NullBridge);
    assert!(matches!(
        recovered,
        Err(HostError::Ledger(LedgerError::Corrupt(_)))
    ));
}
