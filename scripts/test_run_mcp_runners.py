"""Focused tests for deterministic offline MCP runners."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import argparse
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import run_mcp_benchmark as benchmark
import run_mcp_chaos_test as chaos
import run_mcp_load_test as load_test
import run_mcp_release_drill as release_drill
import run_mcp_soak_test as soak


class McpOfflineRunnerTests(unittest.TestCase):
    def test_benchmark_is_deterministic_and_offline(self) -> None:
        first = benchmark.build_report(200, min_samples_p95=100, min_samples_p99=200)
        self.assertEqual(first, benchmark.build_report(200, min_samples_p95=100, min_samples_p99=200))
        self.assertEqual(first["tool_catalog"]["tool_count"], 76)
        self.assertFalse(first["release_eligible"])
        self.assertFalse(first["safety"]["network_access"])
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.build_report(benchmark.MAX_SAMPLES + 1, min_samples_p99=benchmark.MAX_SAMPLES)
        with self.assertRaises(benchmark.BenchmarkError):
            benchmark.build_report(10, min_samples_p95=5, min_samples_p99=11)

    def test_load_accounts_for_capacity_and_bounds_total(self) -> None:
        report = load_test.build_report(clients=2, requests_per_client=50)
        self.assertEqual(report["accounting"], {"attempted": 100, "admitted": 72, "rejected": 28, "typed_overload_rejections": {"ModeledQueueFull": 28}})
        self.assertFalse(report["release_eligible"])
        with self.assertRaises(load_test.LoadError):
            load_test.build_report(clients=32, requests_per_client=1_000)

    def test_soak_duration_cycles_and_unmeasured_resources(self) -> None:
        self.assertEqual(soak.parse_duration("10m"), 600)
        with self.assertRaises(argparse.ArgumentTypeError):
            soak.parse_duration("25h")
        report = soak.build_report(600, 20)
        self.assertEqual(report["duration"]["simulated_runtime_seconds"], 0)
        self.assertTrue(all(item["value"] is None for item in report["resource_observations"].values()))
        with self.assertRaises(ValueError):
            soak.build_report(10, soak.MAX_CYCLES + 1)

    def test_chaos_fault_accounting_is_deterministic_and_no_replay(self) -> None:
        report = chaos.build_report(seeds=2, repetitions=10)
        self.assertEqual(report, chaos.build_report(seeds=2, repetitions=10))
        for seed in report["seed_results"]:
            self.assertEqual(len(seed["fault_results"]), len(chaos.FAULTS))
            self.assertTrue(all(fault["accounted"] == 10 and not fault["mutation_replayed"] for fault in seed["fault_results"]))
        self.assertFalse(report["safety"]["fault_injection_performed"])
        self.assertFalse(report["release_eligible"])

    def test_release_drill_stays_offline_and_ineligible(self) -> None:
        report = release_drill.build_report()
        self.assertEqual(set(report["components"]), {"benchmark", "load", "soak", "chaos"})
        self.assertFalse(report["release_eligible"])
        self.assertFalse(report["rollback_model"]["rollback_performed"])
        self.assertFalse(report["safety"]["installation_performed"])

    def test_cli_writes_bounded_enveloped_report_inside_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=benchmark.ARTIFACT_ROOT) as temporary:
            output_dir = Path(temporary) / "mcp"
            relative = output_dir.relative_to(benchmark.ROOT).as_posix()
            self.assertEqual(benchmark.main(["--samples", "10", "--min-samples-p95", "5", "--min-samples-p99", "10", "--artifact-dir", relative]), 0)
            artifact_path = output_dir / "report.json"
            self.assertLess(artifact_path.stat().st_size, benchmark.MAX_ARTIFACT_BYTES)
            report = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertFalse(report["release_eligible"])
            self.assertEqual(report["redaction_status"]["status"], "applied")
            self.assertEqual(report["build_tuple"]["artifact_kind"], "mcp-benchmark")

    def test_every_cli_emits_an_ineligible_enveloped_artifact(self) -> None:
        with tempfile.TemporaryDirectory(dir=benchmark.ARTIFACT_ROOT) as temporary:
            root = Path(temporary)
            runs = (
                ("benchmark", benchmark, ["--samples", "5", "--min-samples-p95", "2", "--min-samples-p99", "5"]),
                ("load", load_test, ["--clients", "1", "--requests-per-client", "3"]),
                ("soak", soak, ["--duration", "10s", "--cycles", "3"]),
                ("chaos", chaos, ["--seeds", "1", "--repetitions", "2"]),
                ("release", release_drill, []),
            )
            for name, module, arguments in runs:
                with self.subTest(runner=name):
                    output = root / name
                    relative = output.relative_to(benchmark.ROOT).as_posix()
                    self.assertEqual(module.main([*arguments, "--artifact-dir", relative]), 0)
                    path = output / "report.json"
                    self.assertLess(path.stat().st_size, 8 * 1024 * 1024)
                    report = json.loads(path.read_text(encoding="utf-8"))
                    self.assertFalse(report["release_eligible"])
                    self.assertEqual(report["redaction_status"]["status"], "applied")


if __name__ == "__main__":
    unittest.main()
