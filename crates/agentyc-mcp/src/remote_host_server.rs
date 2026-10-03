//! Remote host-backed MCP service over the owner-only local host socket.
//!
//! Unlike [`super::host_server::HostBrowserServer`], this adapter never opens a
//! broker. It forwards bounded logical requests to the broker-owning native host
//! through [`agentyc_host::LocalSocketClient`].

use std::{collections::BTreeMap, sync::Arc};

use agentyc_core::{CoreError, ErrorCode};
use agentyc_host::{HostError, LocalSocketClient};
use rmcp::{
    ServerHandler, ServiceExt,
    handler::server::{
        router::tool::{ToolRoute, ToolRouter},
        tool::ToolCallContext,
    },
    model::{CallToolResult, JsonObject, ServerCapabilities, ServerInfo, Tool},
    tool_handler,
};
use serde_json::{Map, Value, json};

use crate::host_adapter::error_result;

const MAX_LOGICAL_PARAMS: usize = 16;
const MAX_LOGICAL_PARAM_NAME_BYTES: usize = 64;
const MAX_LOGICAL_PARAM_VALUE_BYTES: usize = 4 * 1024;
const DEFAULT_LEASE_TTL: &str = "60000";

#[derive(Clone, Copy)]
struct FieldSpec {
    name: &'static str,
    kind: &'static str,
    description: &'static str,
    required: bool,
}

#[derive(Clone, Copy)]
struct RemoteToolSpec {
    name: &'static str,
    method: &'static str,
    description: &'static str,
    fields: &'static [FieldSpec],
    supported_by_local_protocol: bool,
}

const NO_FIELDS: &[FieldSpec] = &[];
const LABEL_FIELDS: &[FieldSpec] = &[FieldSpec {
    name: "label",
    kind: "string",
    description: "Human-readable logical label.",
    required: true,
}];
const SPACE_ID_FIELDS: &[FieldSpec] = &[FieldSpec {
    name: "space_id",
    kind: "string",
    description: "Validated logical space identity (space_*), never a browser or tab id.",
    required: true,
}];
const SPACE_LEASE_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds; defaults to the host protocol default.",
        required: false,
    },
];
const LEASE_ACQUIRE_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
    FieldSpec {
        name: "ttl",
        kind: "integer",
        description: "Optional lease duration in milliseconds; defaults to 60000.",
        required: false,
    },
];
const LEASE_FENCE_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Fence or lease epoch to acknowledge.",
        required: true,
    },
];
const LEASE_RENEW_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
    FieldSpec {
        name: "ttl",
        kind: "integer",
        description: "Optional lease duration in milliseconds; defaults to 60000.",
        required: false,
    },
];
const PAGE_LEASE_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "page_id",
        kind: "string",
        description: "Validated logical page identity (page_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
];
const PAGE_CREATE_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "label",
        kind: "string",
        description: "Human-readable logical page label.",
        required: true,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
];
const MANAGED_PAGE_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "label",
        kind: "string",
        description: "Human-readable logical page label.",
        required: true,
    },
    FieldSpec {
        name: "url",
        kind: "string",
        description: "Optional initial page URL.",
        required: false,
    },
    FieldSpec {
        name: "title",
        kind: "string",
        description: "Optional logical page title.",
        required: false,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
];
const PAGE_BINDING_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "page_id",
        kind: "string",
        description: "Validated logical page identity (page_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
    FieldSpec {
        name: "url",
        kind: "string",
        description: "Optional logical page URL metadata.",
        required: false,
    },
    FieldSpec {
        name: "title",
        kind: "string",
        description: "Optional logical page title metadata.",
        required: false,
    },
    FieldSpec {
        name: "frame_count",
        kind: "integer",
        description: "Optional logical frame count.",
        required: false,
    },
];
const SNAPSHOT_PUT_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "envelope",
        kind: "object",
        description: "Serialized logical SnapshotEnvelope object.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
];
const ACTION_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "request_id",
        kind: "string",
        description: "Logical request identity (request_*).",
        required: true,
    },
    FieldSpec {
        name: "action_id",
        kind: "string",
        description: "Logical action identity (action_*).",
        required: true,
    },
    FieldSpec {
        name: "idempotency_key",
        kind: "string",
        description: "Logical idempotency identity.",
        required: true,
    },
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Validated logical space identity (space_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "page_id",
        kind: "string",
        description: "Optional validated logical page identity (page_*).",
        required: false,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "operation",
        kind: "string",
        description: "Logical operation: navigate, click, input, evaluate, scroll, wait, screenshot, storage_write, cookie_write, upload, or close.",
        required: true,
    },
    FieldSpec {
        name: "payload",
        kind: "object",
        description: "Logical operation payload as string values.",
        required: false,
    },
    FieldSpec {
        name: "postcondition",
        kind: "object",
        description: "Optional logical postcondition object.",
        required: false,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
];
const ACTION_ID_FIELDS: &[FieldSpec] = &[FieldSpec {
    name: "action_id",
    kind: "string",
    description: "Logical action identity (action_*), never a browser or tab id.",
    required: true,
}];
const ACTION_LEASE_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "action_id",
        kind: "string",
        description: "Logical action identity (action_*), never a browser or tab id.",
        required: true,
    },
    FieldSpec {
        name: "lease_epoch",
        kind: "integer",
        description: "Current logical lease fencing epoch.",
        required: true,
    },
    FieldSpec {
        name: "now",
        kind: "integer",
        description: "Optional host timestamp in milliseconds.",
        required: false,
    },
];
const EVENT_RESUME_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "after",
        kind: "object",
        description: "Required logical cursor object with broker_epoch and sequence fields.",
        required: true,
    },
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Optional validated logical space scope (space_*).",
        required: false,
    },
    FieldSpec {
        name: "page_id",
        kind: "string",
        description: "Optional validated logical page scope (page_*); requires space_id.",
        required: false,
    },
    FieldSpec {
        name: "limit",
        kind: "integer",
        description: "Optional maximum number of logical events.",
        required: false,
    },
];
const EVENT_PUBLISH_FIELDS: &[FieldSpec] = &[
    FieldSpec {
        name: "event",
        kind: "string",
        description: "Logical event kind such as space.changed, page.changed, lease.changed, action.changed, snapshot.changed, broker.draining, or connection.changed.",
        required: true,
    },
    FieldSpec {
        name: "space_id",
        kind: "string",
        description: "Optional validated logical space scope (space_*).",
        required: false,
    },
    FieldSpec {
        name: "page_id",
        kind: "string",
        description: "Optional validated logical page scope (page_*); requires space_id.",
        required: false,
    },
    FieldSpec {
        name: "payload",
        kind: "object",
        description: "Logical event payload as string values.",
        required: false,
    },
];

const REMOTE_TOOL_SPECS: &[RemoteToolSpec] = &[
    RemoteToolSpec {
        name: "host_space_list",
        method: "space.list",
        description: "List visible logical task spaces. Parameters: none. Results contain logical space records and space_id values; browser or tab identifiers are never returned as authority inputs.",
        fields: NO_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_space_create",
        method: "space.create",
        description: "Create a logical task space. Fields: label (required logical label).",
        fields: LABEL_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_space_describe",
        method: "space.describe",
        description: "Describe one logical task space. Fields: space_id (required validated space_* identity).",
        fields: SPACE_ID_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_space_finish",
        method: "space.finish",
        description: "Finish an agent-owned logical task space. Fields: space_id, lease_epoch (required logical fields), now (optional host timestamp).",
        fields: SPACE_LEASE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_space_release",
        method: "space.release",
        description: "Release a finished logical task space. Fields: space_id, lease_epoch (required logical fields), now (optional host timestamp).",
        fields: SPACE_LEASE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_lease_acquire",
        method: "lease.acquire",
        description: "Acquire a logical task-space lease. Fields: space_id (required validated space_* identity), now (optional timestamp), ttl (optional milliseconds; default 60000).",
        fields: LEASE_ACQUIRE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_lease_renew",
        method: "lease.renew",
        description: "Renew a logical task-space lease. Fields: space_id, lease_epoch (required logical fields), now and ttl (optional; ttl defaults to 60000).",
        fields: LEASE_RENEW_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_lease_return_control",
        method: "space.return_control",
        description: "Return a logical task space to explicit user control. Fields: space_id, lease_epoch (required logical fields), now (optional timestamp).",
        fields: SPACE_LEASE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_lease_takeover",
        method: "space.takeover",
        description: "Take over a non-user-owned logical task space. Fields: space_id (required), now and ttl (optional logical lease fields; ttl defaults to 60000).",
        fields: LEASE_ACQUIRE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_lease_takeover_with_control_ticket",
        method: "space.takeover_with_control_ticket",
        description: "Reclaim a user-owned logical task space with its connection-scoped control ticket. Fields: space_id (required), now and ttl (optional logical lease fields).",
        fields: LEASE_ACQUIRE_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_lease_control_ticket",
        method: "space.control_ticket",
        description: "Read logical user-control ticket metadata. Fields: space_id (required validated space_* identity).",
        fields: SPACE_ID_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_lease_acknowledge_return_control",
        method: "space.acknowledge_return_control",
        description: "Acknowledge a pending logical user-control fence. Fields: space_id and lease_epoch (required logical fields).",
        fields: LEASE_FENCE_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_lease_acknowledge_fence",
        method: "space.acknowledge_fence",
        description: "Acknowledge a pending logical takeover fence. Fields: space_id and lease_epoch (required logical fields).",
        fields: LEASE_FENCE_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_page_create",
        method: "page.create",
        description: "Create a planned logical page. Fields: space_id, lease_epoch, label (required logical fields), now (optional timestamp).",
        fields: PAGE_CREATE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_page_list",
        method: "page.list",
        description: "List logical pages in one task space. Fields: space_id (required validated space_* identity).",
        fields: SPACE_ID_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_page_create_managed",
        method: "page.create_managed",
        description: "Create an inactive browser page owned by a logical space and present it in that space's visual Chrome tab group. Fields: space_id, lease_epoch, label (required); url, title, and now (optional).",
        fields: MANAGED_PAGE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_page_bind",
        method: "page.bind",
        description: "Bind logical page metadata after host-side discovery. Fields: space_id, page_id, lease_epoch (required logical fields), now, url, title, and frame_count (optional logical metadata).",
        fields: PAGE_BINDING_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_page_mark_lost",
        method: "page.mark_lost",
        description: "Mark a logical page target as lost. Fields: space_id, page_id, lease_epoch (required logical fields), now (optional timestamp).",
        fields: PAGE_LEASE_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_page_close",
        method: "page.close",
        description: "Close one explicitly authorized logical page. Fields: space_id, page_id, lease_epoch (required logical fields), now (optional timestamp).",
        fields: PAGE_LEASE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_snapshot_read",
        method: "snapshot.read",
        description: "Read a logical page snapshot. Fields: space_id, page_id, lease_epoch (required logical fields), now (optional timestamp).",
        fields: PAGE_LEASE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_snapshot_put",
        method: "snapshot.put",
        description: "Store a validated logical page snapshot. Fields: envelope (required SnapshotEnvelope object), lease_epoch (required), now (optional timestamp).",
        fields: SNAPSHOT_PUT_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_snapshot_mark_dirty",
        method: "snapshot.mark_dirty",
        description: "Mark a logical snapshot cache entry dirty. Fields: space_id, page_id, lease_epoch (required logical fields), now (optional timestamp).",
        fields: PAGE_LEASE_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_action_execute",
        method: "action.execute",
        description: "Execute one logical action through the host. Fields: request_id, action_id, idempotency_key, space_id, lease_epoch, and operation (required logical fields); page_id, payload, postcondition, and now are optional logical fields.",
        fields: ACTION_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_action_enqueue",
        method: "action.enqueue",
        description: "Enqueue one logical action. Fields: request_id, action_id, idempotency_key, space_id, lease_epoch, and operation (required logical fields); page_id, payload, postcondition, and now are optional logical fields.",
        fields: ACTION_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_action_dispatch",
        method: "action.dispatch",
        description: "Dispatch one queued logical action. Fields: action_id, lease_epoch (required logical fields), now (optional timestamp).",
        fields: ACTION_LEASE_FIELDS,
        supported_by_local_protocol: false,
    },
    RemoteToolSpec {
        name: "host_action_status",
        method: "action.status",
        description: "Read one logical action receipt. Fields: action_id (required validated action_* identity).",
        fields: ACTION_ID_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_action_reconcile",
        method: "action.reconcile",
        description: "Reconcile one unknown logical action without replaying dispatch. Fields: action_id, lease_epoch (required logical fields), now (optional timestamp).",
        fields: ACTION_LEASE_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_event_cursor",
        method: "events.cursor",
        description: "Return the current logical event cursor. Parameters: none. The cursor contains broker_epoch and sequence, not browser identifiers.",
        fields: NO_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_event_resume",
        method: "events.resume",
        description: "Resume logical events after a cursor. Fields: after (required object with broker_epoch and sequence), space_id and page_id (optional logical scope), and limit (optional event bound).",
        fields: EVENT_RESUME_FIELDS,
        supported_by_local_protocol: true,
    },
    RemoteToolSpec {
        name: "host_event_publish",
        method: "events.publish",
        description: "Publish one principal-scoped logical event. Fields: event (required logical event kind), space_id and page_id (optional logical scope), payload (optional object of string values).",
        fields: EVENT_PUBLISH_FIELDS,
        supported_by_local_protocol: false,
    },
];

/// Host-backed MCP service that forwards logical tools to the owner host socket.
#[derive(Clone)]
pub struct RemoteHostBrowserServer {
    client: Arc<LocalSocketClient>,
    tool_router: ToolRouter<Self>,
}

impl std::fmt::Debug for RemoteHostBrowserServer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RemoteHostBrowserServer")
            .field("client", &self.client)
            .finish_non_exhaustive()
    }
}

impl RemoteHostBrowserServer {
    /// Construct a remote host service around one completed local socket hello.
    pub fn new(client: LocalSocketClient) -> Self {
        Self::from_client(Arc::new(client))
    }

    /// Construct a remote host service around a shared local socket client.
    pub fn from_client(client: Arc<LocalSocketClient>) -> Self {
        Self {
            client,
            tool_router: Self::build_tool_router(),
        }
    }

    /// Return the socket client used by this service.
    pub fn client(&self) -> &LocalSocketClient {
        &self.client
    }

    fn build_tool_router() -> ToolRouter<Self> {
        let mut router = ToolRouter::new();
        for spec in REMOTE_TOOL_SPECS {
            let tool = Tool::new(spec.name, spec.description, schema_for_fields(spec.fields));
            router.add_route(ToolRoute::new_dyn(
                tool,
                move |context: ToolCallContext<'_, Self>| {
                    let service = context.service;
                    let arguments = context.arguments;
                    Box::pin(async move { service.invoke_tool(spec, arguments).await })
                },
            ));
        }
        router
    }

    async fn invoke_tool(
        &self,
        spec: &'static RemoteToolSpec,
        arguments: Option<JsonObject>,
    ) -> Result<CallToolResult, rmcp::ErrorData> {
        let mut params = match json_object_to_params(arguments) {
            Ok(params) => params,
            Err(error) => return Ok(error_result(error, None)),
        };
        if !spec.supported_by_local_protocol {
            return Ok(error_result(
                CoreError::new(
                    ErrorCode::CapabilityUnavailable,
                    format!(
                        "the owner host does not expose {} through its local protocol",
                        spec.method
                    ),
                ),
                Some(json!({
                    "tool": spec.name,
                    "method": spec.method,
                })),
            ));
        }

        if let Err(error) = normalize_wire_params(spec.name, &mut params) {
            return Ok(error_result(error, None));
        }

        let client = Arc::clone(&self.client);
        let method = spec.method;
        let response =
            match tokio::task::spawn_blocking(move || client.request(method, params)).await {
                Ok(response) => response,
                Err(error) => {
                    return Ok(error_result(
                        CoreError::new(
                            ErrorCode::NativeHostUnavailable,
                            format!("remote host request task failed: {error}"),
                        ),
                        None,
                    ));
                }
            };
        Ok(string_map_to_call_tool_result(response))
    }
}

/// Alias matching the shorter host-server naming used by callers.
pub type RemoteHostServer = RemoteHostBrowserServer;

#[tool_handler(router = self.tool_router)]
impl ServerHandler for RemoteHostBrowserServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build())
            .with_instructions("agentyc remote host-backed logical task-space service")
    }
}

/// Construct a remote host-backed MCP service from a connected local client.
pub fn remote_host_service(client: LocalSocketClient) -> RemoteHostBrowserServer {
    RemoteHostBrowserServer::new(client)
}

/// Run the remote host-backed MCP service over stdio.
pub async fn run_remote_host_stdio(client: LocalSocketClient) -> anyhow::Result<()> {
    let server = remote_host_service(client);
    let transport = rmcp::transport::stdio();
    let service = server.serve(transport).await?;
    service.waiting().await?;
    Ok(())
}

fn schema_for_fields(fields: &[FieldSpec]) -> Arc<JsonObject> {
    let mut properties = Map::new();
    let mut required = Vec::new();
    for field in fields {
        properties.insert(
            field.name.to_owned(),
            json!({
                "type": field.kind,
                "description": field.description,
            }),
        );
        if field.required {
            required.push(Value::String(field.name.to_owned()));
        }
    }

    let mut schema = Map::new();
    schema.insert("type".to_owned(), Value::String("object".to_owned()));
    schema.insert("properties".to_owned(), Value::Object(properties));
    schema.insert("additionalProperties".to_owned(), Value::Bool(false));
    if !required.is_empty() {
        schema.insert("required".to_owned(), Value::Array(required));
    }
    Arc::new(schema)
}

/// Convert MCP's JSON object arguments to the bounded local string-map format.
pub(crate) fn json_object_to_params(
    arguments: Option<JsonObject>,
) -> Result<BTreeMap<String, String>, CoreError> {
    let arguments = arguments.unwrap_or_default();
    if arguments.len() > MAX_LOGICAL_PARAMS {
        return Err(CoreError::new(
            ErrorCode::MessageTooLarge,
            format!("too many logical request parameters; maximum is {MAX_LOGICAL_PARAMS}"),
        ));
    }

    let mut params = BTreeMap::new();
    for (name, value) in arguments {
        if name.is_empty() || name.len() > MAX_LOGICAL_PARAM_NAME_BYTES {
            return Err(CoreError::invalid_argument(
                "logical request parameter name is invalid",
            ));
        }
        let value = match value {
            Value::String(value) => value,
            value => serde_json::to_string(&value).map_err(|error| {
                CoreError::new(
                    ErrorCode::InvalidJson,
                    format!("could not encode logical request parameter {name}: {error}"),
                )
            })?,
        };
        if value.len() > MAX_LOGICAL_PARAM_VALUE_BYTES {
            return Err(CoreError::new(
                ErrorCode::MessageTooLarge,
                format!(
                    "logical request parameter {name} exceeds the {MAX_LOGICAL_PARAM_VALUE_BYTES}-byte limit"
                ),
            ));
        }
        params.insert(name, value);
    }
    Ok(params)
}

fn normalize_wire_params(
    tool_name: &str,
    params: &mut BTreeMap<String, String>,
) -> Result<(), CoreError> {
    if matches!(
        tool_name,
        "host_lease_acquire" | "host_lease_renew" | "host_lease_takeover"
    ) {
        params
            .entry("ttl".to_owned())
            .or_insert_with(|| DEFAULT_LEASE_TTL.to_owned());
    }

    if tool_name == "host_event_resume"
        && let Some(after) = params.remove("after")
    {
        let after: Value = serde_json::from_str(&after).map_err(|error| {
            CoreError::invalid_argument(format!("after must be a logical cursor object: {error}"))
        })?;
        let after = after
            .as_object()
            .ok_or_else(|| CoreError::invalid_argument("after must be a logical cursor object"))?;
        let broker_epoch = after
            .get("broker_epoch")
            .or_else(|| after.get("after_epoch"))
            .ok_or_else(|| CoreError::invalid_argument("after.broker_epoch is required"))?;
        let sequence = after
            .get("sequence")
            .or_else(|| after.get("after_sequence"))
            .ok_or_else(|| CoreError::invalid_argument("after.sequence is required"))?;
        params.insert(
            "after_epoch".to_owned(),
            scalar_param(broker_epoch, "after.broker_epoch")?,
        );
        params.insert(
            "after_sequence".to_owned(),
            scalar_param(sequence, "after.sequence")?,
        );
    }
    if params.len() > MAX_LOGICAL_PARAMS {
        return Err(CoreError::new(
            ErrorCode::MessageTooLarge,
            format!(
                "too many logical request parameters after wire normalization; maximum is {MAX_LOGICAL_PARAMS}"
            ),
        ));
    }
    Ok(())
}

fn scalar_param(value: &Value, field: &str) -> Result<String, CoreError> {
    match value {
        Value::String(value) => Ok(value.clone()),
        Value::Number(_) | Value::Bool(_) => serde_json::to_string(value).map_err(|error| {
            CoreError::new(
                ErrorCode::InvalidJson,
                format!("could not encode {field}: {error}"),
            )
        }),
        _ => Err(CoreError::invalid_argument(format!(
            "{field} must be a scalar logical value"
        ))),
    }
}

/// Decode the host protocol's JSON-string fields into one structured MCP result.
pub(crate) fn string_map_to_call_tool_result(
    response: Result<BTreeMap<String, String>, HostError>,
) -> CallToolResult {
    let fields = match response {
        Ok(fields) => fields,
        Err(error) => return error_result(error.as_core_error(), None),
    };

    let mut decoded = Map::new();
    for (field, value) in fields {
        let value = match serde_json::from_str::<Value>(&value) {
            Ok(value) => value,
            Err(error) => {
                return error_result(
                    CoreError::new(
                        ErrorCode::InvalidJson,
                        format!("host result field {field} is not valid JSON: {error}"),
                    ),
                    Some(json!({ "field": field })),
                );
            }
        };
        decoded.insert(field, value);
    }

    CallToolResult::structured(json!({
        "ok": true,
        "result": Value::Object(decoded),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_conversion_preserves_strings_and_decodes_structured_values_on_the_wire() {
        let arguments = json!({
            "label": "main",
            "lease_epoch": 7,
            "payload": {"url": "https://logical.test"},
            "enabled": true,
        })
        .as_object()
        .cloned();

        let params = json_object_to_params(arguments).expect("bounded params");

        assert_eq!(params.get("label"), Some(&"main".to_owned()));
        assert_eq!(params.get("lease_epoch"), Some(&"7".to_owned()));
        assert_eq!(
            params.get("payload"),
            Some(&r#"{"url":"https://logical.test"}"#.to_owned())
        );
        assert_eq!(params.get("enabled"), Some(&"true".to_owned()));
    }

    #[test]
    fn request_conversion_rejects_values_outside_local_protocol_bounds() {
        let too_many = (0..=MAX_LOGICAL_PARAMS)
            .map(|index| (format!("field_{index}"), Value::String("v".to_owned())))
            .collect();
        let error = json_object_to_params(Some(too_many)).expect_err("parameter count bound");
        assert_eq!(error.code, ErrorCode::MessageTooLarge);

        let oversized = Map::from_iter([(
            "label".to_owned(),
            Value::String("x".repeat(MAX_LOGICAL_PARAM_VALUE_BYTES + 1)),
        )]);
        let error = json_object_to_params(Some(oversized)).expect_err("parameter size bound");
        assert_eq!(error.code, ErrorCode::MessageTooLarge);
    }

    #[test]
    fn event_cursor_request_is_normalized_to_local_protocol_fields() {
        let arguments = json!({
            "after": {"broker_epoch": 3, "sequence": 9},
            "limit": 20,
        })
        .as_object()
        .cloned();
        let mut params = json_object_to_params(arguments).expect("params");

        normalize_wire_params("host_event_resume", &mut params).expect("cursor normalization");

        assert_eq!(params.get("after_epoch"), Some(&"3".to_owned()));
        assert_eq!(params.get("after_sequence"), Some(&"9".to_owned()));
        assert_eq!(params.get("limit"), Some(&"20".to_owned()));
        assert!(!params.contains_key("after"));
    }

    #[test]
    fn result_conversion_decodes_each_string_map_field_into_structured_content() {
        let response = Ok(BTreeMap::from([
            (
                "spaces".to_owned(),
                r#"[{"space_id":"space_main"}]"#.to_owned(),
            ),
            ("space_id".to_owned(), r#""space_main""#.to_owned()),
            ("count".to_owned(), "1".to_owned()),
        ]));

        let result = string_map_to_call_tool_result(response);
        assert_eq!(result.is_error, Some(false));
        assert_eq!(
            result.structured_content,
            Some(json!({
                "ok": true,
                "result": {
                    "spaces": [{"space_id": "space_main"}],
                    "space_id": "space_main",
                    "count": 1,
                },
            }))
        );
    }

    #[test]
    fn result_conversion_returns_structured_errors_for_transport_and_invalid_json() {
        let transport = string_map_to_call_tool_result(Err(HostError::Core(CoreError::new(
            ErrorCode::PermissionDenied,
            "host rejected request",
        ))));
        assert_eq!(transport.is_error, Some(true));
        assert_eq!(
            transport
                .structured_content
                .as_ref()
                .expect("structured transport error")["error"]["code"],
            "permission_denied"
        );

        let invalid_fields = BTreeMap::from([("spaces".to_owned(), "not-json".to_owned())]);
        let invalid = string_map_to_call_tool_result(Ok(invalid_fields));
        assert_eq!(invalid.is_error, Some(true));
        assert_eq!(
            invalid
                .structured_content
                .as_ref()
                .expect("structured json error")["error"]["code"],
            "invalid_json"
        );
    }
}
