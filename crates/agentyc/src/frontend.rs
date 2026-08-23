//! Shared command parsing and dispatch for the one-shot CLI and REPL.

use anyhow::Result;
use clap::{Args, Parser, Subcommand};
use serde_json::{Value, json};

use agentyc_runtime::BrowserRuntime;

#[derive(Debug, Clone, Args)]
pub struct NavigateArgs {
    /// URL to open.
    pub url: String,
    /// Create a new tab instead of navigating the active tab.
    #[arg(long)]
    pub new_tab: bool,
}

#[derive(Debug, Clone, Args)]
pub struct EvaluateArgs {
    /// JavaScript source. Multiple unquoted words are joined with spaces.
    #[arg(required = true, trailing_var_arg = true, allow_hyphen_values = true)]
    pub code: Vec<String>,
}

#[derive(Debug, Clone, Subcommand)]
pub enum TabsCommand {
    /// List page targets.
    List,
    /// Create and activate a page target.
    New { url: Option<String> },
    /// Switch to a tab by its compatibility ID.
    Switch { tab_id: String },
    /// Close a tab by its compatibility ID.
    Close { tab_id: String },
}

#[derive(Debug, Clone, Subcommand)]
pub enum Action {
    /// Navigate the active tab or create a new one.
    Navigate(NavigateArgs),
    /// Return the active page and all page tabs as JSON.
    State,
    /// Evaluate JavaScript in the active page.
    Evaluate(EvaluateArgs),
    /// Manage page tabs.
    Tabs {
        #[command(subcommand)]
        command: TabsCommand,
    },
    /// Close all page targets and release the owned browser.
    Close,
}

#[derive(Debug, Parser)]
#[command(
    name = "agentyc",
    no_binary_name = true,
    disable_help_subcommand = true,
    about = "Browser automation commands"
)]
struct ActionCli {
    #[command(subcommand)]
    action: Action,
}

/// Parse one REPL line using the same clap command model as the one-shot CLI.
pub fn parse_line(input: &str) -> std::result::Result<Action, String> {
    let tokens = tokenize(input)?;
    if tokens.is_empty() {
        return Err(String::new());
    }
    let mut argv = Vec::with_capacity(tokens.len());
    argv.extend(tokens);
    ActionCli::try_parse_from(argv)
        .map(|parsed| parsed.action)
        .map_err(|error| error.to_string())
}

/// Execute a frontend-neutral action against the supplied runtime.
pub async fn dispatch(runtime: &BrowserRuntime, action: Action) -> Result<Value> {
    match action {
        Action::Navigate(args) => Ok(serde_json::to_value(
            runtime.navigate(&args.url, args.new_tab).await?,
        )?),
        Action::State => Ok(serde_json::to_value(runtime.page_info().await?)?),
        Action::Evaluate(args) => {
            let code = args.code.join(" ");
            Ok(json!({"value": runtime.evaluate(&code).await?}))
        }
        Action::Tabs { command } => match command {
            TabsCommand::List => Ok(serde_json::to_value(runtime.list_tabs().await?)?),
            TabsCommand::New { url } => Ok(json!({
                "tab": runtime.new_tab(url.as_deref()).await?
            })),
            TabsCommand::Switch { tab_id } => Ok(json!({
                "tab": runtime.switch_tab(&tab_id).await?
            })),
            TabsCommand::Close { tab_id } => {
                runtime.close_tab(&tab_id).await?;
                Ok(json!({"closed_tab_id": tab_id}))
            }
        },
        Action::Close => {
            runtime.close_all().await?;
            Ok(json!({"closed": true}))
        }
    }
}

/// Split a shell-like REPL line without adding a dependency just for lexing.
/// Single and double quotes group whitespace; backslash escapes the next byte.
fn tokenize(input: &str) -> std::result::Result<Vec<String>, String> {
    let mut tokens = Vec::new();
    let mut current = String::new();
    let mut quote = None;
    let mut escaped = false;

    for ch in input.chars() {
        if escaped {
            current.push(ch);
            escaped = false;
            continue;
        }
        if ch == '\\' && quote != Some('\'') {
            escaped = true;
            continue;
        }
        match quote {
            Some('\'') => {
                if ch == '\'' {
                    quote = None;
                } else {
                    current.push(ch);
                }
            }
            Some('"') => {
                if ch == '"' {
                    quote = None;
                } else {
                    current.push(ch);
                }
            }
            Some(_) => current.push(ch),
            None if ch == '\'' || ch == '"' => quote = Some(ch),
            None if ch.is_whitespace() => {
                if !current.is_empty() {
                    tokens.push(std::mem::take(&mut current));
                }
            }
            None => current.push(ch),
        }
    }

    if escaped {
        return Err("unfinished escape".to_string());
    }
    if quote.is_some() {
        return Err("unfinished quote".to_string());
    }
    if !current.is_empty() {
        tokens.push(current);
    }
    Ok(tokens)
}

/// Build a runtime profile from CLI-level options and environment defaults.
pub fn runtime_config(
    cdp_url: Option<String>,
    headless: Option<bool>,
) -> agentyc_runtime::RuntimeConfig {
    let mut profile = agentyc_browser::BrowserProfile::default();
    if let Some(headless) = headless {
        profile.headless = headless;
    }
    agentyc_runtime::RuntimeConfig { cdp_url, profile }
}

/// Render a value in the stable output format shared by CLI and REPL.
pub fn render_json(value: &Value) -> String {
    serde_json::to_string_pretty(value).unwrap_or_else(|_| "null".to_string())
}

/// Convert a parser/runtime error into a JSON error object for agent callers.
pub fn render_error(error: impl std::fmt::Display) -> String {
    render_json(&json!({"error": error.to_string()}))
}
