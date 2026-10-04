//! Direct host-backed interfaces for logical task spaces and pages.
//!
//! This module is deliberately separate from the legacy CDP/runtime frontend. The
//! direct path opens the durable host ledger, performs the core handshake, and
//! talks only in logical identities. It never launches a browser or accepts a
//! copied browser debugging endpoint.

use std::{
    cell::RefCell,
    collections::BTreeMap,
    path::PathBuf,
    str::FromStr,
    time::{SystemTime, UNIX_EPOCH},
};

use agentyc_core::{
    ActionId, BrokerEpoch, ClientId, ConnectionNonce, CoreError, ErrorCode, EventSequence,
    HelloEnvelope, PROTOCOL_VERSION, PageId, PrincipalId, ProfileBindingId, SpaceId, Timestamp,
};
use agentyc_host::{
    AuthorityTicket, Broker, ControlTicket, FakeBridge, HostError, LocalSocketClient,
    configured_socket_path,
};
use anyhow::{Result, anyhow};
use clap::{Args, Subcommand};
use serde_json::{Value, json};
use uuid::Uuid;

mod actions;
mod events;
mod extension;
mod host;
mod pages;
mod snapshot;
mod spaces;
mod waits;

/// Options shared by every direct command.
#[derive(Debug, Clone, Default)]
pub struct DirectOptions {
    /// Optional durable state directory. Defaults to `$AGENTYC_STATE_DIR` or
    /// `~/.agentyc/state`.
    pub state_dir: Option<String>,
    /// Logical principal suffix or complete `principal_` identity.
    pub principal: Option<String>,
    /// Optional enrolled profile binding suffix or complete `profile_` identity.
    pub profile_binding_id: Option<String>,
    /// Use the explicit deterministic in-process fake bridge seam.
    pub offline: bool,
    /// Emit compact JSON instead of the default pretty JSON record.
    pub json: bool,
}

/// Direct command tree exposed by the CLI.
#[derive(Debug, Clone)]
pub enum DirectCommand {
    /// Manage logical task spaces.
    Space(SpaceCommand),
    /// Manage logical pages inside a task space.
    Page(PageCommand),
    /// Read a logical page snapshot.
    Snapshot(SnapshotArgs),
    /// Inspect or reconcile durable action receipts.
    Action(ActionCommand),
    /// Resume logical host events.
    Events(EventsArgs),
    /// Inspect direct host state and bridge capabilities.
    Host(HostCommand),
    /// Wait for a host-observed logical condition.
    Wait(WaitArgs),
    /// Inspect extension state observed by the host.
    Extension(ExtensionCommand),
}

/// Logical task-space operations.
#[derive(Debug, Clone, Subcommand)]
pub enum SpaceCommand {
    /// Create a logical space without launching or discovering a browser.
    Create(SpaceCreateArgs),
    /// List logical spaces visible to the current principal.
    List,
    /// Remove bounded, cleanup-proven released spaces owned by this principal.
    Prune(SpacePruneArgs),
    /// Claim an available logical space with a lease.
    Claim(LeaseArgs),
    /// Renew the current logical lease.
    Renew(LeaseRenewArgs),
    /// Explicitly fence and take over a logical space.
    Takeover(LeaseArgs),
    /// Reclaim a user-owned space with its one-time control ticket.
    Reclaim(SpaceReclaimArgs),
    /// Return the current lease to explicit user control.
    #[command(name = "return")]
    Return(LeaseReturnArgs),
    /// Pause an agent-owned space through the host lifecycle transition.
    Pause(LeaseArgs),
    /// Request user handoff through the host lifecycle transition.
    Handoff(LeaseArgs),
    /// Mark an agent-owned space finished through the host lifecycle transition.
    Finish(SpaceTransitionArgs),
    /// Release a finished space through the host lifecycle transition.
    Release(SpaceTransitionArgs),
}

/// Logical page operations.
#[derive(Debug, Clone, Subcommand)]
pub enum PageCommand {
    /// Create a planned logical page; it does not create a browser target.
    Create(PageCreateArgs),
    /// Create and bind an inactive managed page through the extension bridge.
    #[command(name = "create-managed")]
    CreateManaged(PageCreateManagedArgs),
    /// Close one logically owned page through the host lifecycle transition.
    Close(PageCloseArgs),
    /// List logical pages in one space.
    List(PageListArgs),
    /// Return the bounded logical page inventory for one space.
    Inventory(PageInventoryArgs),
}

/// Action operations.
#[derive(Debug, Clone, Subcommand)]
pub enum ActionCommand {
    /// Execute one logical action through the host bridge.
    Execute(ActionExecuteArgs),
    /// Read one durable action receipt.
    Status(ActionStatusArgs),
    /// Reconcile an action whose outcome is unknown; never re-dispatches it.
    Reconcile(ActionReconcileArgs),
}

/// Host operations.
#[derive(Debug, Clone, Subcommand)]
pub enum HostCommand {
    /// Show host lifecycle, broker epoch, and bridge capabilities.
    Status,
}

/// Extension observations exposed by the host status protocol.
#[derive(Debug, Clone, Subcommand)]
pub enum ExtensionCommand {
    /// Show extension connection and version data observed by the host.
    Status,
}

/// Arguments for `wait`.
#[derive(Debug, Clone, Args)]
pub struct WaitArgs {
    /// Bounded JSON wait condition accepted by the host `wait.for` method.
    #[arg(long, value_name = "JSON")]
    pub condition: String,
    /// Maximum wait duration in milliseconds.
    #[arg(long, default_value_t = DEFAULT_WAIT_TIMEOUT_MS)]
    pub timeout_ms: u64,
    /// Broker epoch of the last processed cursor.
    #[arg(long)]
    pub after_epoch: Option<u64>,
    /// Event sequence of the last processed cursor.
    #[arg(long, default_value_t = 0)]
    pub after_sequence: u64,
    /// Optional logical space scope.
    #[arg(long)]
    pub space_id: Option<String>,
    /// Optional logical page scope; requires `--space-id`.
    #[arg(long)]
    pub page_id: Option<String>,
}

/// Arguments for `space create`.
#[derive(Debug, Clone, Args)]
pub struct SpaceCreateArgs {
    /// User-facing logical label.
    #[arg(long)]
    pub label: String,
    /// Explicitly acknowledge that existing-profile state is shared and is not isolation.
    #[arg(long)]
    pub accept_shared_profile_disclosure: bool,
}

/// Arguments for bounded released-space pruning.
#[derive(Debug, Clone, Args)]
pub struct SpacePruneArgs {
    /// Maximum number of released spaces to remove.
    #[arg(long, default_value_t = 8)]
    pub max_count: u64,
}

/// Arguments shared by lease acquisition and takeover.
#[derive(Debug, Clone, Args)]
pub struct LeaseArgs {
    /// Logical space identity.
    #[arg(long)]
    pub space_id: String,
    /// Lease duration in the host clock domain.
    #[arg(long, default_value_t = DEFAULT_TTL)]
    pub ttl: u64,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `space renew`.
#[derive(Debug, Clone, Args)]
pub struct LeaseRenewArgs {
    /// Logical space identity.
    #[arg(long)]
    pub space_id: String,
    /// Current fencing epoch.
    #[arg(long)]
    pub lease_epoch: u64,
    /// Lease duration in the host clock domain.
    #[arg(long, default_value_t = DEFAULT_TTL)]
    pub ttl: u64,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `space reclaim`.
#[derive(Debug, Clone, Args)]
pub struct SpaceReclaimArgs {
    /// Logical space identity.
    #[arg(long)]
    pub space_id: String,
    /// Optional bounded JSON ticket envelope retained by the caller in memory.
    #[arg(long, value_name = "JSON")]
    pub control_ticket: Option<String>,
    /// Lease duration in the host clock domain.
    #[arg(long, default_value_t = DEFAULT_TTL)]
    pub ttl: u64,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `space return`.
#[derive(Debug, Clone, Args)]
pub struct LeaseReturnArgs {
    /// Logical space identity.
    #[arg(long)]
    pub space_id: String,
    /// Current fencing epoch.
    #[arg(long)]
    pub lease_epoch: u64,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `space finish` and `space release`.
#[derive(Debug, Clone, Args)]
pub struct SpaceTransitionArgs {
    /// Logical space identity.
    #[arg(long)]
    pub space_id: String,
    /// Current fencing epoch authorizing the transition.
    #[arg(long)]
    pub lease_epoch: u64,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `page create`.
#[derive(Debug, Clone, Args)]
pub struct PageCreateArgs {
    /// Logical owning space identity.
    #[arg(long)]
    pub space_id: String,
    /// Current space lease epoch.
    #[arg(long)]
    pub lease_epoch: u64,
    /// User-facing logical label.
    #[arg(long)]
    pub label: String,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `page create-managed`.
#[derive(Debug, Clone, Args)]
pub struct PageCreateManagedArgs {
    /// Logical owning space identity.
    #[arg(long)]
    pub space_id: String,
    /// Current space lease epoch.
    #[arg(long)]
    pub lease_epoch: u64,
    /// User-facing logical label.
    #[arg(long)]
    pub label: String,
    /// Optional initial page URL.
    #[arg(long)]
    pub url: Option<String>,
    /// Optional logical page title.
    #[arg(long)]
    pub title: Option<String>,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `page close`.
#[derive(Debug, Clone, Args)]
pub struct PageCloseArgs {
    /// Logical owning space identity.
    #[arg(long)]
    pub space_id: String,
    /// Logical page identity.
    #[arg(long)]
    pub page_id: String,
    /// Current space lease epoch.
    #[arg(long)]
    pub lease_epoch: u64,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `page list`.
#[derive(Debug, Clone, Args)]
pub struct PageListArgs {
    /// Logical owning space identity.
    #[arg(long)]
    pub space_id: String,
}

/// Arguments for `page inventory`.
#[derive(Debug, Clone, Args)]
pub struct PageInventoryArgs {
    /// Logical owning space identity used to scope the inventory.
    #[arg(long)]
    pub space_id: String,
}

/// Arguments for `snapshot`.
#[derive(Debug, Clone, Args)]
pub struct SnapshotArgs {
    /// Logical owning space identity.
    #[arg(long)]
    pub space_id: String,
    /// Logical page identity.
    #[arg(long)]
    pub page_id: String,
    /// Current space lease epoch.
    #[arg(long)]
    pub lease_epoch: u64,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `action execute`.
#[derive(Debug, Clone, Args)]
pub struct ActionExecuteArgs {
    /// Logical request identity.
    #[arg(long)]
    pub request_id: String,
    /// Durable logical action identity.
    #[arg(long)]
    pub action_id: String,
    /// Caller-supplied logical idempotency identity.
    #[arg(long)]
    pub idempotency_key: String,
    /// Logical owning space identity.
    #[arg(long)]
    pub space_id: String,
    /// Optional logical page target.
    #[arg(long)]
    pub page_id: Option<String>,
    /// Current space lease epoch.
    #[arg(long)]
    pub lease_epoch: u64,
    /// Logical action operation.
    #[arg(long, value_name = "OPERATION")]
    pub operation: String,
    /// Optional bounded JSON object whose values are strings.
    #[arg(long, value_name = "JSON")]
    pub payload: Option<String>,
    /// Optional bounded JSON postcondition object.
    #[arg(long, value_name = "JSON")]
    pub postcondition: Option<String>,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `action status`.
#[derive(Debug, Clone, Args)]
pub struct ActionStatusArgs {
    /// Logical action identity.
    #[arg(long)]
    pub action_id: String,
}

/// Arguments for `action reconcile`.
#[derive(Debug, Clone, Args)]
pub struct ActionReconcileArgs {
    /// Logical action identity.
    #[arg(long)]
    pub action_id: String,
    /// Lease epoch that authorizes reconciliation.
    #[arg(long)]
    pub lease_epoch: u64,
    /// Explicit host timestamp for deterministic callers.
    #[arg(long)]
    pub now: Option<u64>,
}

/// Arguments for `events`.
#[derive(Debug, Clone, Args)]
pub struct EventsArgs {
    /// Broker epoch of the last processed cursor. Defaults to this host epoch.
    #[arg(long)]
    pub after_epoch: Option<u64>,
    /// Event sequence of the last processed cursor.
    #[arg(long, default_value_t = 0)]
    pub after_sequence: u64,
    /// Optional logical space scope.
    #[arg(long)]
    pub space_id: Option<String>,
    /// Optional logical page scope; requires `--space-id`.
    #[arg(long)]
    pub page_id: Option<String>,
    /// Maximum number of returned logical events.
    #[arg(long, default_value_t = DEFAULT_EVENT_LIMIT)]
    pub limit: usize,
}

const DEFAULT_TTL: u64 = 60_000;
const DEFAULT_WAIT_TIMEOUT_MS: u64 = 30_000;
const DEFAULT_EVENT_LIMIT: usize = 256;

/// The host-backed direct client used by command modules.
pub(crate) struct DirectContext {
    transport: DirectTransport,
    control_tickets: RefCell<BTreeMap<SpaceId, ControlTicket>>,
    pub(crate) state_dir: PathBuf,
    pub(crate) offline: bool,
}

pub(crate) type DirectResult<T> = std::result::Result<T, CoreError>;

enum DirectTransport {
    Offline {
        broker: Broker,
        authority: AuthorityTicket,
    },
    Remote {
        client: LocalSocketClient,
    },
    /// Keep command execution structured when the host is not running. This
    /// preserves the CLI's one-JSON-record error contract without creating a
    /// second broker in the CLI process.
    RemoteUnavailable(CoreError),
}

impl DirectContext {
    fn open(options: &DirectOptions) -> DirectResult<Self> {
        let state_dir = resolve_state_dir(options.state_dir.as_deref())?;
        let offline = options.offline || explicit_fake_host_env();
        let principal = principal_id(options.principal.as_deref())?;
        let profile_binding_id =
            resolve_profile_binding_id(options.profile_binding_id.as_deref(), offline)?;
        let hello = direct_hello(principal, profile_binding_id)?;

        if offline {
            let broker = Broker::open(&state_dir, FakeBridge::new()).map_err(host_error)?;
            let authority = broker
                .hello(&hello)
                .map_err(host_error)?
                .authority()
                .clone();
            return Ok(Self {
                transport: DirectTransport::Offline { broker, authority },
                control_tickets: RefCell::new(BTreeMap::new()),
                state_dir,
                offline: true,
            });
        }

        let socket_path = configured_socket_path(&state_dir);
        let transport = match LocalSocketClient::connect(socket_path, hello) {
            Ok(client) => DirectTransport::Remote { client },
            Err(error) => {
                let error = host_error(error);
                let error = if error.code == ErrorCode::NativeHostUnavailable {
                    CoreError::new(ErrorCode::ExtensionNotConnected, error.message)
                } else {
                    error
                };
                DirectTransport::RemoteUnavailable(error)
            }
        };
        Ok(Self {
            transport,
            control_tickets: RefCell::new(BTreeMap::new()),
            state_dir,
            offline: false,
        })
    }

    pub(crate) fn local(&self) -> Option<(&Broker, &AuthorityTicket)> {
        match &self.transport {
            DirectTransport::Offline { broker, authority } => Some((broker, authority)),
            DirectTransport::Remote { .. } | DirectTransport::RemoteUnavailable(_) => None,
        }
    }

    pub(crate) fn remember_control_ticket(&self, ticket: ControlTicket) {
        self.control_tickets
            .borrow_mut()
            .insert(ticket.space_id().clone(), ticket);
    }

    pub(crate) fn control_ticket(&self, space_id: &SpaceId) -> Option<ControlTicket> {
        self.control_tickets.borrow().get(space_id).cloned()
    }

    pub(crate) fn take_control_ticket(&self, space_id: &SpaceId) -> Option<ControlTicket> {
        self.control_tickets.borrow_mut().remove(space_id)
    }

    pub(crate) fn request(
        &self,
        method: impl Into<String>,
        params: BTreeMap<String, String>,
    ) -> DirectResult<BTreeMap<String, String>> {
        match &self.transport {
            DirectTransport::Remote { client } => {
                client.request(method, params).map_err(host_error)
            }
            DirectTransport::RemoteUnavailable(error) => Err(error.clone()),
            DirectTransport::Offline { .. } => Err(CoreError::invalid_argument(
                "remote request is unavailable in offline mode",
            )),
        }
    }
}

fn direct_hello(
    principal: PrincipalId,
    profile_binding_id: Option<ProfileBindingId>,
) -> DirectResult<HelloEnvelope> {
    let nonce = ConnectionNonce::from_suffix(format!("cli-{}", Uuid::new_v4().simple()))
        .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
    Ok(HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: principal,
        resume_from: None,
        client_metadata: Some(agentyc_core::ClientMetadata {
            client_id: Some(
                ClientId::from_suffix("cli")
                    .map_err(|error| CoreError::invalid_argument(error.to_string()))?,
            ),
            client_name: Some("agentyc-cli".to_owned()),
            client_version: Some(env!("CARGO_PKG_VERSION").to_owned()),
            connection_nonce: Some(nonce),
            profile_binding_id,
        }),
    })
}

/// A direct command failure with a stable process exit code.
#[derive(Debug)]
pub struct DirectCommandError {
    code: String,
    exit_code: i32,
}

impl DirectCommandError {
    fn new(code: impl Into<String>, exit_code: i32) -> Self {
        Self {
            code: code.into(),
            exit_code,
        }
    }

    /// Return the process exit code assigned to this direct failure.
    pub const fn exit_code(&self) -> i32 {
        self.exit_code
    }
}

impl std::fmt::Display for DirectCommandError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "direct command failed: {}", self.code)
    }
}

impl std::error::Error for DirectCommandError {}

/// Map a core error to the stable direct CLI exit-code contract.
pub const fn exit_code_for(error: ErrorCode) -> i32 {
    match error {
        ErrorCode::InvalidArgument => 2,
        ErrorCode::NativeHostUnavailable
        | ErrorCode::ExtensionNotConnected
        | ErrorCode::HostDraining
        | ErrorCode::LedgerIncompatible
        | ErrorCode::ProtocolMismatch
        | ErrorCode::TruncatedFrame
        | ErrorCode::InvalidUtf8
        | ErrorCode::InvalidJson
        | ErrorCode::MessageTooLarge => 3,
        ErrorCode::PermissionDenied
        | ErrorCode::CapabilityUnavailable
        | ErrorCode::ProfileNotFound
        | ErrorCode::SpaceForbidden
        | ErrorCode::UserControlRequired => 4,
        ErrorCode::Timeout | ErrorCode::Cancelled => 6,
        ErrorCode::UnknownOutcome => 7,
        _ => 5,
    }
}

pub fn exit_code_for_name(code: &str) -> i32 {
    match code {
        "invalid_argument" => 2,
        "native_host_unavailable"
        | "extension_not_connected"
        | "host_draining"
        | "ledger_incompatible"
        | "protocol_mismatch"
        | "truncated_frame"
        | "invalid_utf8"
        | "invalid_json"
        | "message_too_large" => 3,
        "permission_denied"
        | "capability_unavailable"
        | "profile_not_found"
        | "space_forbidden"
        | "user_control_required" => 4,
        "timeout" | "cancelled" => 6,
        "unknown_outcome" => 7,
        _ => 5,
    }
}

fn serialize_response(response: &Value, compact: bool) -> Result<String> {
    if compact {
        serde_json::to_string(response).map_err(|error| anyhow!(error))
    } else {
        serde_json::to_string_pretty(response).map_err(|error| anyhow!(error))
    }
}

/// Run one direct command and print exactly one JSON value to stdout.
pub fn run(
    command: DirectCommand,
    options: DirectOptions,
) -> std::result::Result<(), DirectCommandError> {
    let response =
        match DirectContext::open(&options).and_then(|context| execute(&context, command)) {
            Ok(result) => success(result),
            Err(error) => failure(&error),
        };
    let compact = options.json;
    let output = serialize_response(&response, compact).map_err(|_error| {
        DirectCommandError::new("invalid_json", exit_code_for(ErrorCode::InvalidJson))
    })?;
    println!("{output}");
    if response.get("ok") == Some(&Value::Bool(false)) {
        let code = response
            .get("error")
            .and_then(|error| error.get("code"))
            .and_then(Value::as_str)
            .unwrap_or("invalid_argument");
        return Err(DirectCommandError::new(code, exit_code_for_name(code)));
    }
    Ok(())
}

fn execute(context: &DirectContext, command: DirectCommand) -> DirectResult<Value> {
    match command {
        DirectCommand::Space(command) => spaces::run(context, command),
        DirectCommand::Page(command) => pages::run(context, command),
        DirectCommand::Snapshot(args) => snapshot::run(context, args),
        DirectCommand::Action(command) => actions::run(context, command),
        DirectCommand::Events(args) => events::run(context, args),
        DirectCommand::Host(command) => host::run(context, command),
        DirectCommand::Wait(args) => waits::run(context, args),
        DirectCommand::Extension(command) => extension::run(context, command),
    }
}

pub(crate) fn host_error(error: HostError) -> CoreError {
    error.as_core_error()
}

pub(crate) fn parse_value<T>(value: &str, field: &str) -> DirectResult<T>
where
    T: FromStr,
    T::Err: std::fmt::Display,
{
    value
        .parse::<T>()
        .map_err(|error| CoreError::invalid_argument(format!("invalid {field}: {error}")))
}

pub(crate) fn parse_space(value: &str) -> DirectResult<SpaceId> {
    parse_value(value, "space_id")
}

pub(crate) fn parse_page(value: &str) -> DirectResult<PageId> {
    parse_value(value, "page_id")
}

pub(crate) fn parse_action(value: &str) -> DirectResult<ActionId> {
    parse_value(value, "action_id")
}

pub(crate) fn lease_epoch(value: u64) -> agentyc_core::LeaseEpoch {
    agentyc_core::LeaseEpoch::new(value)
}

pub(crate) fn timestamp(value: Option<u64>) -> Timestamp {
    Timestamp::new(value.unwrap_or_else(now_millis))
}

pub(crate) fn remote_field(
    response: &BTreeMap<String, String>,
    field: &str,
) -> DirectResult<Value> {
    let encoded = response.get(field).ok_or_else(|| {
        CoreError::new(
            ErrorCode::InvalidJson,
            format!("remote response is missing result field {field}"),
        )
    })?;
    match serde_json::from_str(encoded) {
        Ok(value) => Ok(value),
        Err(_error)
            if !matches!(
                encoded.trim_start().chars().next(),
                Some('{') | Some('[') | Some('"')
            ) =>
        {
            // The legacy string-map adapter may return a scalar string without
            // JSON quotes. Structured-looking values remain strict so malformed
            // objects and arrays cannot silently change result shape.
            Ok(Value::String(encoded.clone()))
        }
        Err(error) => Err(CoreError::new(
            ErrorCode::InvalidJson,
            format!("remote result field {field} is not valid JSON: {error}"),
        )),
    }
}

pub(crate) fn remote_string(
    response: &BTreeMap<String, String>,
    field: &str,
) -> DirectResult<String> {
    match remote_field(response, field)? {
        Value::String(value) => Ok(value),
        value => Err(CoreError::new(
            ErrorCode::InvalidJson,
            format!("remote result field {field} is not a JSON string: {value}"),
        )),
    }
}

pub(crate) fn success(result: Value) -> Value {
    json!({"ok": true, "result": result})
}

pub(crate) fn failure(error: &CoreError) -> Value {
    json!({
        "ok": false,
        "error": {
            "code": error.code.as_str(),
            "message": error.message,
            "retryable": error.retryable,
            "guidance": error.guidance,
        }
    })
}

fn resolve_state_dir(explicit: Option<&str>) -> DirectResult<PathBuf> {
    if let Some(path) = explicit.filter(|value| !value.is_empty()) {
        return Ok(PathBuf::from(path));
    }
    if let Ok(path) = std::env::var("AGENTYC_STATE_DIR")
        && !path.trim().is_empty()
    {
        return Ok(PathBuf::from(path));
    }
    dirs::home_dir()
        .map(|home| home.join(".agentyc").join("state"))
        .ok_or_else(|| {
            CoreError::new(
                agentyc_core::ErrorCode::LedgerIncompatible,
                "home directory is unavailable; pass --state-dir",
            )
        })
}

fn principal_id(explicit: Option<&str>) -> DirectResult<PrincipalId> {
    let value = explicit
        .map(str::to_owned)
        .or_else(|| std::env::var("AGENTYC_PRINCIPAL").ok())
        .unwrap_or_else(|| "principal_cli".to_owned());
    if value.starts_with(PrincipalId::PREFIX) {
        PrincipalId::new(value).map_err(|error| CoreError::invalid_argument(error.to_string()))
    } else {
        PrincipalId::from_suffix(value)
            .map_err(|error| CoreError::invalid_argument(error.to_string()))
    }
}

fn resolve_profile_binding_id(
    explicit: Option<&str>,
    offline: bool,
) -> DirectResult<Option<ProfileBindingId>> {
    let value = explicit
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| std::env::var("AGENTYC_PROFILE_BINDING").ok())
        .filter(|value| !value.trim().is_empty());
    let Some(value) = value else {
        return if offline {
            Ok(Some(ProfileBindingId::from_suffix("cli-offline").map_err(
                |error| CoreError::invalid_argument(error.to_string()),
            )?))
        } else {
            Ok(None)
        };
    };
    if value.starts_with(ProfileBindingId::PREFIX) {
        ProfileBindingId::new(value)
            .map(Some)
            .map_err(|error| CoreError::invalid_argument(error.to_string()))
    } else {
        ProfileBindingId::from_suffix(value)
            .map(Some)
            .map_err(|error| CoreError::invalid_argument(error.to_string()))
    }
}

fn explicit_fake_host_env() -> bool {
    matches!(
        std::env::var("AGENTYC_FAKE_HOST").as_deref(),
        Ok("1") | Ok("true") | Ok("yes")
    )
}

fn now_millis() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok())
        .unwrap_or(0)
}

#[allow(dead_code)]
fn _keep_direct_protocol_types_visible(_epoch: BrokerEpoch, _sequence: EventSequence) {}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn options(path: &std::path::Path) -> DirectOptions {
        DirectOptions {
            state_dir: Some(path.display().to_string()),
            principal: Some("principal_test".to_owned()),
            profile_binding_id: None,
            offline: true,
            json: false,
        }
    }

    #[test]
    fn fake_host_direct_path_is_logical_and_persistent() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let space = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "test space".to_owned(),
                accept_shared_profile_disclosure: true,
            })),
        )
        .expect("create");
        let space_id = space["space_id"].as_str().expect("logical space id");
        assert!(space_id.starts_with("space_"));
        let serialized = serde_json::to_string(&space).expect("json");
        for forbidden in ["target_id", "tab_id", "session_id", "debugger_id", "cdp"] {
            assert!(!serialized.contains(forbidden), "unexpected {forbidden}");
        }
        drop(context);

        let reopened = DirectContext::open(&options(directory.path())).expect("reopen");
        let spaces = execute(&reopened, DirectCommand::Space(SpaceCommand::List)).expect("list");
        assert_eq!(spaces["spaces"].as_array().expect("spaces").len(), 1);
    }

    #[test]
    fn direct_hello_carries_an_explicit_profile_binding() {
        let principal = PrincipalId::from_suffix("profile-test").expect("principal");
        let profile = resolve_profile_binding_id(Some("enrolled"), false)
            .expect("profile binding")
            .expect("profile binding present");
        let hello = direct_hello(principal, Some(profile.clone())).expect("hello");
        assert_eq!(
            hello
                .client_metadata
                .expect("client metadata")
                .profile_binding_id,
            Some(profile)
        );
        assert_eq!(
            resolve_profile_binding_id(None, true)
                .expect("offline profile")
                .expect("offline profile present")
                .as_str(),
            "profile_cli-offline"
        );
    }

    #[test]
    fn offline_return_control_reclaims_with_one_time_ticket() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let created = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "handoff".to_owned(),
                accept_shared_profile_disclosure: true,
            })),
        )
        .expect("create space");
        let space_id = created["space_id"].as_str().expect("space id").to_owned();
        let claimed = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                space_id: space_id.clone(),
                ttl: DEFAULT_TTL,
                now: Some(1),
            })),
        )
        .expect("claim space");
        let lease_epoch = claimed["lease"]["lease_epoch"]
            .as_u64()
            .expect("lease epoch");

        let returned = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Return(LeaseReturnArgs {
                space_id: space_id.clone(),
                lease_epoch,
                now: Some(2),
            })),
        )
        .expect("return control");
        assert_eq!(returned["lifecycle"], "user_owned");
        assert_eq!(returned["control_ticket"]["in_memory"], true);
        let mut mismatched_ticket = returned["control_ticket"].clone();
        mismatched_ticket["fence_epoch"] = json!(999);
        let error = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Reclaim(SpaceReclaimArgs {
                space_id: space_id.clone(),
                control_ticket: Some(
                    serde_json::to_string(&mismatched_ticket).expect("mismatched ticket json"),
                ),
                ttl: DEFAULT_TTL,
                now: Some(3),
            })),
        )
        .expect_err("mismatched ticket metadata must be rejected");
        assert_eq!(error.code, ErrorCode::InvalidArgument);

        let ticket_json = serde_json::to_string(&returned["control_ticket"]).expect("ticket json");
        let reclaimed = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Reclaim(SpaceReclaimArgs {
                space_id: space_id.clone(),
                control_ticket: Some(ticket_json),
                ttl: DEFAULT_TTL,
                now: Some(3),
            })),
        )
        .expect("reclaim space");
        assert_eq!(reclaimed["space_id"], space_id);
        assert_eq!(reclaimed["lifecycle"], "agent_owned");

        let error = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Reclaim(SpaceReclaimArgs {
                space_id,
                control_ticket: None,
                ttl: DEFAULT_TTL,
                now: Some(4),
            })),
        )
        .expect_err("one-time ticket must be consumed");
        assert_eq!(error.code, ErrorCode::UserControlRequired);
    }

    #[test]
    fn offline_inventory_and_action_execute_stay_logical() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let created = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "action space".to_owned(),
                accept_shared_profile_disclosure: true,
            })),
        )
        .expect("create space");
        let space_id = created["space_id"].as_str().expect("space id").to_owned();
        let claimed = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                space_id: space_id.clone(),
                ttl: DEFAULT_TTL,
                now: Some(1),
            })),
        )
        .expect("claim space");
        let lease_epoch = claimed["lease"]["lease_epoch"]
            .as_u64()
            .expect("lease epoch");
        execute(
            &context,
            DirectCommand::Page(PageCommand::Create(PageCreateArgs {
                space_id: space_id.clone(),
                lease_epoch,
                label: "logical page".to_owned(),
                now: Some(2),
            })),
        )
        .expect("create page");

        let inventory = execute(
            &context,
            DirectCommand::Page(PageCommand::Inventory(PageInventoryArgs {
                space_id: space_id.clone(),
            })),
        )
        .expect("inventory");
        assert_eq!(inventory["space_id"], space_id);
        assert_eq!(inventory["pages"].as_array().expect("pages").len(), 1);

        let action = execute(
            &context,
            DirectCommand::Action(ActionCommand::Execute(ActionExecuteArgs {
                request_id: "req_direct_wait".to_owned(),
                action_id: "action_direct_wait".to_owned(),
                idempotency_key: "idem_direct_wait".to_owned(),
                space_id,
                page_id: None,
                lease_epoch,
                operation: "wait".to_owned(),
                payload: Some(r#"{"timeout_ms":"1"}"#.to_owned()),
                postcondition: Some(
                    r#"{"kind":"page_generation","document_generation":0}"#.to_owned(),
                ),
                now: Some(3),
            })),
        )
        .expect("execute action");
        assert_eq!(action["action_id"], "action_direct_wait");
        assert_eq!(action["receipt"]["status"], "succeeded");
    }

    #[cfg(unix)]
    #[test]
    fn remote_direct_path_uses_the_owner_socket_and_preserves_shapes() {
        let directory = tempdir().expect("tempdir");
        let broker = Broker::open(directory.path(), FakeBridge::new()).expect("broker");
        let server =
            agentyc_host::LocalHostServer::start(broker, configured_socket_path(directory.path()))
                .expect("local host server");
        let options = DirectOptions {
            state_dir: Some(directory.path().display().to_string()),
            principal: Some("principal_remote_test".to_owned()),
            profile_binding_id: None,
            offline: false,
            json: true,
        };
        let context = DirectContext::open(&options).expect("remote context");

        let created = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "remote space".to_owned(),
                accept_shared_profile_disclosure: true,
            })),
        )
        .expect("remote create");
        let space_id = created["space_id"].as_str().expect("space id").to_owned();
        assert!(created["space"].is_object());

        let claimed = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                space_id: space_id.clone(),
                ttl: DEFAULT_TTL,
                now: Some(1),
            })),
        )
        .expect("remote claim");
        let lease_epoch = claimed["lease"]["lease_epoch"]
            .as_u64()
            .expect("lease epoch");

        let renewed = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Renew(LeaseRenewArgs {
                space_id: space_id.clone(),
                lease_epoch,
                ttl: DEFAULT_TTL,
                now: Some(2),
            })),
        )
        .expect("remote renew");
        assert_eq!(renewed["space_id"], space_id);

        let page = execute(
            &context,
            DirectCommand::Page(PageCommand::Create(PageCreateArgs {
                space_id: space_id.clone(),
                lease_epoch,
                label: "main".to_owned(),
                now: Some(3),
            })),
        )
        .expect("remote page create");
        assert!(page["page"].is_object());
        let pages = execute(
            &context,
            DirectCommand::Page(PageCommand::List(PageListArgs {
                space_id: space_id.clone(),
            })),
        )
        .expect("remote page list");
        assert_eq!(pages["pages"].as_array().expect("pages").len(), 1);

        let action = execute(
            &context,
            DirectCommand::Action(ActionCommand::Execute(ActionExecuteArgs {
                request_id: "req_remote_wait".to_owned(),
                action_id: "action_remote_wait".to_owned(),
                idempotency_key: "idem_remote_wait".to_owned(),
                space_id: space_id.clone(),
                page_id: None,
                lease_epoch,
                operation: "wait".to_owned(),
                payload: None,
                postcondition: None,
                now: Some(3),
            })),
        )
        .expect("remote action execute");
        assert_eq!(action["action_id"], "action_remote_wait");
        assert_eq!(action["receipt"]["status"], "succeeded");

        let wait_error = execute(
            &context,
            DirectCommand::Wait(WaitArgs {
                condition: r#"{"kind":"event_kind","event":"page.changed"}"#.to_owned(),
                timeout_ms: 1,
                after_epoch: None,
                after_sequence: 0,
                space_id: Some(space_id.clone()),
                page_id: None,
            }),
        )
        .expect_err("wait.for should time out without a matching event");
        assert_eq!(wait_error.code, ErrorCode::Timeout);

        let events = execute(
            &context,
            DirectCommand::Events(EventsArgs {
                after_epoch: None,
                after_sequence: 0,
                space_id: Some(space_id.clone()),
                page_id: None,
                limit: DEFAULT_EVENT_LIMIT,
            }),
        )
        .expect("remote events");
        assert!(!events["events"].as_array().expect("events").is_empty());

        let status = execute(&context, DirectCommand::Host(HostCommand::Status))
            .expect("remote host status");
        assert_eq!(status["bridge"]["test_seam"], false);
        let extension = execute(&context, DirectCommand::Extension(ExtensionCommand::Status))
            .expect("remote extension status");
        assert_eq!(extension["observed_connected"], true);
        assert_eq!(status["bridge"]["connected"], true);
        assert_eq!(status["lifecycle"], "ready");

        let create_remote_space = |label: &str| {
            let created = execute(
                &context,
                DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                    label: label.to_owned(),
                    accept_shared_profile_disclosure: true,
                })),
            )
            .expect("remote create");
            let created_id = created["space_id"].as_str().expect("space id").to_owned();
            execute(
                &context,
                DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                    space_id: created_id,
                    ttl: DEFAULT_TTL,
                    now: Some(4),
                })),
            )
            .expect("remote claim")["space_id"]
                .as_str()
                .expect("space id")
                .to_owned()
        };
        let paused_id = create_remote_space("remote-paused");
        let paused = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Pause(LeaseArgs {
                space_id: paused_id,
                ttl: DEFAULT_TTL,
                now: Some(5),
            })),
        )
        .expect("remote pause");
        assert_eq!(paused["lifecycle"], "paused");
        let handoff_id = create_remote_space("remote-handoff");
        let handoff = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Handoff(LeaseArgs {
                space_id: handoff_id,
                ttl: DEFAULT_TTL,
                now: Some(5),
            })),
        )
        .expect("remote handoff");
        assert_eq!(handoff["lifecycle"], "handoff_requested");

        let finished = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Finish(SpaceTransitionArgs {
                space_id: space_id.clone(),
                lease_epoch,
                now: Some(4),
            })),
        )
        .expect("remote finish");
        assert_eq!(finished["lifecycle"], "finished");
        let released = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Release(SpaceTransitionArgs {
                space_id,
                lease_epoch,
                now: Some(5),
            })),
        )
        .expect("remote release");
        assert_eq!(released["lifecycle"], "released");

        drop(context);
        server.stop();
    }

    #[test]
    fn host_lifecycle_transitions_finish_then_release_a_claimed_space() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let created = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "retained".to_owned(),
                accept_shared_profile_disclosure: true,
            })),
        )
        .expect("create");
        let space_id = created["space_id"].as_str().expect("space id").to_owned();
        let claimed = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                space_id: space_id.clone(),
                ttl: DEFAULT_TTL,
                now: Some(1),
            })),
        )
        .expect("claim");
        let lease_epoch = claimed["lease"]["lease_epoch"]
            .as_u64()
            .expect("lease epoch");

        let finished = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Finish(SpaceTransitionArgs {
                space_id: space_id.clone(),
                lease_epoch,
                now: Some(2),
            })),
        )
        .expect("finish");
        assert_eq!(finished["space_id"], space_id);
        assert_eq!(finished["lifecycle"], "finished");

        let released = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Release(SpaceTransitionArgs {
                space_id: space_id.clone(),
                lease_epoch,
                now: Some(3),
            })),
        )
        .expect("release");
        assert_eq!(released["space_id"], space_id);
        assert_eq!(released["lifecycle"], "released");

        let listed = execute(&context, DirectCommand::Space(SpaceCommand::List)).expect("list");
        assert_eq!(listed["spaces"][0]["lifecycle"], "released");
    }

    #[test]
    fn pause_and_handoff_wrappers_use_host_lifecycle_transitions() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");

        let create_and_claim = |label: &str| {
            let created = execute(
                &context,
                DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                    label: label.to_owned(),
                    accept_shared_profile_disclosure: true,
                })),
            )
            .expect("create");
            let space_id = created["space_id"].as_str().expect("space id").to_owned();
            let claimed = execute(
                &context,
                DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                    space_id,
                    ttl: DEFAULT_TTL,
                    now: Some(1),
                })),
            )
            .expect("claim");
            (
                claimed["space_id"].as_str().expect("space id").to_owned(),
                claimed["lease"]["lease_epoch"]
                    .as_u64()
                    .expect("lease epoch"),
            )
        };

        let (paused_id, _) = create_and_claim("paused");
        let (handoff_id, _) = create_and_claim("handoff");
        let paused = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Pause(LeaseArgs {
                space_id: paused_id,
                ttl: DEFAULT_TTL,
                now: Some(2),
            })),
        )
        .expect("pause");
        assert_eq!(paused["lifecycle"], "paused");

        let handoff = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Handoff(LeaseArgs {
                space_id: handoff_id,
                ttl: DEFAULT_TTL,
                now: Some(2),
            })),
        )
        .expect("handoff");
        assert_eq!(handoff["lifecycle"], "handoff_requested");
    }

    #[test]
    fn extension_status_reports_observed_host_data_without_install_claims() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let status = execute(&context, DirectCommand::Extension(ExtensionCommand::Status))
            .expect("extension status");
        assert_eq!(status["observed_connected"], false);
        assert!(status.get("installed").is_none());
        assert!(status.get("extension_id").is_none());
    }

    #[test]
    fn wait_wrapper_rejects_non_object_conditions_before_transport() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let error = execute(
            &context,
            DirectCommand::Wait(WaitArgs {
                condition: "[]".to_owned(),
                timeout_ms: 1,
                after_epoch: None,
                after_sequence: 0,
                space_id: None,
                page_id: None,
            }),
        )
        .expect_err("non-object wait condition");
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }

    #[test]
    fn lifecycle_transitions_reject_a_stale_lease_epoch() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let created = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "stale".to_owned(),
                accept_shared_profile_disclosure: true,
            })),
        )
        .expect("create");
        let space_id = created["space_id"].as_str().expect("space id").to_owned();
        execute(
            &context,
            DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                space_id: space_id.clone(),
                ttl: DEFAULT_TTL,
                now: Some(1),
            })),
        )
        .expect("claim");

        let error = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Finish(SpaceTransitionArgs {
                space_id,
                lease_epoch: 99,
                now: Some(2),
            })),
        )
        .expect_err("stale finish");
        assert_eq!(error.code, agentyc_core::ErrorCode::StaleLease);
    }

    #[test]
    fn unavailable_extension_is_structured_and_never_legacy_runtime() {
        let directory = tempdir().expect("tempdir");
        let mut opts = options(directory.path());
        opts.offline = false;
        let context = DirectContext::open(&opts).expect("context");
        let error = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Claim(LeaseArgs {
                space_id: "space_missing".to_owned(),
                ttl: DEFAULT_TTL,
                now: Some(1),
            })),
        )
        .expect_err("claim must fail without extension");
        assert!(matches!(
            error.code,
            agentyc_core::ErrorCode::CapabilityUnavailable
                | agentyc_core::ErrorCode::ExtensionNotConnected
                | agentyc_core::ErrorCode::SpaceNotFound
        ));
        assert!(
            !serde_json::to_string(&failure(&error))
                .expect("json")
                .contains("cdp")
        );
    }

    #[test]
    fn direct_exit_codes_cover_the_structured_error_contract() {
        assert_eq!(exit_code_for(ErrorCode::InvalidArgument), 2);
        assert_eq!(exit_code_for(ErrorCode::NativeHostUnavailable), 3);
        assert_eq!(exit_code_for(ErrorCode::MessageTooLarge), 3);
        assert_eq!(exit_code_for(ErrorCode::PermissionDenied), 4);
        assert_eq!(exit_code_for(ErrorCode::Timeout), 6);
        assert_eq!(exit_code_for(ErrorCode::UnknownOutcome), 7);
        assert_eq!(exit_code_for_name("not_yet_known"), 5);
    }

    #[test]
    fn remote_string_map_decoding_accepts_json_scalars_and_legacy_raw_strings() {
        let response = BTreeMap::from([
            ("object".to_owned(), r#"{"ok":true}"#.to_owned()),
            ("array".to_owned(), "[1,2]".to_owned()),
            ("quoted".to_owned(), r#""quoted""#.to_owned()),
            ("number".to_owned(), "7".to_owned()),
            ("boolean".to_owned(), "true".to_owned()),
            ("null".to_owned(), "null".to_owned()),
            ("raw".to_owned(), "space_raw".to_owned()),
        ]);

        assert_eq!(
            remote_field(&response, "object").expect("object"),
            json!({"ok": true})
        );
        assert_eq!(
            remote_field(&response, "array").expect("array"),
            json!([1, 2])
        );
        assert_eq!(
            remote_string(&response, "quoted").expect("quoted"),
            "quoted"
        );
        assert_eq!(remote_field(&response, "number").expect("number"), json!(7));
        assert_eq!(
            remote_field(&response, "boolean").expect("boolean"),
            json!(true)
        );
        assert_eq!(remote_field(&response, "null").expect("null"), Value::Null);
        assert_eq!(remote_string(&response, "raw").expect("raw"), "space_raw");
    }

    #[test]
    fn remote_string_map_decoding_keeps_malformed_structured_values_strict() {
        let response = BTreeMap::from([("object".to_owned(), "{not-json".to_owned())]);
        let error = remote_field(&response, "object").expect_err("malformed object");
        assert_eq!(error.code, ErrorCode::InvalidJson);
    }

    #[test]
    fn direct_serialization_has_compact_and_pretty_machine_forms() {
        let response = success(json!({"space_id": "space_test"}));
        let compact = serialize_response(&response, true).expect("compact json");
        let pretty = serialize_response(&response, false).expect("pretty json");
        assert_eq!(compact, r#"{"ok":true,"result":{"space_id":"space_test"}}"#);
        assert!(pretty.contains('\n'));
        assert_eq!(
            serde_json::from_str::<Value>(&compact).expect("compact value"),
            response
        );
        assert_eq!(
            serde_json::from_str::<Value>(&pretty).expect("pretty value"),
            response
        );
    }
}
