//! Canonical space, page, lease, and retention records.

use serde::{Deserialize, Serialize};

use crate::{
    ids::{
        BrokerEpoch, ConnectionEpoch, ConnectionNonce, ContentHash, Generation, LeaseEpoch, PageId,
        PrincipalId, ProfileBindingId, SpaceId, Timestamp, UserIntentTicketId,
    },
    states::{
        Capability, LeaseState, PageBindingState, PageLifecycle, PageOwnership,
        ProfileBindingState, SpaceLifecycle, UserIntentTicketState,
    },
};

/// Host-issued proof that one explicit user confirmation authorizes one exact operation.
///
/// The ticket is a transport-neutral claim, not an authentication credential: callers
/// must compare every binding against host-owned current state and atomically consume it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UserIntentTicket {
    /// Host-issued opaque ticket identity.
    pub ticket_id: UserIntentTicketId,
    /// Enrolled browser profile binding that presented the confirmation.
    pub profile_binding_id: ProfileBindingId,
    /// Logical task space authorized by the confirmation.
    pub space_id: SpaceId,
    /// Optional logical page authorized by the confirmation.
    pub page_id: Option<PageId>,
    /// Document generation shown to the user when confirming, if page-scoped.
    pub document_generation: Option<Generation>,
    /// Hash of the canonical operation and its payload.
    pub action_hash: ContentHash,
    /// Lease epoch that was current when confirmation was issued.
    pub lease_epoch: LeaseEpoch,
    /// Side-panel connection epoch that presented the confirmation.
    pub connection_epoch: ConnectionEpoch,
    /// Side-panel connection nonce that presented the confirmation.
    pub connection_nonce: ConnectionNonce,
    /// Expiry in the host's monotonic/core timestamp domain.
    pub expires_at: Timestamp,
    /// Whether this ticket remains available for its one use.
    pub state: UserIntentTicketState,
}

impl UserIntentTicket {
    /// Validate structural invariants that do not depend on the current clock.
    pub fn validate_shape(&self) -> Result<(), crate::errors::CoreError> {
        if self.page_id.is_some() != self.document_generation.is_some() {
            return Err(crate::errors::CoreError::invalid_argument(
                "page_id and document_generation must be supplied together",
            ));
        }
        if self.expires_at.get() == 0 {
            return Err(crate::errors::CoreError::invalid_argument(
                "user-intent ticket expiry must be non-zero",
            ));
        }
        Ok(())
    }

    /// Check ticket bindings and atomically consume a valid ticket.
    ///
    /// Expiry is exclusive (`now == expires_at` is expired). Rejection codes and
    /// messages are deliberately stable and do not reveal the mismatching binding.
    pub fn validate_and_consume(
        &mut self,
        presented: &UserIntentContext<'_>,
        now: Timestamp,
    ) -> Result<(), crate::errors::CoreError> {
        use crate::errors::{CoreError, ErrorCode};

        if self.state != UserIntentTicketState::Issued {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "user-intent ticket is not available",
            ));
        }
        if now >= self.expires_at {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "user-intent ticket has expired",
            ));
        }
        if presented.page_id.is_some() != presented.document_generation.is_some()
            || self.profile_binding_id != *presented.profile_binding_id
            || self.space_id != *presented.space_id
            || self.page_id != presented.page_id.cloned()
            || self.document_generation != presented.document_generation
            || self.action_hash != *presented.action_hash
            || self.lease_epoch != presented.lease_epoch
            || self.connection_epoch != presented.connection_epoch
            || self.connection_nonce != *presented.connection_nonce
        {
            return Err(CoreError::new(
                ErrorCode::PermissionDenied,
                "user-intent ticket does not match the requested operation",
            ));
        }
        self.validate_shape()?;
        self.state = UserIntentTicketState::Consumed;
        Ok(())
    }
}

/// Host-owned context against which a user-intent ticket is checked.
#[derive(Debug, Clone, Copy)]
pub struct UserIntentContext<'a> {
    /// Current enrolled profile binding.
    pub profile_binding_id: &'a ProfileBindingId,
    /// Current logical space.
    pub space_id: &'a SpaceId,
    /// Target page, or `None` for a space-wide operation.
    pub page_id: Option<&'a PageId>,
    /// Current document generation when a page is targeted.
    pub document_generation: Option<Generation>,
    /// Canonical hash of the operation being admitted.
    pub action_hash: &'a ContentHash,
    /// Current lease epoch.
    pub lease_epoch: LeaseEpoch,
    /// Current side-panel connection epoch.
    pub connection_epoch: ConnectionEpoch,
    /// Current side-panel connection nonce.
    pub connection_nonce: &'a ConnectionNonce,
}

/// Explicit user acknowledgement of shared existing-profile semantics.
///
/// This contract is required at every external `space.create` boundary. The
/// host does not infer consent from a profile selector, extension connection,
/// or client principal.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProfileDisclosure {
    /// The profile mode shown to the user.
    pub profile_scope: String,
    /// The shared-state notice shown to the user.
    pub shared_state_notice: String,
    /// Must remain false; task spaces are not isolation boundaries.
    pub isolation_claim: bool,
    /// Must be true only after an explicit user acknowledgement.
    pub acknowledged: bool,
}

impl ProfileDisclosure {
    /// Canonical shared-profile disclosure values.
    pub const PROFILE_SCOPE: &'static str = "shared_existing_profile";
    pub const SHARED_STATE_NOTICE: &'static str = "shared_profile_state";

    /// Validate the disclosure before a ledger create commit.
    pub fn validate(&self) -> Result<(), crate::errors::CoreError> {
        if self.acknowledged
            && !self.isolation_claim
            && self.profile_scope == Self::PROFILE_SCOPE
            && self.shared_state_notice == Self::SHARED_STATE_NOTICE
        {
            return Ok(());
        }
        Err(crate::errors::CoreError::new(
            crate::errors::ErrorCode::PermissionDenied,
            "explicit shared-profile disclosure acknowledgement is required before space creation",
        ))
    }
}

/// Retention policy for a completed logical space or page.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum RetentionPolicy {
    /// Keep the record and live page until an explicit release.
    #[default]
    Retain,
    /// Permit broker-owned cleanup after the space is finished.
    ReleaseOnFinish,
    /// Keep the record until a logical timestamp.
    Until {
        /// Expiry timestamp in the core clock domain.
        at: Timestamp,
    },
}

/// A lease epoch authorizing one principal to mutate a space.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Lease {
    /// Principal that received the lease.
    pub principal_id: PrincipalId,
    /// Fencing epoch. A newer value invalidates every lower value.
    pub lease_epoch: LeaseEpoch,
    /// Expiry timestamp in the core clock domain.
    pub expires_at: Timestamp,
    /// Recommended renewal deadline.
    pub renew_by: Timestamp,
    /// Current lease state.
    pub state: LeaseState,
}

impl Lease {
    /// Construct an active lease.
    pub fn active(
        principal_id: PrincipalId,
        lease_epoch: LeaseEpoch,
        expires_at: Timestamp,
        renew_by: Timestamp,
    ) -> Self {
        Self {
            principal_id,
            lease_epoch,
            expires_at,
            renew_by,
            state: LeaseState::Active,
        }
    }

    /// Return whether the lease epoch matches a presented epoch.
    pub fn accepts_epoch(&self, presented: LeaseEpoch) -> bool {
        self.state.admits_mutations() && self.lease_epoch.get() == presented.get()
    }

    /// Return whether this lease is expired in the supplied core clock domain.
    pub const fn is_expired_at(&self, now: Timestamp) -> bool {
        now.get() >= self.expires_at.get()
            || matches!(
                self.state,
                LeaseState::Expired | LeaseState::Fenced | LeaseState::Released
            )
    }

    /// Return whether this lease can admit an ordinary mutation at `now`.
    pub fn accepts_epoch_at(&self, presented: LeaseEpoch, now: Timestamp) -> bool {
        self.accepts_epoch(presented) && !self.is_expired_at(now)
    }

    /// Validate an epoch for mutation admission, distinguishing expiry from a stale fence.
    pub fn validate_epoch_at(
        &self,
        presented: LeaseEpoch,
        now: Timestamp,
    ) -> Result<(), crate::errors::CoreError> {
        use crate::errors::{CoreError, ErrorCode};

        if self.lease_epoch != presented {
            return Err(CoreError::stale_lease(
                self.lease_epoch.get(),
                presented.get(),
            ));
        }
        if self.is_expired_at(now) {
            return Err(CoreError::new(
                ErrorCode::LeaseExpired,
                "lease has expired or is no longer active",
            ));
        }
        Ok(())
    }

    /// Validate the lease's own temporal and state invariants.
    pub fn validate(&self) -> Result<(), crate::errors::CoreError> {
        if self.expires_at < self.renew_by {
            return Err(crate::errors::CoreError::invalid_argument(
                "lease renew_by must not be later than expires_at",
            ));
        }
        Ok(())
    }
}

/// Durable logical task-space descriptor.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SpaceDescriptor {
    /// Opaque logical space identity.
    pub space_id: SpaceId,
    /// User-facing label, not an authority or identity.
    pub label: String,
    /// Lifecycle state owned by the host ledger.
    pub lifecycle: SpaceLifecycle,
    /// Current logical owner principal.
    pub owner: PrincipalId,
    /// Current lease, when the space is agent-owned.
    pub lease: Option<Lease>,
    /// Profile-binding state without exposing a profile handle.
    pub profile_binding: ProfileBindingState,
    /// Logical pages belonging to this space.
    pub pages: Vec<PageDescriptor>,
    /// Presentation-only hint; never used as authorization.
    pub visual_group_hint: Option<String>,
    /// Capabilities available to this space.
    pub capabilities: Vec<Capability>,
    /// Bounded non-fatal warnings.
    pub warnings: Vec<String>,
    /// Cleanup/retention policy.
    pub retention: RetentionPolicy,
}

impl SpaceDescriptor {
    /// Validate the principal, lifecycle, page-independent lease, and epoch before
    /// admitting an ordinary mutation.
    pub fn validate_mutation_admission(
        &self,
        principal_id: &PrincipalId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> Result<(), crate::errors::CoreError> {
        use crate::errors::{CoreError, ErrorCode};

        if !self.lifecycle.admits_mutations() {
            return Err(CoreError::new(
                ErrorCode::UserControlRequired,
                "space lifecycle does not admit ordinary mutations",
            ));
        }
        if self.owner != *principal_id {
            return Err(CoreError::new(
                ErrorCode::SpaceForbidden,
                "principal does not own the space",
            ));
        }
        let lease = self
            .lease
            .as_ref()
            .ok_or_else(|| CoreError::new(ErrorCode::LeaseExpired, "space has no active lease"))?;
        if lease.principal_id != *principal_id {
            return Err(CoreError::new(
                ErrorCode::SpaceForbidden,
                "lease principal does not own the space",
            ));
        }
        lease.validate_epoch_at(lease_epoch, now)
    }

    /// Find a page by its logical identity.
    pub fn page(&self, page_id: &PageId) -> Option<&PageDescriptor> {
        self.pages.iter().find(|page| &page.page_id == page_id)
    }

    /// Find a mutable page by its logical identity.
    pub fn page_mut(&mut self, page_id: &PageId) -> Option<&mut PageDescriptor> {
        self.pages.iter_mut().find(|page| &page.page_id == page_id)
    }
}

/// Durable logical page descriptor.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PageDescriptor {
    /// Opaque logical page identity.
    pub page_id: PageId,
    /// Canonical owning space identity.
    pub space_id: SpaceId,
    /// User-facing label, not a locator.
    pub label: String,
    /// Logical page lifecycle.
    pub lifecycle: PageLifecycle,
    /// Current authority class.
    pub ownership: PageOwnership,
    /// Binding state independent of browser handles.
    pub binding: PageBindingState,
    /// Last known page URL as data.
    pub url: Option<String>,
    /// Last known title as data.
    pub title: Option<String>,
    /// Generation of the current managed binding.
    pub target_generation: Generation,
    /// Generation of the current navigation.
    pub navigation_generation: Generation,
    /// Generation of the current document.
    pub document_generation: Generation,
    /// Number of logical frames represented by the current snapshot.
    pub frame_count: u32,
    /// Whether the page record should be retained after close.
    pub retained: bool,
}

impl PageDescriptor {
    /// Return whether mutations are safe for this page under the current state.
    pub fn admits_agent_mutations(&self) -> bool {
        self.lifecycle == PageLifecycle::Managed
            && self.ownership == PageOwnership::Agent
            && self.binding == PageBindingState::Bound
    }
}

/// Proof that an old lease was fenced before authority changed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FenceProof {
    /// Logical space whose old authority was fenced.
    pub space_id: SpaceId,
    /// Broker epoch that issued this proof.
    pub broker_epoch: BrokerEpoch,
    /// Lease epoch that was invalidated, when one existed.
    pub released_epoch: Option<LeaseEpoch>,
    /// New epoch that forms the fencing barrier.
    pub fence_epoch: LeaseEpoch,
    /// The execution boundary acknowledged the fence.
    pub acknowledged: bool,
}

impl FenceProof {
    /// Validate that this record proves a strictly newer acknowledged fence.
    pub fn validate(&self) -> Result<(), crate::errors::CoreError> {
        if !self.acknowledged
            || self
                .released_epoch
                .is_some_and(|released| self.fence_epoch <= released)
        {
            return Err(crate::errors::CoreError::invalid_argument(
                "fence proof must be acknowledged and strictly newer than the released epoch",
            ));
        }
        Ok(())
    }
}

/// Proof returned after an agent lease is handed back to user control.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ControlReturnProof {
    /// Logical space returned to the user.
    pub space_id: SpaceId,
    /// Epoch released by the returning agent.
    pub released_epoch: LeaseEpoch,
    /// Fence barrier that invalidates the released epoch.
    pub fence: FenceProof,
    /// Durable lifecycle recorded after the barrier.
    pub lifecycle: SpaceLifecycle,
}

impl ControlReturnProof {
    /// Validate scope, epoch, and lifecycle invariants.
    pub fn validate(&self) -> Result<(), crate::errors::CoreError> {
        self.fence.validate()?;
        if self.fence.space_id != self.space_id
            || self.fence.released_epoch != Some(self.released_epoch)
            || self.lifecycle != SpaceLifecycle::UserOwned
        {
            return Err(crate::errors::CoreError::invalid_argument(
                "control-return proof does not match its fence or lifecycle",
            ));
        }
        Ok(())
    }
}

/// Compatibility name for callers that call the transition a return proof.
pub type ReturnControlProof = ControlReturnProof;

/// Proof that a logical space was durably released.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReleaseProof {
    /// Logical space that was released.
    pub space_id: SpaceId,
    /// Last epoch accepted before release.
    pub released_epoch: Option<LeaseEpoch>,
    /// Lifecycle recorded by the ledger.
    pub lifecycle: SpaceLifecycle,
    /// Core timestamp at which release was committed.
    pub released_at: Timestamp,
}

impl ReleaseProof {
    /// Validate that release is terminal and timestamped.
    pub fn validate(&self) -> Result<(), crate::errors::CoreError> {
        if !matches!(
            self.lifecycle,
            SpaceLifecycle::Released | SpaceLifecycle::Finished
        ) || self.released_at.get() == 0
        {
            return Err(crate::errors::CoreError::invalid_argument(
                "release proof must identify a terminal lifecycle and non-zero timestamp",
            ));
        }
        Ok(())
    }
}

/// Proof that broker-owned cleanup completed after a release barrier.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CleanupProof {
    /// Logical space whose retained records were cleaned.
    pub space_id: SpaceId,
    /// Release barrier that authorized cleanup.
    pub released_epoch: Option<LeaseEpoch>,
    /// Logical pages removed from the retained record.
    pub page_ids: Vec<PageId>,
    /// Core timestamp at which cleanup completed.
    pub completed_at: Timestamp,
}

impl CleanupProof {
    /// Validate bounded, unique cleanup scope and completion time.
    pub fn validate(&self) -> Result<(), crate::errors::CoreError> {
        const MAX_CLEANUP_PAGES: usize = 4096;
        if self.page_ids.len() > MAX_CLEANUP_PAGES
            || self.page_ids.windows(2).any(|pair| pair[0] >= pair[1])
            || self.completed_at.get() == 0
        {
            return Err(crate::errors::CoreError::invalid_argument(
                "cleanup proof has an invalid page scope or completion timestamp",
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::{
        ConnectionEpoch, ConnectionNonce, PageId, PrincipalId, ProfileBindingId, SpaceId,
    };

    #[test]
    fn lease_epoch_is_the_only_mutation_fence() {
        let principal = PrincipalId::from_suffix("agent").expect("valid principal");
        let lease = Lease::active(
            principal,
            LeaseEpoch::new(4),
            Timestamp::new(100),
            Timestamp::new(80),
        );
        assert!(lease.accepts_epoch(LeaseEpoch::new(4)));
        assert!(!lease.accepts_epoch(LeaseEpoch::new(3)));
    }

    #[test]
    fn profile_disclosure_requires_explicit_non_isolating_acknowledgement() {
        let valid = ProfileDisclosure {
            profile_scope: ProfileDisclosure::PROFILE_SCOPE.to_owned(),
            shared_state_notice: ProfileDisclosure::SHARED_STATE_NOTICE.to_owned(),
            isolation_claim: false,
            acknowledged: true,
        };
        valid.validate().expect("valid disclosure");
        assert!(
            ProfileDisclosure {
                acknowledged: false,
                ..valid.clone()
            }
            .validate()
            .is_err()
        );
        assert!(
            ProfileDisclosure {
                isolation_claim: true,
                ..valid
            }
            .validate()
            .is_err()
        );
    }

    #[test]
    fn page_mutation_requires_logical_managed_binding() {
        let page = PageDescriptor {
            page_id: PageId::from_suffix("one").expect("valid page"),
            space_id: SpaceId::from_suffix("one").expect("valid space"),
            label: "main".to_owned(),
            lifecycle: PageLifecycle::Managed,
            ownership: PageOwnership::Agent,
            binding: PageBindingState::Bound,
            url: None,
            title: None,
            target_generation: Generation::new(1),
            navigation_generation: Generation::new(1),
            document_generation: Generation::new(1),
            frame_count: 1,
            retained: true,
        };
        assert!(page.admits_agent_mutations());
    }

    fn intent_ticket() -> UserIntentTicket {
        UserIntentTicket {
            ticket_id: UserIntentTicketId::from_suffix("test_1").expect("ticket"),
            profile_binding_id: ProfileBindingId::from_suffix("default").expect("profile"),
            space_id: SpaceId::from_suffix("one").expect("space"),
            page_id: Some(PageId::from_suffix("one").expect("page")),
            document_generation: Some(Generation::new(3)),
            action_hash: ContentHash::from_bytes(b"action"),
            lease_epoch: LeaseEpoch::new(7),
            connection_epoch: ConnectionEpoch::new(4),
            connection_nonce: ConnectionNonce::from_suffix("panel").expect("nonce"),
            expires_at: Timestamp::new(100),
            state: UserIntentTicketState::Issued,
        }
    }

    #[test]
    fn user_intent_ticket_round_trips_and_is_single_use() {
        let mut ticket = intent_ticket();
        let json = serde_json::to_string(&ticket).expect("serialize ticket");
        assert_eq!(
            serde_json::from_str::<UserIntentTicket>(&json).expect("deserialize"),
            ticket
        );
        let profile = ticket.profile_binding_id.clone();
        let space = ticket.space_id.clone();
        let page = ticket.page_id.clone();
        let hash = ticket.action_hash.clone();
        let nonce = ticket.connection_nonce.clone();
        let context = UserIntentContext {
            profile_binding_id: &profile,
            space_id: &space,
            page_id: page.as_ref(),
            document_generation: ticket.document_generation,
            action_hash: &hash,
            lease_epoch: ticket.lease_epoch,
            connection_epoch: ticket.connection_epoch,
            connection_nonce: &nonce,
        };
        ticket
            .validate_and_consume(&context, Timestamp::new(99))
            .expect("valid ticket");
        assert_eq!(ticket.state, UserIntentTicketState::Consumed);
        assert_eq!(
            ticket
                .validate_and_consume(&context, Timestamp::new(99))
                .expect_err("replay rejected")
                .code,
            crate::errors::ErrorCode::PermissionDenied
        );
    }

    #[test]
    fn lease_admission_is_expiry_aware_and_distinguishes_stale_epochs() {
        let principal = PrincipalId::from_suffix("agent").expect("principal");
        let lease = Lease::active(
            principal,
            LeaseEpoch::new(4),
            Timestamp::new(100),
            Timestamp::new(80),
        );
        assert!(lease.accepts_epoch_at(LeaseEpoch::new(4), Timestamp::new(99)));
        assert!(!lease.accepts_epoch_at(LeaseEpoch::new(4), Timestamp::new(100)));
        assert_eq!(
            lease
                .validate_epoch_at(LeaseEpoch::new(4), Timestamp::new(100))
                .expect_err("expired lease")
                .code,
            crate::errors::ErrorCode::LeaseExpired
        );
        assert_eq!(
            lease
                .validate_epoch_at(LeaseEpoch::new(3), Timestamp::new(99))
                .expect_err("stale epoch")
                .code,
            crate::errors::ErrorCode::StaleLease
        );
    }

    #[test]
    fn recovery_and_proof_records_fail_closed() {
        assert!(!SpaceLifecycle::Recovering.admits_mutations());
        assert!(SpaceLifecycle::Recovering.admits_recovery_operations());

        let space_id = SpaceId::from_suffix("one").expect("space");
        let fence = FenceProof {
            space_id: space_id.clone(),
            broker_epoch: BrokerEpoch::new(2),
            released_epoch: Some(LeaseEpoch::new(3)),
            fence_epoch: LeaseEpoch::new(4),
            acknowledged: true,
        };
        let returned = ControlReturnProof {
            space_id: space_id.clone(),
            released_epoch: LeaseEpoch::new(3),
            fence,
            lifecycle: SpaceLifecycle::UserOwned,
        };
        returned.validate().expect("valid return proof");
        assert!(
            ControlReturnProof {
                lifecycle: SpaceLifecycle::Recovering,
                ..returned
            }
            .validate()
            .is_err()
        );
    }

    #[test]
    fn user_intent_ticket_rejects_expiry_and_each_authority_binding() {
        let mut expired = intent_ticket();
        let profile = expired.profile_binding_id.clone();
        let space = expired.space_id.clone();
        let page = expired.page_id.clone();
        let hash = expired.action_hash.clone();
        let nonce = expired.connection_nonce.clone();
        let context = UserIntentContext {
            profile_binding_id: &profile,
            space_id: &space,
            page_id: page.as_ref(),
            document_generation: expired.document_generation,
            action_hash: &hash,
            lease_epoch: expired.lease_epoch,
            connection_epoch: expired.connection_epoch,
            connection_nonce: &nonce,
        };
        let error = expired
            .validate_and_consume(&context, Timestamp::new(100))
            .expect_err("expiry is exclusive");
        assert_eq!(error.code, crate::errors::ErrorCode::PermissionDenied);
        assert_eq!(error.message, "user-intent ticket has expired");

        let mut mismatch = intent_ticket();
        let reject = |ticket: &mut UserIntentTicket,
                      profile_binding_id: &ProfileBindingId,
                      space_id: &SpaceId,
                      page_id: Option<&PageId>,
                      document_generation: Option<Generation>,
                      action_hash: &ContentHash,
                      lease_epoch: LeaseEpoch,
                      connection_epoch: ConnectionEpoch,
                      connection_nonce: &ConnectionNonce| {
            let context = UserIntentContext {
                profile_binding_id,
                space_id,
                page_id,
                document_generation,
                action_hash,
                lease_epoch,
                connection_epoch,
                connection_nonce,
            };
            let error = ticket
                .validate_and_consume(&context, Timestamp::new(50))
                .expect_err("mismatched authority binding");
            assert_eq!(error.code, crate::errors::ErrorCode::PermissionDenied);
            assert_eq!(
                error.message,
                "user-intent ticket does not match the requested operation"
            );
            assert_eq!(ticket.state, UserIntentTicketState::Issued);
        };

        let profile = mismatch.profile_binding_id.clone();
        let space = mismatch.space_id.clone();
        let page = mismatch.page_id.clone();
        let hash = mismatch.action_hash.clone();
        let generation = mismatch.document_generation;
        let lease = mismatch.lease_epoch;
        let connection = mismatch.connection_epoch;
        let nonce = mismatch.connection_nonce.clone();
        let other_profile = ProfileBindingId::from_suffix("other").expect("profile");
        reject(
            &mut mismatch,
            &other_profile,
            &space,
            page.as_ref(),
            generation,
            &hash,
            lease,
            connection,
            &nonce,
        );
        let other_space = SpaceId::from_suffix("other").expect("space");
        reject(
            &mut mismatch,
            &profile,
            &other_space,
            page.as_ref(),
            generation,
            &hash,
            lease,
            connection,
            &nonce,
        );
        let other_page = PageId::from_suffix("other").expect("page");
        reject(
            &mut mismatch,
            &profile,
            &space,
            Some(&other_page),
            generation,
            &hash,
            lease,
            connection,
            &nonce,
        );
        reject(
            &mut mismatch,
            &profile,
            &space,
            page.as_ref(),
            Some(Generation::new(4)),
            &hash,
            lease,
            connection,
            &nonce,
        );
        let wrong_hash = ContentHash::from_bytes(b"different action");
        reject(
            &mut mismatch,
            &profile,
            &space,
            page.as_ref(),
            generation,
            &wrong_hash,
            lease,
            connection,
            &nonce,
        );
        reject(
            &mut mismatch,
            &profile,
            &space,
            page.as_ref(),
            generation,
            &hash,
            LeaseEpoch::new(8),
            connection,
            &nonce,
        );
        reject(
            &mut mismatch,
            &profile,
            &space,
            page.as_ref(),
            generation,
            &hash,
            lease,
            ConnectionEpoch::new(5),
            &nonce,
        );
        let wrong_nonce = ConnectionNonce::from_suffix("other").expect("nonce");
        reject(
            &mut mismatch,
            &profile,
            &space,
            page.as_ref(),
            generation,
            &hash,
            lease,
            connection,
            &wrong_nonce,
        );

        mismatch.page_id = None;
        reject(
            &mut mismatch,
            &profile,
            &space,
            page.as_ref(),
            generation,
            &hash,
            lease,
            connection,
            &nonce,
        );
        assert_eq!(mismatch.state, UserIntentTicketState::Issued);
    }
}
