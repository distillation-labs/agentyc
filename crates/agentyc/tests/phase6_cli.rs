use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;

fn run_cli(state_dir: &std::path::Path, args: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_agentyc"))
        .args(["--offline", "--state-dir"])
        .arg(state_dir)
        .args(["--json"])
        .args(args)
        .output()
        .expect("agentyc process")
}

#[test]
fn direct_success_emits_one_structured_json_value_on_stdout() {
    let directory = tempdir().expect("state directory");
    let output = run_cli(
        directory.path(),
        &[
            "space",
            "create",
            "--label",
            "stdout-contract",
            "--accept-shared-profile-disclosure",
        ],
    );

    assert!(output.status.success(), "stderr: {:?}", output.stderr);
    let value: Value = serde_json::from_slice(&output.stdout).expect("stdout JSON");
    assert_eq!(value["ok"], true);
    assert!(
        value["result"]["space_id"]
            .as_str()
            .is_some_and(|id| id.starts_with("space_"))
    );
    assert!(!String::from_utf8_lossy(&output.stdout).contains("target_id"));
}

#[test]
fn direct_failure_emits_structured_stdout_and_diagnostic_stderr() {
    let directory = tempdir().expect("state directory");
    let output = run_cli(
        directory.path(),
        &["space", "create", "--label", "missing-ack"],
    );

    assert_eq!(output.status.code(), Some(4));
    let value: Value = serde_json::from_slice(&output.stdout).expect("stdout JSON error");
    assert_eq!(value["ok"], false);
    assert_eq!(value["error"]["code"], "permission_denied");
    assert!(String::from_utf8_lossy(&output.stderr).contains("Error:"));
}

#[test]
fn malformed_cli_input_emits_one_json_error_when_json_was_requested() {
    let output = Command::new(env!("CARGO_BIN_EXE_agentyc"))
        .args(["--json", "space", "create", "--label"])
        .output()
        .expect("agentyc process");

    assert_eq!(output.status.code(), Some(2));
    let stdout = String::from_utf8(output.stdout).expect("UTF-8 stdout");
    assert_eq!(stdout.lines().count(), 1);
    let value: Value = serde_json::from_str(&stdout).expect("stdout JSON error");
    assert_eq!(value["ok"], false);
    assert_eq!(value["error"]["code"], "invalid_argument");
    assert_eq!(value["error"]["retryable"], false);
    assert!(!String::from_utf8_lossy(&output.stderr).contains("usage:"));
}

#[test]
fn sensitive_actions_fail_before_dispatch_without_host_intent_ticket() {
    let directory = tempdir().expect("state directory");
    for (index, (operation, payload)) in [
        ("evaluate", r#"{"expression":"1"}"#),
        ("storage_write", r#"{"key":"x","value":"y"}"#),
        ("cookie_write", r#"{"name":"x","value":"y"}"#),
        ("upload", r#"{"path":"x"}"#),
        ("click", r#"{"sensitive_boundary":"payment"}"#),
    ]
    .into_iter()
    .enumerate()
    {
        let request_id = format!("req_sensitive_{index}");
        let action_id = format!("action_sensitive_{index}");
        let idempotency_key = format!("idem_sensitive_{index}");
        let output = Command::new(env!("CARGO_BIN_EXE_agentyc"))
            .args(["--offline", "--state-dir"])
            .arg(directory.path())
            .args([
                "--json",
                "action",
                "execute",
                "--request-id",
                &request_id,
                "--action-id",
                &action_id,
                "--idempotency-key",
                &idempotency_key,
                "--space-id",
                "space_sensitive",
                "--page-id",
                "page_sensitive",
                "--lease-epoch",
                "1",
                "--operation",
                operation,
                "--payload",
                payload,
            ])
            .output()
            .expect("agentyc process");

        assert_eq!(output.status.code(), Some(4), "{operation}");
        let value: Value = serde_json::from_slice(&output.stdout).expect("stdout JSON error");
        assert_eq!(value["ok"], false, "{operation}");
        assert_eq!(value["error"]["code"], "permission_denied", "{operation}");
        assert!(
            value["error"]["message"]
                .as_str()
                .is_some_and(|message| message.contains("user-intent ticket")),
            "{operation}"
        );
    }
}

#[test]
fn extension_status_is_observation_only() {
    let directory = tempdir().expect("state directory");
    let output = run_cli(directory.path(), &["extension", "status"]);

    assert!(output.status.success(), "stderr: {:?}", output.stderr);
    let value: Value = serde_json::from_slice(&output.stdout).expect("stdout JSON");
    assert_eq!(value["ok"], true);
    assert_eq!(value["result"]["observed_connected"], false);
    assert!(value["result"].get("installed").is_none());
}
