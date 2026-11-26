"""Import the Joulescope UI's relative-time statistics CSV into a DutyBench capture.
API: import_joulescope_csv(stream, spec); CLI: python3 dutybench_import.py stats.csv windows.json.
Each finite row covers one declared interval ending at its timestamp; gaps reduce coverage.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import json
from pathlib import Path
import re
import sys
from typing import TextIO

from dutybench_core import fields, nonnegative_number, validate_capture
from dutybench_mcp import MAX_CAPTURE_BYTES, decode_json


_HEADER = ["#time", "current", "voltage", "power", "charge", "energy"]
_RELATIVE = re.compile(r"[0-9]+(?:\.[0-9]+)?")


@dataclass
class _Interval:
    row: dict
    start: int
    end: int
    current_sum_a: Fraction = Fraction(0)
    count: int = 0

    def summary(self, period: Fraction) -> dict:
        if not self.count:
            raise ValueError(f"Window {self.row['id']!r} has no observed current intervals.")
        bins = self.end - self.start
        result = {"id": self.row["id"], "phase": self.row["phase"],
                  "duration_s": float(bins * period),
                  "mean_current_ma": float(self.current_sum_a * 1000 / self.count),
                  "coverage_fraction": self.count / bins}
        if "uncertainty_ma" in self.row:
            result["uncertainty_ma"] = self.row["uncertainty_ma"]
        return result


def _tick(seconds: Fraction, period: Fraction, name: str) -> int:
    ticks = seconds / period
    if ticks.denominator != 1:
        raise ValueError(f"{name} must be on the specified statistics interval grid.")
    return ticks.numerator


def _plan(spec: dict, period: Fraction) -> list[_Interval]:
    rows = spec["windows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10_000:
        raise ValueError("windows must have 1–10000 explicitly labelled entries.")
    plan = []
    for i, row in enumerate(rows):
        path = f"windows[{i}]"
        fields(row, path, {"id", "phase", "start_s", "end_s"}, {"uncertainty_ma"})
        start = _tick(nonnegative_number(row["start_s"], f"{path}.start_s"), period, path)
        end = _tick(nonnegative_number(row["end_s"], f"{path}.end_s"), period, path)
        if end <= start or (plan and start < plan[-1].end):
            raise ValueError(f"{path} must be positive in duration, chronological and nonoverlapping.")
        plan.append(_Interval(row, start, end))
    return plan


def _observed_current(text: str, line: int) -> Fraction | None:
    if text.lower() == "nan":
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        value = Decimal("NaN")
    if (len(text) > 64 or not value.is_finite() or abs(value.adjusted()) > 324
            or "_" in text):
        raise ValueError(f"CSV line {line} must contain a finite current in amperes or nan.")
    return Fraction(value)


def import_joulescope_csv(stream: TextIO, spec: dict) -> dict:
    """Read a Relative-time Statistics Record CSV. Supply the configured period and phase bounds.

    Bounds are (start_s, end_s]. The first row ends at t=0 and describes a preceding
    interval. Coverage counts exported statistical bins; sub-bin loss is undisclosed.
    """
    fields(spec, "spec", {"label", "conditions", "period_s", "windows"}, set())
    period = nonnegative_number(spec["period_s"], "period_s", positive=True)
    windows = _plan(spec, period)
    reader = csv.reader(stream, strict=True)
    if next(reader, None) != _HEADER:
        raise ValueError("Expected Joulescope UI statistics CSV with Relative time and six columns.")
    previous = -1
    index = 0
    for row in reader:
        line = reader.line_num
        if len(row) != len(_HEADER) or len(row[0]) > 64 or not _RELATIVE.fullmatch(row[0]):
            raise ValueError(f"CSV line {line} requires six columns and relative seconds.")
        tick = _tick(Fraction(row[0]), period, f"CSV line {line} timestamp")
        if (previous == -1 and tick > 0) or tick <= previous:
            raise ValueError(f"CSV line {line} timestamps must start at zero and strictly increase.")
        previous = tick
        current = _observed_current(row[1], line)
        while index < len(windows) and windows[index].end < tick:
            index += 1
        if index < len(windows) and tick > windows[index].start and current is not None:
            windows[index].count += 1
            windows[index].current_sum_a += current
    if previous < windows[-1].end:
        raise ValueError("Last requested window extends beyond the recorded timestamps.")
    try:
        summaries = [w.summary(period) for w in windows]
    except OverflowError as exc:
        raise ValueError("Current or duration exceeds the finite JSON number range.") from exc
    result = {"schema_version": 1, "label": spec["label"], "conditions": spec["conditions"],
              "windows": summaries}
    validate_capture(result)
    return result


def main() -> int:
    """Write capture JSON to stdout after successful validation of the entire input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("statistics_csv", type=Path, help="Joulescope UI statistics, Relative time")
    parser.add_argument("window_spec", type=Path,
                        help="JSON with label, conditions, period_s, and labelled start_s/end_s windows")
    args = parser.parse_args()
    try:
        with args.window_spec.open("rb") as source:
            raw = source.read(MAX_CAPTURE_BYTES + 1)
        if len(raw) > MAX_CAPTURE_BYTES:
            raise ValueError(f"Window spec exceeds {MAX_CAPTURE_BYTES} bytes.")
        spec = decode_json(raw)
        with args.statistics_csv.open(encoding="utf-8-sig", newline="") as stream:
            result = import_joulescope_csv(stream, spec)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, csv.Error) as exc:
        print(f"dutybench import: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
