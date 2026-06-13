//! Shared integration-test harness for the agentyc MCP server.
//!
//! Spawns the compiled `agentyc` binary and drives it over stdio JSON-RPC,
//! exactly as a real MCP client would. Every integration test file under
//! `tests/` links against this crate and uses [`Mcp`].
//!
//! Browser-dependent tests should call [`Mcp::browser_available`] and return
//! early when no Chrome/Chromium is installed so the suite degrades gracefully
//! on machines without a browser.

use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

use serde_json::{Value, json};

/// Locate the compiled `agentyc` binary (release preferred, debug fallback).
pub fn binary_path() -> std::path::PathBuf {
    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let release = manifest.join("../../target/release/agentyc");
    if release.exists() {
        return release;
    }
    let debug = manifest.join("../../target/debug/agentyc");
    if debug.exists() {
        return debug;
    }
    // Fallback: sibling of the current test executable.
    let mut p = std::env::current_exe()
        .unwrap()
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    p.push("agentyc");
    p
}

/// A live MCP server process plus a JSON-RPC client over its stdio pipes.
pub struct Mcp {
    proc: Child,
    reader: BufReader<ChildStdout>,
    stdin: ChildStdin,
    id: u64,
    /// Responses read out of order while waiting for a specific id.
    pending: Vec<Value>,
}

impl Mcp {
    /// Start a headless MCP server and complete the initialize handshake.
    pub fn start() -> Self {
        Self::start_with(&[("AGENTYC_HEADLESS", "1")])
    }

    /// Start a headed (visible) MCP server. `AGENTYC_HEADLESS` is cleared so a
    /// real window is shown — used by the headed stress suite.
    pub fn start_headed() -> Self {
        Self::start_with(&[])
    }

    /// Start with an explicit set of environment overrides.
    pub fn start_with(env: &[(&str, &str)]) -> Self {
        let binary = binary_path();
        assert!(binary.exists(), "build first: cargo build -p agentyc");
        let mut cmd = Command::new(&binary);
        cmd.arg("mcp")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        if !env.iter().any(|(k, _)| *k == "AGENTYC_HEADLESS") {
            cmd.env_remove("AGENTYC_HEADLESS");
        }
        for (k, v) in env {
            cmd.env(k, v);
        }
        let mut proc = cmd.spawn().expect("failed to spawn agentyc");
        let stdin = proc.stdin.take().unwrap();
        let reader = BufReader::new(proc.stdout.take().unwrap());
        let mut m = Self {
            proc,
            reader,
            stdin,
            id: 0,
            pending: Vec::new(),
        };
        let r = m.rpc(
            "initialize",
            json!({
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agentyc-tests", "version": "1"}
            }),
        );
        assert!(r["result"].is_object(), "initialize failed: {r:?}");
        m
    }

    /// Write a request and return its id without waiting for the response.
    pub fn send_async(&mut self, method: &str, params: Value) -> u64 {
        self.id += 1;
        let id = self.id;
        let msg = serde_json::to_string(&json!({
            "jsonrpc": "2.0", "id": id, "method": method, "params": params
        }))
        .unwrap()
            + "\n";
        self.stdin.write_all(msg.as_bytes()).unwrap();
        self.stdin.flush().unwrap();
        id
    }

    /// Read responses until the one matching `id` arrives, buffering any others
    /// (so concurrent in-flight requests are handled) and skipping notifications.
    pub fn read_response(&mut self, id: u64) -> Value {
        if let Some(pos) = self.pending.iter().position(|v| v["id"] == json!(id)) {
            return self.pending.remove(pos);
        }
        loop {
            let mut buf = String::new();
            let n = self.reader.read_line(&mut buf).expect("read failed");
            if n == 0 {
                return json!({"error": "eof"});
            }
            let v: Value = match serde_json::from_str(buf.trim()) {
                Ok(v) => v,
                Err(_) => continue,
            };
            if v.get("id").is_none() {
                continue; // notification
            }
            if v["id"] == json!(id) {
                return v;
            }
            self.pending.push(v);
        }
    }

    /// Send a JSON-RPC request and wait for its response.
    pub fn rpc(&mut self, method: &str, params: Value) -> Value {
        let id = self.send_async(method, params);
        self.read_response(id)
    }

    /// Call a tool by name and wait for the result.
    pub fn call(&mut self, tool: &str, args: Value) -> Value {
        self.rpc("tools/call", json!({"name": tool, "arguments": args}))
    }

    /// Fetch the advertised tool list.
    pub fn tools_list(&mut self) -> Value {
        self.rpc("tools/list", json!({}))
    }

    // ── result extraction helpers ───────────────────────────────────────────

    /// First text content block of a tool result.
    pub fn text(r: &Value) -> String {
        r["result"]["content"][0]["text"]
            .as_str()
            .unwrap_or("")
            .to_string()
    }

    /// Whether the result is an error (tool `isError` or a JSON-RPC error).
    pub fn is_err(r: &Value) -> bool {
        r["result"]["isError"].as_bool().unwrap_or(false) || r.get("error").is_some()
    }

    /// Parse the first text content block as JSON (Null if not JSON).
    pub fn json(r: &Value) -> Value {
        serde_json::from_str(&Self::text(r)).unwrap_or(Value::Null)
    }

    /// Whether the result carries any image content.
    pub fn has_image(r: &Value) -> bool {
        r["result"]["content"]
            .as_array()
            .map(|cs| {
                cs.iter().any(|c| {
                    c["type"].as_str() == Some("image")
                        || c["mimeType"]
                            .as_str()
                            .map(|m| m.starts_with("image/"))
                            .unwrap_or(false)
                })
            })
            .unwrap_or(false)
    }

    // ── convenience tool wrappers ───────────────────────────────────────────

    /// Navigate and return the raw result (does not assert success).
    pub fn navigate(&mut self, url: &str) -> Value {
        self.call("browser_navigate", json!({"url": url}))
    }

    /// Navigate and assert the call succeeded.
    pub fn nav(&mut self, url: &str) {
        let r = self.navigate(url);
        assert!(
            !Self::is_err(&r),
            "navigate {url} failed: {}",
            Self::text(&r)
        );
    }

    pub fn wait(&mut self, secs: f64) {
        self.call("browser_wait", json!({"seconds": secs}));
    }

    /// Evaluate JS and return the raw (JSON-encoded) text result.
    pub fn eval(&mut self, code: &str) -> String {
        Self::text(&self.call("browser_evaluate", json!({"code": code})))
    }

    /// Evaluate JS and parse the result as JSON.
    pub fn eval_json(&mut self, code: &str) -> Value {
        Self::json(&self.call("browser_evaluate", json!({"code": code})))
    }

    /// Fetch a page-state snapshot in the given mode.
    pub fn state(&mut self, mode: &str) -> Value {
        Self::json(&self.call("browser_get_state", json!({"mode": mode})))
    }

    /// Probe whether a real browser can launch. Returns false when Chrome is
    /// unavailable so browser tests can skip instead of failing.
    pub fn browser_available(&mut self) -> bool {
        let r = self.navigate("data:text/html,<title>probe</title>");
        !Self::is_err(&r)
    }

    // ── element lookup over a state snapshot ────────────────────────────────

    pub fn ref_by_tag(state: &Value, tag: &str) -> Option<String> {
        state["interactive_elements"]
            .as_array()?
            .iter()
            .find(|e| e["tag"].as_str() == Some(tag))
            .and_then(|e| e["ref"].as_str())
            .map(str::to_string)
    }

    pub fn ref_by_type(state: &Value, ty: &str) -> Option<String> {
        state["interactive_elements"]
            .as_array()?
            .iter()
            .find(|e| e["type"].as_str() == Some(ty))
            .and_then(|e| e["ref"].as_str())
            .map(str::to_string)
    }

    pub fn ref_by_placeholder(state: &Value, needle: &str) -> Option<String> {
        let needle = needle.to_lowercase();
        state["interactive_elements"]
            .as_array()?
            .iter()
            .find(|e| {
                e["placeholder"]
                    .as_str()
                    .map(|p| p.to_lowercase().contains(&needle))
                    .unwrap_or(false)
            })
            .and_then(|e| e["ref"].as_str())
            .map(str::to_string)
    }

    pub fn elements(state: &Value) -> Vec<Value> {
        state["interactive_elements"]
            .as_array()
            .cloned()
            .unwrap_or_default()
    }
}

impl Drop for Mcp {
    fn drop(&mut self) {
        // Best-effort cleanup: ask the server to close the browser, then kill.
        let msg = serde_json::to_string(&json!({
            "jsonrpc": "2.0", "id": 0, "method": "tools/call",
            "params": {"name": "browser_close_all", "arguments": {}}
        }))
        .unwrap()
            + "\n";
        let _ = self.stdin.write_all(msg.as_bytes());
        let _ = self.stdin.flush();
        self.proc.kill().ok();
    }
}

/// Loop-count helper for stress tests: returns `base` scaled by the
/// `AGENTYC_TEST_SCALE` env var (default 1.0). Set e.g. `AGENTYC_TEST_SCALE=25`
/// to reproduce the original ~10k-operation soak run.
pub fn iters(base: usize) -> usize {
    let scale: f64 = std::env::var("AGENTYC_TEST_SCALE")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|v: &f64| v.is_finite() && *v > 0.0)
        .unwrap_or(1.0);
    ((base as f64) * scale).round().max(1.0) as usize
}

// ── Real-world battle-test suite ────────────────────────────────────────────

pub mod fixtures;
pub mod runner;
pub mod scenario;

use std::sync::{Mutex, OnceLock};

/// Shared headless browser for the generated real-world suite. `None` means no
/// browser is available, so cases skip instead of failing.
static SHARED_MCP: OnceLock<Mutex<Option<Mcp>>> = OnceLock::new();
static CATALOG: OnceLock<Vec<scenario::Scenario>> = OnceLock::new();

fn shared_mcp() -> &'static Mutex<Option<Mcp>> {
    SHARED_MCP.get_or_init(|| {
        let mut m = Mcp::start();
        if m.browser_available() {
            Mutex::new(Some(m))
        } else {
            Mutex::new(None)
        }
    })
}

/// Total number of generated real-world scenarios in the catalog.
pub fn scenario_count() -> usize {
    CATALOG.get_or_init(scenario::catalog).len()
}

/// Execute real-world scenario `index`. Called by the generated `#[test]`
/// wrappers. All cases in a test binary share one headless browser (serialized
/// by a mutex) and one fixtures server. Skips when no browser is installed;
/// panics with a descriptive message on scenario failure.
pub fn run_index(index: usize) {
    let catalog = CATALOG.get_or_init(scenario::catalog);
    let scenario = match catalog.get(index) {
        Some(s) => s.clone(),
        None => panic!(
            "scenario index {index} out of range ({} total)",
            catalog.len()
        ),
    };
    let base = fixtures::base_url().to_string();
    let guard = shared_mcp();
    let mut lock = guard.lock().unwrap_or_else(|e| e.into_inner());
    match lock.as_mut() {
        Some(m) => {
            if let Err(e) = runner::run(m, &base, &scenario) {
                panic!("{e}");
            }
        }
        None => {
            eprintln!("skipping {}: no Chrome/Chromium available", scenario.id);
        }
    }
}
