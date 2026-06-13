//! Shared helpers for all tool modules.

pub mod frames_storage;
pub mod inspection;
pub mod interaction;
pub mod navigation;
pub mod state_tools;
pub mod tabs_session;

use anyhow::{Result, anyhow};
use rmcp::model::{CallToolResult, Content};
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::Mutex;

use agentyc_cdp::CdpClient;

pub struct ServerState {
    pub cdp: Option<CdpClient>,
    pub session_id: Option<String>,
    pub current_tab_id: Option<String>,
    /// Launched browser process — kept alive for session lifetime.
    pub launched_browser: Option<agentyc_browser::LaunchedBrowser>,
}

impl ServerState {
    pub fn new() -> Self {
        Self {
            cdp: None,
            session_id: None,
            current_tab_id: None,
            launched_browser: None,
        }
    }

    pub fn cdp(&self) -> Result<&CdpClient> {
        self.cdp
            .as_ref()
            .ok_or_else(|| anyhow!("No browser connected. Use browser_navigate first."))
    }

    pub fn sid(&self) -> Option<&str> {
        self.session_id.as_deref()
    }
}

pub type SharedState = Arc<Mutex<ServerState>>;

/// Read AGENTYC_ACTION_TIMEOUT_S env var (default 180s).
pub fn action_timeout() -> std::time::Duration {
    let secs: f64 = std::env::var("AGENTYC_ACTION_TIMEOUT_S")
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .filter(|&v: &f64| v.is_finite() && v > 0.0)
        .unwrap_or(180.0);
    std::time::Duration::from_secs_f64(secs)
}

pub fn parse_ref(r: &str) -> Result<u64> {
    let s = r.trim().trim_start_matches('e');
    s.parse::<u64>()
        .map_err(|_| anyhow!("Invalid element ref: {r:?}"))
}

pub fn tab_id_from(target_id: &str) -> String {
    let s = target_id;
    if s.len() >= 4 {
        s[s.len() - 4..].to_string()
    } else {
        s.to_string()
    }
}

pub fn ok_text(s: impl Into<String>) -> CallToolResult {
    CallToolResult::success(vec![Content::text(s.into())])
}

pub fn ok_json(v: &Value) -> CallToolResult {
    CallToolResult::success(vec![Content::text(
        serde_json::to_string_pretty(v).unwrap_or_default(),
    )])
}

/// Convert anyhow::Result<CallToolResult> into the rmcp Result type.
/// Errors are returned as isError=true tool content so agents can read and recover from them.
pub fn res(r: Result<CallToolResult>) -> std::result::Result<CallToolResult, rmcp::ErrorData> {
    match r {
        Ok(v) => Ok(v),
        Err(e) => {
            let msg = e.to_string();
            // Add structured error code prefix so agents can branch programmatically
            let coded = if msg.contains("No node with given id")
                || msg.contains("No node found for given backend id")
            {
                format!("[stale_ref] {msg}\nHint: Call browser_get_state() to get fresh refs.")
            } else if msg.contains("Could not compute box model") || msg.contains("No box model") {
                format!(
                    "[element_not_interactable] {msg}\nHint: Element may be off-screen or in Shadow DOM. Try browser_evaluate() or use coordinates."
                )
            } else if msg.contains("No browser connected") {
                format!("[no_browser] {msg}\nHint: Call browser_navigate() to auto-launch Chrome.")
            } else if msg.contains("blocked") && msg.contains("AGENTYC_ALLOWED_DOMAINS") {
                format!("[domain_blocked] {msg}")
            } else if msg.contains("timed out") || msg.contains("Timeout") {
                format!(
                    "[timeout] {msg}\nHint: Increase timeout_seconds or check the page loaded with browser_evaluate(\"location.href\")."
                )
            } else if msg.contains("Not attached") || msg.contains("session") {
                format!("[session_error] {msg}\nHint: Call browser_navigate() to reconnect.")
            } else {
                msg
            };
            Ok(CallToolResult::error(vec![Content::text(coded)]))
        }
    }
}
