"""Regression tests for comparisons of phase-labelled current windows.
Exercises compare() on real reported means and constructed opposite controls.
Whole-capture arithmetic must never override missing or inconsistent evidence.
"""
from __future__ import annotations

import copy
import math
import unittest

from dutybench_core import compare


def sample(ident: str, phase: str, current: float, *, duration: float = 95,
           uncertainty: float | None = 0.0, coverage: float | None = 1.0) -> dict:
    return {"id": ident, "phase": phase, "mean_current_ma": current, "duration_s": duration,
            "uncertainty_ma": uncertainty, "coverage_fraction": coverage}


def capture(label: str, *windows: dict) -> dict:
    return {"schema_version": 1, "label": label,
            "conditions": {"board": "unit-test-board", "instrument": "unit-test-meter",
                           "supply_v": 3.7, "workload": "periodic sensor"},
            "windows": list(windows)}


def pair(values: tuple[float, float], *, label: str = "test", bound: float = 0.0) -> dict:
    return capture(label, *(sample(str(i), "idle", value, uncertainty=bound) for i, value in enumerate(values)))


class CurrentComparisonTests(unittest.TestCase):
    def test_real_duty_windows_do_not_become_regression(self) -> None:
        # Adjacent 295-second means from the public SimpleAir audit, with a scheduled
        # sensor event in the second window here. Neither side has reported uncertainty.
        a = capture("nominal", sample("2", "nominal", 1.260, duration=295, uncertainty=None))
        b = capture("scheduled", sample("3", "sensor-event", 14.487, duration=295, uncertainty=None))
        for side in (a, b):
            side["windows"][0].pop("coverage_fraction")
        result = compare(a, b)
        self.assertAlmostEqual(result["raw_mean_change_pct"], 1049.7619047619)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual([p["phase"] for p in result["phases"]], ["nominal", "sensor-event"])
        self.assertIsNone(result["phases"][0]["candidate"])
        self.assertIn("coverage", " ".join(result["phases"][0]["reasons"]))

    def test_unknown_coverage_cannot_weight_different_means(self) -> None:
        a = pair((1, 2))
        a["windows"][0]["coverage_fraction"] = None
        result = compare(a, pair((4, 4)))
        self.assertEqual(result["status"], "inconclusive")
        self.assertIsNone(result["raw_mean_change_pct"])
        self.assertIsNone(result["phases"][0]["baseline"]["mean_current_ma"])
        for row in a["windows"]:
            row["coverage_fraction"] = 0
        self.assertIsNone(compare(a, a)["raw_mean_change_pct"])

    def test_same_phases_different_duty_mix_retains_phase_results(self) -> None:
        def two_phases(tx_count: int, idle_count: int) -> dict:
            return capture("test", *(sample(f"{phase}-{i}", phase, mean)
                           for phase, mean, count in [("idle", 1, idle_count), ("tx", 8, tx_count)]
                           for i in range(count)))
        a = two_phases(2, 2)
        result = compare(a, two_phases(4, 2))
        self.assertEqual(result["status"], "inconclusive")
        self.assertTrue(all(p["status"] == "within_budget" for p in result["phases"]))
        self.assertIn("share", " ".join(result["reasons"]))
        self.assertEqual(compare(a, two_phases(4, 4))["status"], "within_budget")

    def test_overflow_of_descriptive_delta_preserves_phase_diagnosis(self) -> None:
        result = compare(pair((1e-300, 1e-300)), pair((1e300, 1e300)))
        self.assertEqual(result["status"], "regression")
        self.assertIsNone(result["raw_mean_change_pct"])
        report = compare(pair((1, 1)), pair((1e308, 1e308), bound=1e308))
        self.assertEqual(report["status"], "inconclusive")
        self.assertIsNone(report["phases"][0]["candidate"]["lower_ma"])
        self.assertIsNone(report["phases"][0]["candidate"]["upper_ma"])

    def test_constructed_regression_and_budget_inside_outside(self) -> None:
        a = pair((1.0, 1.01), bound=0.005)
        b = pair((1.10, 1.11), label="new", bound=0.005)
        result = compare(a, b)
        self.assertEqual(result["status"], "regression")
        self.assertEqual(result["phases"][0]["baseline"]["lower_ma"], 0.995)
        self.assertEqual(result["phases"][0]["candidate"]["upper_ma"], 1.115)
        self.assertEqual(result["phases"][0]["baseline"]["window_count"], 2)
        self.assertEqual(result["phases"][0]["baseline"]["window_ids"], ["0", "1"])
        self.assertEqual(compare(b, a)["status"], "within_budget")
        self.assertEqual(compare(pair((1, 1)), pair((1.05, 1.05)))["status"], "within_budget")
        self.assertEqual(compare(pair((1, 1)), pair((1.051, 1.051)))["status"], "regression")
        self.assertEqual(compare(a, pair((1.04, 1.06), bound=0.005))["status"], "inconclusive")

    def test_spread_and_unreported_uncertainty_are_independent_limits(self) -> None:
        a = pair((1, 1.2))
        b = pair((1.15, 1.16))
        self.assertEqual(compare(a, b)["status"], "inconclusive")
        zero_bound = pair((1, 1))
        no_bound = copy.deepcopy(zero_bound)
        no_bound["windows"][0].pop("uncertainty_ma")
        self.assertEqual(compare(zero_bound, zero_bound, 0)["status"], "within_budget")
        result = compare(no_bound, b)
        self.assertEqual(result["status"], "inconclusive")
        self.assertIsNone(result["phases"][0]["baseline"]["lower_ma"])

    def test_capture_conditions_and_phase_coverage_fail_closed(self) -> None:
        a, b = pair((1, 1)), pair((2, 2))
        for field, value in [("duration_s", 96), ("coverage_fraction", 0.99)]:
            trial = copy.deepcopy(b)
            trial["windows"][0][field] = value
            self.assertEqual(compare(a, trial)["status"], "inconclusive")
        for key, value in [("instrument", "other"), ("supply_v", 3.6), ("probe", "attached")]:
            trial = copy.deepcopy(b)
            trial["conditions"][key] = value
            result = compare(a, trial)
            self.assertEqual(result["status"], "inconclusive")
            self.assertEqual(result["phases"][0]["status"], "inconclusive")
            self.assertIn(key, " ".join(result["reasons"]))
        partial = copy.deepcopy(b)
        partial["windows"] += [sample("tx", "tx", 3)]
        result = compare(a, partial)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["phases"][0]["status"], "regression")

    def test_pin_phase_weight_and_zero_baseline(self) -> None:
        a = capture("a", sample("a1", "idle", 1, duration=9), sample("a2", "tx", 10, duration=1))
        b = capture("b", sample("b1", "idle", 1, duration=1), sample("b2", "tx", 10, duration=9))
        self.assertAlmostEqual(compare(a, b)["raw_mean_change_pct"], (9.1 / 1.9 - 1) * 100)
        zero = pair((0, 0))
        self.assertIsNone(compare(zero, zero)["raw_mean_change_pct"])
        self.assertEqual(compare(zero, zero)["status"], "within_budget")
        self.assertEqual(compare(zero, pair((0.1, 0.2)))["status"], "regression")

    def test_unclassified_windows_are_not_independent_evidence(self) -> None:
        a = capture("a", sample("w", "idle", 1))
        self.assertEqual(compare(a, a)["status"], "inconclusive")
        for phase in ("mixed", "unknown", "unclassified", "unspecified", "N/A", "unlabelled"):
            unknown = capture("a", sample("a", phase, 1), sample("b", phase, 1))
            self.assertEqual(compare(unknown, unknown)["status"], "inconclusive")

    def test_invalid_numeric_units_and_structure(self) -> None:
        good = pair((1, 1))
        for key, value in [("mean_current_ma", False), ("mean_current_ma", -1), ("mean_current_ma", "1"),
                           ("duration_s", 0), ("duration_s", math.inf), ("uncertainty_ma", -0.1),
                           ("coverage_fraction", 1.001), ("coverage_fraction", math.nan), ("phase", " "),
                           ("mean_current_ua", 2000), ("phase", "idle\nrun")]:
            bad = copy.deepcopy(good)
            bad["windows"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                compare(good, bad)
        for invalid in [True, None, -1, math.inf]:
            with self.assertRaises(ValueError):
                compare(good, good, invalid)
        for key, value in [("schema_version", True), ("label", ""), ("windows", []), ("windows", [good["windows"][0]] * 2)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                compare(good, {**good, key: value})
