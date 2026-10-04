//! Lease, authority, and ownership transition result types.

use agentyc_core::{
    BrokerEpoch, ConnectionEpoch, ConnectionNonce, ContentHash, Lease, LeaseEpoch,
    ProfileBindingId, ReconcileToken, SpaceId, SpaceLifecycle, Timestamp,
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

    /// Return the one-time reconciliation token for an authenticated caller.
    pub fn token(&self) -> &ReconcileToken {
        &self.token
    }
}

/// Single-use user confirmation for one exact sensitive action request.
///
/// Tickets are issued by the host only after a trusted user-confirmation
/// surface explicitly approves the request. They are process-local, expire,
/// and cannot authorize a different lease or canonical action hash.
#[derive(Clone, PartialEq, Eq)]
pub struct UserIntentTicket {
    // Opaque bearer token; keep it out of Debug output.
    token: ReconcileToken,
    space_id: SpaceId,
    lease_epoch: LeaseEpoch,
    action_hash: ContentHash,
    expires_at: Timestamp,
}

impl std::fmt::Debug for UserIntentTicket {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("UserIntentTicket")
            .field("token", &"[redacted]")
            .field("space_id", &self.space_id)
            .field("lease_epoch", &self.lease_epoch)
            .field("action_hash", &self.action_hash)
            .field("expires_at", &self.expires_at)
            .finish()
    }
}

impl UserIntentTicket {
    pub(crate) fn host_issued(
        token: ReconcileToken,
        space_id: SpaceId,
        lease_epoch: LeaseEpoch,
        action_hash: ContentHash,
        expires_at: Timestamp,
    ) -> Self {
        Self {
            token,
            space_id,
            lease_epoch,
            action_hash,
            expires_at,
        }
    }

    /// Logical space covered by this confirmation.
    pub fn space_id(&self) -> &SpaceId {
        &self.space_id
    }

    /// Lease epoch covered by this confirmation.
    pub const fn lease_epoch(&self) -> LeaseEpoch {
        self.lease_epoch
    }

    /// Canonical action hash covered by this confirmation.
    pub fn action_hash(&self) -> &ContentHash {
        &self.action_hash
    }

    /// Expiry timestamp in the host clock domain.
    pub const fn expires_at(&self) -> Timestamp {
        self.expires_at
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
