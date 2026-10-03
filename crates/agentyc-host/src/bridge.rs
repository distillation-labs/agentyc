//! Replaceable logical bridge boundary.
//!
//! No method in this module accepts or returns a Chrome target, tab, session,
//! debugger, or process identifier. The production Native Messaging adapter
//! keeps those values private to the extension and exposes only logical records;
//! [`NullBridge`] and [`FakeBridge`] remain deterministic test/offline seams.

use std::{
    collections::{BTreeMap, VecDeque},
    sync::Mutex,
};

use agentyc_core::{
    ActionReceipt, ActionRequest, BrokerEpoch, Capability, CoreError, ErrorCode, LeaseEpoch,
    PageId, SnapshotEnvelope, SpaceId, UnknownReason,
};
use serde_json::{Map, Value};

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

/// Epochs observed at an extension transport boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExtensionEpochs {
    /// MV3 service-worker instance epoch.
    pub worker_instance_epoch: u64,
    /// Browser/profile session epoch.
    pub browser_session_epoch: u64,
}

const MAX_OBSERVATION_RECORDS: usize = 256;
const MAX_OBSERVATION_RECORD_BYTES: usize = 64 * 1024;
const MAX_OBSERVATION_BYTES: usize = 512 * 1024;
const MAX_OBSERVATION_ID_BYTES: usize = 128;
const MAX_OBSERVATION_HINT_BYTES: usize = 128;
const MAX_OBSERVATION_TEXT_BYTES: usize = 4 * 1024;

const OBSERVATION_FIELDS: &[&str] = &[
    "page_id",
    "space_id",
    "ownership",
    "lifecycle",
    "binding_state",
    "generation",
    "target_generation",
    "navigation_generation",
    "document_generation",
    "lease_epoch",
    "browser_session_epoch",
    "url",
    "title",
    "incognito",
    "discarded",
    "frozen",
    "active",
    "window_hint",
    "tab_hint",
];

/// Sanitize bounded extension observations into logical page records.
///
/// Unknown fields are deliberately discarded instead of forwarded. This keeps
/// browser handles, filesystem paths, and future extension-only fields outside
/// the host protocol even when a bridge implementation returns them.
pub(crate) fn sanitize_observation_records(records: &[Value]) -> Result<Vec<Value>, CoreError> {
    if records.len() > MAX_OBSERVATION_RECORDS {
        return Err(CoreError::new(
            ErrorCode::MessageTooLarge,
            "observation record count exceeds the host bound",
        ));
    }

    let mut sanitized_records = Vec::with_capacity(records.len());
    let mut total_bytes = 2_usize;
    for record in records {
        let object = record.as_object().ok_or_else(|| {
            CoreError::new(
                ErrorCode::InvalidJson,
                "observation record is not a JSON object",
            )
        })?;
        let mut sanitized = Map::new();
        for field in OBSERVATION_FIELDS {
            let Some(value) = object.get(*field) else {
                continue;
            };
            let value = sanitize_observation_field(field, value)?;
            sanitized.insert((*field).to_owned(), value);
        }

        let space_id = sanitized
            .get("space_id")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                CoreError::new(ErrorCode::InvalidJson, "observation space_id is missing")
            })?;
        let page_id = sanitized
            .get("page_id")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                CoreError::new(ErrorCode::InvalidJson, "observation page_id is missing")
            })?;
        let _: SpaceId = space_id.parse().map_err(|_| {
            CoreError::new(ErrorCode::InvalidJson, "observation space_id is invalid")
        })?;
        let _: PageId = page_id.parse().map_err(|_| {
            CoreError::new(ErrorCode::InvalidJson, "observation page_id is invalid")
        })?;

        let value = Value::Object(sanitized);
        let bytes = serde_json::to_vec(&value).map_err(|_| {
            CoreError::new(
                ErrorCode::InvalidJson,
                "observation record could not be encoded",
            )
        })?;
        if bytes.len() > MAX_OBSERVATION_RECORD_BYTES {
            return Err(CoreError::new(
                ErrorCode::MessageTooLarge,
                "observation record exceeds the host bound",
            ));
        }
        total_bytes = total_bytes
            .checked_add(bytes.len().saturating_add(1))
            .ok_or_else(|| {
                CoreError::new(
                    ErrorCode::MessageTooLarge,
                    "observation size exceeds the host bound",
                )
            })?;
        if total_bytes > MAX_OBSERVATION_BYTES {
            return Err(CoreError::new(
                ErrorCode::MessageTooLarge,
                "observation size exceeds the host bound",
            ));
        }
        sanitized_records.push(value);
    }
    Ok(sanitized_records)
}

fn sanitize_observation_field(field: &str, value: &Value) -> Result<Value, CoreError> {
    match field {
        "page_id" | "space_id" => bounded_observation_string(
            value,
            MAX_OBSERVATION_ID_BYTES,
            "observation logical identifier is invalid",
        ),
        "ownership" | "lifecycle" | "binding_state" => bounded_observation_string(
            value,
            MAX_OBSERVATION_HINT_BYTES,
            "observation state is invalid",
        ),
        "url" | "title" => {
            if value.is_null() {
                return Ok(Value::Null);
            }
            let text = bounded_observation_text(
                value,
                MAX_OBSERVATION_TEXT_BYTES,
                "observation text is invalid",
            )?;
            if field == "url"
                && text
                    .as_str()
                    .is_some_and(|url| url.to_ascii_lowercase().starts_with("file:"))
            {
                return Err(CoreError::new(
                    ErrorCode::InvalidJson,
                    "filesystem URLs are not logical observations",
                ));
            }
            Ok(text)
        }
        "window_hint" | "tab_hint" => {
            if value.is_null() {
                return Ok(Value::Null);
            }
            bounded_observation_string(
                value,
                MAX_OBSERVATION_HINT_BYTES,
                "observation browser hint is invalid",
            )
        }
        "generation"
        | "target_generation"
        | "navigation_generation"
        | "document_generation"
        | "lease_epoch"
        | "browser_session_epoch" => value.as_u64().map(Value::from).ok_or_else(|| {
            CoreError::new(
                ErrorCode::InvalidJson,
                "observation generation or epoch is invalid",
            )
        }),
        "incognito" | "discarded" | "frozen" | "active" => value
            .as_bool()
            .map(Value::from)
            .ok_or_else(|| CoreError::new(ErrorCode::InvalidJson, "observation flag is invalid")),
        _ => Err(CoreError::new(
            ErrorCode::InvalidJson,
            "unknown observation field",
        )),
    }
}

fn bounded_observation_string(
    value: &Value,
    max_bytes: usize,
    message: &'static str,
) -> Result<Value, CoreError> {
    let text = value
        .as_str()
        .filter(|text| !text.is_empty() && text.len() <= max_bytes)
        .ok_or_else(|| CoreError::new(ErrorCode::InvalidJson, message))?;
    Ok(Value::String(text.to_owned()))
}

fn bounded_observation_text(
    value: &Value,
    max_bytes: usize,
    message: &'static str,
) -> Result<Value, CoreError> {
    let text = value
        .as_str()
        .filter(|text| text.len() <= max_bytes)
        .ok_or_else(|| CoreError::new(ErrorCode::InvalidJson, message))?;
    Ok(Value::String(text.to_owned()))
}

/// Transport-neutral bridge owned by the host broker.
pub trait Bridge: Send + Sync {
    /// Capabilities currently available through this bridge.
    fn capabilities(&self) -> Vec<Capability>;

    /// Return extension transport epochs when this bridge is Native Messaging.
    /// Other bridge implementations do not have an extension epoch authority.
    fn extension_epochs(&self) -> Option<ExtensionEpochs> {
        None
    }

    /// Dispatch one already-admitted logical action.
    fn dispatch(
        &self,
        request: &ActionRequest<BTreeMap<String, String>>,
    ) -> Result<BridgeDispatchResult, CoreError>;

    /// Reconcile an unknown receipt with a read-only proof operation.
    fn reconcile(&self, receipt: &ActionReceipt) -> Result<BridgeReconcileResult, CoreError>;

    /// Observe bounded logical page records from the browser bridge.
    ///
    /// Bridges without a live browser inventory inherit an empty observation;
    /// callers still apply the host-side logical-record bounds and scope.
    fn observe(&self) -> Result<Vec<Value>, CoreError> {
        Ok(Vec::new())
    }

    /// Read a logical snapshot for a page under the requesting lease epoch.
    fn snapshot(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
    ) -> Result<SnapshotEnvelope, CoreError>;

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

    /// Create one inactive, host-authorized logical page in the browser.
    fn create_page(
        &self,
        _space_id: &SpaceId,
        _page_id: &PageId,
        _lease_epoch: LeaseEpoch,
        _url: Option<&str>,
        _title: Option<&str>,
        _ownership_proof: Value,
    ) -> Result<Value, CoreError> {
        Err(CoreError::new(
            ErrorCode::CapabilityUnavailable,
            "bridge does not support managed page creation",
        ))
    }

    /// Present one managed page in its space's visual tab group.
    fn present_group(
        &self,
        _space_id: &SpaceId,
        _page_id: &PageId,
        _lease_epoch: LeaseEpoch,
        _title: Option<&str>,
    ) -> Result<Value, CoreError> {
        Err(CoreError::new(
            ErrorCode::CapabilityUnavailable,
            "bridge does not support visual group presentation",
        ))
    }
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
        _lease_epoch: LeaseEpoch,
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
    observations: Vec<Value>,
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
                observations: Vec::new(),
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

    /// Seed bounded bridge observations for protocol tests.
    pub fn set_observation(&self, observations: Vec<Value>) {
        if let Ok(mut state) = self.state.lock() {
            state.observations = observations;
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

    fn observe(&self) -> Result<Vec<Value>, CoreError> {
        self.state
            .lock()
            .map(|state| state.observations.clone())
            .map_err(|_| {
                CoreError::new(
                    ErrorCode::ExtensionNotConnected,
                    "fake bridge state poisoned",
                )
            })
    }

    fn snapshot(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        _lease_epoch: LeaseEpoch,
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
    fn observation_sanitizer_keeps_only_bounded_logical_fields() {
        let records = vec![serde_json::json!({
            "space_id": "space_one",
            "page_id": "page_one",
            "ownership": "agent",
            "lifecycle": "managed",
            "binding_state": "bound",
            "target_generation": 2,
            "url": "https://example.test/private",
            "tab_hint": "hint_test",
            "tab_id": 42,
            "path": "/private/browser-profile",
            "nested": {"target_id": "raw"},
        })];
        let sanitized = sanitize_observation_records(&records).expect("sanitized records");
        assert_eq!(sanitized.len(), 1);
        let record = sanitized[0].as_object().expect("record object");
        assert_eq!(
            record.get("space_id"),
            Some(&serde_json::json!("space_one"))
        );
        assert!(record.get("tab_id").is_none());
        assert!(record.get("path").is_none());
        assert!(record.get("nested").is_none());
    }

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
