"""Serve measured firmware power comparisons to local agents over stdio.
serve() implements MCP initialization, discovery, and dutybench_compare.
Tool arguments contain inline measurements and never designate files or hardware.
"""
from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, TextIO

from dutybench_core import compare


VERSION = "0.1.0"
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * MAX_CAPTURE_BYTES + 1024 * 1024
PROTOCOLS = ("2025-11-25", "2025-06-18")
TOOL_NAME = "dutybench_compare"

NAME = {"type": "string", "minLength": 1, "maxLength": 128,
        "description": "Use trimmed text without control characters."}
POSITIVE = {"type": "number", "exclusiveMinimum": 0}
NONNEGATIVE = {"type": "number", "minimum": 0}
CONDITIONS_SCHEMA = {
    "type": "object", "required": ["board", "instrument", "supply_v", "workload"],
    "properties": {"board": NAME, "instrument": NAME, "supply_v": POSITIVE, "workload": NAME},
    "propertyNames": NAME, "additionalProperties": NAME,
    "description": "Use the same board, instrument, supply voltage and workload in both captures. "
                   "Record other experimental controls as named strings.",
}
WINDOW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["id", "phase", "duration_s", "mean_current_ma"],
    "properties": {
        "id": {**NAME, "description": "Unique window identifier within this capture."},
        "phase": NAME, "duration_s": POSITIVE, "mean_current_ma": NONNEGATIVE,
        "coverage_fraction": {"type": ["number", "null"], "minimum": 0, "maximum": 1,
                              "description": "Observed fraction of the planned window. Omit or pass null if unknown."},
        "uncertainty_ma": {"type": ["number", "null"], "minimum": 0,
                           "description": "Absolute bound on mean current in mA. Omit or pass null if unknown."},
    },
}
CAPTURE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema_version", "label", "conditions", "windows"],
    "properties": {"schema_version": {"type": "integer", "const": 1}, "label": NAME,
                   "conditions": CONDITIONS_SCHEMA,
                   "windows": {"type": "array", "minItems": 1, "maxItems": 10000, "items": WINDOW_SCHEMA}},
}
TOOL = {
    "name": TOOL_NAME, "title": "Compare measured firmware power",
    "description": "Compare baseline and candidate phase-labelled current summaries at a budget in percent. "
                   "Returns within_budget, regression or inconclusive and the evidence for each phase. "
                   "Pass two inline JSON objects.",
    "inputSchema": {
        "type": "object", "required": ["baseline", "candidate"], "additionalProperties": False,
        "$defs": {"capture": CAPTURE_SCHEMA},
        "properties": {"baseline": {"$ref": "#/$defs/capture"}, "candidate": {"$ref": "#/$defs/capture"},
                       "budget_pct": {**NONNEGATIVE, "default": 5}},
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
}


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"{value} is not a JSON number")


def decode_json(raw: bytes) -> Any:
    """Read consistent, unambiguous UTF-8 JSON from either local transport."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
    except RecursionError as exc:
        raise ValueError("JSON input is nested too deeply") from exc


def _error(ident: str | int | None, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def _call_tool(args: Any) -> dict:
    try:
        if not isinstance(args, dict) or not {"baseline", "candidate"} <= args.keys():
            raise ValueError("pass baseline and candidate as inline capture objects")
        if args.keys() - {"baseline", "candidate", "budget_pct"}:
            raise ValueError("supported arguments are baseline, candidate and budget_pct")
        report = compare(args["baseline"], args["candidate"], args.get("budget_pct", 5.0))
    except ValueError as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
    return {"isError": False, "structuredContent": report,
            "content": [{"type": "text", "text": json.dumps(report, allow_nan=False)}]}


def _dispatch(request: Any, initialized: bool) -> tuple[dict | None, bool]:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return _error(None, -32600, "Expected a JSON-RPC 2.0 request"), initialized
    if "id" not in request:
        return None, initialized
    ident = request["id"]
    if type(ident) not in (str, int):
        return _error(None, -32600, "Request id must be a string or integer"), initialized
    method = request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error(ident, -32602, "Expected an object for params"), initialized
    if method == "initialize":
        if initialized:
            return _error(ident, -32600, "Session already initialized"), initialized
        version = params.get("protocolVersion")
        if not isinstance(version, str) or not all(isinstance(params.get(k), dict) for k in ("clientInfo", "capabilities")):
            return _error(ident, -32602, "initialize needs protocolVersion, clientInfo and capabilities"), initialized
        result = {"protocolVersion": version if version in PROTOCOLS else PROTOCOLS[0],
                  "serverInfo": {"name": "dutybench", "version": VERSION}, "capabilities": {"tools": {}}}
        initialized = True
    elif method == "ping":
        result = {}
    elif not initialized:
        return _error(ident, -32002, "Initialize this MCP session first"), initialized
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        if params.get("name") != TOOL_NAME:
            return _error(ident, -32602, f"Unknown tool. Use {TOOL_NAME}"), initialized
        result = _call_tool(params.get("arguments"))
    else:
        return _error(ident, -32601, "Method not found"), initialized
    return {"jsonrpc": "2.0", "id": ident, "result": result}, initialized


def serve(source: BinaryIO | None = None, target: TextIO | None = None) -> int:
    """Serve one JSON-RPC message per line until the MCP client closes stdin."""
    source = sys.stdin.buffer if source is None else source
    target = sys.stdout if target is None else target
    initialized = False
    while raw := source.readline(MAX_REQUEST_BYTES + 1):
        if len(raw) > MAX_REQUEST_BYTES:
            while raw and not raw.endswith(b"\n"):
                raw = source.readline(65536)
            reply = _error(None, -32600, f"MCP request exceeds {MAX_REQUEST_BYTES} bytes")
        else:
            try:
                request = decode_json(raw)
            except ValueError as exc:
                reply = _error(None, -32700, f"Invalid UTF-8 JSON: {exc}")
            else:
                reply, initialized = _dispatch(request, initialized)
        if reply is not None:
            target.write(json.dumps(reply, allow_nan=False, separators=(",", ":")) + "\n")
            target.flush()
    return 0
