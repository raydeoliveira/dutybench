"""Exercise the DutyBench CLI and MCP as child processes over their actual transports.
Invoke the same comparison through files, stdin, and inline MCP tool arguments.
A scientific verdict is a valid report even when a caller elects to gate on it.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ENTRY = Path(__file__).resolve().parents[1] / "dutybench.py"
COMMAND = [sys.executable, "-B", str(ENTRY)]


def capture(label: str, current_ma: float) -> dict:
    return {
        "schema_version": 1, "label": label,
        "conditions": {"board": "test-board", "instrument": "test-meter", "supply_v": 3.7,
                       "workload": "idle control"},
        "windows": [{"id": f"repeat-{i}", "phase": "idle", "duration_s": 5,
                     "mean_current_ma": current_ma, "coverage_fraction": 1, "uncertainty_ma": 0}
                    for i in (1, 2)],
    }


def invoke(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run([*COMMAND, *args], input=stdin, capture_output=True, text=True, timeout=10)


def rpc(method: str, ident: str | int | None = None, **params: object) -> dict:
    request = {"jsonrpc": "2.0", "method": method, "params": params}
    return request if ident is None else {**request, "id": ident}


def initialize() -> dict:
    return rpc("initialize", 0, clientInfo={"name": "test", "version": "1"},
               protocolVersion="2025-11-25", capabilities={})


class EntryPointsTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = capture("baseline", 1.0)
        self.candidate = capture("candidate", 1.1)
        self.path = Path(temp.name, "baseline.json")
        self.path.write_text(json.dumps(self.base), encoding="utf-8")

    def compare(self, candidate: dict, *flags: str) -> subprocess.CompletedProcess[str]:
        return invoke("--json", "compare", str(self.path), "-", *flags, stdin=json.dumps(candidate))

    def test_gates_and_dry_run(self) -> None:
        for value, expected, code in [
                (self.candidate, "regression", 1), (self.base, "within_budget", 0),
                ({**self.candidate, "windows": self.candidate["windows"][:1]}, "inconclusive", 3)]:
            with self.subTest(verdict=expected):
                regular = self.compare(value)
                gated = self.compare(value, "--gate", "--dry-run")
                self.assertEqual((regular.returncode, gated.returncode), (0, code))
                report = json.loads(gated.stdout)
                self.assertTrue(report["ok"])
                self.assertEqual(report["data"]["status"], expected)
                self.assertEqual(gated.stdout, regular.stdout)
                self.assertEqual(gated.stderr, "")
        changed_budget = self.compare(self.candidate, "--gate", "--budget-pct", "15")
        self.assertEqual(json.loads(changed_budget.stdout)["data"]["status"], "within_budget")

    def test_diagnostics_and_discovery(self) -> None:
        for args, stdin in [
                (["compare", "-", "-"], ""), (["compare", str(self.path), "-"], "[]"),
                (["compare", str(self.path), "-"], "{"), (["compare", str(self.path) + ".absent", "-"], ""),
                (["compare", str(self.path), "-", "--budget-pct", "bad"], "{}")]:
            with self.subTest(args=args):
                failed = invoke(*args, "--json", stdin=stdin)
                self.assertEqual(failed.returncode, 2)
                self.assertFalse(json.loads(failed.stdout)["ok"])
                self.assertTrue(json.loads(failed.stdout)["error"]["message"])
        info = invoke("--describe")
        self.assertEqual(info.returncode, 0)
        manifest = json.loads(info.stdout)
        self.assertEqual(set(manifest["exit_codes"]), {"0", "1", "2", "3"})
        self.assertIn("--gate", manifest["flags"])
        self.assertIn("windows", manifest["capture_schema"]["required"])
        fields = manifest["capture_schema"]["properties"]["windows"]["items"]
        self.assertNotIn("coverage_fraction", fields["required"])
        human = invoke("compare", str(self.path), str(self.path))
        self.assertEqual(human.returncode, 0)
        self.assertIn("within_budget", human.stdout)
        self.assertIn("idle", human.stdout)

    def test_human_output_with_no_observed_samples(self) -> None:
        for window in self.candidate["windows"]:
            window["coverage_fraction"] = 0
        human = invoke("compare", str(self.path), "-", "--gate", stdin=json.dumps(self.candidate))
        self.assertEqual(human.returncode, 3)
        self.assertIn("mean unavailable", human.stdout)

    def test_human_output_when_current_bound_exceeds_numeric_range(self) -> None:
        candidate = capture("candidate", 1e308)
        for window in candidate["windows"]:
            window["uncertainty_ma"] = 1e308
        human = invoke("compare", str(self.path), "-", "--gate", stdin=json.dumps(candidate))
        self.assertEqual((human.returncode, human.stderr), (3, ""))
        self.assertIn("bounds unavailable", human.stdout)

    def test_human_output_explains_descriptive_pooled_increase(self) -> None:
        self.candidate["windows"] = self.candidate["windows"][:1]
        self.candidate["windows"][0]["mean_current_ma"] = 11.4976
        human = invoke("compare", str(self.path), "-", "--gate", stdin=json.dumps(self.candidate))
        self.assertEqual(human.returncode, 3)
        self.assertIn("inconclusive", human.stdout)
        self.assertIn("Pooled mean change +1,049.76% (descriptive)", human.stdout)

    def test_inline_mcp_tool_matches_cli_and_survives_bad_args(self) -> None:
        args = {"baseline": self.base, "candidate": self.candidate}
        requests = [initialize(), rpc("notifications/initialized"), rpc("tools/list", 1),
                    rpc("tools/call", 2, name="dutybench_compare", arguments={**args, "baseline": str(self.path)}),
                    rpc("tools/call", "ok", name="dutybench_compare", arguments=args),
                    rpc("tools/call", 4, name="unlisted", arguments={})]
        wire = "\n".join(map(json.dumps, requests)) + "\n"
        session = invoke("mcp", stdin=wire)
        self.assertEqual((session.returncode, session.stderr), (0, ""))
        hello, listing, invalid, actual, unknown = map(json.loads, session.stdout.splitlines())
        self.assertEqual(hello["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("tools", hello["result"]["capabilities"])
        tool, = listing["result"]["tools"]
        self.assertEqual(tool["name"], "dutybench_compare")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertTrue(invalid["result"]["isError"])
        report = json.loads(self.compare(self.candidate).stdout)["data"]
        self.assertEqual(actual["id"], "ok")
        self.assertEqual(actual["result"]["structuredContent"], report)
        self.assertEqual(json.loads(actual["result"]["content"][0]["text"]), report)
        self.assertEqual(unknown["error"]["code"], -32602)

    def test_stdio_parse_failure_does_not_end_session(self) -> None:
        session = invoke("mcp", stdin="{\n" + json.dumps(initialize()) + "\n")
        error, hello = map(json.loads, session.stdout.splitlines())
        self.assertEqual(error["error"]["code"], -32700)
        self.assertEqual(hello["id"], 0)


if __name__ == "__main__":
    unittest.main()
