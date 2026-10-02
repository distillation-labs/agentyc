"""Unit tests for the dependency-free deterministic harness."""

import unittest

from deterministic import DeterministicScheduler, Redactor, VirtualClock, stable_json


class DeterministicHarnessTests(unittest.TestCase):
    def test_scheduler_orders_time_then_insertion(self) -> None:
        clock = VirtualClock()
        scheduler = DeterministicScheduler(clock)
        seen: list[str] = []
        scheduler.schedule(10, lambda: seen.append("late"), "late")
        scheduler.schedule(0, lambda: seen.append("first"), "first")
        scheduler.schedule(0, lambda: seen.append("second"), "second")

        self.assertEqual(scheduler.run_until_idle(), 3)
        self.assertEqual(seen, ["first", "second", "late"])
        self.assertEqual(clock.now_ms, 10)

    def test_clock_never_moves_backwards(self) -> None:
        clock = VirtualClock(4)
        clock.advance_by(3)
        self.assertEqual(clock.now_ms, 7)
        with self.assertRaises(ValueError):
            clock.advance_to(6)

    def test_redactor_is_stable_and_hides_secrets_and_raw_ids(self) -> None:
        value = {
            "space_id": "space-research",
            "token": "do-not-print",
            "target_id": 42,
            "message": "Bearer do-not-print",
        }
        redacted = Redactor().redact(value)
        self.assertEqual(
            stable_json(redacted),
            '{"message":"<redacted>","space_id":"space-research",'
            '"target_id":"<redacted>","token":"<redacted>"}',
        )


if __name__ == "__main__":
    unittest.main()
