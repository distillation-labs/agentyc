//! Host-backed MCP compatibility adapter.
//!
//! This module is deliberately separate from [`crate::BrowserServer`]. The
//! legacy server keeps its existing CDP/runtime tools and transport behavior;
//! this adapter exposes the host broker's logical contracts as stable MCP tool
//! results without adding those operations to the legacy default router.
//!
//! Every operation below uses the host-issued authority returned by
//! [`Broker::hello`]. Callers provide only validated logical identities such as
//! `space_*`, `page_*`, and `action_*`; browser target, session, tab, and node
//! identities are not an input to this boundary.

use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
};

use agentyc_core::{
    ActionId, ActionOperation, ActionReceipt, ActionRequest, CacheState, ContentHash, CoreError,
    ErrorCode, EventCursor, EventKind, EventScope, HelloEnvelope, HelloOkEnvelope, IdempotencyKey,
    LeaseEpoch, PageId, Postcondition, PrincipalId, RequestId, ResumeResult, SnapshotEnvelope,
    SpaceId, Timestamp,
};
use agentyc_host::{
    ActionResult, AuthorityTicket, Broker, Connection, ControlReturn, ControlTicket, EventBatch,
    EventQuery, HostError, LeaseGrant, TakeoverResult, canonical_action_hash,
};
use rmcp::model::CallToolResult;
use serde::Serialize;
use serde_json::{Value, json};

/// A host-backed MCP compatibility adapter for one admitted connection.
///
/// The adapter is not an MCP transport or a tool router. It is a small
/// transport-independent boundary that can be called by a future stdio or HTTP
/// MCP surface. [`BrowserServer`](crate::BrowserServer) remains the legacy
/// default server and does not install these operations in its tool list.
#[derive(Clone)]
pub struct HostAdapter {
    broker: Broker,
    connection: Connection,
    control_tickets: Arc<Mutex<BTreeMap<SpaceId, ControlTicket>>>,
}

impl std::fmt::Debug for HostAdapter {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("HostAdapter")
            .field("broker", &self.broker)
            .field("connection", &self.connection)
            .finish_non_exhaustive()
    }
}

impl HostAdapter {
    /// Admit one adapter connection through the host handshake.
    ///
    /// The returned connection contains the host-issued authority used by every
    /// subsequent method. The caller cannot provide an authority ticket to an
    /// operation, so a connection cannot accidentally act as another principal.
    pub fn with_host(broker: Broker, hello: HelloEnvelope) -> Result<Self, HostError> {
        let connection = broker.hello(&hello)?;
        Ok(Self {
            broker,
            connection,
            control_tickets: Arc::new(Mutex::new(BTreeMap::new())),
        })
    }

    /// Alias for [`Self::with_host`].
    pub fn new(broker: Broker, hello: HelloEnvelope) -> Result<Self, HostError> {
        Self::with_host(broker, hello)
    }

    /// Return the host-admitted connection metadata.
    pub const fn connection(&self) -> &Connection {
        &self.connection
    }

    /// Return the principal bound to this adapter connection.
    pub fn principal_id(&self) -> &PrincipalId {
        &self.connection.principal_id
    }

    /// Return the host's handshake acknowledgement for this adapter connection.
    pub fn hello_ok(&self) -> HelloOkEnvelope {
        self.connection.hello_ok()
    }

    fn authority(&self) -> &AuthorityTicket {
        self.connection.authority()
    }

    /// Parse one logical space identity at the MCP boundary.
    pub fn logical_space_id(value: &str) -> Result<SpaceId, CoreError> {
        SpaceId::new(value).map_err(|error| invalid_logical_id("space_id", error))
    }

    /// Parse one logical page identity at the MCP boundary.
    pub fn logical_page_id(value: &str) -> Result<PageId, CoreError> {
        PageId::new(value).map_err(|error| invalid_logical_id("page_id", error))
    }

    /// Parse one logical action identity at the MCP boundary.
    pub fn logical_action_id(value: &str) -> Result<ActionId, CoreError> {
        ActionId::new(value).map_err(|error| invalid_logical_id("action_id", error))
    }

    /// Parse one logical request identity at the MCP boundary.
    pub fn logical_request_id(value: &str) -> Result<RequestId, CoreError> {
        RequestId::new(value).map_err(|error| invalid_logical_id("request_id", error))
    }

    /// Parse one logical idempotency identity at the MCP boundary.
    pub fn logical_idempotency_key(value: &str) -> Result<IdempotencyKey, CoreError> {
        IdempotencyKey::new(value).map_err(|error| invalid_logical_id("idempotency_key", error))
    }

    /// Build a logical event scope, requiring a space when a page is supplied.
    pub fn logical_event_scope(
        space_id: Option<&str>,
        page_id: Option<&str>,
    ) -> Result<EventScope, CoreError> {
        let space_id = space_id.map(Self::logical_space_id).transpose()?;
        let page_id = page_id.map(Self::logical_page_id).transpose()?;
        if page_id.is_some() && space_id.is_none() {
            return Err(CoreError::invalid_argument(
                "page_id requires its logical space_id",
            ));
        }
        Ok(EventScope { space_id, page_id })
    }

    /// List spaces visible to this adapter connection.
    pub fn list_spaces(&self) -> CallToolResult {
        host_result(self.broker.list_spaces(self.authority()))
    }

    /// Create a host-assigned logical space.
    pub fn create_space(&self, label: impl Into<String>) -> CallToolResult {
        host_result(self.broker.create_space(self.authority(), label))
    }

    /// Describe one logical space visible to this connection.
    pub fn describe_space(&self, space_id: &SpaceId) -> CallToolResult {
        host_result(self.broker.describe_space(self.authority(), space_id))
    }

    /// Describe a logical space supplied as a validated wire identity.
    pub fn describe_space_id(&self, space_id: &str) -> CallToolResult {
        match Self::logical_space_id(space_id) {
            Ok(space_id) => self.describe_space(&space_id),
            Err(error) => error_result(error, None),
        }
    }

    /// List logical pages in one visible space.
    pub fn list_pages(&self, space_id: &SpaceId) -> CallToolResult {
        host_result(
            self.broker
                .describe_space(self.authority(), space_id)
                .map(|space| {
                    json!({
                        "space_id": space.space_id,
                        "pages": space.pages,
                    })
                }),
        )
    }

    /// Finish an agent-owned logical space after page cleanup.
    pub fn finish_space(
        &self,
        space_id: &SpaceId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        host_result(
            self.broker
                .finish_space(space_id, self.authority(), lease_epoch, now),
        )
    }

    /// Release a finished logical space without closing pages implicitly.
    pub fn release_space(
        &self,
        space_id: &SpaceId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        host_result(
            self.broker
                .release_space(space_id, self.authority(), lease_epoch, now),
        )
    }

    /// Acquire a lease for one logical space.
    pub fn acquire_lease(&self, space_id: &SpaceId, now: Timestamp, ttl: u64) -> CallToolResult {
        lease_result(
            self.broker
                .acquire_lease(space_id, self.authority(), now, ttl),
        )
    }

    /// Renew a lease without changing its fencing epoch.
    pub fn renew_lease(
        &self,
        space_id: &SpaceId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
        ttl: u64,
    ) -> CallToolResult {
        lease_result(
            self.broker
                .renew_lease(space_id, self.authority(), lease_epoch, now, ttl),
        )
    }

    /// Add a planned logical page to a leased space.
    pub fn create_page(
        &self,
        space_id: &SpaceId,
        lease_epoch: LeaseEpoch,
        label: impl Into<String>,
    ) -> CallToolResult {
        host_result(
            self.broker
                .create_page(space_id, self.authority(), lease_epoch, label),
        )
    }

    /// Add a planned logical page with an explicit authorization timestamp.
    pub fn create_page_at(
        &self,
        space_id: &SpaceId,
        lease_epoch: LeaseEpoch,
        label: impl Into<String>,
        now: Timestamp,
    ) -> CallToolResult {
        host_result(
            self.broker
                .create_page_at(space_id, self.authority(), lease_epoch, label, now),
        )
    }

    /// Bind a logical page using only logical metadata.
    #[allow(clippy::too_many_arguments)]
    pub fn bind_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
        url: Option<String>,
        title: Option<String>,
        frame_count: u32,
    ) -> CallToolResult {
        host_result(self.broker.bind_page(
            space_id,
            page_id,
            self.authority(),
            lease_epoch,
            now,
            url,
            title,
            frame_count,
        ))
    }

    /// Mark a logical page binding as lost.
    pub fn mark_page_lost(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        host_result(self.broker.mark_page_lost(
            space_id,
            page_id,
            self.authority(),
            lease_epoch,
            now,
        ))
    }

    /// Close one explicitly authorized logical page.
    pub fn close_page(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        host_result(
            self.broker
                .close_page(space_id, page_id, self.authority(), lease_epoch, now),
        )
    }

    /// Read a clean logical snapshot or perform one host-controlled scan.
    pub fn read_snapshot(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        snapshot_read_result(self.broker.read_snapshot(
            space_id,
            page_id,
            self.authority(),
            lease_epoch,
            now,
        ))
    }

    /// Seed a validated logical snapshot cache entry.
    pub fn put_snapshot(
        &self,
        envelope: SnapshotEnvelope,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        match self
            .broker
            .put_snapshot(self.authority(), envelope, lease_epoch, now)
        {
            Ok(()) => success_value(json!({ "stored": true })),
            Err(error) => error_result(error.as_core_error(), None),
        }
    }

    /// Mark one logical snapshot cache entry dirty without scanning.
    pub fn mark_snapshot_dirty(
        &self,
        space_id: &SpaceId,
        page_id: &PageId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        host_result(self.broker.mark_snapshot_dirty(
            self.authority(),
            space_id,
            page_id,
            lease_epoch,
            now,
        ))
    }

    /// Build a canonical action request from validated logical identities.
    #[allow(clippy::too_many_arguments)]
    pub fn build_action_request(
        request_id: RequestId,
        action_id: ActionId,
        idempotency_key: IdempotencyKey,
        space_id: SpaceId,
        page_id: Option<PageId>,
        lease_epoch: LeaseEpoch,
        operation: ActionOperation,
        payload: BTreeMap<String, String>,
        postcondition: Option<Postcondition>,
    ) -> Result<ActionRequest<BTreeMap<String, String>>, CoreError> {
        let mut request = ActionRequest {
            request_id,
            action_id,
            idempotency_key,
            request_hash: ContentHash::from_bytes(b"adapter-request"),
            space_id,
            page_id,
            lease_epoch,
            operation,
            payload,
            postcondition,
        };
        request.request_hash =
            canonical_action_hash(&request).map_err(|error| error.as_core_error())?;
        Ok(request)
    }

    /// Build an action request while parsing only logical wire identities.
    #[allow(clippy::too_many_arguments)]
    pub fn build_action_request_from_logical_ids(
        request_id: &str,
        action_id: &str,
        idempotency_key: &str,
        space_id: &str,
        page_id: Option<&str>,
        lease_epoch: u64,
        operation: ActionOperation,
        payload: BTreeMap<String, String>,
        postcondition: Option<Postcondition>,
    ) -> Result<ActionRequest<BTreeMap<String, String>>, CoreError> {
        Self::build_action_request(
            Self::logical_request_id(request_id)?,
            Self::logical_action_id(action_id)?,
            Self::logical_idempotency_key(idempotency_key)?,
            Self::logical_space_id(space_id)?,
            page_id.map(Self::logical_page_id).transpose()?,
            LeaseEpoch::new(lease_epoch),
            operation,
            payload,
            postcondition,
        )
    }

    /// Admit an action into the host journal.
    pub fn enqueue_action(
        &self,
        request: ActionRequest<BTreeMap<String, String>>,
        now: Timestamp,
    ) -> CallToolResult {
        match self.broker.enqueue_action(request, self.authority(), now) {
            Ok(receipt) => action_receipt_result(receipt),
            Err(error) => error_result(error.as_core_error(), None),
        }
    }

    /// Dispatch one admitted action without replaying an unknown action.
    pub fn dispatch_action(
        &self,
        action_id: &ActionId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        action_result(
            self.broker
                .dispatch_action(action_id, self.authority(), lease_epoch, now),
        )
    }

    /// Admit and dispatch one action through the host bridge boundary.
    pub fn execute_action(
        &self,
        request: ActionRequest<BTreeMap<String, String>>,
        now: Timestamp,
    ) -> CallToolResult {
        action_result(self.broker.execute_action(request, self.authority(), now))
    }

    /// Read an action receipt without dispatching it.
    pub fn action_status(&self, action_id: &ActionId) -> CallToolResult {
        match self.broker.action_status(self.authority(), action_id) {
            Ok(receipt) => action_receipt_result(receipt),
            Err(error) => error_result(error.as_core_error(), None),
        }
    }

    /// Reconcile an unknown action through a read-only host bridge operation.
    pub fn reconcile_action(
        &self,
        action_id: &ActionId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        action_result(
            self.broker
                .reconcile_action(action_id, self.authority(), lease_epoch, now),
        )
    }

    /// Publish a principal-scoped logical event.
    pub fn publish_event(
        &self,
        scope: EventScope,
        event: EventKind,
        payload: BTreeMap<String, String>,
    ) -> CallToolResult {
        host_result(
            self.broker
                .publish_event(self.authority(), scope, event, payload),
        )
    }

    /// Return the current event cursor for this connection's principal.
    pub fn event_cursor(&self) -> CallToolResult {
        host_result(self.broker.event_cursor(self.authority()))
    }

    /// Resume principal-visible events after a logical cursor.
    pub fn resume_events(&self, query: EventQuery) -> CallToolResult {
        match self.broker.resume_events(self.authority(), query) {
            Ok(batch) => event_batch_result(batch),
            Err(error) => error_result(error.as_core_error(), None),
        }
    }

    /// Resume all principal-visible events after a cursor.
    pub fn resume_events_from(
        &self,
        cursor: EventCursor,
        scope: Option<EventScope>,
    ) -> CallToolResult {
        self.resume_events(EventQuery {
            after: cursor,
            scope,
        })
    }

    /// Return a logical space to explicit user control.
    pub fn return_control(
        &self,
        space_id: &SpaceId,
        lease_epoch: LeaseEpoch,
        now: Timestamp,
    ) -> CallToolResult {
        match self
            .broker
            .return_control(space_id, self.authority(), lease_epoch, now)
        {
            Ok(result) => control_return_result(self, result),
            Err(error) => error_result(error.as_core_error(), None),
        }
    }

    /// Read safe control-ticket metadata without exposing its opaque token.
    pub fn control_ticket(&self, space_id: &SpaceId) -> CallToolResult {
        match self.broker.control_ticket(self.authority(), space_id) {
            Ok(ticket) => {
                if let Err(error) = self.remember_control_ticket(&ticket) {
                    return error_result(error, None);
                }
                success_value(control_ticket_value(&ticket))
            }
            Err(error) => error_result(error.as_core_error(), None),
        }
    }

    /// Reclaim a user-owned space with a ticket previously obtained by this adapter.
    pub fn takeover_with_control_ticket(
        &self,
        space_id: &SpaceId,
        now: Timestamp,
        ttl: u64,
    ) -> CallToolResult {
        let ticket = match self.control_ticket_for(space_id) {
            Ok(Some(ticket)) => ticket,
            Ok(None) => {
                return error_result(
                    CoreError::new(
                        ErrorCode::UserControlRequired,
                        "an explicit control ticket is required before takeover",
                    ),
                    None,
                );
            }
            Err(error) => return error_result(error, None),
        };
        match self.broker.takeover_with_control_ticket(
            space_id,
            self.authority(),
            &ticket,
            now,
            ttl,
        ) {
            Ok(result) => {
                self.remove_control_ticket(space_id);
                takeover_result(Ok(result))
            }
            Err(error) => error_result(error.as_core_error(), None),
        }
    }

    /// Take over a non-user-owned logical space through the host fence protocol.
    pub fn takeover(&self, space_id: &SpaceId, now: Timestamp, ttl: u64) -> CallToolResult {
        takeover_result(self.broker.takeover(space_id, self.authority(), now, ttl))
    }

    /// Retry a pending user-control fence without allocating another epoch.
    pub fn acknowledge_return_control(
        &self,
        space_id: &SpaceId,
        fence_epoch: LeaseEpoch,
    ) -> CallToolResult {
        match self
            .broker
            .acknowledge_return_control(space_id, self.authority(), fence_epoch)
        {
            Ok(result) => control_return_result(self, result),
            Err(error) => error_result(error.as_core_error(), None),
        }
    }

    /// Acknowledge a pending host fence.
    pub fn acknowledge_fence(&self, space_id: &SpaceId, lease_epoch: LeaseEpoch) -> CallToolResult {
        takeover_result(
            self.broker
                .acknowledge_fence(space_id, self.authority(), lease_epoch),
        )
    }

    fn remember_control_ticket(&self, ticket: &ControlTicket) -> Result<(), CoreError> {
        self.control_tickets
            .lock()
            .map_err(|_| {
                CoreError::new(
                    ErrorCode::LedgerIncompatible,
                    "control-ticket state is poisoned",
                )
            })?
            .insert(ticket.space_id().clone(), ticket.clone());
        Ok(())
    }

    fn control_ticket_for(&self, space_id: &SpaceId) -> Result<Option<ControlTicket>, CoreError> {
        Ok(self
            .control_tickets
            .lock()
            .map_err(|_| {
                CoreError::new(
                    ErrorCode::LedgerIncompatible,
                    "control-ticket state is poisoned",
                )
            })?
            .get(space_id)
            .cloned())
    }

    fn remove_control_ticket(&self, space_id: &SpaceId) {
        if let Ok(mut tickets) = self.control_tickets.lock() {
            tickets.remove(space_id);
        }
    }
}

fn invalid_logical_id(field: &str, error: impl std::fmt::Display) -> CoreError {
    CoreError::invalid_argument(format!(
        "{field} must be a validated logical identity: {error}"
    ))
}

fn success_value(value: Value) -> CallToolResult {
    CallToolResult::structured(json!({
        "ok": true,
        "result": value,
    }))
}

pub(crate) fn error_result(error: CoreError, details: Option<Value>) -> CallToolResult {
    let mut body = json!({
        "ok": false,
        "error": {
            "code": error.code.as_str(),
            "retryable": error.retryable,
            "guidance": error.guidance,
            "message": error.message,
        },
    });
    if let Some(details) = details
        && let Some(error_object) = body.get_mut("error").and_then(Value::as_object_mut)
    {
        error_object.insert("details".to_owned(), details);
    }
    CallToolResult::structured_error(body)
}

fn host_result<T: Serialize>(result: Result<T, HostError>) -> CallToolResult {
    match result {
        Ok(value) => match serde_json::to_value(value) {
            Ok(value) => success_value(value),
            Err(error) => error_result(
                CoreError::new(
                    ErrorCode::InvalidJson,
                    format!("could not encode result: {error}"),
                ),
                None,
            ),
        },
        Err(error) => error_result(error.as_core_error(), None),
    }
}

fn snapshot_read_result(result: Result<agentyc_host::SnapshotRead, HostError>) -> CallToolResult {
    match result {
        Ok(read) => {
            let mut envelope = match serde_json::to_value(&read.envelope) {
                Ok(value) => value,
                Err(error) => {
                    return error_result(
                        CoreError::new(
                            ErrorCode::InvalidJson,
                            format!("could not encode snapshot envelope: {error}"),
                        ),
                        None,
                    );
                }
            };
            if read.cache_state == CacheState::Cached && !read.scan_performed {
                let Some(envelope_object) = envelope.as_object_mut() else {
                    return error_result(
                        CoreError::new(
                            ErrorCode::InvalidJson,
                            "snapshot envelope did not serialize as an object",
                        ),
                        None,
                    );
                };
                envelope_object.remove("delta_or_elements");
            }
            success_value(json!({
                "envelope": envelope,
                "cache_state": read.cache_state,
                "scan_performed": read.scan_performed,
            }))
        }
        Err(error) => error_result(error.as_core_error(), None),
    }
}

fn lease_result(result: Result<LeaseGrant, HostError>) -> CallToolResult {
    match result {
        Ok(grant) => success_value(json!({
            "space_id": grant.space_id,
            "lease": grant.lease,
        })),
        Err(error) => error_result(error.as_core_error(), None),
    }
}

fn action_result(result: Result<ActionResult, HostError>) -> CallToolResult {
    match result {
        Ok(result) => action_receipt_result(result.receipt),
        Err(error) => error_result(error.as_core_error(), None),
    }
}

fn action_receipt_result(receipt: ActionReceipt) -> CallToolResult {
    let receipt_value = match serde_json::to_value(&receipt) {
        Ok(value) => value,
        Err(error) => {
            return error_result(
                CoreError::new(
                    ErrorCode::InvalidJson,
                    format!("could not encode action receipt: {error}"),
                ),
                None,
            );
        }
    };
    if let Some(code) = receipt.error_code {
        return action_error_result(
            CoreError {
                code,
                retryable: receipt.retryable,
                guidance: code.guidance(),
                message: format!("logical action completed with {}", code.as_str()),
            },
            receipt_value,
        );
    }
    success_value(json!({ "receipt": receipt_value }))
}

fn action_error_result(error: CoreError, receipt_value: Value) -> CallToolResult {
    let action_id = receipt_value
        .get("action_id")
        .cloned()
        .unwrap_or(Value::Null);
    let reconcile_token = receipt_value
        .get("reconcile_token")
        .cloned()
        .unwrap_or(Value::Null);
    let next_action = receipt_value
        .get("next_action")
        .cloned()
        .unwrap_or(Value::Null);
    let mut result = error_result(error, Some(json!({ "receipt": receipt_value })));
    if let Some(structured_content) = result.structured_content.as_mut()
        && let Some(error_object) = structured_content
            .get_mut("error")
            .and_then(Value::as_object_mut)
    {
        error_object.insert("action_id".to_owned(), action_id);
        error_object.insert("reconcile_token".to_owned(), reconcile_token);
        error_object.insert("next_action".to_owned(), next_action);
    }
    result
}

fn event_batch_result(batch: EventBatch) -> CallToolResult {
    let value = event_batch_value(&batch);
    if batch.result == ResumeResult::ResyncRequired {
        return error_result(
            CoreError::new(
                ErrorCode::EventLagged,
                "event cursor is no longer retained; request a fresh logical snapshot",
            ),
            Some(json!({ "batch": value })),
        );
    }
    success_value(value)
}

fn event_batch_value(batch: &EventBatch) -> Value {
    json!({
        "broker_epoch": batch.broker_epoch,
        "result": batch.result,
        "events": batch.events,
        "cursor": batch.cursor,
    })
}

fn control_return_result(adapter: &HostAdapter, result: ControlReturn) -> CallToolResult {
    if let Err(error) = adapter.remember_control_ticket(&result.control_ticket) {
        return error_result(error, None);
    }
    success_value(json!({
        "space_id": result.space_id,
        "released_epoch": result.released_epoch,
        "fence_epoch": result.fence_epoch,
        "lifecycle": result.lifecycle,
        "control_ticket": control_ticket_value(&result.control_ticket),
    }))
}

fn control_ticket_value(ticket: &ControlTicket) -> Value {
    json!({
        "space_id": ticket.space_id(),
        "broker_epoch": ticket.broker_epoch(),
        "fence_epoch": ticket.fence_epoch(),
        "opaque": true,
    })
}

fn takeover_result(result: Result<TakeoverResult, HostError>) -> CallToolResult {
    match result {
        Ok(result) => success_value(json!({
            "space_id": result.space_id,
            "lease_epoch": result.lease_epoch,
            "fence_acknowledged": result.fence_acknowledged,
            "lifecycle": result.lifecycle,
        })),
        Err(error) => error_result(error.as_core_error(), None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    use agentyc_core::{
        BrokerEpoch, ClientMetadata, ConnectionNonce, EventSequence, PROTOCOL_VERSION,
    };
    use agentyc_host::{BridgeDispatchResult, BridgeReconcileResult, FakeBridge, Ledger};
    use tempfile::{TempDir, tempdir};

    fn hello(nonce: &str, principal: &str) -> HelloEnvelope {
        HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix(principal).expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("mcp-host-adapter-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(ConnectionNonce::from_suffix(nonce).expect("nonce")),
                profile_binding_id: None,
            }),
        }
    }

    fn adapter_with_bridge(bridge: Arc<FakeBridge>) -> (TempDir, Broker, HostAdapter) {
        let directory = tempdir().expect("tempdir");
        let ledger = Ledger::open(directory.path()).expect("ledger");
        let broker = Broker::with_shared_bridge(ledger, bridge);
        let adapter = HostAdapter::with_host(broker.clone(), hello("one", "adapter"))
            .expect("host admission");
        (directory, broker, adapter)
    }

    fn code(result: &CallToolResult) -> &str {
        result
            .structured_content
            .as_ref()
            .and_then(|value| value.get("error"))
            .and_then(|value| value.get("code"))
            .and_then(Value::as_str)
            .expect("structured error code")
    }

    fn setup_space(broker: &Broker, adapter: &HostAdapter, label: &str) -> (SpaceId, LeaseEpoch) {
        let space = broker
            .create_space(adapter.connection().authority(), label)
            .expect("space");
        let lease = broker
            .acquire_lease(
                &space.space_id,
                adapter.connection().authority(),
                Timestamp::new(1),
                100,
            )
            .expect("lease");
        (space.space_id, lease.lease.lease_epoch)
    }

    #[test]
    fn host_connection_scopes_calls_and_rejects_non_logical_ids() {
        let bridge = Arc::new(FakeBridge::new());
        let (_directory, _broker, adapter) = adapter_with_bridge(bridge);

        assert_eq!(adapter.principal_id().as_str(), "principal_adapter");
        assert_eq!(adapter.connection().connection_epoch.get(), 1);

        let result = adapter.describe_space_id("[id]");
        assert_eq!(result.is_error, Some(true));
        assert_eq!(code(&result), "invalid_argument");
        let wire = serde_json::to_value(&result).expect("MCP result serializes");
        assert_eq!(wire["isError"], true);
        assert_eq!(
            wire["structuredContent"]["error"]["code"],
            "invalid_argument"
        );
    }

    #[test]
    fn two_spaces_cannot_cross_resolve_logical_pages() {
        let bridge = Arc::new(FakeBridge::new());
        let (_directory, broker, adapter) = adapter_with_bridge(bridge);
        let (space_one, lease_one) = setup_space(&broker, &adapter, "one");
        let (space_two, lease_two) = setup_space(&broker, &adapter, "two");
        let page = broker
            .create_page_at(
                &space_one,
                adapter.connection().authority(),
                lease_one,
                "one-page",
                Timestamp::new(1),
            )
            .expect("page");

        let result = adapter.bind_page(
            &space_two,
            &page.page_id,
            lease_two,
            Timestamp::new(1),
            Some("https://logical.test".to_owned()),
            None,
            1,
        );
        assert_eq!(result.is_error, Some(true));
        assert_eq!(code(&result), "page_not_found");
    }

    #[test]
    fn clean_cached_snapshot_result_is_metadata_only() {
        let bridge = Arc::new(FakeBridge::new());
        let (_directory, broker, adapter) = adapter_with_bridge(bridge);
        let (space_id, lease_epoch) = setup_space(&broker, &adapter, "snapshots");
        let page = broker
            .create_page_at(
                &space_id,
                adapter.connection().authority(),
                lease_epoch,
                "main",
                Timestamp::new(1),
            )
            .expect("page");
        broker
            .bind_page(
                &space_id,
                &page.page_id,
                adapter.connection().authority(),
                lease_epoch,
                Timestamp::new(1),
                Some("https://logical.test".to_owned()),
                Some("Logical test".to_owned()),
                1,
            )
            .expect("managed page");

        let fresh = adapter.read_snapshot(&space_id, &page.page_id, lease_epoch, Timestamp::new(2));
        assert_eq!(fresh.is_error, Some(false));
        assert!(
            fresh
                .structured_content
                .as_ref()
                .and_then(|value| value.get("result"))
                .and_then(|value| value.get("envelope"))
                .and_then(|value| value.get("delta_or_elements"))
                .is_some()
        );

        let cached =
            adapter.read_snapshot(&space_id, &page.page_id, lease_epoch, Timestamp::new(2));
        assert_eq!(cached.is_error, Some(false));
        let cached_wire = serde_json::to_value(&cached).expect("cached MCP result");
        assert_eq!(cached_wire["isError"], false);
        let result = cached
            .structured_content
            .as_ref()
            .and_then(|value| value.get("result"))
            .expect("snapshot result");
        assert_eq!(
            result.get("cache_state").and_then(Value::as_str),
            Some("cached")
        );
        assert_eq!(result.get("scan_performed"), Some(&Value::Bool(false)));
        let envelope = result.get("envelope").expect("snapshot envelope");
        assert!(envelope.get("delta_or_elements").is_none());
        assert!(envelope.get("snapshot_hash").is_some());
        assert!(envelope.get("document_generation").is_some());
        assert!(envelope.get("cache_state").is_some());
    }

    #[test]
    fn unknown_outcome_is_an_error_and_reconcile_never_replays_dispatch() {
        let bridge = Arc::new(FakeBridge::new());
        bridge.push_dispatch_result(BridgeDispatchResult::Unknown {
            reason: agentyc_core::UnknownReason::LostResponse,
        });
        bridge.push_reconcile_result(BridgeReconcileResult::Succeeded);
        let (_directory, broker, adapter) = adapter_with_bridge(bridge.clone());
        let (space_id, lease_epoch) = setup_space(&broker, &adapter, "actions");
        let request = HostAdapter::build_action_request(
            RequestId::from_suffix("request").expect("request"),
            ActionId::from_suffix("action").expect("action"),
            IdempotencyKey::from_suffix("key").expect("key"),
            space_id,
            None,
            lease_epoch,
            ActionOperation::Wait,
            BTreeMap::new(),
            None,
        )
        .expect("request");

        let unknown = adapter.execute_action(request, Timestamp::new(2));
        assert_eq!(unknown.is_error, Some(true));
        assert_eq!(code(&unknown), "unknown_outcome");
        let unknown_wire = serde_json::to_value(&unknown).expect("unknown MCP result");
        assert_eq!(unknown_wire["isError"], true);
        let unknown_error = unknown
            .structured_content
            .as_ref()
            .and_then(|value| value.get("error"))
            .expect("unknown action error");
        let receipt = unknown_error
            .get("details")
            .and_then(|value| value.get("receipt"))
            .expect("unknown action receipt");
        assert_eq!(unknown_error.get("action_id"), receipt.get("action_id"));
        assert_eq!(
            unknown_error.get("reconcile_token"),
            receipt.get("reconcile_token")
        );
        assert_eq!(unknown_error.get("next_action"), Some(&json!("reconcile")));
        assert_eq!(receipt.get("next_action"), Some(&json!("reconcile")));

        let action_id = ActionId::from_suffix("action").expect("action");
        let reconciled = adapter.reconcile_action(&action_id, lease_epoch, Timestamp::new(3));
        assert_eq!(reconciled.is_error, Some(false));
        assert_eq!(
            reconciled
                .structured_content
                .as_ref()
                .and_then(|value| value.get("result"))
                .and_then(|value| value.get("receipt"))
                .and_then(|value| value.get("status"))
                .and_then(Value::as_str),
            Some("succeeded")
        );
        assert_eq!(bridge.dispatch_count(), 1);
        assert_eq!(bridge.reconcile_count(), 1);
    }

    #[test]
    fn event_resume_returns_events_and_marks_lagged_cursors_for_resync() {
        let bridge = Arc::new(FakeBridge::new());
        let (_directory, broker, adapter) = adapter_with_bridge(bridge);
        let (space_id, _lease_epoch) = setup_space(&broker, &adapter, "events");
        let before = broker
            .event_cursor(adapter.connection().authority())
            .expect("cursor");
        let published = adapter.publish_event(
            EventScope::space(space_id),
            EventKind::ConnectionChanged,
            BTreeMap::new(),
        );
        assert_eq!(published.is_error, Some(false));

        let resumed = adapter.resume_events(EventQuery::all(before));
        assert_eq!(resumed.is_error, Some(false));
        assert_eq!(
            resumed
                .structured_content
                .as_ref()
                .and_then(|value| value.get("result"))
                .and_then(|value| value.get("events"))
                .and_then(Value::as_array)
                .map(Vec::len),
            Some(1)
        );

        let lagged = adapter.resume_events_from(
            EventCursor {
                broker_epoch: BrokerEpoch::new(0),
                sequence: EventSequence::new(0),
            },
            None,
        );
        assert_eq!(lagged.is_error, Some(true));
        assert_eq!(code(&lagged), "event_lagged");
    }

    #[test]
    fn user_control_is_explicit_and_ticket_reclaim_is_connection_scoped() {
        let bridge = Arc::new(FakeBridge::new());
        let (_directory, broker, adapter) = adapter_with_bridge(bridge);
        let (space_id, lease_epoch) = setup_space(&broker, &adapter, "control");

        let returned = adapter.return_control(&space_id, lease_epoch, Timestamp::new(2));
        assert_eq!(returned.is_error, Some(false));

        let blocked = adapter.acquire_lease(&space_id, Timestamp::new(3), 100);
        assert_eq!(blocked.is_error, Some(true));
        assert_eq!(code(&blocked), "user_control_required");

        let reclaimed = adapter.takeover_with_control_ticket(&space_id, Timestamp::new(4), 100);
        assert_eq!(reclaimed.is_error, Some(false));
    }
}
