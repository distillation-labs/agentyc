//! Transport-neutral lifecycle and contract state enums.

use serde::{Deserialize, Serialize};

/// Lifecycle of a logical task space.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SpaceLifecycle {
    /// The space exists but has not yet received an owner lease.
    Created,
    /// An agent currently owns the space.
    AgentOwned,
    /// Ownership transfer has been requested.
    HandoffRequested,
    /// Existing work is draining before ownership changes.
    Draining,
    /// Mutations are fenced while the space remains retained.
    Paused,
    /// A user currently owns the space.
    UserOwned,
    /// The previous owner or bridge was lost.
    Orphaned,
    /// An explicit claimant is reconciling the space.
    Recovering,
    /// The space is complete and retained according to policy.
    Finished,
    /// The space has been explicitly released.
    Released,
    /// A takeover fence is waiting to be dispatched.
    FencePending,
    /// A takeover fence was dispatched but not acknowledged.
    FenceDispatched,
    /// A takeover fence was acknowledged before user ownership is recorded.
    FenceAcknowledged,
}

impl SpaceLifecycle {
    /// Whether new mutations should be admitted in this state.
    pub const fn admits_mutations(self) -> bool {
        matches!(self, Self::AgentOwned | Self::Recovering)
    }
}

/// Lifecycle of a logical page.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PageLifecycle {
    /// A page has been requested but has not been created.
    Planned,
    /// A page creation operation is in progress.
    Creating,
    /// The host has a managed page binding.
    Managed,
    /// The prior page binding disappeared.
    TargetLost,
    /// An explicit rebind is in progress.
    Rebinding,
    /// The user owns the page temporarily.
    UserOwned,
    /// A managed page is being closed under an ownership proof.
    Closing,
    /// The page is closed but its record remains addressable.
    Closed,
    /// The page record is no longer retained.
    Retired,
    /// The page was observed without a managed record.
    Unknown,
    /// The page is explicitly outside host ownership.
    Unmanaged,
    /// The page can be adopted only after an explicit claim.
    Adoptable,
}

/// Authority class for a page record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PageOwnership {
    /// The agent's current lease authorizes operations.
    Agent,
    /// The user has control and agent mutations are fenced.
    User,
    /// The broker is performing an internal lifecycle operation.
    Broker,
    /// No authority has been established.
    Unmanaged,
}

/// Binding state independent of a browser-specific handle.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PageBindingState {
    /// No live binding has been established.
    Unbound,
    /// The current logical binding is proven live.
    Bound,
    /// The prior binding was lost.
    Lost,
    /// More than one candidate could match.
    Ambiguous,
    /// A fresh explicit rebind is required.
    RebindRequired,
    /// The user owns the live page.
    UserOwned,
    /// The binding is closed.
    Closed,
}

/// State of a profile binding as seen by the transport-neutral host.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProfileBindingState {
    /// No profile has been enrolled.
    Unbound,
    /// The enrolled binding is active.
    Bound,
    /// A mismatch or storage reset requires explicit confirmation.
    RebindRequired,
    /// The binding can no longer be used.
    Revoked,
}

/// State of a lease record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LeaseState {
    /// The epoch can authorize operations until expiry or fencing.
    Active,
    /// Renewal is being processed without changing the epoch.
    Renewing,
    /// The expiry has passed.
    Expired,
    /// A newer epoch has fenced this lease.
    Fenced,
    /// The lease was explicitly returned.
    Released,
}

/// Lifecycle of a host-issued, single-use user-intent confirmation ticket.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UserIntentTicketState {
    /// The ticket may authorize its exact bound operation before expiry.
    Issued,
    /// The ticket has authorized one operation and cannot be replayed.
    Consumed,
    /// The host revoked the ticket before use.
    Revoked,
}

/// Durable action outcome state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActionStatus {
    /// The request is accepted but has not been dispatched.
    Queued,
    /// The operation may have side effects.
    Running,
    /// The operation completed successfully.
    Succeeded,
    /// The operation completed unsuccessfully.
    Failed,
    /// The operation was cancelled before completion.
    Cancelled,
    /// Dispatch happened but completion is not known.
    Unknown,
}

/// Dispatch acknowledgement state for an action receipt.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DispatchState {
    /// No dispatch attempt has been made.
    NotDispatched,
    /// The action is waiting in a scheduler.
    Queued,
    /// The action crossed the execution boundary.
    Dispatched,
    /// The execution boundary acknowledged receipt.
    Acknowledged,
    /// The execution boundary rejected the action.
    Rejected,
}

/// State of post-unknown-outcome reconciliation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReconciliationState {
    /// No reconciliation is needed.
    NotRequired,
    /// The receipt must be reconciled before retry or completion.
    Required,
    /// Reconciliation has started.
    InProgress,
    /// Reconciliation proved success.
    ReconciledSucceeded,
    /// Reconciliation proved failure.
    ReconciledFailed,
    /// A user or policy decision is required.
    RequiresConfirmation,
}

/// Source from which an action completion was established.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CompletionSource {
    /// The action has no completion source yet.
    None,
    /// The execution boundary returned a completion.
    Extension,
    /// A reconciliation read established the outcome.
    Reconciliation,
    /// Durable host state established the outcome.
    Ledger,
    /// A deadline elapsed without a definitive outcome.
    Timeout,
}

/// Guidance for the next client operation after a result.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NextAction {
    /// No follow-up is needed.
    None,
    /// The caller may retry the operation.
    Retry,
    /// The caller must reconcile the receipt first.
    Reconcile,
    /// A user confirmation is required.
    Confirm,
    /// The caller must obtain a current lease.
    RefreshLease,
    /// The caller must request a fresh snapshot.
    Resync,
    /// The caller must explicitly claim or adopt the resource.
    Claim,
}

/// Capability names advertised by the host contract.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    /// Read a coherent page snapshot.
    Snapshot,
    /// Apply an action against a leased page.
    Action,
    /// Wait for a broker event or condition.
    Wait,
    /// Capture a bounded artifact.
    Artifact,
    /// Evaluate a policy-approved page operation.
    Evaluate,
    /// Reconcile an unknown action receipt.
    Reconcile,
}

/// Snapshot representation requested or returned by the contract.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SnapshotMode {
    /// A complete representation with all retained elements.
    Full,
    /// A bounded representation optimized for compact consumers.
    Compact,
    /// A patch against a declared base snapshot.
    Delta,
    /// A base is invalid or unavailable and a fresh snapshot is required.
    Resync,
}

/// Whether a snapshot covers all required frames/elements.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SnapshotCoverage {
    /// All requested content is represented.
    Complete,
    /// Some content was omitted or could not be made coherent.
    Partial,
}

/// Cache freshness of a snapshot result.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CacheState {
    /// Captured for this request.
    Fresh,
    /// Served from a known-valid cache.
    Cached,
    /// Served while a refresh is required.
    Stale,
    /// The cached value cannot be used as a delta base.
    Invalidated,
}

/// Why a delta or ref cannot be used as-is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResyncReason {
    /// The requested base was not retained.
    BaseMissing,
    /// The base hash did not match.
    BaseHashMismatch,
    /// The patch chain exceeded its bound.
    ChainTooLong,
    /// The patch costs more than a compact/full response.
    CostExceeded,
    /// Frame versions could not form a coherent snapshot.
    Incoherent,
    /// Navigation invalidated the prior provenance.
    NavigationChanged,
    /// The caller explicitly requested resynchronization.
    Explicit,
    /// A ref was created from an invalid snapshot.
    StaleReference,
    /// The requested delta representation is unsupported.
    UnsupportedDelta,
}

/// Cause attached to a dirty snapshot/event.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DirtyReason {
    /// A page mutation changed the logical tree.
    DomMutation,
    /// Navigation changed the document.
    Navigation,
    /// A frame was added, removed, or replaced.
    FrameChanged,
    /// An action may have changed the page.
    Action,
    /// The source could not classify the change.
    Unknown,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mutation_admission_is_fenced_for_user_and_recovery_states() {
        assert!(SpaceLifecycle::AgentOwned.admits_mutations());
        assert!(SpaceLifecycle::Recovering.admits_mutations());
        assert!(!SpaceLifecycle::UserOwned.admits_mutations());
        assert!(!SpaceLifecycle::FencePending.admits_mutations());
    }
}
