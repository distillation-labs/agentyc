//! Action requests and receipts, including unknown and reconciliation states.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::{
    errors::{CoreError, ErrorCode},
    ids::{
        ActionId, ContentHash, Generation, IdempotencyKey, LeaseEpoch, PageId, ReconcileToken,
        RequestId, SpaceId, Timestamp,
    },
    states::{ActionStatus, CompletionSource, DispatchState, NextAction, ReconciliationState},
};

/// Side-effect class understood by the host scheduler without naming a browser API.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActionOperation {
    /// Navigate a logical page.
    Navigate,
    /// Activate a logical element ref.
    Click,
    /// Insert bounded text into a logical control.
    Input,
    /// Run a policy-approved evaluation operation.
    Evaluate,
    /// Scroll a logical page.
    Scroll,
    /// Wait for a condition or event.
    Wait,
    /// Capture a bounded artifact.
    Screenshot,
    /// Write storage under an explicit policy.
    StorageWrite,
    /// Write cookies under an explicit policy.
    CookieWrite,
    /// Upload data under an explicit policy.
    Upload,
    /// Close a logically owned page.
    Close,
}

/// Why an action outcome became unknown.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UnknownReason {
    /// The execution response was lost after dispatch.
    LostResponse,
    /// The bridge disconnected after dispatch.
    BridgeLost,
    /// The host restarted after dispatch.
    HostRestarted,
    /// A deadline elapsed after dispatch.
    TimeoutAfterDispatch,
    /// The browser session changed before completion was observed.
    BrowserSessionChanged,
    /// A takeover fence interrupted an in-flight operation.
    FenceInterrupted,
}

/// Optional action postcondition used during reconciliation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum Postcondition {
    /// The page must reach a logical generation.
    PageGeneration {
        /// Expected document generation.
        document_generation: Generation,
    },
    /// The page must produce a snapshot with this hash.
    SnapshotHash {
        /// Expected snapshot hash.
        snapshot_hash: ContentHash,
    },
}

/// A mutation request with all identities needed for idempotency and fencing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ActionRequest<P = BTreeMap<String, String>> {
    /// Request identity.
    pub request_id: RequestId,
    /// Durable action identity.
    pub action_id: ActionId,
    /// Caller-supplied idempotency identity.
    pub idempotency_key: IdempotencyKey,
    /// Hash of the canonical request payload.
    pub request_hash: ContentHash,
    /// Owning logical space.
    pub space_id: SpaceId,
    /// Optional logical page target.
    pub page_id: Option<PageId>,
    /// Lease epoch presented for admission.
    pub lease_epoch: LeaseEpoch,
    /// Transport-neutral operation class.
    pub operation: ActionOperation,
    /// Typed operation payload.
    pub payload: P,
    /// Optional postcondition used during reconciliation.
    pub postcondition: Option<Postcondition>,
}

impl<P> ActionRequest<P> {
    /// Reject a request whose lease epoch is not current.
    pub fn validate_lease(&self, current: LeaseEpoch) -> Result<(), CoreError> {
        if self.lease_epoch != current {
            return Err(CoreError::stale_lease(
                current.get(),
                self.lease_epoch.get(),
            ));
        }
        Ok(())
    }
}

/// Durable receipt for one action attempt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ActionReceipt {
    /// Durable action identity.
    pub action_id: ActionId,
    /// Request identity used for response matching.
    pub request_id: RequestId,
    /// Idempotency key used for duplicate behavior.
    pub idempotency_key: IdempotencyKey,
    /// Hash of the admitted request.
    pub request_hash: ContentHash,
    /// Owning logical space.
    pub space_id: SpaceId,
    /// Optional logical page target.
    pub page_id: Option<PageId>,
    /// Lease epoch used for admission.
    pub lease_epoch: LeaseEpoch,
    /// Operation class.
    pub operation: ActionOperation,
    /// Durable outcome status.
    pub status: ActionStatus,
    /// Dispatch boundary status.
    pub dispatch_state: DispatchState,
    /// Whether a safe retry is currently possible.
    pub retryable: bool,
    /// Whether the receipt has or had an unknown outcome.
    pub unknown: bool,
    /// Cause of an unknown outcome.
    pub unknown_reason: Option<UnknownReason>,
    /// Reconciliation state.
    pub reconciliation_state: ReconciliationState,
    /// Source of the definitive completion.
    pub completion_source: CompletionSource,
    /// Optional declared postcondition.
    pub postcondition: Option<Postcondition>,
    /// Stable terminal error code, when any.
    pub error_code: Option<ErrorCode>,
    /// Token used to reconcile an unknown outcome.
    pub reconcile_token: Option<ReconcileToken>,
    /// Client guidance after this receipt.
    pub next_action: NextAction,
    /// Start timestamp in the core clock domain.
    pub started_at: Option<Timestamp>,
    /// Completion timestamp in the core clock domain.
    pub completed_at: Option<Timestamp>,
}

impl ActionReceipt {
    /// Construct a queued receipt for a newly admitted action.
    #[allow(clippy::too_many_arguments)]
    pub fn queued(
        action_id: ActionId,
        request_id: RequestId,
        idempotency_key: IdempotencyKey,
        request_hash: ContentHash,
        space_id: SpaceId,
        page_id: Option<PageId>,
        lease_epoch: LeaseEpoch,
        operation: ActionOperation,
        postcondition: Option<Postcondition>,
        started_at: Option<Timestamp>,
    ) -> Self {
        Self {
            action_id,
            request_id,
            idempotency_key,
            request_hash,
            space_id,
            page_id,
            lease_epoch,
            operation,
            status: ActionStatus::Queued,
            dispatch_state: DispatchState::NotDispatched,
            retryable: false,
            unknown: false,
            unknown_reason: None,
            reconciliation_state: ReconciliationState::NotRequired,
            completion_source: CompletionSource::None,
            postcondition,
            error_code: None,
            reconcile_token: None,
            next_action: NextAction::None,
            started_at,
            completed_at: None,
        }
    }

    /// Ensure the receipt still belongs to the current lease epoch.
    pub fn validate_lease(&self, current: LeaseEpoch) -> Result<(), CoreError> {
        if self.lease_epoch != current {
            return Err(CoreError::stale_lease(
                current.get(),
                self.lease_epoch.get(),
            ));
        }
        Ok(())
    }

    /// Mark the action as dispatched across the execution boundary.
    pub fn mark_dispatched(&mut self) -> Result<(), ActionTransitionError> {
        self.require_status(ActionStatus::Queued)?;
        self.status = ActionStatus::Running;
        self.dispatch_state = DispatchState::Dispatched;
        Ok(())
    }

    /// Mark dispatch as acknowledged while execution remains in progress.
    pub fn mark_acknowledged(&mut self) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Running || self.dispatch_state != DispatchState::Dispatched
        {
            return Err(ActionTransitionError::InvalidDispatchState);
        }
        self.dispatch_state = DispatchState::Acknowledged;
        Ok(())
    }

    /// Mark a definitive successful completion.
    pub fn mark_succeeded(
        &mut self,
        completed_at: Option<Timestamp>,
        source: CompletionSource,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Running {
            return Err(ActionTransitionError::InvalidStatus {
                expected: ActionStatus::Running,
                actual: self.status,
            });
        }
        self.status = ActionStatus::Succeeded;
        self.retryable = false;
        self.unknown = false;
        self.unknown_reason = None;
        self.reconciliation_state = ReconciliationState::NotRequired;
        self.completion_source = source;
        self.next_action = NextAction::None;
        self.completed_at = completed_at;
        Ok(())
    }

    /// Mark a definitive failed completion.
    pub fn mark_failed(
        &mut self,
        code: ErrorCode,
        retryable: bool,
        completed_at: Option<Timestamp>,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Running {
            return Err(ActionTransitionError::InvalidStatus {
                expected: ActionStatus::Running,
                actual: self.status,
            });
        }
        self.status = ActionStatus::Failed;
        self.retryable = retryable;
        self.error_code = Some(code);
        self.completion_source = CompletionSource::Extension;
        self.next_action = if retryable {
            NextAction::Retry
        } else {
            NextAction::None
        };
        self.completed_at = completed_at;
        Ok(())
    }

    /// Mark a pre-dispatch action cancelled.
    pub fn cancel(&mut self, completed_at: Option<Timestamp>) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Queued {
            return Err(ActionTransitionError::InvalidStatus {
                expected: ActionStatus::Queued,
                actual: self.status,
            });
        }
        self.status = ActionStatus::Cancelled;
        self.dispatch_state = DispatchState::Rejected;
        self.error_code = Some(ErrorCode::Cancelled);
        self.completion_source = CompletionSource::Ledger;
        self.next_action = NextAction::None;
        self.completed_at = completed_at;
        Ok(())
    }

    /// Record an unknown post-dispatch outcome without replaying the operation.
    pub fn mark_unknown(
        &mut self,
        reason: UnknownReason,
        reconcile_token: ReconcileToken,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Running
            || !matches!(
                self.dispatch_state,
                DispatchState::Dispatched | DispatchState::Acknowledged
            )
        {
            return Err(ActionTransitionError::NotDispatched);
        }
        self.status = ActionStatus::Unknown;
        self.unknown = true;
        self.unknown_reason = Some(reason);
        self.reconciliation_state = ReconciliationState::Required;
        self.reconcile_token = Some(reconcile_token);
        self.error_code = Some(ErrorCode::UnknownOutcome);
        self.retryable = false;
        self.next_action = NextAction::Reconcile;
        Ok(())
    }

    /// Start reconciliation of an unknown receipt.
    pub fn begin_reconciliation(&mut self) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Unknown
            || self.reconciliation_state != ReconciliationState::Required
        {
            return Err(ActionTransitionError::ReconciliationNotRequired);
        }
        self.reconciliation_state = ReconciliationState::InProgress;
        Ok(())
    }

    /// Reconcile an unknown receipt as succeeded without replaying it.
    pub fn reconcile_succeeded(
        &mut self,
        completed_at: Option<Timestamp>,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Unknown
            || self.reconciliation_state != ReconciliationState::InProgress
        {
            return Err(ActionTransitionError::ReconciliationNotInProgress);
        }
        self.status = ActionStatus::Succeeded;
        self.unknown = false;
        self.retryable = false;
        self.reconciliation_state = ReconciliationState::ReconciledSucceeded;
        self.completion_source = CompletionSource::Reconciliation;
        self.error_code = None;
        self.next_action = NextAction::None;
        self.completed_at = completed_at;
        Ok(())
    }

    /// Reconcile an unknown receipt as failed or requiring confirmation.
    pub fn reconcile_failed(
        &mut self,
        code: ErrorCode,
        requires_confirmation: bool,
        completed_at: Option<Timestamp>,
    ) -> Result<(), ActionTransitionError> {
        if self.status != ActionStatus::Unknown
            || self.reconciliation_state != ReconciliationState::InProgress
        {
            return Err(ActionTransitionError::ReconciliationNotInProgress);
        }
        self.status = ActionStatus::Failed;
        self.unknown = false;
        self.retryable = false;
        self.reconciliation_state = if requires_confirmation {
            ReconciliationState::RequiresConfirmation
        } else {
            ReconciliationState::ReconciledFailed
        };
        self.completion_source = CompletionSource::Reconciliation;
        self.error_code = Some(code);
        self.next_action = if requires_confirmation {
            NextAction::Confirm
        } else {
            NextAction::None
        };
        self.completed_at = completed_at;
        Ok(())
    }

    /// Return whether another mutation must reconcile this receipt first.
    pub const fn requires_reconciliation(&self) -> bool {
        matches!(
            self.reconciliation_state,
            ReconciliationState::Required
                | ReconciliationState::InProgress
                | ReconciliationState::RequiresConfirmation
        )
    }

    fn require_status(&self, expected: ActionStatus) -> Result<(), ActionTransitionError> {
        if self.status == expected {
            Ok(())
        } else {
            Err(ActionTransitionError::InvalidStatus {
                expected,
                actual: self.status,
            })
        }
    }
}

/// Invalid receipt transition.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum ActionTransitionError {
    /// The receipt was not in the expected status.
    #[error("invalid action status: expected {expected:?}, got {actual:?}")]
    InvalidStatus {
        /// Expected state.
        expected: ActionStatus,
        /// Actual state.
        actual: ActionStatus,
    },
    /// Dispatch has not crossed the execution boundary.
    #[error("action was not dispatched")]
    NotDispatched,
    /// Dispatch cannot be acknowledged from this state.
    #[error("invalid dispatch state")]
    InvalidDispatchState,
    /// Reconciliation was not requested.
    #[error("action does not require reconciliation")]
    ReconciliationNotRequired,
    /// Reconciliation has not started.
    #[error("action reconciliation is not in progress")]
    ReconciliationNotInProgress,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ids::{ActionId, RequestId};

    fn receipt() -> ActionReceipt {
        ActionReceipt::queued(
            ActionId::from_suffix("one").expect("action"),
            RequestId::from_suffix("one").expect("request"),
            IdempotencyKey::from_suffix("one").expect("idempotency"),
            ContentHash::from_bytes(b"request"),
            SpaceId::from_suffix("one").expect("space"),
            Some(PageId::from_suffix("one").expect("page")),
            LeaseEpoch::new(1),
            ActionOperation::Click,
            None,
            Some(Timestamp::new(1)),
        )
    }

    #[test]
    fn dispatched_unknown_receipts_require_reconcile_without_replay() {
        let mut receipt = receipt();
        receipt.mark_dispatched().expect("dispatch");
        receipt
            .mark_unknown(
                UnknownReason::LostResponse,
                ReconcileToken::from_suffix("one").expect("token"),
            )
            .expect("unknown");
        assert_eq!(receipt.status, ActionStatus::Unknown);
        assert!(receipt.requires_reconciliation());
        assert_eq!(receipt.next_action, NextAction::Reconcile);
        receipt.begin_reconciliation().expect("begin");
        receipt
            .reconcile_succeeded(Some(Timestamp::new(2)))
            .expect("reconcile");
        assert_eq!(receipt.status, ActionStatus::Succeeded);
        assert_eq!(receipt.completion_source, CompletionSource::Reconciliation);
    }

    #[test]
    fn stale_lease_is_a_stable_error() {
        let receipt = receipt();
        let error = receipt
            .validate_lease(LeaseEpoch::new(2))
            .expect_err("stale lease");
        assert_eq!(error.code, ErrorCode::StaleLease);
        assert_eq!(error.guidance, crate::errors::ErrorGuidance::RefreshLease);
    }
}
