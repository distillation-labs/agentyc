//! Replaceable logical bridge boundary.
//!
//! No method in this module accepts or returns a Chrome target, tab, session,
//! debugger, or process identifier. A real extension/CDP adapter can keep such
//! values private to its implementation in a later phase. The current crate has
//! no live browser bridge; [`NullBridge`] and [`FakeBridge`] are deterministic
//! seams only.

use std::{
    collections::{BTreeMap, VecDeque},
    sync::Mutex,
};

use agentyc_core::{
    ActionReceipt, ActionRequest, BrokerEpoch, Capability, CoreError, ErrorCode, LeaseEpoch,
    PageId, SnapshotEnvelope, SpaceId, UnknownReason,
};

use crate::snapshots::empty_snapshot;

/// Result of crossing the bridge's side-effect boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BridgeDispatchResult {
    /// The bridge returned a definitive success.
    Succeeded,
    /// The bridge returned a definitive failure.
    Failed {
        /// Stable failure code.
        code: ErrorCode,
        /// Whether the operation may be safely attempted again.
        retryable: bool,
    },
    /// Dispatch crossed the boundary but completion cannot be trusted.
    Unknown {
        /// Why the outcome is not known.
        reason: UnknownReason,
    },
}

/// Result of reconciling an already-dispatched action.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BridgeReconcileResult {
    /// A read proved the action took effect.
    Succeeded,
    /// A read proved the action did not complete or needs a decision.
    Failed {
        /// Stable failure code.
        code: ErrorCode,
        /// Whether user confirmation is required.
        requires_confirmation: bool,
    },
    /// The read was insufficient; the action remains unknown.
    StillUnknown,
}

/// Result of a logical takeover fence barrier.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FenceResult {
    /// Whether the bridge durably acknowledged the new fence.
    pub acknowledged: bool,
}

/// Transport-neutral bridge owned by the host broker.
pub trait Bridge: Send + Sync {
    /// Capabilities currently available through this bridge.
    fn capabilities(&self) -> Vec<Capability>;

    /// Dispatch one already-admitted logical action.
    fn dispatch(
        &self,
        request: &ActionRequest<BTreeMap<String, String>>,
    ) -> Result<BridgeDispatchResult, CoreError>;

    /// Reconcile an unknown receipt with a read-only proof operation.
    fn reconcile(&self, receipt: &ActionReceipt) -> Result<BridgeReconcileResult, CoreError>;

    /// Read a logical snapshot for a page.
    fn snapshot(&self, space_id: &SpaceId, page_id: &PageId)
    -> Result<SnapshotEnvelope, CoreError>;

    /// Fence all work at or below `new_epoch` for one logical space.
    fn fence(
        &self,
        space_id: &SpaceId,
        old_epoch: Option<LeaseEpoch>,
        new_epoch: LeaseEpoch,
        broker_epoch: BrokerEpoch,
    ) -> Result<FenceResult, CoreError>;

    /// Close one explicitly authorized logical page; never close a whole space.
    fn close_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
    ) -> Result<(), CoreError>;
}

/// Bridge placeholder used until a real extension transport is installed.
#[derive(Debug, Default, Clone, Copy)]
pub struct NullBridge;

impl Bridge for NullBridge {
    fn capabilities(&self) -> Vec<Capability> {
        Vec::new()
    }

    fn dispatch(
        &self,
        _request: &ActionRequest<BTreeMap<String, String>>,
    ) -> Result<BridgeDispatchResult, CoreError> {
        Err(CoreError::new(
            ErrorCode::ExtensionNotConnected,
            "no browser bridge is connected",
        ))
    }

    fn reconcile(&self, _receipt: &ActionReceipt) -> Result<BridgeReconcileResult, CoreError> {
        Err(CoreError::new(
            ErrorCode::ExtensionNotConnected,
            "no browser bridge is connected",
        ))
    }

    fn snapshot(
        &self,
        _space_id: &SpaceId,
        _page_id: &PageId,
    ) -> Result<SnapshotEnvelope, CoreError> {
        Err(CoreError::new(
            ErrorCode::ExtensionNotConnected,
            "no browser bridge is connected",
        ))
    }

    fn fence(
        &self,
        _space_id: &SpaceId,
        _old_epoch: Option<LeaseEpoch>,
        _new_epoch: LeaseEpoch,
        _broker_epoch: BrokerEpoch,
    ) -> Result<FenceResult, CoreError> {
        Err(CoreError::new(
            ErrorCode::ExtensionNotConnected,
            "no browser bridge is connected",
        ))
    }

    fn close_page(
        &self,
        _space_id: &SpaceId,
        _page_id: &PageId,
        _lease_epoch: LeaseEpoch,
    ) -> Result<(), CoreError> {
        Err(CoreError::new(
            ErrorCode::ExtensionNotConnected,
            "no browser bridge is connected",
        ))
    }
}

#[derive(Debug)]
struct FakeState {
    capabilities: Vec<Capability>,
    snapshots: BTreeMap<SpaceId, BTreeMap<PageId, SnapshotEnvelope>>,
    dispatch_results: VecDeque<BridgeDispatchResult>,
    reconcile_results: VecDeque<BridgeReconcileResult>,
    close_results: VecDeque<Result<(), CoreError>>,
    fence_acknowledged: bool,
    dispatch_count: usize,
    reconcile_count: usize,
    snapshot_count: usize,
    close_count: usize,
    closed_pages: Vec<(SpaceId, PageId)>,
}

/// Deterministic in-process bridge seam for host and adapter tests.
#[derive(Debug)]
pub struct FakeBridge {
    state: Mutex<FakeState>,
}

impl Default for FakeBridge {
    fn default() -> Self {
        Self::new()
    }
}

impl FakeBridge {
    /// Construct a fake bridge with successful fences and no queued outcomes.
    pub fn new() -> Self {
        Self {
            state: Mutex::new(FakeState {
                capabilities: vec![
                    Capability::Snapshot,
                    Capability::Action,
                    Capability::Wait,
                    Capability::Artifact,
                    Capability::Evaluate,
                    Capability::Reconcile,
                ],
                snapshots: BTreeMap::new(),
                dispatch_results: VecDeque::new(),
                reconcile_results: VecDeque::new(),
                close_results: VecDeque::new(),
                fence_acknowledged: true,
                dispatch_count: 0,
                reconcile_count: 0,
                snapshot_count: 0,
                close_count: 0,
                closed_pages: Vec::new(),
            }),
        }
    }

    /// Restrict the capabilities exposed by this deterministic bridge seam.
    pub fn set_capabilities(&self, capabilities: Vec<Capability>) {
        if let Ok(mut state) = self.state.lock() {
            state.capabilities = capabilities;
        }
    }

    /// Queue a bridge result for the next action dispatch.
    pub fn push_dispatch_result(&self, result: BridgeDispatchResult) {
        if let Ok(mut state) = self.state.lock() {
            state.dispatch_results.push_back(result);
        }
    }

    /// Queue a bridge result for the next reconciliation read.
    pub fn push_reconcile_result(&self, result: BridgeReconcileResult) {
        if let Ok(mut state) = self.state.lock() {
            state.reconcile_results.push_back(result);
        }
    }

    /// Queue a result for the next explicit page close.
    pub fn push_close_result(&self, result: Result<(), CoreError>) {
        if let Ok(mut state) = self.state.lock() {
            state.close_results.push_back(result);
        }
    }

    /// Choose whether takeover fences acknowledge.
    pub fn set_fence_acknowledged(&self, acknowledged: bool) {
        if let Ok(mut state) = self.state.lock() {
            state.fence_acknowledged = acknowledged;
        }
    }

    /// Seed a deterministic logical snapshot.
    pub fn set_snapshot(&self, snapshot: SnapshotEnvelope) {
        if let Ok(mut state) = self.state.lock() {
            state
                .snapshots
                .entry(snapshot.space_id.clone())
                .or_default()
                .insert(snapshot.page_id.clone(), snapshot);
        }
    }

    /// Number of bridge dispatches, excluding read-only reconciliation.
    pub fn dispatch_count(&self) -> usize {
        self.state
            .lock()
            .map(|state| state.dispatch_count)
            .unwrap_or(0)
    }

    /// Number of reconciliation reads.
    pub fn reconcile_count(&self) -> usize {
        self.state
            .lock()
            .map(|state| state.reconcile_count)
            .unwrap_or(0)
    }

    /// Number of snapshot scans requested by the broker.
    pub fn snapshot_scan_count(&self) -> usize {
        self.state
            .lock()
            .map(|state| state.snapshot_count)
            .unwrap_or(0)
    }

    /// Number of explicit single-page close calls.
    pub fn close_count(&self) -> usize {
        self.state
            .lock()
            .map(|state| state.close_count)
            .unwrap_or(0)
    }

    /// Return the logical pages explicitly closed by the host.
    pub fn closed_pages(&self) -> Vec<(SpaceId, PageId)> {
        self.state
            .lock()
            .map(|state| state.closed_pages.clone())
            .unwrap_or_default()
    }
}

impl Bridge for FakeBridge {
    fn capabilities(&self) -> Vec<Capability> {
        self.state
            .lock()
            .map(|state| state.capabilities.clone())
            .unwrap_or_default()
    }

    fn dispatch(
        &self,
        _request: &ActionRequest<BTreeMap<String, String>>,
    ) -> Result<BridgeDispatchResult, CoreError> {
        let mut state = self.state.lock().map_err(|_| {
            CoreError::new(
                ErrorCode::ExtensionNotConnected,
                "fake bridge state poisoned",
            )
        })?;
        state.dispatch_count = state.dispatch_count.saturating_add(1);
        Ok(state
            .dispatch_results
            .pop_front()
            .unwrap_or(BridgeDispatchResult::Succeeded))
    }

    fn reconcile(&self, _receipt: &ActionReceipt) -> Result<BridgeReconcileResult, CoreError> {
        let mut state = self.state.lock().map_err(|_| {
            CoreError::new(
                ErrorCode::ExtensionNotConnected,
                "fake bridge state poisoned",
            )
        })?;
        state.reconcile_count = state.reconcile_count.saturating_add(1);
        Ok(state
            .reconcile_results
            .pop_front()
            .unwrap_or(BridgeReconcileResult::Succeeded))
    }

    fn snapshot(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
    ) -> Result<SnapshotEnvelope, CoreError> {
        let mut state = self.state.lock().map_err(|_| {
            CoreError::new(
                ErrorCode::ExtensionNotConnected,
                "fake bridge state poisoned",
            )
        })?;
        state.snapshot_count = state.snapshot_count.saturating_add(1);
        Ok(state
            .snapshots
            .get(space_id)
            .and_then(|pages| pages.get(page_id))
            .cloned()
            .unwrap_or_else(|| empty_snapshot(space_id.clone(), page_id.clone())))
    }

    fn fence(
        &self,
        _space_id: &SpaceId,
        _old_epoch: Option<LeaseEpoch>,
        _new_epoch: LeaseEpoch,
        _broker_epoch: BrokerEpoch,
    ) -> Result<FenceResult, CoreError> {
        let state = self.state.lock().map_err(|_| {
            CoreError::new(
                ErrorCode::ExtensionNotConnected,
                "fake bridge state poisoned",
            )
        })?;
        Ok(FenceResult {
            acknowledged: state.fence_acknowledged,
        })
    }

    fn close_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        _lease_epoch: LeaseEpoch,
    ) -> Result<(), CoreError> {
        let mut state = self.state.lock().map_err(|_| {
            CoreError::new(
                ErrorCode::ExtensionNotConnected,
                "fake bridge state poisoned",
            )
        })?;
        state.close_count = state.close_count.saturating_add(1);
        let result = state.close_results.pop_front().unwrap_or(Ok(()));
        if result.is_ok() {
            state.closed_pages.push((space_id.clone(), page_id.clone()));
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use agentyc_core::{ActionId, ActionOperation, ContentHash, IdempotencyKey, RequestId};

    #[test]
    fn fake_bridge_is_deterministic_and_logical_only() {
        let bridge = FakeBridge::new();
        bridge.push_dispatch_result(BridgeDispatchResult::Unknown {
            reason: UnknownReason::LostResponse,
        });
        let request = ActionRequest {
            request_id: RequestId::from_suffix("request").expect("request"),
            action_id: ActionId::from_suffix("action").expect("action"),
            idempotency_key: IdempotencyKey::from_suffix("key").expect("key"),
            request_hash: ContentHash::from_bytes(b"request"),
            space_id: SpaceId::from_suffix("space").expect("space"),
            page_id: None,
            lease_epoch: LeaseEpoch::new(1),
            operation: ActionOperation::Wait,
            payload: BTreeMap::new(),
            postcondition: None,
        };
        assert!(matches!(
            bridge.dispatch(&request).expect("dispatch"),
            BridgeDispatchResult::Unknown { .. }
        ));
        assert_eq!(bridge.dispatch_count(), 1);
    }
}
