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
mod frontend;

use commands::direct::{
    ActionCommand as DirectActionCommand, DirectCommand, DirectCommandError, DirectOptions,
    EventsArgs, HostCommand, PageCommand as DirectPageCommand, SnapshotArgs, SpaceCommand,
};
use frontend::{Action, dispatch, render_error, render_json, runtime_config};

const SKILL_MD: &str = include_str!("../../../SKILL.md");

#[derive(Parser)]
#[command(
    name = "agentyc",
    about = "Deterministic browser automation MCP server",
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
    /// Use the explicit deterministic fake-host seam for direct commands.
    #[arg(long, global = true)]
    offline: bool,
    /// Emit compact structured JSON for direct commands.
    #[arg(long, global = true)]
    json: bool,
}

#[derive(Subcommand)]
enum Cmd {
    /// Run MCP server over stdio (default).
    Mcp {
        #[arg(long)]
        cdp_url: Option<String>,
        /// Explicitly use the legacy direct-CDP compatibility server.
        #[arg(long, conflicts_with = "host")]
        legacy_cdp: bool,
        /// Expose the extended tool profile (observability: console/network logs,
        /// mocks, conditions, replay, debug bundle, downloads, trace).
        #[arg(long)]
        extended: bool,
        /// Run the isolated host-backed logical task-space service.
        #[arg(long, conflicts_with = "legacy_cdp")]
        host: bool,
    },
    /// Run MCP server over Streamable HTTP.
    Serve {
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        #[arg(long, default_value = "8765")]
        port: u16,
        #[arg(long)]
        cdp_url: Option<String>,
        /// Expose the extended tool profile (observability tools).
        #[arg(long)]
        extended: bool,
    },
    /// Write the agentyc skills guide to a file.
    Init {
        #[arg(long, default_value = "agentyc-skill.md")]
        output: String,
        #[arg(long)]
        print: bool,
        #[arg(long)]
        force: bool,
    },
    /// Launch Chrome with remote debugging and print the CDP WebSocket URL.
    Browser {
        #[arg(long, default_value = "9222")]
        port: u16,
        #[arg(long)]
        headless: bool,
        #[arg(long)]
        detach: bool,
    },
    /// Run shared browser automation commands.
    Run {
        #[arg(long)]
        cdp_url: Option<String>,
        #[arg(long)]
        headless: Option<bool>,
        #[command(subcommand)]
        action: Action,
    },
    /// Run the shared browser automation command REPL.
    Repl {
        #[arg(long)]
        cdp_url: Option<String>,
        #[arg(long)]
        headless: Option<bool>,
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

    let cli = Cli::parse();
    let direct_options = DirectOptions {
        state_dir: cli.state_dir.clone(),
        principal: cli.principal.clone(),
        offline: cli.offline,
        json: cli.json,
    };

    match cli.command {
        None => run_host_mcp(&direct_options).await,
        Some(Cmd::Mcp {
            cdp_url,
            legacy_cdp,
            extended,
            host,
        }) => {
            if host || (!legacy_cdp && cdp_url.is_none()) {
                run_host_mcp(&direct_options).await
            } else {
                if extended {
                    unsafe { std::env::set_var("AGENTYC_EXTENDED", "1") };
                }
                agentyc_mcp::run_stdio(cdp_url.as_deref()).await
            }
        }
        Some(Cmd::Serve {
            host,
            port,
            cdp_url,
            extended,
        }) => {
            if extended {
                unsafe { std::env::set_var("AGENTYC_EXTENDED", "1") };
            }
            run_serve(&host, port, cdp_url.as_deref()).await
        }
        Some(Cmd::Init {
            output,
            print,
            force,
        }) => cmd_init(&output, print, force),
        Some(Cmd::Browser {
            port,
            headless,
            detach,
        }) => cmd_browser(port, headless, detach).await,
        Some(Cmd::Run {
            cdp_url,
            headless,
            action,
        }) => run_action(cdp_url, headless, action).await,
        Some(Cmd::Repl { cdp_url, headless }) => run_repl(cdp_url, headless).await,
        Some(Cmd::Space { command }) => run_direct(DirectCommand::Space(command), direct_options),
        Some(Cmd::Page { command }) => run_direct(DirectCommand::Page(command), direct_options),
        Some(Cmd::Snapshot(args)) => run_direct(DirectCommand::Snapshot(args), direct_options),
        Some(Cmd::Action { command }) => run_direct(DirectCommand::Action(command), direct_options),
        Some(Cmd::Events(args)) => run_direct(DirectCommand::Events(args), direct_options),
        Some(Cmd::Host { command }) => run_direct(DirectCommand::Host(command), direct_options),
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

async fn run_action(cdp_url: Option<String>, headless: Option<bool>, action: Action) -> Result<()> {
    let cdp_url = cdp_url.ok_or_else(|| {
        anyhow!(
            "legacy direct-CDP run requires an explicit --cdp-url; the default product path does not launch Chrome"
        )
    })?;
    let runtime =
        agentyc_runtime::BrowserRuntime::open(runtime_config(Some(cdp_url), headless)).await?;
    match dispatch(&runtime, action).await {
        Ok(value) => println!("{}", render_json(&value)),
        Err(error) => {
            println!("{}", render_error(&error));
            runtime.close().await.ok();
            return Err(anyhow!("command failed: {error}"));
        }
    }
    runtime.close().await.ok();
    Ok(())
}

async fn run_repl(cdp_url: Option<String>, headless: Option<bool>) -> Result<()> {
    use tokio::io::{AsyncBufReadExt, BufReader};

    let cdp_url = cdp_url.ok_or_else(|| {
        anyhow!(
            "legacy direct-CDP repl requires an explicit --cdp-url; the default product path does not launch Chrome"
        )
    })?;
    let runtime =
        agentyc_runtime::BrowserRuntime::open(runtime_config(Some(cdp_url), headless)).await?;
    let stdin = BufReader::new(tokio::io::stdin());
    let mut lines = stdin.lines();
    eprintln!("agentyc REPL — type 'help' for commands, 'exit' to close");
    while let Some(line) = lines.next_line().await? {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if matches!(line, "exit" | "quit") {
            break;
        }
        if line == "help" {
            println!(
                "navigate <url> [--new-tab] | state | evaluate <javascript> | tabs list|new|switch|close | close"
            );
            continue;
        }
        match frontend::parse_line(line) {
            Ok(action) => match dispatch(&runtime, action).await {
                Ok(value) => println!("{}", render_json(&value)),
                Err(error) => println!("{}", render_error(error)),
            },
            Err(error) if error.is_empty() => {}
            Err(error) => println!("{}", render_error(error)),
        }
    }
    runtime.close().await.ok();
    Ok(())
}

async fn run_serve(host: &str, port: u16, cdp_url: Option<&str>) -> Result<()> {
    let cdp_url = cdp_url.ok_or_else(|| {
        anyhow!(
            "agentyc serve is legacy compatibility mode and requires an explicit --cdp-url; use `agentyc mcp` for the host-backed adapter"
        )
    })?;
    use rmcp::transport::streamable_http_server::{
        StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
    };

    let cdp_owned = Some(cdp_url.to_string());
    let service: StreamableHttpService<agentyc_mcp::BrowserServer, LocalSessionManager> =
        StreamableHttpService::new(
            move || Ok(agentyc_mcp::BrowserServer::with_cdp_url(cdp_owned.clone())),
            Default::default(),
            StreamableHttpServerConfig::default(),
        );
    let addr = format!("{host}:{port}");
    let listener = tokio::net::TcpListener::bind(&addr).await?;
    eprintln!("agentyc MCP server listening on http://{addr}/mcp");
    let router = axum::Router::new().nest_service("/mcp", service);
    axum::serve(listener, router).await?;
    Ok(())
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

async fn cmd_browser(port: u16, headless: bool, detach: bool) -> Result<()> {
    let chrome = agentyc_browser::find_chrome_binary().ok_or_else(|| {
        anyhow!("Could not find Chrome or Chromium. Install Chrome and try again.")
    })?;
    let user_data_dir = tempfile::Builder::new().prefix("agentyc-cli-").tempdir()?;

    let mut args = vec![
        format!("--remote-debugging-port={port}"),
        format!("--user-data-dir={}", user_data_dir.path().display()),
        "--no-first-run".to_string(),
        "--no-default-browser-check".to_string(),
        "--disable-background-networking".to_string(),
    ];
    if headless {
        args.push("--headless=new".to_string());
    }

    let mut child = tokio::process::Command::new(&chrome)
        .args(&args)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()?;

    // Poll /json/version until ready
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(15);
    let mut cdp_url: Option<String> = None;
    while tokio::time::Instant::now() < deadline {
        if let Ok(resp) = reqwest::get(format!("http://localhost:{port}/json/version")).await {
            if let Ok(data) = resp.json::<serde_json::Value>().await {
                if let Some(url) = data["webSocketDebuggerUrl"].as_str() {
                    cdp_url = Some(url.to_string());
                    break;
                }
            }
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    }

    let url = match cdp_url {
        Some(url) => url,
        None => {
            let _ = child.kill().await;
            return Err(anyhow!(
                "Chrome did not start within 15 seconds on port {port}"
            ));
        }
    };
    println!("{url}");

    if !detach {
        let _ = child.wait().await;
    } else {
        // Detached mode intentionally transfers ownership to the caller. The
        // caller can terminate the browser using the printed CDP endpoint.
        std::mem::forget(user_data_dir);
        std::mem::forget(child);
    }
    Ok(())
}
