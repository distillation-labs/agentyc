#![allow(clippy::collapsible_if)]

use mimalloc::MiMalloc;
#[global_allocator]
static GLOBAL: MiMalloc = MiMalloc;

use anyhow::{Result, anyhow};
use clap::{Parser, Subcommand};
use tracing_subscriber::EnvFilter;

mod frontend;

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
}

#[derive(Subcommand)]
enum Cmd {
    /// Run MCP server over stdio (default).
    Mcp {
        #[arg(long)]
        cdp_url: Option<String>,
        /// Expose the extended tool profile (observability: console/network logs,
        /// mocks, conditions, replay, debug bundle, downloads, trace).
        #[arg(long)]
        extended: bool,
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
}

#[tokio::main]
async fn main() -> Result<()> {
    // stderr-only tracing — stdout is the JSON-RPC channel
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(
            EnvFilter::try_from_env("AGENTYC_LOGGING_LEVEL")
                .unwrap_or_else(|_| EnvFilter::new("warn")),
        )
        .init();

    let cli = Cli::parse();

    match cli.command {
        None => agentyc_mcp::run_stdio(None).await,
        Some(Cmd::Mcp { cdp_url, extended }) => {
            if extended {
                unsafe { std::env::set_var("AGENTYC_EXTENDED", "1") };
            }
            agentyc_mcp::run_stdio(cdp_url.as_deref()).await
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
    }
}

async fn run_action(cdp_url: Option<String>, headless: Option<bool>, action: Action) -> Result<()> {
    let runtime = agentyc_runtime::BrowserRuntime::open(runtime_config(cdp_url, headless)).await?;
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

    let runtime = agentyc_runtime::BrowserRuntime::open(runtime_config(cdp_url, headless)).await?;
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
    use rmcp::transport::streamable_http_server::{
        StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
    };

    let cdp_owned = cdp_url.map(str::to_string);
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
