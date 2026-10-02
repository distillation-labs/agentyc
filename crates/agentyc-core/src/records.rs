//! Canonical space, page, lease, and retention records.

use serde::{Deserialize, Serialize};

use crate::{
    ids::{Generation, LeaseEpoch, PageId, PrincipalId, SpaceId, Timestamp},
    states::{
        Capability, LeaseState, PageBindingState, PageLifecycle, PageOwnership,
        ProfileBindingState, SpaceLifecycle,
    },
};

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
        self.state == LeaseState::Active && self.lease_epoch.get() == presented.get()
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::{PageId, PrincipalId, SpaceId};

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
}
