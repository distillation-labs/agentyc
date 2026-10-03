#![cfg(unix)]

use std::{path::PathBuf, process::Command};

use agentyc_host::{Broker, FakeBridge, LocalHostServer, NullBridge};
use tempfile::tempdir;

#[test]
fn probe_succeeds_against_running_local_host_socket() {
    let directory = tempdir().expect("tempdir");
    let socket_path = directory.path().join("host.sock");
    let broker = Broker::open(directory.path().join("state"), FakeBridge::new()).expect("broker");
    let server = LocalHostServer::start(broker, &socket_path).expect("server");

    let output = run_probe(&socket_path);
    assert!(
        output.status.success(),
        "probe failed:\nstdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let report: serde_json::Value = serde_json::from_slice(&output.stdout).expect("json report");
    assert_eq!(report["success"], serde_json::Value::Bool(true));
    assert_eq!(
        report["checkpoints"][0]["status"],
        serde_json::Value::String("passed".to_owned())
    );
    assert_eq!(
        report["checkpoints"][5]["status"],
        serde_json::Value::String("passed".to_owned())
    );
    assert_eq!(
        report["checkpoints"][7]["status"],
        serde_json::Value::String("passed".to_owned())
    );
    assert_eq!(
        report["checkpoints"][8]["status"],
        serde_json::Value::String("skipped".to_owned())
    );

    server.stop();
}

#[test]
fn probe_fails_closed_when_extension_capabilities_are_unavailable() {
    let directory = tempdir().expect("tempdir");
    let socket_path = directory.path().join("host.sock");
    let broker = Broker::open(directory.path().join("state"), NullBridge).expect("broker");
    let server = LocalHostServer::start(broker, &socket_path).expect("server");

    let output = run_probe(&socket_path);
    assert!(!output.status.success(), "probe unexpectedly succeeded");

    let report: serde_json::Value = serde_json::from_slice(&output.stdout).expect("json report");
    assert_eq!(report["success"], serde_json::Value::Bool(false));
    assert_eq!(
        report["checkpoints"][1]["status"],
        serde_json::Value::String("failed".to_owned())
    );
    assert_eq!(
        report["checkpoints"][2]["status"],
        serde_json::Value::String("skipped".to_owned())
    );

    server.stop();
}

fn run_probe(socket_path: &PathBuf) -> std::process::Output {
    let binary = std::env::var("CARGO_BIN_EXE_agentyc-existing-chrome-probe")
        .expect("probe binary path env var");
    Command::new(binary)
        .env("AGENTYC_HOST_SOCKET", socket_path)
        .output()
        .expect("run probe")
}
