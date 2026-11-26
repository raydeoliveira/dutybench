"""Compare phase-labelled current summaries from two saved firmware captures.
Public API: compare(baseline, candidate, budget_pct=5.0) returns a JSON-ready report.
Every gate compares matched phases; the pooled current delta is descriptive only.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from typing import Any


_UNKNOWN_LABELS = {"?", "n/a", "unknown", "unspecified", "unclassified", "unlabeled", "unlabelled", "not recorded"}


@dataclass(frozen=True)
class _Window:
    ident: str
    phase: str
    duration: Fraction
    current: Fraction
    coverage: Fraction | None
    uncertainty: Fraction | None


@dataclass(frozen=True)
class _Capture:
    label: str
    conditions: dict[str, str | Fraction]
    windows: list[_Window]
    phases: dict[str, list[_Window]]


def fields(value: Any, path: str, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be a JSON object with string keys.")
    if missing := required - value.keys():
        raise ValueError(f"{path} is missing {', '.join(sorted(missing))}.")
    if optional is not None and (unknown := value.keys() - required - optional):
        raise ValueError(f"{path} has unknown fields: {', '.join(sorted(unknown))}.")
    return value


def _label(value: Any, path: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 128 or value.strip() != value
            or not value.isprintable()):
        raise ValueError(f"{path} must be 1–128 printable characters without outer whitespace.")
    return value


def nonnegative_number(value: Any, path: str, *, positive: bool = False) -> Fraction:
    if type(value) not in (int, float):
        raise ValueError(f"{path} must be a finite JSON number.")
    try:
        finite = isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0 or (positive and value == 0):
        bound = "positive" if positive else "nonnegative"
        raise ValueError(f"{path} must be a {bound}, finite number within the floating-point input range.")
    # Preserve the JSON number's ordinary decimal interpretation at the exact budget boundary.
    return Fraction(str(value))


def _capture(value: Any, path: str) -> _Capture:
    obj = fields(value, path, {"schema_version", "label", "conditions", "windows"}, set())
    if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
        raise ValueError(f"{path}.schema_version must be the integer 1.")
    label = _label(obj["label"], f"{path}.label")
    cpath = f"{path}.conditions"
    conditions = fields(obj["conditions"], cpath, {"board", "instrument", "supply_v", "workload"})
    parsed_conditions = {}
    for key, item in conditions.items():
        _label(key, f"{cpath} key")
        parsed_conditions[key] = (nonnegative_number(item, f"{cpath}.{key}", positive=True) if key == "supply_v"
                                  else _label(item, f"{cpath}.{key}"))
    rows = obj["windows"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10_000:
        raise ValueError(f"{path}.windows must contain 1–10000 observations.")
    ids: set[str] = set()
    windows = []
    phases: dict[str, list[_Window]] = {}
    for index, row in enumerate(rows):
        wpath = f"{path}.windows[{index}]"
        fields(row, wpath, {"id", "phase", "duration_s", "mean_current_ma"}, {"coverage_fraction", "uncertainty_ma"})
        ident = _label(row["id"], f"{wpath}.id")
        if ident in ids:
            raise ValueError(f"{wpath}.id duplicates window ID '{ident}'.")
        ids.add(ident)
        coverage = row.get("coverage_fraction")
        coverage = None if coverage is None else nonnegative_number(coverage, f"{wpath}.coverage_fraction")
        if coverage is not None and coverage > 1:
            raise ValueError(f"{wpath}.coverage_fraction must be between 0 and 1.")
        uncertainty = row.get("uncertainty_ma")
        window = _Window(ident, _label(row["phase"], f"{wpath}.phase"),
                         nonnegative_number(row["duration_s"], f"{wpath}.duration_s", positive=True),
                         nonnegative_number(row["mean_current_ma"], f"{wpath}.mean_current_ma"), coverage,
                         None if uncertainty is None else nonnegative_number(uncertainty, f"{wpath}.uncertainty_ma"))
        windows.append(window)
        phases.setdefault(window.phase, []).append(window)
    return _Capture(label, parsed_conditions, windows, phases)


def validate_capture(value: dict) -> None:
    """Validate a normalized capture from any input adapter, raising ValueError on malformed input."""
    _capture(value, "capture")


def _observed_mean(windows: list[_Window]) -> Fraction | None:
    """Unknown weights permit a pooled mean only if every possible observation has the same mean."""
    if any(row.coverage is None for row in windows):
        means = {row.current for row in windows if row.coverage != 0}
        return means.pop() if len(means) == 1 else None
    elapsed = Fraction(0)
    current_time = Fraction(0)
    for row in windows:
        assert row.coverage is not None
        observed = row.duration * row.coverage
        elapsed += observed
        current_time += observed * row.current
    return current_time / elapsed if elapsed else None


def _json_number(value: Fraction | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except OverflowError:
        return None


def _bounds(windows: list[_Window]) -> tuple[Fraction, Fraction] | None:
    """Observed repeat range, expanded by each reading's absolute uncertainty; no statistical inference."""
    if not windows or any(w.uncertainty is None or w.coverage == 0 for w in windows):
        return None
    lower = min(w.current - w.uncertainty for w in windows if w.uncertainty is not None)
    upper = max(w.current + w.uncertainty for w in windows if w.uncertainty is not None)
    return max(Fraction(0), lower), upper


def _summary(rows: list[_Window], bounds: tuple[Fraction, Fraction] | None) -> dict | None:
    if not rows:
        return None
    return {"window_count": len(rows), "window_ids": [w.ident for w in rows],
            "mean_current_ma": _json_number(_observed_mean(rows)),
            "lower_ma": _json_number(bounds[0]) if bounds else None,
            "upper_ma": _json_number(bounds[1]) if bounds else None}


def _condition_issues(a: _Capture, b: _Capture) -> list[str]:
    issues = []
    for key in sorted(a.conditions.keys() | b.conditions.keys()):
        before = a.conditions.get(key)
        after = b.conditions.get(key)
        if before != after:
            issues.append(f"Condition '{key}' differs or is missing on one capture.")
        elif isinstance(before, str) and before.casefold() in _UNKNOWN_LABELS:
            issues.append(f"Condition '{key}' is unspecified.")
    return issues


def _phase(phase: str, a: list[_Window], b: list[_Window], conditions_match: bool, factor: Fraction) -> dict:
    reasons = []
    if not conditions_match:
        reasons.append("Capture conditions do not establish a match.")
    if phase.casefold() in _UNKNOWN_LABELS or phase.casefold() == "mixed":
        reasons.append("A single operating phase was not identified.")
    for role, windows in [("Baseline", a), ("Candidate", b)]:
        if not windows:
            reasons.append(f"{role} has no window for this phase.")
            continue
        if len(windows) < 2:
            reasons.append(f"{role} needs at least two distinct windows in this phase.")
        if any(w.coverage is None for w in windows):
            reasons.append(f"{role} needs the observed coverage fraction for each window.")
        if any(w.coverage is not None and w.coverage < 1 for w in windows):
            reasons.append(f"{role} has incomplete observed coverage.")
        if any(w.uncertainty is None for w in windows):
            reasons.append(f"{role} lacks an absolute current uncertainty for at least one window.")
    if len({w.duration for w in a + b}) > 1:
        reasons.append("Window durations differ within this phase.")
    left, right = _bounds(a), _bounds(b)
    if left is not None and _json_number(left[1]) is None:
        left = None
        reasons.append("A baseline current bound exceeds the finite JSON output range.")
    if right is not None and _json_number(right[1]) is None:
        right = None
        reasons.append("A candidate current bound exceeds the finite JSON output range.")
    status = "inconclusive"
    if not reasons and left is not None and right is not None:
        if right[0] > left[1] * factor:
            status = "regression"
        elif right[1] <= left[0] * factor:
            status = "within_budget"
        else:
            reasons.append("The observed current bounds straddle the budget boundary.")
    return {"phase": phase, "status": status, "baseline": _summary(a, left),
            "candidate": _summary(b, right), "reasons": reasons}


def _phase_shares(capture: _Capture) -> dict[str, Fraction]:
    durations = {phase: sum(w.duration for w in rows) for phase, rows in capture.phases.items()}
    total = sum(durations.values())
    return {phase: duration / total for phase, duration in durations.items()}


def compare(baseline: dict, candidate: dict, budget_pct: float = 5.0) -> dict:
    """Compare nonnegative mean current draw in mA; malformed data raises ValueError."""
    budget = nonnegative_number(budget_pct, "budget_pct")
    a, b = _capture(baseline, "baseline"), _capture(candidate, "candidate")
    reasons = _condition_issues(a, b)
    factor = 1 + budget / 100
    phases = [_phase(name, a.phases.get(name, []), b.phases.get(name, []), not reasons, factor)
              for name in sorted(a.phases.keys() | b.phases.keys())]
    if a.phases.keys() != b.phases.keys():
        reasons.append("The captures contain different operating phases.")
    elif _phase_shares(a) != _phase_shares(b):
        reasons.append("The captures assign different shares of their planned duration to these phases.")
    states = {p["status"] for p in phases}
    status = "regression" if "regression" in states else "within_budget"
    if "inconclusive" in states:
        reasons.append("At least one phase lacks a conclusive comparison; see its reasons.")
    if reasons:
        status = "inconclusive"
    before, after = _observed_mean(a.windows), _observed_mean(b.windows)
    raw_delta = None if before is None or before == 0 or after is None else (after / before - 1) * 100
    raw_pct = _json_number(raw_delta)
    if raw_delta is not None and raw_pct is None:
        reasons.append("The descriptive current change exceeds the finite JSON output range.")
    return {"status": status, "budget_pct": _json_number(budget), "baseline_label": a.label,
            "candidate_label": b.label, "raw_mean_change_pct": raw_pct,
            "reasons": reasons, "phases": phases}
