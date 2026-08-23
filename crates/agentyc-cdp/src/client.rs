//! CdpClient: WebSocket CDP transport with per-request timeout and event subscriptions.

use std::{
    collections::HashMap,
    env,
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};

use anyhow::{Context, Result, anyhow};
use futures::{SinkExt, StreamExt};
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use tokio::{
    net::TcpStream,
    sync::{Mutex, Notify, RwLock, broadcast},
    time::timeout,
};
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream, connect_async, tungstenite::Message};
use tracing::{debug, warn};

#[derive(Debug, Clone)]
pub struct CdpEvent {
    pub session_id: Option<String>,
    pub params: Value,
}

type WsSink = futures::stream::SplitSink<WebSocketStream<MaybeTlsStream<TcpStream>>, Message>;
type PendingMap = Arc<Mutex<HashMap<u64, tokio::sync::oneshot::Sender<Value>>>>;
type EventSubs = Arc<RwLock<HashMap<String, broadcast::Sender<Value>>>>;
type SessionEventSubs = Arc<RwLock<HashMap<String, broadcast::Sender<CdpEvent>>>>;

const CDP_TIMEOUT_FALLBACK_S: f64 = 60.0;

fn parse_env_cdp_timeout() -> Duration {
    let raw = env::var("AGENTYC_CDP_TIMEOUT_S").unwrap_or_default();
    let secs = parse_cdp_timeout_secs(raw.trim());
    Duration::from_secs_f64(secs)
}

fn parse_cdp_timeout_secs(raw: &str) -> f64 {
    if raw.is_empty() {
        return CDP_TIMEOUT_FALLBACK_S;
    }
    match raw.parse::<f64>() {
        Ok(v) if v.is_finite() && v > 0.0 => v,
        Ok(bad) => {
            warn!(
                "AGENTYC_CDP_TIMEOUT_S={:?} is not a finite positive number; falling back to {:.0}s",
                bad, CDP_TIMEOUT_FALLBACK_S
            );
            CDP_TIMEOUT_FALLBACK_S
        }
        Err(_) => {
            warn!(
                "Invalid AGENTYC_CDP_TIMEOUT_S={:?}; falling back to {:.0}s",
                raw, CDP_TIMEOUT_FALLBACK_S
            );
            CDP_TIMEOUT_FALLBACK_S
        }
    }
}

/// WebSocket-based CDP transport.
///
/// - Connects to a `ws://` or `wss://` URL (or resolves `http://host/json/version` first).
/// - Multiplexes requests over a single connection using numeric message IDs.
/// - Each `send()` call is wrapped in a per-request timeout.
/// - Events are fan-out via broadcast channels keyed on CDP method name.
#[derive(Clone)]
pub struct CdpClient {
    sink: Arc<Mutex<WsSink>>,
    pending: PendingMap,
    event_subs: EventSubs,
    session_event_subs: SessionEventSubs,
    next_id: Arc<AtomicU64>,
    timeout: Duration,
    closed: Arc<std::sync::atomic::AtomicBool>,
    closed_notify: Arc<Notify>,
}

impl CdpClient {
    /// Connect to `ws_url` (must be a `ws://` or `wss://` URL).
    pub async fn connect(ws_url: &str) -> Result<Self> {
        let (ws_stream, _) = connect_async(ws_url)
            .await
            .with_context(|| format!("Failed to connect to CDP WebSocket at {ws_url}"))?;
        let (sink, stream) = ws_stream.split();
        let client = Self {
            sink: Arc::new(Mutex::new(sink)),
            pending: Arc::new(Mutex::new(HashMap::new())),
            event_subs: Arc::new(RwLock::new(HashMap::new())),
            session_event_subs: Arc::new(RwLock::new(HashMap::new())),
            next_id: Arc::new(AtomicU64::new(1)),
            timeout: parse_env_cdp_timeout(),
            closed: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            closed_notify: Arc::new(Notify::new()),
        };
        // Spawn background reader task.
        tokio::spawn(Self::read_loop(
            stream,
            Arc::clone(&client.pending),
            Arc::clone(&client.event_subs),
            Arc::clone(&client.session_event_subs),
            Arc::clone(&client.closed),
            Arc::clone(&client.closed_notify),
        ));
        Ok(client)
    }

    /// Resolve `http://host/json/version` → `webSocketDebuggerUrl`, then connect.
    pub async fn connect_via_http(http_url: &str) -> Result<Self> {
        let ws_url = resolve_ws_url(http_url).await?;
        Self::connect(&ws_url).await
    }

    /// Send a CDP command and return the parsed result.
    ///
    /// `session_id` is `None` for browser-level commands; Some(id) for page sessions.
    pub async fn send<T: DeserializeOwned>(
        &self,
        method: &str,
        params: Value,
        session_id: Option<&str>,
    ) -> Result<T> {
        if self.closed.load(std::sync::atomic::Ordering::Acquire) {
            return Err(anyhow!("CDP connection is closed"));
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let mut msg = json!({ "id": id, "method": method, "params": params });
        if let Some(sid) = session_id {
            msg["sessionId"] = Value::String(sid.to_string());
        }
        let text = serde_json::to_string(&msg)?;

        let (tx, rx) = tokio::sync::oneshot::channel();
        self.pending.lock().await.insert(id, tx);

        if let Err(error) = self
            .sink
            .lock()
            .await
            .send(Message::Text(text.into()))
            .await
        {
            self.pending.lock().await.remove(&id);
            return Err(error).context("CDP WebSocket send failed");
        }

        let response = match timeout(self.timeout, rx).await {
            Ok(result) => result
                .map_err(|_| anyhow!("CDP response channel dropped for method {:?}", method))?,
            Err(_) => {
                self.pending.lock().await.remove(&id);
                return Err(anyhow!(
                    "CDP method {:?} did not respond within {:.0}s. \
                     The browser may be unresponsive.",
                    method,
                    self.timeout.as_secs_f64()
                ));
            }
        };

        if let Some(err) = response.get("error") {
            return Err(anyhow!("CDP error for {:?}: {}", method, err));
        }
        let result = response.get("result").cloned().unwrap_or(Value::Null);
        serde_json::from_value(result).context("Failed to deserialise CDP result")
    }

    /// Subscribe to CDP events matching `method` (e.g. `"Page.loadEventFired"`).
    ///
    /// Returns a `broadcast::Receiver`; dropped when the last sender is removed.
    pub async fn subscribe(&self, method: &str) -> broadcast::Receiver<Value> {
        if self.closed.load(std::sync::atomic::Ordering::Acquire) {
            let (_sender, receiver) = broadcast::channel(1);
            return receiver;
        }
        let mut subs = self.event_subs.write().await;
        if let Some(tx) = subs.get(method) {
            tx.subscribe()
        } else {
            let (tx, rx) = broadcast::channel(64);
            subs.insert(method.to_string(), tx);
            rx
        }
    }

    /// Subscribe to CDP events while retaining the flattened target session ID.
    pub async fn subscribe_with_session(&self, method: &str) -> broadcast::Receiver<CdpEvent> {
        if self.closed.load(std::sync::atomic::Ordering::Acquire) {
            let (_sender, receiver) = broadcast::channel(1);
            return receiver;
        }
        let mut subs = self.session_event_subs.write().await;
        if let Some(tx) = subs.get(method) {
            tx.subscribe()
        } else {
            let (tx, rx) = broadcast::channel(64);
            subs.insert(method.to_string(), tx);
            rx
        }
    }

    /// Wait until the underlying WebSocket reader has terminated.
    pub async fn wait_closed(&self) {
        let notified = self.closed_notify.notified();
        if self.closed.load(std::sync::atomic::Ordering::Acquire) {
            return;
        }
        notified.await;
    }

    /// Close the transport without affecting an externally owned browser.
    pub async fn close(&self) {
        if self.closed.swap(true, std::sync::atomic::Ordering::AcqRel) {
            return;
        }
        self.closed_notify.notify_waiters();
        self.event_subs.write().await.clear();
        self.session_event_subs.write().await.clear();
        self.pending.lock().await.clear();
        let _ = self.sink.lock().await.close().await;
    }

    // Background WebSocket reader — routes responses to pending oneshots and
    // events to broadcast channels.
    async fn read_loop(
        mut stream: futures::stream::SplitStream<WebSocketStream<MaybeTlsStream<TcpStream>>>,
        pending: PendingMap,
        event_subs: EventSubs,
        session_event_subs: SessionEventSubs,
        closed: Arc<std::sync::atomic::AtomicBool>,
        closed_notify: Arc<Notify>,
    ) {
        while let Some(msg) = stream.next().await {
            let text = match msg {
                Ok(Message::Text(t)) => t,
                Ok(Message::Close(_)) => {
                    debug!("CDP WebSocket closed");
                    break;
                }
                Ok(_) => continue,
                Err(e) => {
                    debug!("CDP WebSocket error: {e}");
                    break;
                }
            };
            let Ok(val): Result<Value, _> = serde_json::from_str(&text) else {
                continue;
            };

            // Response to a pending request?
            if let Some(id) = val.get("id").and_then(Value::as_u64) {
                if let Some(tx) = pending.lock().await.remove(&id) {
                    let _ = tx.send(val);
                }
                continue;
            }

            if let Some(method) = val.get("method").and_then(Value::as_str) {
                let params = val.get("params").cloned().unwrap_or(Value::Null);
                let session_id = val
                    .get("sessionId")
                    .and_then(Value::as_str)
                    .map(str::to_string)
                    .or_else(|| params["sessionId"].as_str().map(str::to_string));
                {
                    let subs = event_subs.read().await;
                    if let Some(tx) = subs.get(method) {
                        let _ = tx.send(params.clone());
                    }
                }
                let subs = session_event_subs.read().await;
                if let Some(tx) = subs.get(method) {
                    let _ = tx.send(CdpEvent { session_id, params });
                }
            }
        }
        closed.store(true, std::sync::atomic::Ordering::Release);
        event_subs.write().await.clear();
        session_event_subs.write().await.clear();
        pending.lock().await.clear();
        closed_notify.notify_waiters();
    }
}

/// Fetch `http(s)://host/json/version` and extract the `webSocketDebuggerUrl`.
async fn resolve_ws_url(http_url: &str) -> Result<String> {
    // Build a version URL regardless of whether the caller included the path.
    let base = http_url.trim_end_matches('/');
    let version_url = if base.ends_with("/json/version") {
        base.to_string()
    } else {
        format!("{base}/json/version")
    };

    let body: Value = reqwest::get(&version_url)
        .await
        .with_context(|| format!("GET {version_url} failed"))?
        .json()
        .await
        .context("Failed to parse /json/version response")?;

    body.get("webSocketDebuggerUrl")
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| anyhow!("No webSocketDebuggerUrl in /json/version response: {body}"))
}
