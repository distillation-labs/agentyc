//! Host-backed MCP stdio service for logical browser task spaces.
//!
//! It accepts only logical identities and delegates authorization, lifecycle,
//! lease, snapshot, action, and event behavior to [`HostAdapter`].

use std::collections::BTreeMap;

use agentyc_core::{
    ActionOperation, ActionRequest, ContentHash, CoreError, EventCursor, EventKind, Generation,
    HelloEnvelope, LeaseEpoch, Postcondition, ProfileDisclosure, SnapshotEnvelope, Timestamp,
};
use agentyc_host::{Broker, EventQuery, HostError};
use anyhow::Result as AnyhowResult;
use rmcp::{
    ServerHandler, ServiceExt,
    handler::server::router::tool::ToolRouter,
    model::{CallToolResult, ServerCapabilities, ServerInfo},
    tool_handler, tool_router,
};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::Value;

use crate::host_adapter::{HostAdapter, error_result};

const DEFAULT_LEASE_TTL: u64 = 60_000;

fn current_timestamp_millis() -> u64 {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default()
        .min(u128::from(u64::MAX));
    millis as u64
}

/// Host-backed MCP service exposing only logical task-space operations.
#[derive(Clone)]
pub struct HostBrowserServer {
    adapter: HostAdapter,
    tool_router: ToolRouter<Self>,
}

impl std::fmt::Debug for HostBrowserServer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("HostBrowserServer")
            .field("adapter", &self.adapter)
            .finish_non_exhaustive()
    }
}

impl HostBrowserServer {
    /// Admit a host-backed MCP connection and construct its logical tool service.
    pub fn new(broker: Broker, hello: HelloEnvelope) -> Result<Self, HostError> {
        Self::with_broker(broker, hello)
    }

    /// Construct a host-backed MCP connection with an injected broker.
    pub fn with_broker(broker: Broker, hello: HelloEnvelope) -> Result<Self, HostError> {
        HostAdapter::with_host(broker, hello).map(Self::from_adapter)
    }

    /// Construct the service around an already admitted host adapter.
    pub fn from_adapter(adapter: HostAdapter) -> Self {
        Self {
            adapter,
            tool_router: Self::tool_router(),
        }
    }

    /// Return the adapter used by this service.
    pub const fn adapter(&self) -> &HostAdapter {
        &self.adapter
    }
}

impl From<HostAdapter> for HostBrowserServer {
    fn from(adapter: HostAdapter) -> Self {
        Self::from_adapter(adapter)
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct SpaceCreateParams {
    /// Human-readable logical task-space label.
    label: String,
    /// Must be `shared_existing_profile`; Chrome uses the existing profile.
    profile_scope: String,
    /// Must be `shared_profile_state`; cookies, sessions, and storage are shared.
    shared_state_notice: String,
    /// Must be false; a task space is not a browser-profile isolation boundary.
    isolation_claim: bool,
    /// Must be true only after the user explicitly acknowledges the disclosure.
    profile_disclosure_acknowledged: bool,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct SpaceIdParams {
    space_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct SpaceLeaseParams {
    space_id: String,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct LeaseAcquireParams {
    space_id: String,
    #[serde(default)]
    now: Option<u64>,
    #[serde(default)]
    ttl: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct LeaseRenewParams {
    space_id: String,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
    #[serde(default)]
    ttl: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct LeaseTakeoverParams {
    space_id: String,
    #[serde(default)]
    now: Option<u64>,
    #[serde(default)]
    ttl: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct LeaseFenceParams {
    space_id: String,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
    #[serde(default)]
    ttl: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct LeaseReturnControlParams {
    space_id: String,
    lease_epoch: u64,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct PageCreateParams {
    space_id: String,
    lease_epoch: u64,
    label: String,
    #[serde(default)]
    now: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct PageBindingParams {
    space_id: String,
    page_id: String,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
    #[serde(default)]
    url: Option<String>,
    #[serde(default)]
    title: Option<String>,
    #[serde(default)]
    frame_count: Option<u32>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct PageLeaseParams {
    space_id: String,
    page_id: String,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct SnapshotReadParams {
    space_id: String,
    page_id: String,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct SnapshotPutParams {
    /// A serialized logical [`SnapshotEnvelope`].
    envelope: BTreeMap<String, Value>,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
}

#[derive(Debug, Clone, Copy, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
enum ActionOperationInput {
    Navigate,
    Click,
    Input,
    Evaluate,
    Scroll,
    Wait,
    Screenshot,
    StorageWrite,
    CookieWrite,
    Upload,
    Close,
}

impl From<ActionOperationInput> for ActionOperation {
    fn from(operation: ActionOperationInput) -> Self {
        match operation {
            ActionOperationInput::Navigate => Self::Navigate,
            ActionOperationInput::Click => Self::Click,
            ActionOperationInput::Input => Self::Input,
            ActionOperationInput::Evaluate => Self::Evaluate,
            ActionOperationInput::Scroll => Self::Scroll,
            ActionOperationInput::Wait => Self::Wait,
            ActionOperationInput::Screenshot => Self::Screenshot,
            ActionOperationInput::StorageWrite => Self::StorageWrite,
            ActionOperationInput::CookieWrite => Self::CookieWrite,
            ActionOperationInput::Upload => Self::Upload,
            ActionOperationInput::Close => Self::Close,
        }
    }
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case", tag = "kind")]
enum PostconditionInput {
    PageGeneration { document_generation: u64 },
    SnapshotHash { snapshot_hash: String },
}

impl PostconditionInput {
    fn to_core(&self) -> Result<Postcondition, CoreError> {
        match self {
            Self::PageGeneration {
                document_generation,
            } => Ok(Postcondition::PageGeneration {
                document_generation: Generation::new(*document_generation),
            }),
            Self::SnapshotHash { snapshot_hash } => {
                let snapshot_hash = ContentHash::new(snapshot_hash.clone())
                    .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
                Ok(Postcondition::SnapshotHash { snapshot_hash })
            }
        }
    }
}

#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ActionParams {
    request_id: String,
    action_id: String,
    idempotency_key: String,
    space_id: String,
    #[serde(default)]
    page_id: Option<String>,
    lease_epoch: u64,
    operation: ActionOperationInput,
    #[serde(default)]
    payload: BTreeMap<String, String>,
    #[serde(default)]
    postcondition: Option<PostconditionInput>,
    #[serde(default)]
    now: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ActionIdParams {
    action_id: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ActionDispatchParams {
    action_id: String,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct ActionReconcileParams {
    action_id: String,
    lease_epoch: u64,
    #[serde(default)]
    now: Option<u64>,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct EventCursorParams {
    broker_epoch: u64,
    sequence: u64,
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct EventResumeParams {
    after: EventCursorParams,
    #[serde(default)]
    space_id: Option<String>,
    #[serde(default)]
    page_id: Option<String>,
}

#[derive(Debug, Clone, Copy, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
enum EventKindInput {
    #[serde(rename = "space.changed")]
    SpaceChanged,
    #[serde(rename = "page.changed")]
    PageChanged,
    #[serde(rename = "lease.changed")]
    LeaseChanged,
    #[serde(rename = "action.changed")]
    ActionChanged,
    #[serde(rename = "snapshot.changed")]
    SnapshotChanged,
    #[serde(rename = "broker.draining")]
    BrokerDraining,
    #[serde(rename = "connection.changed")]
    ConnectionChanged,
}

impl From<EventKindInput> for EventKind {
    fn from(event: EventKindInput) -> Self {
        match event {
            EventKindInput::SpaceChanged => Self::SpaceChanged,
            EventKindInput::PageChanged => Self::PageChanged,
            EventKindInput::LeaseChanged => Self::LeaseChanged,
            EventKindInput::ActionChanged => Self::ActionChanged,
            EventKindInput::SnapshotChanged => Self::SnapshotChanged,
            EventKindInput::BrokerDraining => Self::BrokerDraining,
            EventKindInput::ConnectionChanged => Self::ConnectionChanged,
        }
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
struct EventPublishParams {
    event: EventKindInput,
    #[serde(default)]
    space_id: Option<String>,
    #[serde(default)]
    page_id: Option<String>,
    #[serde(default)]
    payload: BTreeMap<String, String>,
}

#[tool_router(router = tool_router)]
impl HostBrowserServer {
    /// List logical task spaces visible to this connection.
    #[rmcp::tool(
        name = "host_space_list",
        description = "List visible logical task spaces."
    )]
    async fn host_space_list(&self) -> Result<CallToolResult, rmcp::ErrorData> {
        Ok(self.adapter.list_spaces())
    }

    /// Create a logical task space in the existing shared Chrome profile.
    #[rmcp::tool(
        name = "host_space_create",
        description = "Create a logical task space in the existing shared Chrome profile. Acknowledgement is required; task spaces do not isolate cookies, sessions, or storage."
    )]
    async fn host_space_create(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SpaceCreateParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let params = p.0;
        Ok(self.adapter.create_space(
            params.label,
            ProfileDisclosure {
                profile_scope: params.profile_scope,
                shared_state_notice: params.shared_state_notice,
                isolation_claim: params.isolation_claim,
                acknowledged: params.profile_disclosure_acknowledged,
            },
        ))
    }

    /// Describe one logical task space.
    #[rmcp::tool(
        name = "host_space_describe",
        description = "Describe a logical task space."
    )]
    async fn host_space_describe(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SpaceIdParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.describe_space(&space_id))
    }

    /// Finish an agent-owned logical task space after page cleanup.
    #[rmcp::tool(
        name = "host_space_finish",
        description = "Finish a logical task space."
    )]
    async fn host_space_finish(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SpaceLeaseParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.finish_space(
            &space_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Release a finished logical task space.
    #[rmcp::tool(
        name = "host_space_release",
        description = "Release a finished logical task space."
    )]
    async fn host_space_release(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SpaceLeaseParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.release_space(
            &space_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Acquire a lease for a logical task space.
    #[rmcp::tool(
        name = "host_lease_acquire",
        description = "Acquire a logical task-space lease."
    )]
    async fn host_lease_acquire(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<LeaseAcquireParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.acquire_lease(
            &space_id,
            timestamp(p.0.now),
            p.0.ttl.unwrap_or(DEFAULT_LEASE_TTL),
        ))
    }

    /// Renew a logical task-space lease.
    #[rmcp::tool(
        name = "host_lease_renew",
        description = "Renew a logical task-space lease."
    )]
    async fn host_lease_renew(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<LeaseRenewParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.renew_lease(
            &space_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
            p.0.ttl.unwrap_or(DEFAULT_LEASE_TTL),
        ))
    }

    /// Return a logical task space to explicit user control.
    #[rmcp::tool(
        name = "host_lease_return_control",
        description = "Return a lease to explicit user control."
    )]
    async fn host_lease_return_control(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SpaceLeaseParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.return_control(
            &space_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Fence and take over a non-user-owned logical task space.
    #[rmcp::tool(
        name = "host_lease_takeover",
        description = "Take over a logical task-space lease."
    )]
    async fn host_lease_takeover(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<LeaseTakeoverParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.takeover(
            &space_id,
            timestamp(p.0.now),
            p.0.ttl.unwrap_or(DEFAULT_LEASE_TTL),
        ))
    }

    /// Reclaim a user-owned logical task space using its connection-scoped ticket.
    #[rmcp::tool(
        name = "host_lease_takeover_with_control_ticket",
        description = "Reclaim a user-owned logical task space with its control ticket."
    )]
    async fn host_lease_takeover_with_control_ticket(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<LeaseTakeoverParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.takeover_with_control_ticket(
            &space_id,
            timestamp(p.0.now),
            p.0.ttl.unwrap_or(DEFAULT_LEASE_TTL),
        ))
    }

    /// Read the opaque-safe metadata for a user-control ticket.
    #[rmcp::tool(
        name = "host_lease_control_ticket",
        description = "Read logical user-control ticket metadata."
    )]
    async fn host_lease_control_ticket(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SpaceIdParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.control_ticket(&space_id))
    }

    /// Retry acknowledgement of a user-control fence.
    #[rmcp::tool(
        name = "host_lease_acknowledge_return_control",
        description = "Acknowledge a pending user-control fence."
    )]
    async fn host_lease_acknowledge_return_control(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<LeaseReturnControlParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self
            .adapter
            .acknowledge_return_control(&space_id, LeaseEpoch::new(p.0.lease_epoch)))
    }

    /// Retry acknowledgement of a pending takeover fence.
    #[rmcp::tool(
        name = "host_lease_acknowledge_fence",
        description = "Acknowledge a pending takeover fence."
    )]
    async fn host_lease_acknowledge_fence(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<LeaseFenceParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.acknowledge_fence(
            &space_id,
            LeaseEpoch::new(p.0.lease_epoch),
            Timestamp::new(p.0.now.unwrap_or_else(current_timestamp_millis)),
            p.0.ttl.unwrap_or(DEFAULT_LEASE_TTL),
        ))
    }

    /// Create a planned logical page inside a leased space.
    #[rmcp::tool(
        name = "host_page_create",
        description = "Create a planned logical page."
    )]
    async fn host_page_create(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<PageCreateParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.create_page_at(
            &space_id,
            LeaseEpoch::new(p.0.lease_epoch),
            p.0.label,
            timestamp(p.0.now),
        ))
    }

    /// List logical pages in one task space.
    #[rmcp::tool(
        name = "host_page_list",
        description = "List logical pages in a task space."
    )]
    async fn host_page_list(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SpaceIdParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.list_pages(&space_id))
    }

    /// Bind logical page metadata after a host-side page discovery.
    #[rmcp::tool(name = "host_page_bind", description = "Bind logical page metadata.")]
    async fn host_page_bind(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<PageBindingParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let space_id = match HostAdapter::logical_space_id(&p.0.space_id) {
            Ok(space_id) => space_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        let page_id = match HostAdapter::logical_page_id(&p.0.page_id) {
            Ok(page_id) => page_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.bind_page(
            &space_id,
            &page_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
            p.0.url,
            p.0.title,
            p.0.frame_count.unwrap_or(1),
        ))
    }

    /// Mark a logical page target as lost.
    #[rmcp::tool(
        name = "host_page_mark_lost",
        description = "Mark a logical page target as lost."
    )]
    async fn host_page_mark_lost(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<PageLeaseParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let (space_id, page_id) = match logical_page_pair(&p.0.space_id, &p.0.page_id) {
            Ok(ids) => ids,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.mark_page_lost(
            &space_id,
            &page_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Close one explicitly authorized logical page.
    #[rmcp::tool(name = "host_page_close", description = "Close one logical page.")]
    async fn host_page_close(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<PageLeaseParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let (space_id, page_id) = match logical_page_pair(&p.0.space_id, &p.0.page_id) {
            Ok(ids) => ids,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.close_page(
            &space_id,
            &page_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Read one logical page snapshot through the host cache/bridge boundary.
    #[rmcp::tool(
        name = "host_snapshot_read",
        description = "Read a logical page snapshot."
    )]
    async fn host_snapshot_read(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SnapshotReadParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let (space_id, page_id) = match logical_page_pair(&p.0.space_id, &p.0.page_id) {
            Ok(ids) => ids,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.read_snapshot(
            &space_id,
            &page_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Store one validated logical page snapshot.
    #[rmcp::tool(
        name = "host_snapshot_put",
        description = "Store a validated logical page snapshot."
    )]
    async fn host_snapshot_put(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<SnapshotPutParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let envelope_value = match serde_json::to_value(p.0.envelope) {
            Ok(value) => value,
            Err(error) => {
                return Ok(error_result(
                    CoreError::new(
                        agentyc_core::ErrorCode::InvalidJson,
                        format!("could not encode snapshot envelope: {error}"),
                    ),
                    None,
                ));
            }
        };
        let envelope: SnapshotEnvelope = match serde_json::from_value(envelope_value) {
            Ok(envelope) => envelope,
            Err(error) => {
                return Ok(error_result(
                    CoreError::invalid_argument(format!("invalid snapshot envelope: {error}")),
                    None,
                ));
            }
        };
        Ok(self.adapter.put_snapshot(
            envelope,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Mark one logical page snapshot cache entry dirty.
    #[rmcp::tool(
        name = "host_snapshot_mark_dirty",
        description = "Mark a logical snapshot cache entry dirty."
    )]
    async fn host_snapshot_mark_dirty(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<PageLeaseParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let (space_id, page_id) = match logical_page_pair(&p.0.space_id, &p.0.page_id) {
            Ok(ids) => ids,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.mark_snapshot_dirty(
            &space_id,
            &page_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Enqueue and dispatch one logical action.
    #[rmcp::tool(
        name = "host_action_execute",
        description = "Execute one logical action through the host broker."
    )]
    async fn host_action_execute(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ActionParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let request = match build_action_request(&p.0) {
            Ok(request) => request,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.execute_action(request, timestamp(p.0.now)))
    }

    /// Enqueue one logical action without dispatching it.
    #[rmcp::tool(
        name = "host_action_enqueue",
        description = "Enqueue one logical action."
    )]
    async fn host_action_enqueue(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ActionParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let request = match build_action_request(&p.0) {
            Ok(request) => request,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.enqueue_action(request, timestamp(p.0.now)))
    }

    /// Dispatch one already-enqueued logical action.
    #[rmcp::tool(
        name = "host_action_dispatch",
        description = "Dispatch one queued logical action."
    )]
    async fn host_action_dispatch(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ActionDispatchParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let action_id = match HostAdapter::logical_action_id(&p.0.action_id) {
            Ok(action_id) => action_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.dispatch_action(
            &action_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Read one logical action receipt without dispatching it.
    #[rmcp::tool(
        name = "host_action_status",
        description = "Read one logical action receipt."
    )]
    async fn host_action_status(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ActionIdParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let action_id = match HostAdapter::logical_action_id(&p.0.action_id) {
            Ok(action_id) => action_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.action_status(&action_id))
    }

    /// Reconcile one unknown logical action without replaying dispatch.
    #[rmcp::tool(
        name = "host_action_reconcile",
        description = "Reconcile an unknown logical action."
    )]
    async fn host_action_reconcile(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<ActionReconcileParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let action_id = match HostAdapter::logical_action_id(&p.0.action_id) {
            Ok(action_id) => action_id,
            Err(error) => return Ok(error_result(error, None)),
        };
        Ok(self.adapter.reconcile_action(
            &action_id,
            LeaseEpoch::new(p.0.lease_epoch),
            timestamp(p.0.now),
        ))
    }

    /// Return the current logical event cursor.
    #[rmcp::tool(
        name = "host_event_cursor",
        description = "Return the current logical event cursor."
    )]
    async fn host_event_cursor(&self) -> Result<CallToolResult, rmcp::ErrorData> {
        Ok(self.adapter.event_cursor())
    }

    /// Resume logical events after a broker cursor and optional scope.
    #[rmcp::tool(
        name = "host_event_resume",
        description = "Resume logical events after a cursor."
    )]
    async fn host_event_resume(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<EventResumeParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let scope =
            match HostAdapter::logical_event_scope(p.0.space_id.as_deref(), p.0.page_id.as_deref())
            {
                Ok(scope) => scope,
                Err(error) => return Ok(error_result(error, None)),
            };
        Ok(self.adapter.resume_events(EventQuery {
            after: EventCursor {
                broker_epoch: agentyc_core::BrokerEpoch::new(p.0.after.broker_epoch),
                sequence: agentyc_core::EventSequence::new(p.0.after.sequence),
            },
            scope: (scope.space_id.is_some() || scope.page_id.is_some()).then_some(scope),
        }))
    }

    /// Publish one principal-scoped logical event.
    #[rmcp::tool(
        name = "host_event_publish",
        description = "Publish one logical event."
    )]
    async fn host_event_publish(
        &self,
        p: rmcp::handler::server::wrapper::Parameters<EventPublishParams>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let scope =
            match HostAdapter::logical_event_scope(p.0.space_id.as_deref(), p.0.page_id.as_deref())
            {
                Ok(scope) => scope,
                Err(error) => return Ok(error_result(error, None)),
            };
        Ok(self
            .adapter
            .publish_event(scope, p.0.event.into(), p.0.payload))
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for HostBrowserServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_instructions("agentyc host-backed logical task-space service")
    }
}

/// Construct a host-backed MCP service from an injected broker and handshake.
pub fn host_service(broker: Broker, hello: HelloEnvelope) -> Result<HostBrowserServer, HostError> {
    HostBrowserServer::new(broker, hello)
}

/// Run the host-backed MCP service over stdio.
pub async fn run_host_stdio(broker: Broker, hello: HelloEnvelope) -> AnyhowResult<()> {
    let server = host_service(broker, hello)?;
    let transport = rmcp::transport::stdio();
    let service = server.serve(transport).await?;
    service.waiting().await?;
    Ok(())
}

fn timestamp(value: Option<u64>) -> Timestamp {
    Timestamp::new(value.unwrap_or(0))
}

fn logical_page_pair(
    space_id: &str,
    page_id: &str,
) -> Result<(agentyc_core::SpaceId, agentyc_core::PageId), CoreError> {
    Ok((
        HostAdapter::logical_space_id(space_id)?,
        HostAdapter::logical_page_id(page_id)?,
    ))
}

fn build_action_request(
    params: &ActionParams,
) -> Result<ActionRequest<BTreeMap<String, String>>, CoreError> {
    let postcondition = params
        .postcondition
        .as_ref()
        .map(PostconditionInput::to_core)
        .transpose()?;
    HostAdapter::build_action_request_from_logical_ids(
        &params.request_id,
        &params.action_id,
        &params.idempotency_key,
        &params.space_id,
        params.page_id.as_deref(),
        params.lease_epoch,
        params.operation.into(),
        params.payload.clone(),
        postcondition,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    use agentyc_core::{
        ClientMetadata, ConnectionNonce, HelloEnvelope, PROTOCOL_VERSION, PrincipalId,
    };
    use agentyc_host::{FakeBridge, Ledger};
    use tempfile::tempdir;

    fn hello() -> HelloEnvelope {
        HelloEnvelope {
            protocol: PROTOCOL_VERSION,
            supported_protocols: vec![PROTOCOL_VERSION],
            principal_id: PrincipalId::from_suffix("host-server-test").expect("principal"),
            resume_from: None,
            client_metadata: Some(ClientMetadata {
                client_id: None,
                client_name: Some("host-server-test".to_owned()),
                client_version: Some("1".to_owned()),
                connection_nonce: Some(
                    ConnectionNonce::from_suffix("host-server-test").expect("nonce"),
                ),
                profile_binding_id: None,
            }),
        }
    }

    fn content(result: &CallToolResult) -> &Value {
        result
            .structured_content
            .as_ref()
            .expect("structured content")
    }

    fn server() -> (tempfile::TempDir, HostBrowserServer) {
        let directory = tempdir().expect("tempdir");
        let ledger = Ledger::open(directory.path()).expect("ledger");
        let bridge = Arc::new(FakeBridge::new());
        let broker = Broker::with_shared_bridge(ledger, bridge);
        (
            directory,
            HostBrowserServer::new(broker, hello()).expect("host service"),
        )
    }

    #[tokio::test]
    async fn invalid_logical_id_is_a_canonical_is_error_result() {
        let (_directory, server) = server();
        let result = server
            .host_space_describe(rmcp::handler::server::wrapper::Parameters(SpaceIdParams {
                space_id: "tab_legacy".to_owned(),
            }))
            .await
            .expect("tool result");
        assert_eq!(result.is_error, Some(true));
        let wire = serde_json::to_value(result).expect("serialized result");
        assert_eq!(wire["isError"], true);
        assert_eq!(
            wire["structuredContent"]["error"]["code"],
            "invalid_argument"
        );
    }

    #[test]
    fn host_router_contains_only_logical_host_tools() {
        let (_directory, server) = server();
        assert_eq!(server.tool_router.map.len(), 29);
        assert!(
            server
                .tool_router
                .map
                .keys()
                .all(|name| name.starts_with("host_"))
        );
        assert!(server.tool_router.map.contains_key("host_page_list"));
        assert!(server.tool_router.map.contains_key("host_action_reconcile"));
    }

    #[tokio::test]
    async fn two_spaces_keep_page_operations_scoped() {
        let (_directory, server) = server();
        let disclosure = || ProfileDisclosure {
            profile_scope: ProfileDisclosure::PROFILE_SCOPE.to_owned(),
            shared_state_notice: ProfileDisclosure::SHARED_STATE_NOTICE.to_owned(),
            isolation_claim: false,
            acknowledged: true,
        };
        let first = server.adapter.create_space("one", disclosure());
        let second = server.adapter.create_space("two", disclosure());
        let first_space = content(&first)["result"]["space_id"]
            .as_str()
            .expect("first space")
            .to_owned();
        let second_space = content(&second)["result"]["space_id"]
            .as_str()
            .expect("second space")
            .to_owned();
        let first_space_id = HostAdapter::logical_space_id(&first_space).expect("space id");
        let second_space_id = HostAdapter::logical_space_id(&second_space).expect("space id");
        let first_lease =
            server
                .adapter
                .acquire_lease(&first_space_id, Timestamp::new(1), DEFAULT_LEASE_TTL);
        let second_lease =
            server
                .adapter
                .acquire_lease(&second_space_id, Timestamp::new(1), DEFAULT_LEASE_TTL);
        let first_epoch = content(&first_lease)["result"]["lease"]["lease_epoch"]
            .as_u64()
            .expect("first lease");
        let second_epoch = content(&second_lease)["result"]["lease"]["lease_epoch"]
            .as_u64()
            .expect("second lease");
        let page = server.adapter.create_page_at(
            &first_space_id,
            LeaseEpoch::new(first_epoch),
            "one-page",
            Timestamp::new(1),
        );
        let page_id = content(&page)["result"]["page_id"]
            .as_str()
            .expect("page id")
            .to_owned();
        let result = server
            .host_page_bind(rmcp::handler::server::wrapper::Parameters(
                PageBindingParams {
                    space_id: second_space,
                    page_id,
                    lease_epoch: second_epoch,
                    now: Some(1),
                    url: None,
                    title: None,
                    frame_count: Some(1),
                },
            ))
            .await
            .expect("tool result");
        assert_eq!(result.is_error, Some(true));
        assert_eq!(content(&result)["error"]["code"], "page_not_found");
    }
}
