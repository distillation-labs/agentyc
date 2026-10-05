#![allow(clippy::collapsible_if)]

use mimalloc::MiMalloc;
#[global_allocator]
static GLOBAL: MiMalloc = MiMalloc;

use agentyc_core::{
    ClientId, ClientMetadata, ConnectionNonce, HelloEnvelope, PROTOCOL_VERSION, PrincipalId,
    ProfileBindingId,
};
use agentyc_host::{Broker, FakeBridge, LocalSocketClient, configured_socket_path};
use anyhow::{Result, anyhow};
use clap::{Parser, Subcommand};
use tracing_subscriber::EnvFilter;
use uuid::Uuid;

mod commands;

use commands::direct::{
    ActionCommand as DirectActionCommand, DirectCommand, DirectCommandError, DirectOptions,
    EventsArgs, ExtensionCommand, HostCommand, PageCommand as DirectPageCommand, SnapshotArgs,
    SpaceCommand, WaitArgs,
};


const SKILL_MD: &str = include_str!("../../../SKILL.md");

#[derive(Parser)]
#[command(
    name = "agentyc",
    about = "Host-backed browser task spaces for coding agents",
    version
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Cmd>,
    /// Durable state directory for direct host-backed commands.
    #[arg(long, global = true, value_name = "PATH")]
    state_dir: Option<String>,
    /// Logical principal suffix or complete `principal_` identity.
    #[arg(long, global = true, value_name = "PRINCIPAL")]
    principal: Option<String>,
    /// Enrolled profile binding suffix or complete `profile_` identity.
    #[arg(long, global = true, value_name = "PROFILE_BINDING_ID")]
    profile_binding_id: Option<String>,
    /// Use the explicit deterministic fake-host seam for direct commands.
    #[arg(long, global = true)]
    offline: bool,
    /// Emit compact structured JSON for direct commands.
    #[arg(long, global = true)]
    json: bool,
}

#[derive(Subcommand)]
enum Cmd {
    /// Run the host-backed MCP server over stdio.
    Mcp,
    /// Write the agentyc skills guide to a file.
    Init {
        #[arg(long, default_value = "agentyc-skill.md")]
        output: String,
        #[arg(long)]
        print: bool,
        #[arg(long)]
        force: bool,
    },

    /// Manage logical task spaces through the host broker.
    Space {
        #[command(subcommand)]
        command: SpaceCommand,
    },
    /// Manage logical pages through the host broker.
    Page {
        #[command(subcommand)]
        command: DirectPageCommand,
    },
    /// Read a logical page snapshot through the host broker.
    Snapshot(SnapshotArgs),
    /// Inspect or reconcile a durable action receipt.
    Action {
        #[command(subcommand)]
        command: DirectActionCommand,
    },
    /// Resume logical host events.
    Events(EventsArgs),
    /// Inspect the direct host lifecycle and bridge.
    Host {
        #[command(subcommand)]
        command: HostCommand,
    },
    /// Wait for a host-observed logical condition.
    Wait(WaitArgs),
    /// Inspect extension state observed by the host.
    Extension {
        #[command(subcommand)]
        command: ExtensionCommand,
    },
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        let exit_code = error
            .downcast_ref::<DirectCommandError>()
            .map_or(1, DirectCommandError::exit_code);
        eprintln!("Error: {error}");
        std::process::exit(exit_code);
    }
}

async fn run() -> Result<()> {
    // stderr-only tracing — stdout is the JSON-RPC channel
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(
            EnvFilter::try_from_env("AGENTYC_LOGGING_LEVEL")
                .unwrap_or_else(|_| EnvFilter::new("warn")),
        )
        .init();

    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(error) => {
            if matches!(
                error.kind(),
                clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion
            ) {
                error.print()?;
                return Ok(());
            }
            let json_requested = std::env::args_os().any(|argument| argument == "--json");
            if json_requested {
                println!(
                    "{}",
                    serde_json::to_string(&serde_json::json!({
                        "ok": false,
                        "error": {
                            "code": "invalid_argument",
                            "message": error.to_string(),
                            "retryable": false,
                            "guidance": "none"
                        }
                    }))?
                );
                return Err(anyhow::Error::new(DirectCommandError::new(
                    "invalid_argument",
                    2,
                )));
            }
            return Err(error.into());
        }
    };
    let direct_options = DirectOptions {
        state_dir: cli.state_dir.clone(),
        principal: cli.principal.clone(),
        profile_binding_id: cli.profile_binding_id.clone(),
        offline: cli.offline,
        json: cli.json,
    };

    match cli.command {
        None | Some(Cmd::Mcp) => run_host_mcp(&direct_options).await,
        Some(Cmd::Init {
            output,
            print,
            force,
        }) => cmd_init(&output, print, force),

        Some(Cmd::Space { command }) => run_direct(DirectCommand::Space(command), direct_options),
        Some(Cmd::Page { command }) => run_direct(DirectCommand::Page(command), direct_options),
        Some(Cmd::Snapshot(args)) => run_direct(DirectCommand::Snapshot(args), direct_options),
        Some(Cmd::Action { command }) => run_direct(DirectCommand::Action(command), direct_options),
        Some(Cmd::Events(args)) => run_direct(DirectCommand::Events(args), direct_options),
        Some(Cmd::Host { command }) => run_direct(DirectCommand::Host(command), direct_options),
        Some(Cmd::Wait(args)) => run_direct(DirectCommand::Wait(args), direct_options),
        Some(Cmd::Extension { command }) => {
            run_direct(DirectCommand::Extension(command), direct_options)
        }
    }
}

fn run_direct(command: DirectCommand, options: DirectOptions) -> Result<()> {
    commands::direct::run(command, options).map_err(anyhow::Error::new)
}

async fn run_host_mcp(options: &DirectOptions) -> Result<()> {
    let state_dir = host_state_dir(options.state_dir.as_deref())?;
    let principal = host_principal(options.principal.as_deref())?;
    let connection_nonce = ConnectionNonce::from_suffix(format!("mcp-{}", Uuid::new_v4().simple()))
        .map_err(|error| anyhow!(error.to_string()))?;
    let hello = HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: principal,
        resume_from: None,
        client_metadata: Some(ClientMetadata {
            client_id: Some(
                ClientId::from_suffix("mcp-host").map_err(|error| anyhow!(error.to_string()))?,
            ),
            client_name: Some("agentyc-host-mcp".to_owned()),
            client_version: Some(env!("CARGO_PKG_VERSION").to_owned()),
            connection_nonce: Some(connection_nonce),
            profile_binding_id: if options.offline {
                Some(
                    ProfileBindingId::from_suffix("cli-offline")
                        .map_err(|error| anyhow!(error.to_string()))?,
                )
            } else {
                None
            },
        }),
    };

    if options.offline {
        let broker = Broker::open(&state_dir, FakeBridge::new())?;
        agentyc_mcp::run_host_stdio(broker, hello).await
    } else {
        let socket_path = configured_socket_path(&state_dir);
        let client = LocalSocketClient::connect(socket_path, hello)?;
        agentyc_mcp::run_remote_host_stdio(client).await
    }
}

fn host_state_dir(explicit: Option<&str>) -> Result<std::path::PathBuf> {
    if let Some(path) = explicit.filter(|value| !value.is_empty()) {
        return Ok(std::path::PathBuf::from(path));
    }
    if let Ok(path) = std::env::var("AGENTYC_STATE_DIR")
        && !path.trim().is_empty()
    {
        return Ok(std::path::PathBuf::from(path));
    }
    dirs::home_dir()
        .map(|home| home.join(".agentyc").join("state"))
        .ok_or_else(|| anyhow!("home directory is unavailable; pass --state-dir"))
}

fn host_principal(explicit: Option<&str>) -> Result<PrincipalId> {
    let value = explicit
        .map(str::to_owned)
        .or_else(|| std::env::var("AGENTYC_PRINCIPAL").ok())
        .unwrap_or_else(|| "principal_cli".to_owned());
    if value.starts_with(PrincipalId::PREFIX) {
        PrincipalId::new(value).map_err(|error| anyhow!(error.to_string()))
    } else {
        PrincipalId::from_suffix(value).map_err(|error| anyhow!(error.to_string()))
    }
}


fn cmd_init(output: &str, print_only: bool, force: bool) -> Result<()> {
    if print_only {
        print!("{SKILL_MD}");
        return Ok(());
    }
    let dest = std::path::Path::new(output);
    if dest.exists() && !force {
        eprintln!("{output} already exists. Use --force to overwrite.");
        std::process::exit(1);
    }
    if let Some(parent) = dest.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    std::fs::write(dest, SKILL_MD)?;
    println!("Written to {output}");
    println!();
    println!("Add this file to your coding agent context:");
    println!("  Claude Code:  add \"{output}\" to CLAUDE.md with @{output}");
    println!("  Cursor:       copy to .cursor/rules/agentyc.md");
    Ok(())
}
