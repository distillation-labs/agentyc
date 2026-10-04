//! Phase 3 host lifecycle, ownership-lock, and endpoint discovery gates.

use std::time::Duration;

use agentyc_host::{
    Broker, EndpointMetadata, FakeBridge, HostDegradedReason, HostLifecycle, Ledger, LedgerError,
    NativeForwardServer, endpoint_metadata_path, native_forward_socket_path,
    publish_endpoint_metadata, read_endpoint_metadata,
};
use tempfile::tempdir_in;

#[test]
fn one_profile_directory_has_one_broker_and_bounded_lifecycle_recovery() {
    let directory = tempdir_in("/tmp").expect("state directory");
    let broker = Broker::open(directory.path(), FakeBridge::new()).expect("first broker");
    assert_eq!(broker.lifecycle().expect("lifecycle"), HostLifecycle::Ready);
    assert!(matches!(
        Broker::open(directory.path(), FakeBridge::new()),
        Err(agentyc_host::HostError::Ledger(LedgerError::AlreadyOwned))
    ));

    broker
        .mark_degraded(HostDegradedReason::ExtensionLost)
        .expect("degrade");
    assert!(matches!(
        broker.lifecycle().expect("degraded lifecycle"),
        HostLifecycle::Degraded(HostDegradedReason::ExtensionLost)
    ));
    broker.begin_recovery().expect("recovery");
    broker.mark_ready().expect("ready");
    assert_eq!(
        broker.lifecycle().expect("ready lifecycle"),
        HostLifecycle::Ready
    );
    assert!(
        broker
            .transition_lifecycle(HostLifecycle::Recovering)
            .is_err()
    );
    broker
        .shutdown(agentyc_host::agentyc_core::Timestamp::new(1))
        .expect("shutdown");
    assert_eq!(
        broker.lifecycle().expect("stopped lifecycle"),
        HostLifecycle::Stopped
    );
    drop(broker);

    let recovered = Broker::open(directory.path(), FakeBridge::new()).expect("reopen after drop");
    assert!(recovered.broker_epoch().expect("epoch").get() >= 2);
}

#[cfg(unix)]
#[test]
fn endpoint_metadata_and_forward_socket_are_owner_scoped_and_bounded() {
    let directory = tempdir_in("/tmp").expect("state directory");
    let forward = NativeForwardServer::start(directory.path()).expect("forward endpoint");
    let broker_epoch = 7;
    let metadata = EndpointMetadata::new(
        broker_epoch,
        std::process::id(),
        directory.path().join("host.sock").display().to_string(),
        forward.socket_path().display().to_string(),
    );
    publish_endpoint_metadata(directory.path(), &metadata).expect("publish metadata");
    assert_eq!(
        read_endpoint_metadata(directory.path()).expect("read metadata"),
        metadata
    );
    assert!(endpoint_metadata_path(directory.path()).exists());
    assert!(native_forward_socket_path(directory.path()).exists());
    assert!(forward.accept_forwarded(Duration::from_millis(1)).is_err());
    forward.stop();
}

#[test]
fn durable_ledger_lock_rejects_a_symlinked_endpoint_without_replacing_owner_state() {
    let directory = tempdir_in("/tmp").expect("state directory");
    let ledger = Ledger::open(directory.path()).expect("ledger");
    let endpoint = endpoint_metadata_path(directory.path());
    let target = directory.path().join("endpoint-target");
    std::fs::write(&target, b"not-owned").expect("target");
    #[cfg(unix)]
    std::os::unix::fs::symlink(&target, &endpoint).expect("symlink");
    #[cfg(not(unix))]
    return;
    let result = publish_endpoint_metadata(
        directory.path(),
        &EndpointMetadata::new(1, std::process::id(), "host", "native"),
    );
    assert!(result.is_err());
    drop(ledger);
    assert!(endpoint.is_symlink());
}
