import unittest

from scripts import check_phase_4_extension as checker


class Phase4CheckerTests(unittest.TestCase):
    def test_active_phase4_manifest_passes_deterministic_gate(self):
        result = checker.check(checker.ROOT)
        self.assertEqual(result["phase"], 4)
        self.assertEqual(result["status"], "active")
        self.assertFalse(result["release_eligible"])

    def test_manifest_and_artifact_are_bounded_and_non_live(self):
        manifest = checker.manifest(checker.ROOT)
        artifact = checker.artifact(checker.ROOT)
        self.assertFalse(manifest["release_eligible"])
        self.assertFalse(artifact["release_eligible"])
        self.assertTrue(artifact["nonclaims"])


if __name__ == "__main__":
    unittest.main()
