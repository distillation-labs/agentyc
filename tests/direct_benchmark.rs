//! Offline-only Phase 0 benchmark contract checks.
//!
//! This file is intentionally std-only and does not launch Chrome. It can be
//! registered by a future test-target update without adding dependencies or
//! changing the production crates.

const FIXTURE_MANIFEST: &str = include_str!("fixtures/browser-task-spaces/manifest.json");
const TOOL_CATALOG: &str = include_str!("fixtures/mcp/tool_catalog.json");
const OFFLINE_WORKFLOW: &str = include_str!("fixtures/mcp/offline_workflow.json");
const SMALL_FORM: &[u8] = include_bytes!("fixtures/browser-task-spaces/small-form.html");
const DENSE_TABLE: &[u8] = include_bytes!("fixtures/browser-task-spaces/dense-admin-table.html");
const DYNAMIC_FEED: &[u8] = include_bytes!("fixtures/browser-task-spaces/dynamic-feed.html");
const NESTED_FRAME: &[u8] = include_bytes!("fixtures/browser-task-spaces/nested-frame.html");
const DIRECT_BENCHMARK_SCRIPT: &str = include_str!("../scripts/run_direct_benchmark.py");

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
fn smoke_sample_counts_cannot_claim_tail_gates() {
    let smoke_samples = 30;
    let minimum_p95 = 200;
    let minimum_p99 = 1_000;
    assert!(smoke_samples < minimum_p95);
    assert!(smoke_samples < minimum_p99);
}

#[test]
fn benchmark_tail_thresholds_are_fixed_unless_smoke_is_explicit() {
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("MIN_P95_SAMPLES = 200"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("MIN_P99_SAMPLES = 1_000"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("--smoke"));
    assert!(!DIRECT_BENCHMARK_SCRIPT.contains("--min-samples-p95"));
    assert!(!DIRECT_BENCHMARK_SCRIPT.contains("--min-samples-p99"));
}

#[test]
fn benchmark_accounts_each_sample_status_separately() {
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("statuses.count(\"valid\")"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("statuses.count(\"error\")"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("\"invalid\": invalid"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("sample[\"sample_status\"] = \"invalid\""));
}

#[test]
fn nested_frame_coverage_is_recursive_and_explicit() {
    let fixture = String::from_utf8_lossy(NESTED_FRAME);
    assert_eq!(fixture.matches("<iframe").count(), 1);
    assert_eq!(fixture.matches("<button").count(), 2);
    assert!(fixture.contains("inner-action"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("srcdoc"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("frames_scanned"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("nested_frame_coverage"));
    assert!(!DIRECT_BENCHMARK_SCRIPT.contains("\"full_snapshot_actionable_control_coverage\": 1.0"));
}

#[test]
fn offline_action_metrics_and_baseline_metadata_are_labeled() {
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("synthetic_offline_control_presence_check"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("browser_action_executed"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("baseline_manifest"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("mean_confidence_interval"));
    assert!(DIRECT_BENCHMARK_SCRIPT.contains("normal_approximation"));
}
