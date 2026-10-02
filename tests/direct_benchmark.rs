//! Offline-only Phase 0 benchmark contract checks.
//!
//! These tests do not launch Chrome. The executable smoke lane runs the local
//! benchmark script and validates its emitted JSON artifact without changing
//! production crates.

const FIXTURE_MANIFEST: &str = include_str!("fixtures/browser-task-spaces/manifest.json");
const TOOL_CATALOG: &str = include_str!("fixtures/mcp/tool_catalog.json");
const OFFLINE_WORKFLOW: &str = include_str!("fixtures/mcp/offline_workflow.json");
const SMALL_FORM: &[u8] = include_bytes!("fixtures/browser-task-spaces/small-form.html");
const DENSE_TABLE: &[u8] = include_bytes!("fixtures/browser-task-spaces/dense-admin-table.html");
const DYNAMIC_FEED: &[u8] = include_bytes!("fixtures/browser-task-spaces/dynamic-feed.html");
const NESTED_FRAME: &[u8] = include_bytes!("fixtures/browser-task-spaces/nested-frame.html");
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};

static RUN_ID: AtomicUsize = AtomicUsize::new(0);

fn repository_root() -> PathBuf {
    option_env!("CARGO_MANIFEST_DIR")
        .map(|manifest| Path::new(manifest).join("../../"))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn run_benchmark_for(fixture: &str, cache_state: &str) -> (Value, PathBuf) {
    let root = repository_root();
    let run_id = RUN_ID.fetch_add(1, Ordering::Relaxed);
    let artifact_name = format!("p0-performance-rust-{fixture}-{run_id}");
    let artifact = root.join("artifacts").join(&artifact_name);
    let _ = fs::remove_dir_all(&artifact);
    let output = Command::new("python3")
        .current_dir(&root)
        .args([
            "scripts/run_direct_benchmark.py",
            "--smoke",
            "--warmups",
            "0",
            "--samples",
            "30",
            "--fixtures",
        ])
        .arg(fixture)
        .args(["--cache-states"])
        .arg(cache_state)
        .args(["--spaces", "1", "--artifact-dir"])
        .arg(format!("artifacts/{artifact_name}"))
        .output()
        .expect("python3 is required for the benchmark contract");
    assert!(output.status.success(), "benchmark failed: {output:?}");
    let report = serde_json::from_slice::<Value>(
        &fs::read(artifact.join("baseline.json")).expect("baseline artifact"),
    )
    .expect("baseline must be valid JSON");
    (report, artifact)
}

fn run_benchmark() -> (Value, PathBuf) {
    run_benchmark_for("small-form", "clean")
}

fn percentile(sorted: &[f64], percent: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let index = ((sorted.len() - 1) as f64 * percent / 100.0).round() as usize;
    sorted[index.min(sorted.len() - 1)]
}

#[test]
fn local_fixture_contract_is_complete() {
    assert!(FIXTURE_MANIFEST.contains("small-form.html"));
    assert!(FIXTURE_MANIFEST.contains("dense-admin-table.html"));
    assert!(FIXTURE_MANIFEST.contains("dynamic-feed.html"));
    assert!(FIXTURE_MANIFEST.contains("nested-frame.html"));
    assert!(!FIXTURE_MANIFEST.contains("https://"));
    assert!(!FIXTURE_MANIFEST.contains("http://"));
    assert!(!SMALL_FORM.is_empty());
    assert!(!DENSE_TABLE.is_empty());
    assert!(!DYNAMIC_FEED.is_empty());
    assert!(!NESTED_FRAME.is_empty());
}

#[test]
fn mcp_offline_workflow_is_browser_safe() {
    assert!(TOOL_CATALOG.contains("browser_get_state"));
    assert!(TOOL_CATALOG.contains("browser_navigate"));
    assert!(OFFLINE_WORKFLOW.contains("\"external_requests\": 0"));
    assert!(OFFLINE_WORKFLOW.contains("\"browser_launches\": 0"));
    assert!(OFFLINE_WORKFLOW.contains("\"cdp_urls\": 0"));
}

#[test]
fn percentile_is_stable_for_tail_gate_reporting() {
    let values = [1.0, 2.0, 3.0, 4.0, 5.0];
    assert_eq!(percentile(&[], 95.0), 0.0);
    assert_eq!(percentile(&values, 50.0), 3.0);
    assert_eq!(percentile(&values, 95.0), 5.0);
    assert_eq!(percentile(&values, 99.0), 5.0);
}

#[test]
fn executable_smoke_report_accounts_samples_and_refuses_tail_gates() {
    let (report, artifact) = run_benchmark();
    assert_eq!(report["status"], "offline-smoke");
    assert_eq!(report["sample_accounting"]["attempted"], 30);
    assert_eq!(report["sample_accounting"]["valid"], 30);
    assert_eq!(report["rows"][0]["samples"]["valid"], 30);
    assert_eq!(
        report["rows"][0]["tail_gates"]["p95"]["status"],
        "not_gateable"
    );
    assert_eq!(
        report["rows"][0]["tail_gates"]["p99"]["status"],
        "not_gateable"
    );
    assert_eq!(
        report["rows"][0]["live_only"]["status"],
        "not_measured_offline"
    );
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
    assert_eq!(
        report["fixtures"][0]["sha256"]
            .as_str()
            .unwrap_or_default()
            .len(),
        64
    );
    assert!(
        fs::metadata(artifact.join("raw_samples.jsonl"))
            .expect("raw samples artifact")
            .len()
            > 0
    );
    let _ = fs::remove_dir_all(artifact);
}

#[test]
fn benchmark_tail_thresholds_are_fixed_unless_smoke_is_explicit() {
    let (report, artifact) = run_benchmark();
    assert_eq!(report["tail_thresholds"]["p95"], 200);
    assert_eq!(report["tail_thresholds"]["p99"], 1_000);
    assert_eq!(report["smoke"], true);
    let _ = fs::remove_dir_all(artifact);
}

#[test]
fn benchmark_accounts_each_sample_status_separately() {
    let (report, artifact) = run_benchmark();
    let accounting = &report["sample_accounting"];
    assert_eq!(accounting["attempted"], 30);
    assert_eq!(accounting["valid"], 30);
    assert_eq!(accounting["errors"], 0);
    assert_eq!(accounting["invalid"], 0);
    let _ = fs::remove_dir_all(artifact);
}

#[test]
fn nested_frame_coverage_is_recursive_and_explicit() {
    let fixture = String::from_utf8_lossy(NESTED_FRAME);
    assert_eq!(fixture.matches("<iframe").count(), 1);
    assert_eq!(fixture.matches("<button").count(), 2);
    assert!(fixture.contains("inner-action"));
    let (report, artifact) = run_benchmark_for("nested-frame", "clean");
    let context = &report["rows"][0]["context"];
    assert_eq!(context["frames_discovered"], 1);
    assert_eq!(context["frames_scanned"], 1);
    assert_eq!(context["nested_frame_coverage"], 1.0);
    assert_eq!(context["max_frame_depth"], 1);
    assert_eq!(context["clean_snapshot_dom_scans"], 0);
    let _ = fs::remove_dir_all(artifact);
}

#[test]
fn offline_action_metrics_and_baseline_metadata_are_labeled() {
    let (report, artifact) = run_benchmark();
    let row = &report["rows"][0];
    assert_eq!(row["offline_action"]["status"], "synthetic");
    assert_eq!(row["offline_action"]["browser_action_executed"], false);
    assert_eq!(
        row["context"]["token_measurement_status"],
        "not_available_in_offline_scaffold"
    );
    assert_eq!(
        report["confidence_intervals"]["method"],
        "normal_approximation"
    );
    assert_eq!(report["baseline_manifest"]["schema_version"], 1);
    let _ = fs::remove_dir_all(artifact);
}
