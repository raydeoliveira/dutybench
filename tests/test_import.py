"""Test the Joulescope UI statistics CSV importer through its file and CLI interfaces.
The constructed rows use the vendor recorder's field order, amperes and interval-end times.
Missing intervals must reduce coverage without reducing the observed mean.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dutybench_import import import_joulescope_csv


# Source: https://github.com/jetperch/pyjoulescope_ui/blob/16099f74a3b789c957e985003cc719b119d1f3bb/joulescope_ui/widgets/statistics_record/statistics_record.py#L64-L107
HEADER = "#time,current,voltage,power,charge,energy\n"
CSV = HEADER + (
    "0.0,0.1,3.3,0.33,0,0\n"  # This interval ended before our first phase began.
    "0.5,0.001,3.3,0.0033,0.0005,0.00165\n"
    "1.0,0.003,3.3,0.0099,0.002,0.0066\n"
    "1.5,0,3.3,0,0.002,0.0066\n"
)


def spec() -> dict:
    return {"label": "fixture A", "conditions": {
        "board": "test board", "instrument": "JS220", "supply_v": 3.3, "workload": "fixture"},
        "period_s": 0.5, "windows": [
            {"id": "a", "phase": "idle", "start_s": 0, "end_s": 1},
            {"id": "b", "phase": "sleep", "start_s": 1, "end_s": 1.5, "uncertainty_ma": 0.02}]}


class ImportTests(unittest.TestCase):
    def test_interval_ends_units_and_explicit_uncertainty(self) -> None:
        capture = import_joulescope_csv(io.StringIO(CSV), spec())
        self.assertEqual(capture["schema_version"], 1)
        self.assertEqual(capture["conditions"], spec()["conditions"])
        a, b = capture["windows"]
        self.assertEqual(a, {"id": "a", "phase": "idle", "duration_s": 1,
                             "mean_current_ma": 2, "coverage_fraction": 1})
        self.assertEqual(b["mean_current_ma"], 0)
        self.assertEqual(b["uncertainty_ma"], 0.02)

    def test_gaps_and_nonfinite_measurements_do_not_count_as_zero(self) -> None:
        rows = CSV.splitlines(keepends=True)
        for capture in ["".join(rows[:2] + rows[3:]), CSV.replace("0.001,", "nan,")]:
            with self.subTest(capture=capture):
                a = import_joulescope_csv(io.StringIO(capture), spec())["windows"][0]
                self.assertEqual((a["coverage_fraction"], a["mean_current_ma"]), (0.5, 3))
        with self.assertRaises(ValueError):
            import_joulescope_csv(io.StringIO(CSV.replace("1.5,0,", "1.5,nan,")), spec())

    def test_reject_ambiguous_timestamps_units_or_window_metadata(self) -> None:
        for invalid in [CSV.replace("#time,current", "time,current_mA"),
                        CSV.replace("0.0,", "20261126T120000.0Z,", 1),
                        CSV.replace("1.5,0,", "1.0,0,"), CSV.replace("0.5,", "0.45,", 1),
                        CSV.replace("1.5,0,", "1.5,inf,"), CSV.split("1.5,")[0]]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                import_joulescope_csv(io.StringIO(invalid), spec())
        for change in [{"start_s": 0.5}, {"end_s": 1.25}, {"id": "a"}, {"phase": " "},
                       {"uncertainty_ma": -1}, {"uncertainty_ua": 10}]:
            malformed = spec()
            malformed["windows"][1].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                import_joulescope_csv(io.StringIO(CSV), malformed)

    def test_standalone_command_writes_only_capture_json(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            here = Path(folder)
            (here / "stats.csv").write_text(CSV, encoding="utf-8")
            (here / "spec.json").write_text(json.dumps(spec()), encoding="utf-8")
            command = [sys.executable, "-B", str(Path(__file__).resolve().parents[1]
                                                / "dutybench_import.py"), "stats.csv", "spec.json"]
            result = subprocess.run(command, cwd=here, text=True, capture_output=True,
                                    check=True, timeout=5)
            self.assertEqual(json.loads(result.stdout)["windows"][0]["mean_current_ma"], 2)
            (here / "stats.csv").write_text(CSV.replace("1.5,0,", "1.5,nan,"), encoding="utf-8")
            failed = subprocess.run(command, cwd=here, text=True, capture_output=True, timeout=5)
            self.assertEqual((failed.returncode, failed.stdout), (2, ""))
            (here / "spec.json").write_text('{"period_s":0.5,"period_s":1}', encoding="utf-8")
            duplicate = subprocess.run(command, cwd=here, text=True, capture_output=True, timeout=5)
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("duplicate", duplicate.stderr)
