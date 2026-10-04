#!/usr/bin/env python3
"""Run the Phase 5 performance evidence benchmark.

``offline`` is a deterministic fixture-model/schema lane and is never release
eligible. ``live`` only ingests an explicitly production-path sample package;
it does not launch Chrome, attach to CDP, or turn a direct/legacy report into
live evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from p5_performance import (
    ARTIFACT_ROOT,
    CACHE_STATES,
    DEFAULT_BOOTSTRAP_RESAMPLES,
    DEFAULT_SAMPLES,
    SNAPSHOT_MODES,
    SPACE_LEVELS,
    TEMPERATURES,
    P5PerformanceError,
    build_live_report,
    build_offline_report,
    parse_csv,
    parse_spaces,
    publish_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = "small-form,dense-admin-table,dynamic-feed,nested-frame,oopif-shell"


def _read_live_input(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            raise P5PerformanceError("live input is missing or exceeds the bounded read limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise P5PerformanceError("live input is not valid JSON") from exc
    if not isinstance(value, dict):
        raise P5PerformanceError("live input must be a JSON object")
    return value


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Generate or ingest Phase 5 context/performance evidence.",
        epilog="Offline evidence is schema/model-only. Live evidence requires an external production-path sample package.",
    )
    value.add_argument("--mode", choices=("offline", "live"), required=True)
    value.add_argument("--smoke", action="store_true", help="mark the offline run non-blocking and use 30 samples by default")
    value.add_argument("--fixtures", default=DEFAULT_FIXTURES)
    value.add_argument("--temperatures", default=",".join(TEMPERATURES))
    value.add_argument("--cache-states", default=",".join(CACHE_STATES))
    value.add_argument("--snapshot-modes", default=",".join(SNAPSHOT_MODES))
    value.add_argument("--spaces", default=",".join(str(item) for item in SPACE_LEVELS))
    value.add_argument("--warmups", type=int, default=10)
    value.add_argument("--samples", type=int)
    value.add_argument("--bootstrap-resamples", type=int, default=50)
    value.add_argument("--live-input", type=Path, help="production-path JSON sample package for --mode live")
    value.add_argument("--artifact-dir", type=Path, default=Path("artifacts/p5-performance"))
    value.add_argument("--no-artifact", action="store_true", help="print the report without publishing files")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        fixtures = parse_csv(args.fixtures, "--fixtures")
        temperatures = parse_csv(args.temperatures, "--temperatures", TEMPERATURES)
        caches = parse_csv(args.cache_states, "--cache-states", CACHE_STATES)
        modes = parse_csv(args.snapshot_modes, "--snapshot-modes", SNAPSHOT_MODES)
        spaces = parse_spaces(args.spaces)
        samples = args.samples if args.samples is not None else (30 if args.smoke else DEFAULT_SAMPLES)
        blocking = not args.smoke
        if args.mode == "offline":
            report, raw_samples = build_offline_report(
                fixture_names=fixtures,
                temperatures=temperatures,
                caches=caches,
                modes=modes,
                spaces=spaces,
                samples=samples,
                warmups=args.warmups,
                bootstrap_resamples=args.bootstrap_resamples,
                blocking=blocking,
                command=sys.argv,
            )
        else:
            if args.live_input is None:
                raise P5PerformanceError("--live-input is required for --mode live")
            report, raw_samples = build_live_report(_read_live_input(args.live_input), command=sys.argv)
        if args.no_artifact:
            print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        else:
            destination = publish_artifact(args.artifact_dir, report, raw_samples)
            print(
                f"wrote Phase 5 performance artifact: {destination.relative_to(ROOT)} "
                f"({len(report['cells'])} matrix cells, {len(raw_samples)} raw samples)"
            )
        return 0
    except (P5PerformanceError, OSError, TypeError, ValueError) as exc:
        print(f"p5 performance error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
