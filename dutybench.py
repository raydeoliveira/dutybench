"""Compare local phase-labelled firmware power measurements from the command line.
main() handles compare, --describe and the stdio MCP server.
Both human and JSON callers receive the same read-only comparison.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import NoReturn

from dutybench_core import compare
from dutybench_mcp import CAPTURE_SCHEMA, MAX_CAPTURE_BYTES, MAX_REQUEST_BYTES, TOOL_NAME, VERSION, decode_json, serve


EXIT_CODES = {"0": "Valid report, or within_budget with --gate",
              "1": "Regression with --gate", "2": "Invalid usage, input or file read",
              "3": "Inconclusive with --gate"}
FLAGS = {
    "--json": "Write the versioned result envelope to stdout.",
    "--dry-run": "Run the complete read-only comparison.",
    "--gate": "Exit 1 for regression or 3 for inconclusive. The report is still available.",
    "--budget-pct": "Allowed current increase in percent, inclusive. Default 5.",
    "--describe": "Show the command contract and input schema as JSON.",
    "--version": "Show the program version.",
}


class UsageParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError(message)


def _parser() -> argparse.ArgumentParser:
    parser = UsageParser(prog="dutybench", allow_abbrev=False,
                         usage="%(prog)s compare BASELINE CANDIDATE [--budget-pct N] [--gate] [--json] [--dry-run]"
                               "\n       %(prog)s mcp | --describe | --version",
                         description="Compare matched operating phases in two saved firmware current summaries.")
    parser.add_argument("command", nargs="?", choices=("compare", "mcp"), help="Compare two captures or serve MCP over stdio.")
    parser.add_argument("paths", nargs="*", metavar="CAPTURE", help="Baseline then candidate JSON file. Use - for one stdin input.")
    for flag, help_text in FLAGS.items():
        if flag == "--budget-pct":
            parser.add_argument(flag, type=float, metavar="N", help=help_text)
        else:
            parser.add_argument(flag, action="store_true", help=help_text)
    return parser


def _describe() -> dict:
    return {"name": "dutybench", "version": VERSION, "schema_version": 1, "side_effects": "read_only",
            "interactive": False, "commands": {
                "compare": {"usage": "dutybench compare BASELINE CANDIDATE [--budget-pct N] [--gate] [--json] [--dry-run]",
                            "input": "UTF-8 JSON files. Use - for one capture on stdin."},
                "mcp": {"usage": "dutybench mcp", "transport": "stdio", "tools": [TOOL_NAME],
                        "arguments": "Pass baseline and candidate as inline capture objects, with optional budget_pct."}},
            "flags": FLAGS, "exit_codes": EXIT_CODES, "capture_schema": CAPTURE_SCHEMA,
            "limits": {"max_cli_capture_bytes": MAX_CAPTURE_BYTES, "max_mcp_request_bytes": MAX_REQUEST_BYTES},
            "example": "dutybench compare baseline.json candidate.json --budget-pct 5 --gate --json"}


def _read_capture(path: str) -> dict:
    if path == "-":
        if sys.stdin.isatty():
            raise ValueError("pipe one JSON capture to stdin when using -")
        raw = sys.stdin.buffer.read(MAX_CAPTURE_BYTES + 1)
    else:
        source = Path(path)
        if not source.is_file():
            raise ValueError(f"{path}: expected a readable regular file")
        with source.open("rb") as stream:
            raw = stream.read(MAX_CAPTURE_BYTES + 1)
    if len(raw) > MAX_CAPTURE_BYTES:
        raise ValueError(f"{path}: capture exceeds {MAX_CAPTURE_BYTES} bytes")
    try:
        value = decode_json(raw)
    except ValueError as exc:
        raise ValueError(f"{path}: invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON capture object")
    return value


def _emit_json(data: dict | None, error: dict | None = None) -> None:
    envelope = {"schema_version": 1, "ok": error is None, "data": data, "error": error}
    sys.stdout.write(json.dumps(envelope, allow_nan=False) + "\n")


def _format_current(summary: dict | None) -> str:
    if summary is None:
        return "missing"
    if summary["mean_current_ma"] is None:
        return "mean unavailable"
    mean = f"{summary['mean_current_ma']:.6g} mA"
    if summary["lower_ma"] is None:
        return f"{mean} (bounds unavailable)"
    return f"{mean} [{summary['lower_ma']:.6g}, {summary['upper_ma']:.6g}]"


def _show_human(report: dict) -> None:
    lines = [f"{report['status']}  {report['baseline_label']} → {report['candidate_label']}"
             f"  (budget {report['budget_pct']:g}%)"]
    if report["raw_mean_change_pct"] is not None:
        lines.append(f"Pooled mean change {report['raw_mean_change_pct']:+,.2f}% (descriptive). "
                     "The verdict uses matched phases and recording conditions.")
    for reason in report["reasons"]:
        lines.append(f"  {reason}")
    for phase in report["phases"]:
        lines.append(f"{phase['phase']}  {phase['status']}  {_format_current(phase['baseline'])}"
                     f" → {_format_current(phase['candidate'])}")
        lines.extend(f"  {reason}" for reason in phase["reasons"])
    sys.stdout.write("\n".join(lines) + "\n")


def _run(args: argparse.Namespace) -> int:
    compare_flags = args.gate or args.dry_run or args.budget_pct is not None
    if args.describe or args.version:
        if args.command or args.paths or compare_flags or (args.describe and args.version):
            raise ValueError("use --describe or --version alone, optionally with --json")
        data = _describe() if args.describe else {"name": "dutybench", "version": VERSION}
        if args.json:
            _emit_json(data)
        else:
            sys.stdout.write((json.dumps(data, indent=2) if args.describe else f"dutybench {VERSION}") + "\n")
        return 0
    if args.command == "mcp":
        if args.paths or args.json or compare_flags:
            raise ValueError("start the stdio server with dutybench mcp")
        return serve()
    if args.command != "compare" or len(args.paths) != 2:
        raise ValueError("use dutybench compare BASELINE CANDIDATE")
    if args.paths == ["-", "-"]:
        raise ValueError("only one capture can come from stdin")
    baseline, candidate = map(_read_capture, args.paths)
    report = compare(baseline, candidate, 5.0 if args.budget_pct is None else args.budget_pct)
    if args.json:
        _emit_json(report)
    else:
        _show_human(report)
    return {"regression": 1, "within_budget": 0, "inconclusive": 3}[report["status"]] if args.gate else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        return _run(_parser().parse_intermixed_args(argv))
    except (OSError, ValueError) as exc:
        if "--json" in argv:
            _emit_json(None, {"code": "invalid_input", "message": str(exc), "hint": "Run dutybench --describe for the schema."})
        else:
            sys.stderr.write(f"dutybench: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
