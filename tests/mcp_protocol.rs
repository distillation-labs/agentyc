//! Wire-level tests for the host-backed MCP server over stdio.

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use tempfile::TempDir;

fn binary_path() -> std::path::PathBuf {
    let mut path = std::env::current_exe()
        .expect("test executable path")
        .parent()
        .expect("test profile directory")
        .parent()
        .expect("target directory")
        .to_path_buf();
    path.push("agentyc");
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
    _state_dir: TempDir,
    id: u64,
}

impl McpProcess {
    fn start() -> Self {
        let binary = binary_path();
        assert!(binary.exists(), "Binary not found at {}", binary.display());
        let state_dir = TempDir::new().expect("isolated host state directory");
        let mut proc = Command::new(&binary)
            .arg("--offline")
            .arg("--state-dir")
            .arg(state_dir.path())
            .arg("mcp")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("failed to start agentyc host-backed MCP");
        let stdin = proc.stdin.take().expect("MCP stdin");
        let reader = BufReader::new(proc.stdout.take().expect("MCP stdout"));
        let mut this = Self {
            proc,
            reader,
            stdin,
            _state_dir: state_dir,
            id: 0,
        };
        let response = this.send(
            "initialize",
            serde_json::json!({
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "host-mcp-test", "version": "1"}
            }),
        );
        assert!(response.get("result").is_some(), "initialize failed: {response:?}");
        assert_eq!(response["result"]["protocolVersion"], "2024-11-05");
        this
    }

    fn send(&mut self, method: &str, params: serde_json::Value) -> serde_json::Value {
        self.id += 1;
        let message = serde_json::json!({
            "jsonrpc": "2.0",
            "id": self.id,
            "method": method,
            "params": params,
        });
        self.write_message(&message);
        self.read_message()
    }

    fn call(&mut self, tool: &str, args: serde_json::Value) -> serde_json::Value {
        self.send(
            "tools/call",
            serde_json::json!({"name": tool, "arguments": args}),
        )
    }

    fn write_message(&mut self, message: &serde_json::Value) {
        let line = serde_json::to_string(message).expect("serialize MCP request") + "\n";
        self.stdin.write_all(line.as_bytes()).expect("write MCP request");
        self.stdin.flush().expect("flush MCP request");
    }

    fn read_message(&mut self) -> serde_json::Value {
        let mut line = String::new();
        self.reader.read_line(&mut line).expect("read MCP response");
        serde_json::from_str(&line).expect("parse MCP response")
    }
}

impl Drop for McpProcess {
    fn drop(&mut self) {
        let _ = self.proc.kill();
        let _ = self.proc.wait();
    }
}

fn structured(response: &serde_json::Value) -> &serde_json::Value {
    response["result"]["structuredContent"]
        .as_object()
        .map(|_| &response["result"]["structuredContent"])
        .expect("structured MCP tool result")
}

#[test]
fn tool_list_contains_only_host_backed_logical_operations() {
    let mut mcp = McpProcess::start();
    let response = mcp.send("tools/list", serde_json::json!({}));
    let tools = response["result"]["tools"].as_array().expect("tools array");
    assert_eq!(tools.len(), 29);

    let names: std::collections::HashSet<_> = tools
        .iter()
        .map(|tool| tool["name"].as_str().expect("tool name"))
        .collect();
    for required in [
        "host_space_create",
        "host_lease_acquire",
        "host_page_create",
        "host_page_list",
        "host_snapshot_read",
        "host_action_execute",
        "host_action_reconcile",
        "host_event_resume",
    ] {
        assert!(names.contains(required), "Missing host operation {required}");
    }
    assert!(names.iter().all(|name| name.starts_with("host_")));
    assert!(!names.iter().any(|name| name.starts_with("browser_")));
    for tool in tools {
        assert!(
            tool["description"].as_str().is_some_and(|description| !description.is_empty()),
            "Tool {} has no description",
            tool["name"]
        );
    }
}

#[test]
fn space_creation_denies_missing_shared_profile_acknowledgement() {
    let mut mcp = McpProcess::start();
    let response = mcp.call(
        "host_space_create",
        serde_json::json!({
            "label": "consent-test",
            "profile_scope": "shared_existing_profile",
            "shared_state_notice": "shared_profile_state",
            "isolation_claim": false,
            "profile_disclosure_acknowledged": false
        }),
    );
    assert_eq!(response["result"]["isError"], true);
    assert_eq!(structured(&response)["error"]["code"], "permission_denied");
}

#[test]
fn create_space_lease_and_logical_page_over_stdio() {
    let mut mcp = McpProcess::start();
    let created = mcp.call(
        "host_space_create",
        serde_json::json!({
            "label": "protocol-test",
            "profile_scope": "shared_existing_profile",
            "shared_state_notice": "shared_profile_state",
            "isolation_claim": false,
            "profile_disclosure_acknowledged": true
        }),
    );
    assert_eq!(created["result"]["isError"], false);
    let space = structured(&created)["result"]["space_id"]
        .as_str()
        .expect("logical space id")
        .to_owned();

    let lease = mcp.call(
        "host_lease_acquire",
        serde_json::json!({"space_id": space, "now": 1, "ttl": 60_000}),
    );
    assert_eq!(lease["result"]["isError"], false);
    let lease_epoch = structured(&lease)["result"]["lease"]["lease_epoch"]
        .as_u64()
        .expect("lease epoch");

    let page = mcp.call(
        "host_page_create",
        serde_json::json!({
            "space_id": space,
            "lease_epoch": lease_epoch,
            "label": "main",
            "now": 2
        }),
    );
    assert_eq!(page["result"]["isError"], false);
    let page_id = structured(&page)["result"]["page_id"]
        .as_str()
        .expect("logical page id");

    let pages = mcp.call("host_page_list", serde_json::json!({"space_id": space}));
    assert_eq!(pages["result"]["isError"], false);
    let page_list = &structured(&pages)["result"]["pages"];
    assert_eq!(page_list.as_array().expect("page list").len(), 1);
    assert_eq!(page_list[0]["page_id"], page_id);
    assert_eq!(page_list[0]["label"], "main");
}
