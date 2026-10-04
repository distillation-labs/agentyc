"""Deterministic statistics used by the Phase 5 performance evidence lane."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Callable


class StatisticsError(ValueError):
    """A statistic cannot be computed from the supplied samples."""


def stable_seed(*parts: object) -> int:
    """Derive a cross-process seed without Python's randomized ``hash``."""
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)


def percentile(values: Sequence[float], percent: float) -> float | None:
    """Return a deterministic nearest-rank percentile."""
    if not values:
        return None
    if not math.isfinite(percent) or not 0.0 <= percent <= 100.0:
        raise StatisticsError("percentile must be between 0 and 100")
    ordered = sorted(float(value) for value in values)
    if any(not math.isfinite(value) for value in ordered):
        raise StatisticsError("percentile input must contain finite values")
    rank = max(1, math.ceil(percent / 100.0 * len(ordered))) - 1
    return ordered[min(rank, len(ordered) - 1)]


def _next_u64(state: int) -> tuple[int, int]:
    """Return the next SplitMix64 state and output."""
    mask = (1 << 64) - 1
    state = (state + 0x9E3779B97F4A7C15) & mask
    value = state
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
    return state, value ^ (value >> 31)


def bootstrap_ci(
    values: Sequence[float],
    *,
    statistic_percentile: float,
    seed: int,
    resamples: int = 200,
) -> dict[str, object]:
    """Compute a deterministic percentile-bootstrap 95% confidence interval."""
    if not values:
        raise StatisticsError("bootstrap CI requires at least one value")
    if resamples < 1 or resamples > 10_000:
        raise StatisticsError("bootstrap resamples must be between 1 and 10000")
    normalized = [float(value) for value in values]
    if any(not math.isfinite(value) or value < 0.0 for value in normalized):
        raise StatisticsError("bootstrap input must contain finite non-negative values")

    point = percentile(normalized, statistic_percentile)
    assert point is not None
    n = len(normalized)
    state = seed & ((1 << 64) - 1)
    estimates: list[float] = []
    for _ in range(resamples):
        sample: list[float] = []
        for _ in range(n):
            state, random_value = _next_u64(state)
            sample.append(normalized[random_value % n])
        estimate = percentile(sample, statistic_percentile)
        assert estimate is not None
        estimates.append(estimate)

    lower = percentile(estimates, 2.5)
    upper = percentile(estimates, 97.5)
    assert lower is not None and upper is not None
    return {
        "method": "bootstrap_percentile",
        "confidence_level": 0.95,
        "statistic": f"p{int(statistic_percentile)}",
        "sample_count": n,
        "resamples": resamples,
        "seed": f"{seed & ((1 << 64) - 1):016x}",
        "estimate": point,
        "lower": lower,
        "upper": upper,
    }


def summarize_distribution(
    values: Sequence[float],
    *,
    seed: int,
    bootstrap_resamples: int,
) -> dict[str, object]:
    """Return p50/p95/p99 and deterministic bootstrap CIs for one metric."""
    if not values:
        raise StatisticsError("distribution requires at least one value")
    normalized = [float(value) for value in values]
    if any(not math.isfinite(value) or value < 0.0 for value in normalized):
        raise StatisticsError("distribution input must contain finite non-negative values")
    return {
        "p50": percentile(normalized, 50.0),
        "p95": percentile(normalized, 95.0),
        "p99": percentile(normalized, 99.0),
        "mean": math.fsum(normalized) / len(normalized),
        "confidence_intervals": {
            "p50": bootstrap_ci(
                normalized,
                statistic_percentile=50.0,
                seed=stable_seed(seed, "p50"),
                resamples=bootstrap_resamples,
            ),
            "p95": bootstrap_ci(
                normalized,
                statistic_percentile=95.0,
                seed=stable_seed(seed, "p95"),
                resamples=bootstrap_resamples,
            ),
            "p99": bootstrap_ci(
                normalized,
                statistic_percentile=99.0,
                seed=stable_seed(seed, "p99"),
                resamples=bootstrap_resamples,
            ),
        },
        "sample_count": len(normalized),
    }


def validate_distribution(distribution: object) -> None:
    """Validate the shape emitted by :func:`summarize_distribution`."""
    if not isinstance(distribution, dict):
        raise StatisticsError("distribution must be an object")
    for key in ("p50", "p95", "p99", "mean"):
        value = distribution.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise StatisticsError(f"distribution is missing finite {key}")
    count = distribution.get("sample_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise StatisticsError("distribution sample_count is invalid")
    intervals = distribution.get("confidence_intervals")
    if not isinstance(intervals, dict) or set(intervals) != {"p50", "p95", "p99"}:
        raise StatisticsError("distribution confidence intervals are incomplete")
    for interval in intervals.values():
        if not isinstance(interval, dict) or interval.get("method") != "bootstrap_percentile":
            raise StatisticsError("distribution CI is not bootstrap percentile")
        if interval.get("confidence_level") != 0.95 or interval.get("sample_count") != count:
            raise StatisticsError("distribution CI confidence or count is invalid")
        lower = interval.get("lower")
        upper = interval.get("upper")
        if (
            isinstance(lower, bool)
            or isinstance(upper, bool)
            or not isinstance(lower, (int, float))
            or not isinstance(upper, (int, float))
            or not math.isfinite(float(lower))
            or not math.isfinite(float(upper))
            or lower > upper
        ):
            raise StatisticsError("distribution CI bounds are invalid")


__all__ = [
    "StatisticsError",
    "bootstrap_ci",
    "percentile",
    "stable_seed",
    "summarize_distribution",
    "validate_distribution",
]
