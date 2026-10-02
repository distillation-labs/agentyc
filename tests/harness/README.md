# Phase 0 deterministic harness

`deterministic.py` provides a virtual monotonic clock, bounded FIFO scheduler,
and recursive redaction using only the Python standard library. It has no wall
clock, randomness, network, browser, or environment-secret dependency.

Run its tests from the repository root:

```bash
python3 -m unittest discover -s tests/harness -p 'test_*.py'
```
