//! Lease, authority, and ownership transition result types.

use agentyc_core::{
    BrokerEpoch, ConnectionEpoch, ConnectionNonce, Lease, LeaseEpoch, ProfileBindingId,
    ReconcileToken, SpaceId, SpaceLifecycle,
};

/// A host-issued authority proof bound to the current broker connection.
///
/// Production callers obtain this only from [`crate::Connection`]. The broker
/// The broker rejects a ticket after a broker restart, after that connection
/// disconnects, or when any connection metadata differs. Multiple live
/// connections may hold independent tickets in one broker epoch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityTicket {
    pub(crate) principal_id: agentyc_core::PrincipalId,
    pub(crate) broker_epoch: BrokerEpoch,
    pub(crate) connection_epoch: ConnectionEpoch,
    pub(crate) profile_binding_id: Option<ProfileBindingId>,
    pub(crate) connection_nonce: ConnectionNonce,
}

impl AuthorityTicket {
    pub(crate) fn host_issued(
        principal_id: agentyc_core::PrincipalId,
        broker_epoch: BrokerEpoch,
        connection_epoch: ConnectionEpoch,
        profile_binding_id: Option<ProfileBindingId>,
        connection_nonce: ConnectionNonce,
    ) -> Self {
        Self {
            principal_id,
            broker_epoch,
            connection_epoch,
            profile_binding_id,
            connection_nonce,
        }
    }

    /// Principal authorized by this ticket.
    pub fn principal_id(&self) -> &agentyc_core::PrincipalId {
        &self.principal_id
    }

    /// Broker epoch in which this ticket was issued.
    pub const fn broker_epoch(&self) -> BrokerEpoch {
        self.broker_epoch
    }

    /// Connection epoch in which this ticket was issued.
    pub const fn connection_epoch(&self) -> ConnectionEpoch {
        self.connection_epoch
    }

    /// Profile binding asserted by the authenticated client, when any.
    pub fn profile_binding_id(&self) -> Option<&ProfileBindingId> {
        self.profile_binding_id.as_ref()
    }

    /// Connection nonce presented when this ticket was issued.
    pub fn connection_nonce(&self) -> &ConnectionNonce {
        &self.connection_nonce
    }
}

/// One-time host-issued handoff proof for returning a space to user control.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ControlTicket {
    space_id: SpaceId,
    broker_epoch: BrokerEpoch,
    fence_epoch: LeaseEpoch,
    token: ReconcileToken,
}

impl ControlTicket {
    pub(crate) fn new(
        space_id: SpaceId,
        broker_epoch: BrokerEpoch,
        fence_epoch: LeaseEpoch,
        token: ReconcileToken,
    ) -> Self {
        Self {
            space_id,
            broker_epoch,
            fence_epoch,
            token,
        }
    }

    /// Logical space covered by this handoff proof.
    pub fn space_id(&self) -> &SpaceId {
        &self.space_id
    }

    /// Broker epoch in which this proof was issued.
    pub const fn broker_epoch(&self) -> BrokerEpoch {
        self.broker_epoch
    }

    /// Fence epoch that must remain current while claiming the space.
    pub const fn fence_epoch(&self) -> LeaseEpoch {
        self.fence_epoch
    }

    pub(crate) fn token(&self) -> &ReconcileToken {
        &self.token
    }
}

/// A lease returned by an acquire or renewal operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseGrant {
    /// Logical space receiving the lease.
    pub space_id: SpaceId,
    /// Current fenced lease record.
    pub lease: Lease,
}

/// Result of a takeover fence attempt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TakeoverResult {
    /// Logical space transferred to the claimant.
    pub space_id: SpaceId,
    /// New monotonic fencing epoch.
    pub lease_epoch: LeaseEpoch,
    /// Whether the bridge acknowledged the fence barrier.
    pub fence_acknowledged: bool,
    /// Lifecycle after the barrier attempt.
    pub lifecycle: SpaceLifecycle,
}

/// Result of returning an agent lease to explicit user control.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ControlReturn {
    /// Logical space returned to user control.
    pub space_id: SpaceId,
    /// Epoch that was released and is no longer accepted.
    pub released_epoch: LeaseEpoch,
    /// New durable fence epoch that invalidated the released lease.
    pub fence_epoch: LeaseEpoch,
    /// One-time proof required to reconcile and reclaim user-owned control.
    pub control_ticket: ControlTicket,
    /// Resulting lifecycle.
    pub lifecycle: SpaceLifecycle,
}
