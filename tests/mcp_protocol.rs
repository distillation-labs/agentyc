//! Integration tests for the agentyc MCP server.
//! Tests the MCP protocol over stdio without launching a real browser.

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

fn binary_path() -> std::path::PathBuf {
    // Use the debug binary for tests (built by cargo)
    let mut path = std::env::current_exe()
        .unwrap()
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    path.push("agentyc");
    // Fallback to workspace target
    if !path.exists() {
        path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../target/debug/agentyc");
    }
    path
}

struct McpProcess {
    proc: std::process::Child,
    reader: BufReader<std::process::ChildStdout>,
    stdin: std::process::ChildStdin,
    id: u64,
}

impl McpProcess {
    fn start() -> Self {
        let binary = binary_path();
        assert!(binary.exists(), "Binary not found at {}", binary.display());
        let mut proc = Command::new(&binary)
            .arg("mcp")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("failed to start agentyc");
        let stdin = proc.stdin.take().unwrap();
        let reader = BufReader::new(proc.stdout.take().unwrap());
        let mut this = Self { proc, reader, stdin, id: 0 };
        // Do the MCP handshake
        let r = this.send("initialize", serde_json::json!({
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"}
        }));
        assert!(r.get("result").is_some(), "initialize failed: {r:?}");
        this
    }

    fn send(&mut self, method: &str, params: serde_json::Value) -> serde_json::Value {
        self.id += 1;
        let msg = serde_json::json!({
            "jsonrpc": "2.0",
            "id": self.id,
            "method": method,
            "params": params,
        });
        let line = serde_json::to_string(&msg).unwrap() + "\n";
        self.stdin.write_all(line.as_bytes()).unwrap();
        self.stdin.flush().unwrap();
        let mut buf = String::new();
        self.reader.read_line(&mut buf).unwrap();
        serde_json::from_str(&buf).unwrap_or(serde_json::json!({"error": "parse failed"}))
    }

    fn call(&mut self, tool: &str, args: serde_json::Value) -> serde_json::Value {
        self.send("tools/call", serde_json::json!({"name": tool, "arguments": args}))
    }

    fn result_text(&self, r: &serde_json::Value) -> String {
        r["result"]["content"][0]["text"].as_str().unwrap_or("").to_string()
    }
}

impl Drop for McpProcess {
    fn drop(&mut self) {
        self.proc.kill().ok();
    }
}

#[test]
fn test_tool_count_is_61() {
    let mut mcp = McpProcess::start();
    let r = mcp.send("tools/list", serde_json::json!({}));
    let tools = r["result"]["tools"].as_array().expect("no tools array");
    assert_eq!(tools.len(), 61, "Expected 61 tools, got {}", tools.len());
}

#[test]
fn test_all_tool_names_present() {
    let mut mcp = McpProcess::start();
    let r = mcp.send("tools/list", serde_json::json!({}));
    let tools: std::collections::HashSet<String> = r["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| t["name"].as_str().unwrap().to_string())
        .collect();

    let required = [
        "browser_navigate", "browser_go_back", "browser_go_forward", "browser_refresh",
        "browser_wait", "browser_wait_for_url", "browser_wait_for_network_idle",
        "browser_wait_for_request", "browser_wait_for_response", "browser_wait_for_stable_dom",
        "browser_get_state", "browser_get_html", "browser_screenshot", "browser_save_as_pdf",
        "browser_set_viewport", "browser_click", "browser_right_click", "browser_double_click",
        "browser_hover", "browser_drag_to", "browser_type", "browser_fill_form",
        "browser_press_key", "browser_scroll", "browser_scroll_to_text", "browser_select_option",
        "browser_get_dropdown_options", "browser_upload_file", "browser_handle_dialog",
        "browser_extract_content", "browser_find_elements", "browser_search_page",
        "browser_wait_for_element", "browser_get_focused_element", "browser_get_attribute",
        "browser_evaluate", "browser_list_frames", "browser_get_frame_html",
        "browser_get_storage", "browser_set_storage", "browser_clear_storage",
        "browser_new_tab", "browser_list_tabs", "browser_switch_tab", "browser_close_tab",
        "browser_wait_for_tab", "browser_get_cookies", "browser_set_cookies",
        "browser_clear_cookies", "browser_grant_permissions", "browser_set_geolocation",
        "browser_set_extra_headers", "browser_set_user_agent", "browser_set_timezone",
        "browser_set_locale", "browser_emulate_media", "browser_save_state", "browser_load_state",
        "browser_list_sessions", "browser_close_session", "browser_close_all",
    ];
    for name in &required {
        assert!(tools.contains(*name), "Missing tool: {name}");
    }
}

#[test]
fn test_browser_wait() {
    let mut mcp = McpProcess::start();
    let r = mcp.call("browser_wait", serde_json::json!({"seconds": 0.1}));
    assert!(mcp.result_text(&r).contains("Waited"), "unexpected: {:?}", r);
}

#[test]
fn test_browser_list_sessions() {
    let mut mcp = McpProcess::start();
    let r = mcp.call("browser_list_sessions", serde_json::json!({}));
    let text = mcp.result_text(&r);
    assert!(text.contains("session_id") || text.contains("has_cdp"), "unexpected: {text}");
}

#[test]
fn test_server_name_in_tool_descriptions() {
    let mut mcp = McpProcess::start();
    let r = mcp.send("tools/list", serde_json::json!({}));
    let tools: Vec<_> = r["result"]["tools"].as_array().unwrap().iter().collect();
    // Every tool must have a non-empty description
    for t in &tools {
        let name = t["name"].as_str().unwrap();
        let desc = t["description"].as_str().unwrap_or("");
        assert!(!desc.is_empty(), "Tool {name} has empty description");
    }
}

#[test]
fn test_navigate_blocked_by_allowed_domains() {
    // Set env var before spawning the process
    let binary = binary_path();
    let mut proc = Command::new(&binary)
        .arg("mcp")
        .env("AGENTYC_ALLOWED_DOMAINS", "example.com")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("failed to start");
    let mut stdin = proc.stdin.take().unwrap();
    let mut reader = BufReader::new(proc.stdout.take().unwrap());

    let send = |stdin: &mut std::process::ChildStdin, reader: &mut BufReader<_>, id: u64, method: &str, params: serde_json::Value| -> serde_json::Value {
        let msg = serde_json::to_string(&serde_json::json!({"jsonrpc":"2.0","id":id,"method":method,"params":params})).unwrap() + "\n";
        stdin.write_all(msg.as_bytes()).unwrap();
        stdin.flush().unwrap();
        let mut buf = String::new();
        reader.read_line(&mut buf).unwrap();
        serde_json::from_str(&buf).unwrap()
    };

    send(&mut stdin, &mut reader, 1, "initialize", serde_json::json!({"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}));
    let r = send(&mut stdin, &mut reader, 2, "tools/call", serde_json::json!({"name":"browser_navigate","arguments":{"url":"https://blocked.io"}}));
    proc.kill().ok();

    // Should be an error result (blocked domain) — either JSON-RPC error or tool error
    let text = r["result"]["content"][0]["text"].as_str().unwrap_or("");
    let is_jsonrpc_error = r.get("error").is_some();
    let is_tool_error = r["result"]["isError"].as_bool().unwrap_or(false) || text.contains("blocked");
    assert!(is_jsonrpc_error || is_tool_error, "Expected blocked navigation, got: {r:?}");
}
