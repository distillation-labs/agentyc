//! Direct host-backed interfaces for logical task spaces and pages.
//!
//! This module is deliberately separate from the legacy CDP/runtime frontend. The
//! direct path opens the durable host ledger, performs the core handshake, and
//! talks only in logical identities. It never launches a browser or accepts a
//! copied browser debugging endpoint.

use std::{
    path::PathBuf,
    str::FromStr,
    time::{SystemTime, UNIX_EPOCH},
};

use agentyc_core::{
    ActionId, BrokerEpoch, ClientId, ConnectionNonce, CoreError, EventSequence, HelloEnvelope,
    PROTOCOL_VERSION, PageId, PrincipalId, SpaceId, Timestamp,
};
use agentyc_host::{Broker, FakeBridge, HostError, NullBridge};
use anyhow::{Result, anyhow};
use clap::{Args, Subcommand};
use serde_json::{Value, json};
use uuid::Uuid;

mod actions;
mod events;
mod host;
mod pages;
mod snapshot;
mod spaces;

/// Options shared by every direct command.
#[derive(Debug, Clone, Default)]
pub struct DirectOptions {
    /// Optional durable state directory. Defaults to `$AGENTYC_STATE_DIR` or
    /// `~/.agentyc/state`.
    pub state_dir: Option<String>,
    /// Logical principal suffix or complete `principal_` identity.
    pub principal: Option<String>,
    /// Use the explicit deterministic in-process fake bridge seam.
    pub offline: bool,
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
}

/// Logical task-space operations.
#[derive(Debug, Clone, Subcommand)]
pub enum SpaceCommand {
    /// Create a logical space without launching or discovering a browser.
    Create(SpaceCreateArgs),
    /// List logical spaces visible to the current principal.
    List,
    /// Claim an available logical space with a lease.
    Claim(LeaseArgs),
    /// Renew the current logical lease.
    Renew(LeaseRenewArgs),
    /// Explicitly fence and take over a logical space.
    Takeover(LeaseArgs),
    /// Return the current lease to explicit user control.
    #[command(name = "return")]
    Return(LeaseReturnArgs),
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
    /// List logical pages in one space.
    List(PageListArgs),
}

/// Action operations.
#[derive(Debug, Clone, Subcommand)]
pub enum ActionCommand {
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

/// Arguments for `space create`.
#[derive(Debug, Clone, Args)]
pub struct SpaceCreateArgs {
    /// User-facing logical label.
    #[arg(long)]
    pub label: String,
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

/// Arguments for `page list`.
#[derive(Debug, Clone, Args)]
pub struct PageListArgs {
    /// Logical owning space identity.
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
const DEFAULT_EVENT_LIMIT: usize = 256;

/// The host-backed direct client used by command modules.
pub(crate) struct DirectContext {
    pub(crate) broker: Broker,
    pub(crate) authority: agentyc_host::AuthorityTicket,
    pub(crate) state_dir: PathBuf,
    pub(crate) offline: bool,
}

pub(crate) type DirectResult<T> = std::result::Result<T, CoreError>;

impl DirectContext {
    fn open(options: &DirectOptions) -> DirectResult<Self> {
        let state_dir = resolve_state_dir(options.state_dir.as_deref())?;
        let offline = options.offline || explicit_fake_host_env();
        let bridge = if offline {
            BridgeKind::Fake(FakeBridge::new())
        } else {
            BridgeKind::Null(NullBridge)
        };
        let broker = match bridge {
            BridgeKind::Fake(fake) => Broker::open(&state_dir, fake),
            BridgeKind::Null(null) => Broker::open(&state_dir, null),
        }
        .map_err(host_error)?;
        let principal = principal_id(options.principal.as_deref())?;
        let authority = if offline {
            broker.test_authority(principal).map_err(host_error)?
        } else {
            let nonce = ConnectionNonce::from_suffix(format!("cli-{}", Uuid::new_v4().simple()))
                .map_err(|error| CoreError::invalid_argument(error.to_string()))?;
            let hello = HelloEnvelope {
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
                    profile_binding_id: None,
                }),
            };
            broker
                .hello(&hello)
                .map_err(host_error)?
                .authority()
                .clone()
        };
        Ok(Self {
            broker,
            authority,
            state_dir,
            offline,
        })
    }
}

enum BridgeKind {
    Fake(FakeBridge),
    Null(NullBridge),
}

/// Run one direct command and print exactly one JSON value to stdout.
pub fn run(command: DirectCommand, options: DirectOptions) -> Result<()> {
    let response =
        match DirectContext::open(&options).and_then(|context| execute(&context, command)) {
            Ok(result) => success(result),
            Err(error) => failure(&error),
        };
    println!(
        "{}",
        serde_json::to_string_pretty(&response).map_err(|error| anyhow!(error))?
    );
    if response.get("ok") == Some(&Value::Bool(false)) {
        let code = response
            .get("error")
            .and_then(|error| error.get("code"))
            .and_then(Value::as_str)
            .unwrap_or("invalid_argument");
        return Err(anyhow!("direct command failed: {code}"));
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
            offline: true,
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
            })),
        )
        .expect("create");
        let space_id = space["space_id"].as_str().expect("logical space id");
        assert!(space_id.starts_with("space_"));
        assert!(
            !serde_json::to_string(&space)
                .expect("json")
                .contains("target_id")
        );
        drop(context);

        let reopened = DirectContext::open(&options(directory.path())).expect("reopen");
        let spaces = execute(&reopened, DirectCommand::Space(SpaceCommand::List)).expect("list");
        assert_eq!(spaces["spaces"].as_array().expect("spaces").len(), 1);
    }

    #[test]
    fn host_lifecycle_transitions_finish_then_release_a_claimed_space() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let created = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "retained".to_owned(),
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
    fn lifecycle_transitions_reject_a_stale_lease_epoch() {
        let directory = tempdir().expect("tempdir");
        let context = DirectContext::open(&options(directory.path())).expect("context");
        let created = execute(
            &context,
            DirectCommand::Space(SpaceCommand::Create(SpaceCreateArgs {
                label: "stale".to_owned(),
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
}
