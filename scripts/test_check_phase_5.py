from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import check_phase_5


class Phase5CheckerTests(unittest.TestCase):
    def test_current_manifest_is_structurally_valid_but_not_release_ready(self) -> None:
        result = check_phase_5.check()
        self.assertEqual(result["phase"], 5)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["live_ready"])
        self.assertFalse(result["release_eligible"])

    def test_require_complete_fails_closed_without_live_evidence(self) -> None:
        with self.assertRaises(check_phase_5.Phase5Error):
            check_phase_5.check(require_complete=True)

    def test_duplicate_task_id_is_rejected(self) -> None:
        manifest = json.loads(
            Path(check_phase_5.DEFAULT_MANIFEST).read_text(encoding="utf-8")
        )
        manifest["tasks"][1]["id"] = manifest["tasks"][0]["id"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phase-5-manifest.yaml"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(check_phase_5.Phase5Error):
                check_phase_5.check(path)


if __name__ == "__main__":
    unittest.main()
