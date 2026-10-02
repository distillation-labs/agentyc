"""Unit checks for benchmark sample accounting and bounded raw artifacts."""

from __future__ import annotations

import html
import importlib.util
import shutil
import sys
import tempfile
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

_ENVELOPE_SCRIPT = ROOT / "scripts" / "artifact_envelope.py"
_envelope_spec = importlib.util.spec_from_file_location("phase0_artifact_envelope", _ENVELOPE_SCRIPT)
if _envelope_spec is None or _envelope_spec.loader is None:
    raise RuntimeError("could not load artifact envelope")
_envelope = importlib.util.module_from_spec(_envelope_spec)
_envelope_spec.loader.exec_module(_envelope)


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

    def test_command_and_nested_values_are_redacted_before_persistence(self) -> None:
        command = _envelope._safe_command(
            [
                "scripts/run_direct_benchmark.py",
                "--artifact-dir=/private/user/artifacts",
                "--profile-dir",
                "/Users/user/Profile",
                "TAB_ID=raw-tab-12345678",
                "--cache-states",
                "clean",
            ]
        )
        rendered_command = " ".join(command)
        self.assertNotIn("/private/user", rendered_command)
        self.assertNotIn("/Users/user", rendered_command)
        self.assertNotIn("raw-tab-12345678", rendered_command)
        self.assertIn("--artifact-dir=<redacted>", rendered_command)
        self.assertIn("TAB_ID=<redacted>", rendered_command)

        value = _envelope.redact_for_persistence(
            {
                "nested": {
                    "tab_id": "raw-tab-12345678",
                    "path": "/private/user/page.html",
                    "page_body": "<html>private body</html>",
                    "error": "secret stack trace",
                },
                "items": [{"session_id": "raw-session-12345678"}],
            }
        )
        self.assertEqual(value["nested"]["tab_id"], "<redacted id>")
        self.assertEqual(value["nested"]["path"], "<absolute-path-redacted>")
        self.assertEqual(value["nested"]["page_body"], "<redacted page body>")
        self.assertEqual(value["nested"]["error"], "<redacted error>")
        self.assertEqual(value["items"][0]["session_id"], "<redacted id>")

    def test_recursive_iframe_limits_are_enforced(self) -> None:
        nested = "<button>ok</button>"
        for _ in range(_module.MAX_IFRAME_DEPTH + 1):
            nested = f'<iframe srcdoc="{html.escape(nested, quote=True)}"></iframe>'
        with self.assertRaises(ValueError):
            parser = _module.ControlCounter()
            parser.feed(nested)
            parser.close()

        with self.assertRaises(ValueError):
            parser = _module.ControlCounter()
            parser.feed("x" * (_module.MAX_TEXT_CHARS + 1))

        with self.assertRaises(ValueError):
            parser = _module.ControlCounter()
            oversized_srcdoc = "x" * (_module.MAX_SRCDOC_CHARS + 1)
            parser.feed(f'<iframe srcdoc="{oversized_srcdoc}"></iframe>')

    def test_symlinked_fixture_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture_root = root / "fixtures"
            fixture_root.mkdir()
            outside = root / "outside.html"
            outside.write_text("<button>outside</button>", encoding="utf-8")
            (fixture_root / "linked.html").symlink_to(outside)
            original = _module.FIXTURE_ROOT
            try:
                vars(_module)["FIXTURE_ROOT"] = fixture_root
                with self.assertRaises(ValueError):
                    _module._safe_fixture_path("linked.html")
            finally:
                vars(_module)["FIXTURE_ROOT"] = original

    def test_publication_commits_a_generation_and_preserves_the_previous_one(self) -> None:
        workspace = Path(tempfile.mkdtemp(dir=_module.ROOT / "artifacts"))
        artifact_dir = workspace / "benchmark"
        try:
            first_nonce = "a" * 32
            _module.publish_benchmark(
                artifact_dir,
                {"nonce": first_nonce, "status": "first"},
                "# first\n",
                [("raw_samples.jsonl", b"{}\n")],
            )
            self.assertTrue((artifact_dir / "generation-manifest.json").is_file())
            self.assertTrue((artifact_dir / "COMMIT").is_file())
            first_baseline = (artifact_dir / "baseline.json").read_bytes()

            second_nonce = "b" * 32
            second = _module.publish_benchmark(
                artifact_dir,
                {"nonce": second_nonce, "status": "second"},
                "# second\n",
                [("raw_samples.jsonl", b"{}\n")],
            )
            self.assertNotEqual((artifact_dir / "baseline.json").read_bytes(), first_baseline)
            previous = _module.ROOT / second["previous_generation"]
            self.assertTrue(previous.is_dir())
            self.assertEqual((previous / "baseline.json").read_bytes(), first_baseline)
            self.assertEqual((artifact_dir / "COMMIT").read_text(encoding="utf-8").count("generation-b"), 1)

            preserved_baseline = (artifact_dir / "baseline.json").read_bytes()
            with self.assertRaises(ValueError):
                _module.publish_benchmark(
                    artifact_dir,
                    {"nonce": "c" * 32, "status": "failed"},
                    "# failed\n",
                    [("../unsafe.jsonl", b"{}\n")],
                )
            self.assertEqual((artifact_dir / "baseline.json").read_bytes(), preserved_baseline)
            self.assertTrue((artifact_dir / "COMMIT").is_file())
        finally:
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
