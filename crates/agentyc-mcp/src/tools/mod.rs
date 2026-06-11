//! Shared helpers for all tool modules.

pub mod navigation;
pub mod state_tools;
pub mod interaction;
pub mod inspection;
pub mod frames_storage;
pub mod tabs_session;
pub mod observability;

use std::{collections::VecDeque, sync::Arc};
use anyhow::{anyhow, Result};
use serde::Serialize;
use serde_json::Value;
use tokio::sync::Mutex;
use rmcp::model::{CallToolResult, Content};

use agentyc_cdp::CdpClient;

pub struct ServerState {
    pub cdp: Option<CdpClient>,
    pub session_id: Option<String>,
    pub current_tab_id: Option<String>,
    pub tabs: Vec<TabEntry>,
    pub console_logs: VecDeque<ConsoleEntry>,
    pub network_log: VecDeque<NetworkEntry>,
    pub mocks: Vec<NetworkMock>,
    pub intent: Option<String>,
    pub downloads: Vec<DownloadEntry>,
    pub tracing: bool,
    pub trace_events: Vec<Value>,
    /// Launched browser process — kept alive for session lifetime.
    pub launched_browser: Option<agentyc_browser::LaunchedBrowser>,
}

impl ServerState {
    pub fn new() -> Self {
        Self {
            cdp: None,
            session_id: None,
            current_tab_id: None,
            tabs: Vec::new(),
            console_logs: VecDeque::with_capacity(500),
            network_log: VecDeque::with_capacity(500),
            mocks: Vec::new(),
            intent: None,
            downloads: Vec::new(),
            tracing: false,
            trace_events: Vec::new(),
            launched_browser: None,
        }
    }

    pub fn cdp(&self) -> Result<&CdpClient> {
        self.cdp.as_ref().ok_or_else(|| anyhow!("No browser connected. Use browser_navigate first."))
    }

    pub fn sid(&self) -> Option<&str> {
        self.session_id.as_deref()
    }

    #[allow(dead_code)]
    pub fn push_console(&mut self, entry: ConsoleEntry) {
        if self.console_logs.len() >= 500 { self.console_logs.pop_front(); }
        self.console_logs.push_back(entry);
    }

    #[allow(dead_code)]
    pub fn push_network(&mut self, entry: NetworkEntry) {
        if self.network_log.len() >= 500 { self.network_log.pop_front(); }
        self.network_log.push_back(entry);
    }
}

pub type SharedState = Arc<Mutex<ServerState>>;

#[derive(Debug, Clone, Serialize)]
pub struct TabEntry {
    pub tab_id: String,
    pub target_id: String,
    pub url: String,
    pub title: String,
    pub session_id: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ConsoleEntry {
    pub level: String,
    pub text: String,
    pub timestamp: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct NetworkEntry {
    pub request_id: String,
    pub url: String,
    pub method: String,
    pub status: Option<u32>,
    pub resource_type: String,
    pub timestamp: f64,
    pub duration_ms: Option<f64>,
    pub request_headers: Option<Value>,
    pub response_headers: Option<Value>,
    pub request_body: Option<String>,
    pub response_body: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct NetworkMock {
    pub mock_id: String,
    pub url_substring: Option<String>,
    pub url_regex: Option<String>,
    pub method: Option<String>,
    pub resource_type: Option<String>,
    pub action: String,
    pub status: u32,
    pub headers: Value,
    pub body: String,
    pub error_reason: String,
    pub match_count: u32,
}

#[derive(Debug, Clone, Serialize)]
pub struct DownloadEntry {
    pub filename: String,
    pub path: String,
    pub size: u64,
    pub mime_type: String,
    pub completed: bool,
}

/// Read AGENTYC_ACTION_TIMEOUT_S env var (default 180s, matching Python).
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

pub fn tool_err(e: anyhow::Error) -> rmcp::ErrorData {
    rmcp::ErrorData::new(rmcp::model::ErrorCode::INTERNAL_ERROR, e.to_string(), None)
}

/// Convert anyhow::Result<CallToolResult> into the rmcp Result type.
pub fn res(r: Result<CallToolResult>) -> std::result::Result<CallToolResult, rmcp::ErrorData> {
    r.map_err(tool_err)
}
