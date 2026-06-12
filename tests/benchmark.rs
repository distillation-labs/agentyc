//! Benchmark: cold-start, tools/list, and per-tool round-trip latency over stdio.
//! Run with: AGENTYC_HEADLESS=1 cargo test --test benchmark -- --nocapture

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn binary_path() -> std::path::PathBuf {
    // Prefer release binary for accurate benchmarks, fall back to debug
    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let release = manifest.join("../../target/release/agentyc");
    if release.exists() { return release; }
    let debug = manifest.join("../../target/debug/agentyc");
    if debug.exists() { return debug; }
    // Fallback via exe path
    let mut path = std::env::current_exe().unwrap()
        .parent().unwrap().parent().unwrap().to_path_buf();
    path.push("agentyc");
    path
}

struct McpProcess {
    proc: std::process::Child,
    reader: BufReader<std::process::ChildStdout>,
    stdin: std::process::ChildStdin,
    id: u64,
}

impl McpProcess {
    fn start() -> (Self, Duration) {
        let binary = binary_path();
        let t0 = Instant::now();
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
        let r = this.send("initialize", serde_json::json!({
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "bench", "version": "1"}
        }));
        let cold_start = t0.elapsed();
        assert!(r.get("result").is_some(), "initialize failed: {r:?}");
        (this, cold_start)
    }

    fn send(&mut self, method: &str, params: serde_json::Value) -> serde_json::Value {
        self.id += 1;
        let msg = serde_json::to_string(&serde_json::json!({
            "jsonrpc": "2.0", "id": self.id,
            "method": method, "params": params,
        })).unwrap() + "\n";
        self.stdin.write_all(msg.as_bytes()).unwrap();
        self.stdin.flush().unwrap();
        let mut buf = String::new();
        self.reader.read_line(&mut buf).unwrap();
        serde_json::from_str(&buf).unwrap_or(serde_json::json!({"error": "parse failed"}))
    }

    fn call_timed(&mut self, tool: &str, args: serde_json::Value) -> (serde_json::Value, Duration) {
        let t0 = Instant::now();
        let r = self.send("tools/call", serde_json::json!({"name": tool, "arguments": args}));
        (r, t0.elapsed())
    }
}

impl Drop for McpProcess {
    fn drop(&mut self) { self.proc.kill().ok(); }
}

fn percentile(sorted: &[Duration], p: f64) -> Duration {
    if sorted.is_empty() { return Duration::ZERO; }
    let idx = ((sorted.len() as f64 - 1.0) * p / 100.0).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

fn ms(d: Duration) -> f64 { d.as_secs_f64() * 1000.0 }

#[test]
fn benchmark_mcp_performance() {
    let binary = binary_path();
    assert!(binary.exists(), "Build first: cargo build -p agentyc");

    println!("\n========================================");
    println!(" agentyc MCP Performance Benchmark");
    println!("========================================\n");

    // ── 1. Cold-start (3 runs) ────────────────────────────────────────────────
    let mut cold_starts = Vec::new();
    for _ in 0..3 {
        let (mut mcp, cold) = McpProcess::start();
        cold_starts.push(cold);
        mcp.proc.kill().ok();
    }
    cold_starts.sort();
    println!("Cold-start (process spawn → initialize response):");
    println!("  min={:.1}ms  median={:.1}ms  max={:.1}ms",
        ms(cold_starts[0]),
        ms(cold_starts[cold_starts.len() / 2]),
        ms(*cold_starts.last().unwrap()));

    // ── 2. tools/list latency (20 calls) ─────────────────────────────────────
    let (mut mcp, _) = McpProcess::start();
    let mut list_times = Vec::new();
    for _ in 0..20 {
        let t0 = Instant::now();
        let r = mcp.send("tools/list", serde_json::json!({}));
        list_times.push(t0.elapsed());
        assert!(r["result"]["tools"].is_array());
    }
    list_times.sort();
    println!("\ntools/list round-trip (n=20):");
    println!("  p50={:.2}ms  p95={:.2}ms  p99={:.2}ms  min={:.2}ms  max={:.2}ms",
        ms(percentile(&list_times, 50.0)),
        ms(percentile(&list_times, 95.0)),
        ms(percentile(&list_times, 99.0)),
        ms(list_times[0]),
        ms(*list_times.last().unwrap()));

    // ── 3. No-op tool calls: browser_list_sessions — pure MCP overhead (50 calls) ─
    let mut wait_times = Vec::new();
    for _ in 0..50 {
        let (_, d) = mcp.call_timed("browser_list_sessions", serde_json::json!({}));
        wait_times.push(d);
    }
    wait_times.sort();
    println!("\nbrowser_list_sessions — MCP overhead baseline (n=50):");
    println!("  p50={:.2}ms  p95={:.2}ms  p99={:.2}ms  min={:.2}ms  max={:.2}ms",
        ms(percentile(&wait_times, 50.0)),
        ms(percentile(&wait_times, 95.0)),
        ms(percentile(&wait_times, 99.0)),
        ms(wait_times[0]),
        ms(*wait_times.last().unwrap()));
    let overhead_p50 = percentile(&wait_times, 50.0);

    // ── 5. Throughput: max calls/sec on no-op (sustained 200 calls) ──────────
    let t_start = Instant::now();
    const THROUGHPUT_N: usize = 200;
    for _ in 0..THROUGHPUT_N {
        mcp.send("tools/call", serde_json::json!({"name": "browser_list_sessions", "arguments": {}}));
    }
    let throughput_duration = t_start.elapsed();
    let calls_per_sec = THROUGHPUT_N as f64 / throughput_duration.as_secs_f64();
    println!("\nSustained throughput — browser_wait(0s) × {THROUGHPUT_N}:");
    println!("  total={:.1}ms  calls/sec={:.0}",
        ms(throughput_duration), calls_per_sec);

    // ── 6. JSON-RPC framing overhead (tools/list payload size) ───────────────
    let r = mcp.send("tools/list", serde_json::json!({}));
    let payload_bytes = serde_json::to_string(&r).unwrap().len();
    let tool_count = r["result"]["tools"].as_array().unwrap().len();
    println!("\ntools/list payload: {payload_bytes} bytes for {tool_count} tools ({:.0} bytes/tool avg)",
        payload_bytes as f64 / tool_count as f64);

    // ── Summary ───────────────────────────────────────────────────────────────
    println!("\n========================================");
    println!(" Summary");
    println!("========================================");
    println!("  Cold-start median:     {:.1}ms", ms(cold_starts[cold_starts.len() / 2]));
    println!("  tools/list p50:        {:.2}ms", ms(percentile(&list_times, 50.0)));
    println!("  MCP overhead p50:      {:.2}ms", ms(overhead_p50));
    println!("  Max throughput:        {:.0} calls/sec", calls_per_sec);

    // Regression assertions
    let cold_median = cold_starts[cold_starts.len() / 2];
    assert!(cold_median < Duration::from_millis(500),
        "Cold-start regression: {:.1}ms > 500ms", ms(cold_median));
    assert!(overhead_p50 < Duration::from_millis(10),
        "MCP overhead regression: {:.2}ms > 10ms", ms(overhead_p50));
    assert!(calls_per_sec > 100.0,
        "Throughput regression: {:.0} calls/sec < 100", calls_per_sec);

    println!("\n✓ All benchmarks passed.");
}
