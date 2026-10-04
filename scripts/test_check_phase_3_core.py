import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_phase_3_core as checker


class Phase3CheckerTests(unittest.TestCase):
    def test_manifest_is_strict_json_and_contains_topology(self):
        manifest = checker.parse_manifest(checker.ROOT)
        self.assertEqual(manifest["phase"], 3)
        self.assertEqual(manifest["topology"], "single_broker_native_shim_forwarding")
        self.assertFalse(manifest["release_eligible"])

    def test_artifact_contains_one_bounded_json_block(self):
        artifact = checker.parse_artifact(checker.ROOT)
        self.assertEqual(artifact["schema_version"], 1)
        self.assertFalse(artifact["release_eligible"])
        self.assertTrue(artifact["nonclaims"])

    def test_read_text_rejects_traversal_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "safe.txt").write_text("ok", encoding="utf-8")
            self.assertEqual(checker.read_text(root, Path("safe.txt")), "ok")
            with self.assertRaises(checker.Phase3Error):
                checker.read_text(root, Path("../safe.txt"))
            link = root / "link.txt"
            link.symlink_to(root / "safe.txt")
            with self.assertRaises(checker.Phase3Error):
                checker.read_text(root, Path("link.txt"))

    def test_full_phase_gate_is_green_after_phase_completion(self):
        result = checker.check(checker.ROOT)
        self.assertEqual(result["phase"], 3)
        self.assertEqual(result["status"], "complete")
        self.assertFalse(result["release_eligible"])


if __name__ == "__main__":
    unittest.main()
