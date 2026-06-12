//! Shared helpers for all tool modules.

pub mod navigation;
pub mod state_tools;
pub mod interaction;
pub mod inspection;
pub mod frames_storage;
pub mod tabs_session;

use std::sync::Arc;
use anyhow::{anyhow, Result};
use serde_json::Value;
use tokio::sync::Mutex;
use rmcp::model::{CallToolResult, Content};

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
        self.cdp.as_ref().ok_or_else(|| anyhow!("No browser connected. Use browser_navigate first."))
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
    s.parse::<u64>().map_err(|_| anyhow!("Invalid element ref: {r:?}"))
}

pub fn tab_id_from(target_id: &str) -> String {
    let s = target_id;
    if s.len() >= 4 { s[s.len()-4..].to_string() } else { s.to_string() }
}

pub fn ok_text(s: impl Into<String>) -> CallToolResult {
    CallToolResult::success(vec![Content::text(s.into())])
}

pub fn ok_json(v: &Value) -> CallToolResult {
    CallToolResult::success(vec![Content::text(
        serde_json::to_string_pretty(v).unwrap_or_default()
    )])
}

/// Convert anyhow::Result<CallToolResult> into the rmcp Result type.
/// Errors are returned as isError=true tool content so agents can read and recover from them.
pub fn res(r: Result<CallToolResult>) -> std::result::Result<CallToolResult, rmcp::ErrorData> {
    match r {
        Ok(v) => Ok(v),
        Err(e) => Ok(CallToolResult::error(vec![Content::text(e.to_string())])),
    }
}
