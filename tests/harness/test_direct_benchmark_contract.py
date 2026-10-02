"""Unit checks for benchmark sample accounting and bounded raw artifacts."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "run_direct_benchmark.py"
_spec = importlib.util.spec_from_file_location("phase0_direct_benchmark", SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError("could not load direct benchmark")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


class DirectBenchmarkContractTests(unittest.TestCase):
    def test_sample_accounting_keeps_error_and_invalid_separate(self) -> None:
        samples = [
            {"sample_status": "valid"},
            {"sample_status": "error"},
            {"sample_status": "invalid"},
            {"sample_status": "valid"},
        ]
        self.assertEqual(
            _module.sample_accounting(samples),
            {"attempted": 4, "valid": 2, "errors": 1, "invalid": 1},
        )

    def test_raw_sample_chunks_are_bounded_and_named(self) -> None:
        chunks = _module.raw_sample_chunks([{"sample_status": "valid", "value": index} for index in range(100)])
        self.assertEqual(chunks[0][0], "raw_samples.jsonl")
        self.assertTrue(all(len(content) <= _module.MAX_RAW_SAMPLE_FILE_BYTES for _, content in chunks))
        self.assertGreater(sum(content.count(b"\n") for _, content in chunks), 0)


if __name__ == "__main__":
    unittest.main()
