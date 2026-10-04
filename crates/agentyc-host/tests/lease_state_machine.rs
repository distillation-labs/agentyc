//! Phase 3 lease/takeover/fencing state-machine gates.

use std::sync::Arc;

use agentyc_core::{
    ClientMetadata, ConnectionNonce, HelloEnvelope, LeaseEpoch, PROTOCOL_VERSION, PrincipalId,
    ProfileBindingId, ProfileBindingState, Timestamp,
};
use agentyc_host::{Broker, FakeBridge, HostError, Ledger};
use tempfile::tempdir_in;

fn authority(broker: &Broker, suffix: &str) -> agentyc_host::AuthorityTicket {
    broker
        .hello(&HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix(suffix).expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("phase3-lease-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(
                    ConnectionNonce::from_suffix(format!("nonce-{suffix}"))
                        .expect("connection nonce"),
                ),
                profile_binding_id: None,
            }),
        })
        .expect("hello")
        .authority()
        .clone()
}

#[test]
fn takeover_fence_pending_blocks_old_epoch_until_acknowledged() {
    let directory = tempdir_in("/tmp").expect("state directory");
    let bridge = Arc::new(FakeBridge::new());
    bridge.set_fence_acknowledged(false);
    let broker = Broker::with_shared_bridge(
        Ledger::open(directory.path()).expect("ledger"),
        bridge.clone(),
    );
    let owner = authority(&broker, "lease-owner");
    let space = broker.create_space(&owner, "lease").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 100)
        .expect("lease");
    let takeover = broker
        .takeover(&space.space_id, &owner, Timestamp::new(1), 100)
        .expect("takeover");
    assert_eq!(takeover.lease_epoch, LeaseEpoch::new(2));
    assert!(!takeover.fence_acknowledged);
    assert!(matches!(
        broker.renew_lease(
            &space.space_id,
            &owner,
            lease.lease.lease_epoch,
            Timestamp::new(2),
            100,
        ),
        Err(HostError::Core(error)) if error.code == agentyc_core::ErrorCode::UserControlRequired
            || error.code == agentyc_core::ErrorCode::StaleLease
    ));
    bridge.set_fence_acknowledged(true);
    let ready = broker
        .acknowledge_fence(&space.space_id, &owner, takeover.lease_epoch)
        .expect("fence ack");
    assert_eq!(ready.lifecycle, agentyc_core::SpaceLifecycle::AgentOwned);
}

#[test]
fn competing_principals_cannot_claim_or_mutate_another_space() {
    let directory = tempdir_in("/tmp").expect("state directory");
    let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
    let first = authority(&broker, "lease-first");
    let second = authority(&broker, "lease-second");
    let space = broker.create_space(&first, "owned").expect("space");
    broker
        .acquire_lease(&space.space_id, &first, Timestamp::new(0), 100)
        .expect("owner lease");
    assert!(
        broker
            .acquire_lease(&space.space_id, &second, Timestamp::new(0), 100)
            .is_err()
    );
    assert!(broker.describe_space(&second, &space.space_id).is_err());
}

#[test]
fn pause_and_handoff_fence_the_space_and_cancel_mutation_admission() {
    let directory = tempdir_in("/tmp").expect("state directory");
    let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
    let owner = authority(&broker, "lease-pause");
    let paused = broker.create_space(&owner, "paused").expect("paused space");
    broker
        .acquire_lease(&paused.space_id, &owner, Timestamp::new(0), 100)
        .expect("paused lease");
    let paused = broker
        .pause_space(&paused.space_id, &owner, Timestamp::new(1), 100)
        .expect("pause");
    assert_eq!(paused.lifecycle, agentyc_core::SpaceLifecycle::Paused);
    assert!(
        broker
            .renew_lease(
                &paused.space_id,
                &owner,
                paused.lease.expect("fenced lease").lease_epoch,
                Timestamp::new(2),
                100,
            )
            .is_err()
    );

    let handed = broker
        .create_space(&owner, "handoff")
        .expect("handoff space");
    broker
        .acquire_lease(&handed.space_id, &owner, Timestamp::new(0), 100)
        .expect("handoff lease");
    let handed = broker
        .handoff_space(&handed.space_id, &owner, Timestamp::new(1), 100)
        .expect("handoff");
    assert_eq!(
        handed.lifecycle,
        agentyc_core::SpaceLifecycle::HandoffRequested
    );
}

#[test]
fn copied_profile_binding_fences_bound_records_without_auto_rebinding() {
    let directory = tempdir_in("/tmp").expect("state directory");
    let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
    let profile_owner = broker
        .hello(&HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix("profile-owner").expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("profile-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(
                    ConnectionNonce::from_suffix("profile-nonce").expect("nonce"),
                ),
                profile_binding_id: Some(
                    ProfileBindingId::from_suffix("profile-a").expect("profile"),
                ),
            }),
        })
        .expect("profile hello")
        .authority()
        .clone();
    let space = broker
        .create_space(&profile_owner, "profile-bound")
        .expect("space");
    let observed = ProfileBindingId::from_suffix("profile-b").expect("observed profile");
    assert_eq!(
        broker
            .mark_profile_rebind_required(&observed)
            .expect("rebind fence"),
        1
    );
    let space = broker
        .describe_space(&profile_owner, &space.space_id)
        .expect("space state");
    assert_eq!(space.profile_binding, ProfileBindingState::RebindRequired);
    assert_eq!(space.lifecycle, agentyc_core::SpaceLifecycle::Created);
}

#[test]
fn expired_leases_are_rejected_before_bridge_side_effects() {
    let directory = tempdir_in("/tmp").expect("state directory");
    let bridge = Arc::new(FakeBridge::new());
    let broker = Broker::with_shared_bridge(
        Ledger::open(directory.path()).expect("ledger"),
        bridge.clone(),
    );
    let owner = authority(&broker, "lease-expiry");
    let space = broker.create_space(&owner, "expiry").expect("space");
    let lease = broker
        .acquire_lease(&space.space_id, &owner, Timestamp::new(0), 1)
        .expect("lease");
    assert!(
        broker
            .renew_lease(
                &space.space_id,
                &owner,
                lease.lease.lease_epoch,
                Timestamp::new(2),
                100,
            )
            .is_err()
    );
    assert_eq!(bridge.dispatch_count(), 0);
}
