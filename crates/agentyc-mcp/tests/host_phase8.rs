use std::{sync::Arc, time::Duration};

use agentyc_core::{ClientMetadata, ConnectionNonce, HelloEnvelope, PROTOCOL_VERSION, PrincipalId};
use agentyc_host::{Broker, FakeBridge, Ledger, LocalHostServer, LocalSocketClient};
use agentyc_mcp::{HostBrowserServer, RemoteHostBrowserServer};
use rmcp::ServiceExt;
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader, ReadHalf, WriteHalf},
    time::timeout,
};

fn hello(principal: &str, nonce: &str) -> HelloEnvelope {
    HelloEnvelope {
        protocol: PROTOCOL_VERSION,
        supported_protocols: vec![PROTOCOL_VERSION],
        principal_id: PrincipalId::from_suffix(principal).expect("principal"),
        resume_from: None,
        client_metadata: Some(ClientMetadata {
            client_id: None,
            client_name: Some("mcp-phase8-test".to_owned()),
            client_version: Some("1".to_owned()),
            connection_nonce: Some(ConnectionNonce::from_suffix(nonce).expect("nonce")),
            profile_binding_id: None,
        }),
    }
}

fn broker(directory: &TempDir, bridge: Arc<FakeBridge>) -> Broker {
    let ledger = Ledger::open(directory.path()).expect("ledger");
    Broker::with_shared_bridge(ledger, bridge)
}

struct WireClient {
    reader: BufReader<ReadHalf<tokio::io::DuplexStream>>,
    writer: WriteHalf<tokio::io::DuplexStream>,
    next_id: u64,
}

impl WireClient {
    async fn connect(io: tokio::io::DuplexStream) -> Self {
        let (reader, writer) = tokio::io::split(io);
        let mut client = Self {
            reader: BufReader::new(reader),
            writer,
            next_id: 1,
        };
        let response = client
            .request(
                "initialize",
                json!({
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "phase8-test", "version": "1"}
                }),
            )
            .await;
        assert!(response.get("result").is_some(), "initialize: {response}");
        client.notification("notifications/initialized").await;
        client
    }

    async fn request(&mut self, method: &str, params: Value) -> Value {
        let id = self.next_id;
        self.next_id += 1;
        self.write_message(&json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        }))
        .await;
        loop {
            let mut line = String::new();
            self.reader
                .read_line(&mut line)
                .await
                .expect("read response");
            let response: Value = serde_json::from_str(&line).expect("JSON-RPC response");
            if response.get("id").and_then(Value::as_u64) == Some(id) {
                return response;
            }
        }
    }

    async fn call(&mut self, name: &str, arguments: Value) -> Value {
        self.request("tools/call", json!({"name": name, "arguments": arguments}))
            .await
    }

    async fn notification(&mut self, method: &str) {
        self.write_message(&json!({"jsonrpc": "2.0", "method": method}))
            .await;
    }

    async fn write_message(&mut self, message: &Value) {
        self.writer
            .write_all(serde_json::to_string(message).unwrap().as_bytes())
            .await
            .expect("write JSON-RPC request");
        self.writer.write_all(b"\n").await.expect("write newline");
        self.writer.flush().await.expect("flush JSON-RPC request");
    }
}

async fn start_inprocess_server(
    broker: Broker,
    hello: HelloEnvelope,
) -> (WireClient, tokio::task::JoinHandle<()>) {
    let (client_io, server_io) = tokio::io::duplex(32 * 1024);
    let server_task = tokio::spawn(async move {
        let server = HostBrowserServer::new(broker, hello).expect("host service");
        let running = server.serve(server_io).await.expect("server transport");
        let _ = running.waiting().await;
    });
    (WireClient::connect(client_io).await, server_task)
}

#[tokio::test]
async fn host_tools_return_structured_results_over_mcp() {
    let directory = tempfile::tempdir().expect("tempdir");
    let (mut client, server_task) = start_inprocess_server(
        broker(&directory, Arc::new(FakeBridge::new())),
        hello("wire-result", "wire-result"),
    )
    .await;

    let response = client
        .call("host_space_create", json!({"label": "phase-eight"}))
        .await;
    assert_eq!(response["result"]["isError"], false);
    let body = &response["result"]["structuredContent"];
    assert_eq!(body["ok"], true);
    assert!(
        body["result"]["space_id"]
            .as_str()
            .unwrap()
            .starts_with("space_")
    );
    assert_eq!(body["result"]["label"], "phase-eight");

    drop(client);
    timeout(Duration::from_secs(2), server_task)
        .await
        .expect("server stops after client disconnect")
        .expect("server task");
}

#[tokio::test]
async fn host_server_returns_structured_unsupported_capability_error() {
    let directory = tempfile::tempdir().expect("tempdir");
    let bridge = Arc::new(FakeBridge::new());
    bridge.set_capabilities(vec![agentyc_core::Capability::Action]);
    let (mut client, server_task) = start_inprocess_server(
        broker(&directory, bridge),
        hello("wire-error", "wire-error"),
    )
    .await;

    let response = client
        .call(
            "host_snapshot_read",
            json!({"space_id": "space_test", "page_id": "page_test", "lease_epoch": 1}),
        )
        .await;
    assert_eq!(response["result"]["isError"], true);
    assert_eq!(
        response["result"]["structuredContent"]["error"]["code"],
        "capability_unavailable"
    );

    drop(client);
    timeout(Duration::from_secs(2), server_task)
        .await
        .expect("server stops")
        .expect("server task");
}

#[tokio::test]
async fn remote_tool_requests_enforce_request_bounds_and_unsupported_methods() {
    let directory = tempfile::tempdir().expect("tempdir");
    let broker = broker(&directory, Arc::new(FakeBridge::new()));
    let socket_path = directory.path().join("phase8.sock");
    let host = LocalHostServer::start(broker, &socket_path).expect("local host");
    let client = LocalSocketClient::connect(&socket_path, hello("remote-bounds", "remote-bounds"))
        .expect("local client");
    let server = RemoteHostBrowserServer::new(client);

    let (client_io, server_io) = tokio::io::duplex(32 * 1024);
    let server_task = tokio::spawn(async move {
        let running = server
            .serve(server_io)
            .await
            .expect("remote server transport");
        let _ = running.waiting().await;
    });
    let mut client = WireClient::connect(client_io).await;

    let too_many = Value::Object(
        (0..17)
            .map(|index| (format!("field_{index}"), Value::String("v".to_owned())))
            .collect(),
    );
    let response = client.call("host_space_create", too_many).await;
    assert_eq!(response["result"]["isError"], true);
    assert_eq!(
        response["result"]["structuredContent"]["error"]["code"],
        "message_too_large"
    );

    let response = client
        .call(
            "host_space_create",
            json!({"label": "x".repeat(4 * 1024 + 1)}),
        )
        .await;
    assert_eq!(response["result"]["isError"], true);
    assert_eq!(
        response["result"]["structuredContent"]["error"]["code"],
        "message_too_large"
    );

    let response = client
        .call(
            "host_page_bind",
            json!({"space_id": "space_test", "page_id": "page_test", "lease_epoch": 1}),
        )
        .await;
    assert_eq!(response["result"]["isError"], true);
    assert_eq!(
        response["result"]["structuredContent"]["error"]["code"],
        "capability_unavailable"
    );

    let response = client
        .call("host_space_create", json!({"label": "within-bound"}))
        .await;
    assert_eq!(response["result"]["isError"], false);
    assert_eq!(
        response["result"]["structuredContent"]["result"]["space"]["label"],
        "within-bound"
    );

    drop(client);
    timeout(Duration::from_secs(2), server_task)
        .await
        .expect("MCP server stops")
        .expect("server task");
    host.stop();
}

#[tokio::test]
async fn concurrent_connections_and_disconnect_are_isolated() {
    let directory = tempfile::tempdir().expect("tempdir");
    let broker = broker(&directory, Arc::new(FakeBridge::new()));
    let (mut first_client, first_task) =
        start_inprocess_server(broker.clone(), hello("concurrent-one", "concurrent-one")).await;
    let (mut second_client, second_task) =
        start_inprocess_server(broker, hello("concurrent-two", "concurrent-two")).await;

    let (one, two) = tokio::join!(
        first_client.call("host_space_create", json!({"label": "first"})),
        second_client.call("host_space_create", json!({"label": "second"})),
    );
    assert_eq!(one["result"]["isError"], false);
    assert_eq!(two["result"]["isError"], false);
    assert_ne!(
        one["result"]["structuredContent"]["result"]["space_id"],
        two["result"]["structuredContent"]["result"]["space_id"]
    );

    drop(first_client);
    timeout(Duration::from_secs(2), first_task)
        .await
        .expect("first server disconnects")
        .expect("first server task");

    let still_connected = second_client.call("host_space_list", json!({})).await;
    assert_eq!(still_connected["result"]["isError"], false);
    let visible_spaces = still_connected["result"]["structuredContent"]["result"]
        .as_array()
        .unwrap_or_else(|| panic!("space list response: {still_connected}"));
    assert_eq!(visible_spaces.len(), 1);
    assert_eq!(visible_spaces[0]["owner"], "principal_concurrent-two");

    drop(second_client);
    timeout(Duration::from_secs(2), second_task)
        .await
        .expect("second server disconnects")
        .expect("second server task");
}
