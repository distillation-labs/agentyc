//! Shared helpers for all tool modules.

pub mod frames_storage;
pub mod inspection;
pub mod interaction;
pub mod navigation;
pub mod observability;
pub mod state_tools;
pub mod tabs_session;

use anyhow::{Result, anyhow};
use rmcp::model::{CallToolResult, Content};
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::Mutex;

use agentyc_cdp::CdpClient;
use agentyc_runtime::BrowserRuntime;

/// Max entries kept in the console/network ring buffers (bounded memory).
pub const RING_CAP: usize = 500;

/// A captured browser console message.
#[derive(Clone)]
pub struct ConsoleEntry {
    pub level: String,
    pub text: String,
    pub timestamp: u64,
}

/// A captured network request/response.
#[derive(Clone)]
pub struct NetworkEntry {
    pub request_id: String,
    pub url: String,
    pub method: String,
    pub status: Option<u32>,
    pub resource_type: String,
    pub timestamp: u64,
    pub duration_ms: Option<f64>,
    pub request_headers: Option<Value>,
    pub response_headers: Option<Value>,
    pub request_body: Option<String>,
    pub response_body: Option<String>,
}

/// A registered network mock rule.
#[derive(Clone)]
pub struct NetworkMock {
    pub mock_id: String,
    pub url_substring: Option<String>,
    pub url_regex: Option<String>,
    pub method: Option<String>,
    #[allow(dead_code)]
    pub resource_type: Option<String>,
    pub action: String,
    pub status: u32,
    pub headers: Value,
    pub body: String,
    pub error_reason: String,
    pub match_count: u64,
}

/// A captured download.
#[derive(Clone)]
pub struct DownloadEntry {
    pub filename: String,
    pub path: String,
    pub size: u64,
    pub mime_type: String,
    pub completed: bool,
}

pub struct ServerState {
    /// Canonical browser/session owner. All frontends and tools use this
    /// handle; target/session/process state is intentionally private to it.
    pub runtime: Option<Arc<BrowserRuntime>>,
    pub initialization_lock: Arc<Mutex<()>>,

    // ── Deterministic dialog policy ──
    /// Whether JS dialogs are accepted (true) or dismissed (false) by default.
    pub dialog_accept: bool,
    /// Prompt answer used when accepting prompt() dialogs.
    pub dialog_prompt: Option<String>,
    /// Last dialog seen, as (type, message), for reporting.
    pub last_dialog: Option<(String, String)>,
    /// Whether the dialog auto-handler task has been spawned.
    pub dialog_handler_started: bool,

    // ── Observability (extended profile) ──
    pub console_logs: Vec<ConsoleEntry>,
    pub network_log: Vec<NetworkEntry>,
    pub mocks: Vec<NetworkMock>,
    pub downloads: Vec<DownloadEntry>,
    pub tracing: bool,
    pub trace_events: Vec<Value>,
    /// Whether the console/network capture tasks have been spawned.
    pub capture_started: bool,
}

impl ServerState {
    pub fn new() -> Self {
        Self {
            runtime: None,
            initialization_lock: Arc::new(Mutex::new(())),
            dialog_accept: true,
            dialog_prompt: None,
            last_dialog: None,
            dialog_handler_started: false,
            console_logs: Vec::new(),
            network_log: Vec::new(),
            mocks: Vec::new(),
            downloads: Vec::new(),
            tracing: false,
            trace_events: Vec::new(),
            capture_started: false,
        }
    }

    pub fn runtime(&self) -> Result<Arc<BrowserRuntime>> {
        self.runtime
            .as_ref()
            .cloned()
            .ok_or_else(|| anyhow!("No browser connected. Use browser_navigate first."))
    }

    /// Push a console entry, keeping the ring buffer bounded.
    pub fn push_console(&mut self, e: ConsoleEntry) {
        self.console_logs.push(e);
        let len = self.console_logs.len();
        if len > RING_CAP {
            self.console_logs.drain(0..len - RING_CAP);
        }
    }
}

pub type SharedState = Arc<Mutex<ServerState>>;

/// Snapshot the canonical browser and active page session before awaiting CDP.
pub async fn page_client(state: &SharedState) -> Result<(CdpClient, String)> {
    let runtime = {
        let g = state.lock().await;
        g.runtime()?
    };
    let page = runtime.session().active_page().await?;
    Ok((runtime.session().client(), page.session_id))
}

/// Snapshot the canonical browser client for a browser-level command.
pub async fn browser_client(state: &SharedState) -> Result<CdpClient> {
    let runtime = {
        let g = state.lock().await;
        g.runtime()?
    };
    Ok(runtime.session().client())
}

/// Snapshot the shared runtime without exposing mutable lifecycle state.
pub async fn runtime_handle(state: &SharedState) -> Result<Arc<BrowserRuntime>> {
    let g = state.lock().await;
    g.runtime()
}

/// Snapshot the active tab ID without exposing mutable lifecycle state.
pub async fn active_tab_id(state: &SharedState) -> Option<String> {
    let runtime = {
        let g = state.lock().await;
        g.runtime.clone()?
    };
    runtime.session().active_page().await.ok().map(|page| page.tab_id)
}

/// Send a page-scoped CDP command after cloning the transport and session ID.
pub async fn page_send(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let (cdp, sid) = page_client(state).await?;
    cdp.send::<Value>(method, params, Some(&sid)).await
}

/// Send a browser-scoped CDP command after cloning the transport.
pub async fn browser_send(state: &SharedState, method: &str, params: Value) -> Result<Value> {
    let cdp = browser_client(state).await?;
    cdp.send::<Value>(method, params, None).await
}

/// Current epoch milliseconds (for log timestamps).
pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Truncate a string to `max` chars, appending a marker noting the elided count.
pub fn cap_text(s: &str, max: usize) -> String {
    if s.len() <= max {
        return s.to_string();
    }
    let cut = s
        .char_indices()
        .take(max)
        .last()
        .map(|(i, c)| i + c.len_utf8())
        .unwrap_or(max);
    format!("{}\n…[truncated {} more chars]", &s[..cut], s.len() - cut)
}

/// Recursively bound the length of any array in a JSON value to `max`,
/// replacing the tail with a marker. Keeps output token-bounded for agents.
pub fn bound_json(v: &mut Value, max: usize) {
    match v {
        Value::Array(arr) => {
            if arr.len() > max {
                let remaining = arr.len() - max;
                arr.truncate(max);
                arr.push(Value::String(format!(
                    "…[{remaining} more items truncated]"
                )));
            }
            for item in arr.iter_mut() {
                bound_json(item, max);
            }
        }
        Value::Object(map) => {
            for (_, val) in map.iter_mut() {
                bound_json(val, max);
            }
        }
        _ => {}
    }
}

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

// ── Background task spawners ──────────────────────────────────────────────────

/// Ensure the deterministic JS-dialog handler is running. Idempotent.
///
/// Once a page session exists, this subscribes to `Page.javascriptDialogOpening`
/// and auto-handles every dialog per the policy in `ServerState` (default:
/// accept). This guarantees dialogs never hang the page and that
/// `browser_handle_dialog` can set the policy for subsequent dialogs.
pub async fn ensure_dialog_handler(state: &SharedState) {
    let runtime = {
        let mut g = state.lock().await;
        if g.dialog_handler_started {
            return;
        }
        let Some(runtime) = g.runtime.clone() else {
            return;
        };
        g.dialog_handler_started = true;
        runtime
    };
    let client = runtime.session().client();
    let state = Arc::clone(state);
    tokio::spawn(async move {
        let mut rx = client.subscribe("Page.javascriptDialogOpening").await;
        while let Ok(params) = rx.recv().await {
            let dtype = params["type"].as_str().unwrap_or("alert").to_string();
            let msg = params["message"].as_str().unwrap_or("").to_string();
            let (accept, prompt) = {
                let mut g = state.lock().await;
                g.last_dialog = Some((dtype, msg));
                (g.dialog_accept, g.dialog_prompt.clone())
            };
            let mut p = serde_json::json!({ "accept": accept });
            if let Some(t) = prompt {
                p["promptText"] = serde_json::json!(t);
            }
            let session_id = params["__agentyc_session_id"]
                .as_str()
                .map(str::to_string)
                .or_else(|| {
                    runtime
                        .session()
                        .active_page()
                        .await
                        .ok()
                        .map(|page| page.session_id)
                });
            if let Some(session_id) = session_id {
                runtime
                    .session()
                    .send_page_with_session(
                        &session_id,
                        "Page.handleJavaScriptDialog",
                        p,
                    )
                    .await
                    .ok();
            }
        }
    });
}

/// Whether the extended tool profile (observability tools + capture) is enabled.
/// Controlled by `AGENTYC_EXTENDED=1` (or `true`), set directly or via `--extended`.
pub fn extended_profile() -> bool {
    matches!(
        std::env::var("AGENTYC_EXTENDED").ok().as_deref(),
        Some("1") | Some("true") | Some("yes")
    )
}

/// Ensure console + network capture tasks are running (extended profile).
/// Idempotent. Pushes into the bounded ring buffers on `ServerState`.
pub async fn ensure_capture(state: &SharedState) {
    if !extended_profile() {
        return;
    }
    let runtime = {
        let mut g = state.lock().await;
        if g.capture_started {
            return;
        }
        let Some(runtime) = g.runtime.clone() else {
            return;
        };
        g.capture_started = true;
        runtime
    };
    let client = runtime.session().client();
    let sid = runtime
        .session()
        .active_page()
        .await
        .ok()
        .map(|page| page.session_id);
    // Browser log entries (network errors, CSP, etc.) need the Log domain.
    client
        .send::<Value>("Log.enable", serde_json::json!({}), sid.as_deref())
        .await
        .ok();

    // Console API calls (console.log/warn/error/...).
    {
        let state = Arc::clone(&state_arc(state));
        let client = client.clone();
        tokio::spawn(async move {
            let mut rx = client.subscribe("Runtime.consoleAPICalled").await;
            while let Ok(params) = rx.recv().await {
                let level = params["type"].as_str().unwrap_or("log").to_string();
                let text = params["args"]
                    .as_array()
                    .map(|args| {
                        args.iter()
                            .map(|a| {
                                a["value"]
                                    .as_str()
                                    .map(str::to_string)
                                    .or_else(|| a["description"].as_str().map(str::to_string))
                                    .unwrap_or_else(|| a["value"].to_string())
                            })
                            .collect::<Vec<_>>()
                            .join(" ")
                    })
                    .unwrap_or_default();
                let entry = ConsoleEntry {
                    level,
                    text,
                    timestamp: now_ms(),
                };
                state.lock().await.push_console(entry);
            }
        });
    }

    // Browser Log entries.
    {
        let state = Arc::clone(&state_arc(state));
        let client = client.clone();
        tokio::spawn(async move {
            let mut rx = client.subscribe("Log.entryAdded").await;
            while let Ok(params) = rx.recv().await {
                let entry = &params["entry"];
                let e = ConsoleEntry {
                    level: entry["level"].as_str().unwrap_or("info").to_string(),
                    text: entry["text"].as_str().unwrap_or("").to_string(),
                    timestamp: now_ms(),
                };
                state.lock().await.push_console(e);
            }
        });
    }

    // Network: request start.
    {
        let state = Arc::clone(&state_arc(state));
        let client = client.clone();
        tokio::spawn(async move {
            let mut rx = client.subscribe("Network.requestWillBeSent").await;
            while let Ok(params) = rx.recv().await {
                let request_id = params["requestId"].as_str().unwrap_or("").to_string();
                if request_id.is_empty() {
                    continue;
                }
                let entry = NetworkEntry {
                    request_id,
                    url: params["request"]["url"].as_str().unwrap_or("").to_string(),
                    method: params["request"]["method"]
                        .as_str()
                        .unwrap_or("GET")
                        .to_string(),
                    status: None,
                    resource_type: params["type"].as_str().unwrap_or("Other").to_string(),
                    timestamp: now_ms(),
                    duration_ms: None,
                    request_headers: params["request"].get("headers").cloned(),
                    response_headers: None,
                    request_body: params["request"]["postData"].as_str().map(str::to_string),
                    response_body: None,
                };
                let mut g = state.lock().await;
                g.network_log.push(entry);
                let len = g.network_log.len();
                if len > RING_CAP {
                    g.network_log.drain(0..len - RING_CAP);
                }
            }
        });
    }

    // Network: response received.
    {
        let state = Arc::clone(&state_arc(state));
        let client = client.clone();
        tokio::spawn(async move {
            let mut rx = client.subscribe("Network.responseReceived").await;
            while let Ok(params) = rx.recv().await {
                let request_id = params["requestId"].as_str().unwrap_or("").to_string();
                let status = params["response"]["status"].as_u64().map(|s| s as u32);
                let headers = params["response"].get("headers").cloned();
                let rtype = params["type"].as_str().map(str::to_string);
                let mut g = state.lock().await;
                if let Some(e) = g
                    .network_log
                    .iter_mut()
                    .rev()
                    .find(|e| e.request_id == request_id)
                {
                    e.status = status;
                    e.response_headers = headers;
                    if let Some(rt) = rtype {
                        e.resource_type = rt;
                    }
                }
            }
        });
    }

    // Network: completion (set duration).
    {
        let state = Arc::clone(&state_arc(state));
        let client = client.clone();
        tokio::spawn(async move {
            let mut rx = client.subscribe("Network.loadingFinished").await;
            while let Ok(params) = rx.recv().await {
                let request_id = params["requestId"].as_str().unwrap_or("").to_string();
                let now = now_ms();
                let mut g = state.lock().await;
                if let Some(e) = g
                    .network_log
                    .iter_mut()
                    .rev()
                    .find(|e| e.request_id == request_id)
                {
                    e.duration_ms = Some((now.saturating_sub(e.timestamp)) as f64);
                }
            }
        });
    }

    // Tracing data.
    {
        let state = Arc::clone(&state_arc(state));
        let client = client.clone();
        tokio::spawn(async move {
            let mut rx = client.subscribe("Tracing.dataCollected").await;
            while let Ok(params) = rx.recv().await {
                if let Some(arr) = params["value"].as_array() {
                    let mut g = state.lock().await;
                    if g.tracing {
                        g.trace_events.extend(arr.iter().cloned());
                        let len = g.trace_events.len();
                        if len > 20000 {
                            g.trace_events.drain(0..len - 20000);
                        }
                    }
                }
            }
        });
    }
}

/// Helper to clone the `Arc` inside a `&SharedState` for moving into tasks.
fn state_arc(state: &SharedState) -> SharedState {
    Arc::clone(state)
}
