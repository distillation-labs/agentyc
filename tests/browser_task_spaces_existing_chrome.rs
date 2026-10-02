//! Phase 0 existing-Chrome task-space contracts.
//!
//! These tests are deliberately offline. They validate the local fixture set
//! and exercise the probe's fail-closed command boundary; they do not launch,
//! attach to, or download Chrome.

use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

const FIXTURE_MANIFEST: &str = include_str!("fixtures/browser-task-spaces/manifest.json");

const FIXTURE_FILES: &[&str] = &[
    "small-form.html",
    "dense-admin-table.html",
    "dynamic-feed.html",
    "nested-frame.html",
];

fn repository_root() -> PathBuf {
    option_env!("CARGO_MANIFEST_DIR")
        .map(|manifest| Path::new(manifest).join("../../"))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn run_probe(artifact_name: &str, extra: &[&str]) -> (std::process::Output, PathBuf) {
    let root = repository_root();
    let artifact = root.join("artifacts/p0-coexistence").join(artifact_name);
    let _ = fs::remove_dir_all(&artifact);

    let mut command = Command::new("python3");
    command
        .current_dir(&root)
        .arg("scripts/run_existing_chrome.py")
        .arg("--artifact-dir")
        .arg(format!("artifacts/p0-coexistence/{artifact_name}"))
        .args(extra);
    let output = command
        .output()
        .expect("python3 is required for the probe contract");
    (output, artifact)
}

#[test]
fn fixture_contract_is_local_and_complete() {
    for fixture in FIXTURE_FILES {
        assert!(
            FIXTURE_MANIFEST.contains(fixture),
            "fixture missing: {fixture}"
        );
    }
    assert!(FIXTURE_MANIFEST.contains("\"deterministic\": true"));
    assert!(FIXTURE_MANIFEST.contains("\"network\": \"local-only\""));
    assert!(FIXTURE_MANIFEST.contains("\"external_resources\": false"));
    assert!(!FIXTURE_MANIFEST.contains("http://"));
    assert!(!FIXTURE_MANIFEST.contains("https://"));
}

#[test]
fn scenario_contract_names_two_spaces_two_agents_and_preserves_user_tab() {
    let (output, artifact) = run_probe("rust-scenario-contract", &[]);
    assert!(
        output.status.success(),
        "offline scenario probe failed: {output:?}"
    );
    let report: Value =
        serde_json::from_slice(&output.stdout).expect("scenario probe must emit JSON");
    let spaces = report["result"]["scenario"]["spaces"]
        .as_array()
        .expect("space records");
    assert_eq!(spaces.len(), 2);
    assert_eq!(spaces[0]["space"], "research");
    assert_eq!(spaces[1]["space"], "testing");
    assert_eq!(spaces[0]["agent"], "agent-a");
    assert_eq!(spaces[1]["agent"], "agent-b");
    assert_eq!(
        report["result"]["scenario"]["user_tab"]["agent_may_close"],
        false
    );
    assert_eq!(
        report["result"]["scenario"]["user_tab"]["agent_may_focus"],
        false
    );
    assert_eq!(
        report["result"]["scenario"]["user_tab"]["must_remain_open"],
        true
    );
    assert_eq!(
        report["result"]["scenario"]["isolation"]["cross_space_mutation"],
        "rejected"
    );
    let _ = fs::remove_dir_all(artifact);
}

#[test]
fn offline_probe_passes_without_a_browser_and_writes_bounded_report() {
    let (output, artifact) = run_probe("rust-offline-contract", &[]);
    assert!(
        output.status.success(),
        "offline probe failed: {:?}",
        output
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    let report: Value = serde_json::from_str(&stdout).expect("probe must emit JSON");
    assert_eq!(report["status"], "offline_passed");
    assert_eq!(report["safety"]["browser_launch"], "never");
    assert_eq!(report["safety"]["browser_download"], "never");
    assert_eq!(
        report["safety"]["measurement_status"],
        "not_measured_offline"
    );
    assert!(report["safety"]["cross_space_mutations"].is_null());
    assert!(report["safety"]["user_tab_close"].is_null());
    for key in [
        "schema_version",
        "build_tuple",
        "environment",
        "timestamp",
        "command",
        "result",
        "redaction_status",
    ] {
        assert!(report.get(key).is_some(), "missing envelope field {key}");
    }
    assert_eq!(report["redaction_status"]["status"], "applied");
    assert!(!report.to_string().contains("Bearer "));
    assert!(
        fs::metadata(artifact.join("report.json"))
            .expect("report artifact")
            .len()
            < 64 * 1024
    );
    let _ = fs::remove_dir_all(&artifact);
}

#[test]
fn headed_probe_fails_closed_without_existing_chrome_harness() {
    let (output, artifact) = run_probe("rust-headed-contract", &["--headed"]);
    assert!(
        !output.status.success(),
        "headed probe must not be false-green"
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    let report: Value = serde_json::from_str(&stdout).expect("headed probe must emit JSON");
    assert_eq!(report["status"], "live_required_unavailable");
    assert!(
        report["live"]["reason"]
            .as_str()
            .unwrap_or_default()
            .contains("no valid existing-Chrome/extension harness descriptor")
    );
    assert!(
        stderr.is_empty(),
        "expected the structured report on stdout: {stderr}"
    );
    assert_eq!(report["safety"]["browser_launch"], "never");
    assert_eq!(report["safety"]["browser_download"], "never");
    assert!(!stdout.contains("launch_chrome"));
    assert!(!stdout.contains("download_chrome"));
    let _ = fs::remove_dir_all(&artifact);
}

#[test]
fn invalid_scenario_counts_are_rejected_before_any_live_work() {
    let (output, artifact) = run_probe("rust-invalid-contract", &["--spaces", "1"]);
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("exactly two spaces and two agents"));
    assert!(String::from_utf8_lossy(&output.stdout).is_empty());
    let _ = fs::remove_dir_all(&artifact);
}
