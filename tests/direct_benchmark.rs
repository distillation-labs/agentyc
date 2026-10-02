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
