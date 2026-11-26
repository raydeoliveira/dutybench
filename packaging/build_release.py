"""Build DutyBench's source ZIP, desktop MCP bundle and SHA-256 checksums.
Run python3 packaging/build_release.py --output-dir PATH from a source checkout.
Both archives use allowlisted content, fixed metadata and no host-specific files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import zipfile


PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = ("dutybench.py", "dutybench_core.py", "dutybench_mcp.py")
SOURCE = (*RUNTIME, "dutybench_import.py", "README.md", "LICENSE", "SECURITY.md", "CHANGELOG.md",
          "pyproject.toml", "packaging/build_release.py",
          "examples/sensor-off.json", "examples/sensor-cycle.json", "examples/idle-baseline.json",
          "examples/idle-candidate.json", "examples/statistics.csv", "examples/windows.json",
          "tests/test_cli.py", "tests/test_core.py", "tests/test_import.py")


def manifest(version: str) -> bytes:
    entry = "server/dutybench.py"
    description = "Compare measured firmware current draw in matched operating phases."
    data = {
        "manifest_version": "0.3", "name": "dutybench", "display_name": "DutyBench", "version": version,
        "description": description,
        "long_description": "Pass baseline and candidate power measurements inline. The server compares them "
                            "locally and reports the evidence for each phase. Install Python 3.11 or later "
                            "where your desktop client can find it on PATH.",
        "author": {"name": "Raymond de Oliveira"}, "license": "SEE LICENSE IN LICENSE",
        "repository": {"type": "git", "url": "https://github.com/raydeoliveira/dutybench"},
        "compatibility": {"runtimes": {"python": ">=3.11"}},
        "server": {"type": "python", "entry_point": entry, "mcp_config": {
            "command": "python3", "args": ["-B", "${__dirname}/" + entry, "mcp"],
            "platform_overrides": {"win32": {"command": "python"}},
        }},
        "tools": [{"name": "dutybench_compare", "description": description}],
    }
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def archive(target: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(target, "w") as bundle:
        for name, contents in sorted(entries.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            bundle.writestr(entry, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build(output: Path) -> None:
    output = output.resolve()
    if output.is_relative_to(PROJECT):
        raise ValueError("select an output directory outside the source checkout")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("select a new or empty output directory")
    files = {}
    for name in SOURCE:
        path = PROJECT / name
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"missing or linked release input: {name}")
        files[name] = path.read_bytes()
    version = tomllib.loads(files["pyproject.toml"].decode("utf-8"))["project"]["version"]
    if not all(part.isascii() and part.isdigit() for part in version.split(".")) or len(version.split(".")) != 3:
        raise ValueError("expected a three-part numeric project version")
    stem = f"dutybench-{version}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        staged = Path(work) / "artifacts"
        staged.mkdir()
        source_zip = staged / f"{stem}.zip"
        archive(source_zip, {f"{stem}/{name}": contents for name, contents in files.items()})
        mcpb = staged / f"{stem}.mcpb"
        modules = {f"server/{name}": files[name] for name in RUNTIME}
        archive(mcpb, {**modules, "manifest.json": manifest(version), "LICENSE": files["LICENSE"]})
        checksums = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in (mcpb, source_zip)]
        (staged / "SHA256SUMS.txt").write_bytes("".join(checksums).encode("ascii"))
        staged.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the two DutyBench downloads and SHA256SUMS.txt.")
    parser.add_argument("--output-dir", required=True, type=Path, help="New or empty directory outside the source checkout.")
    args = parser.parse_args()
    try:
        build(args.output_dir)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"build_release: {exc}\n")
        return 1
    sys.stdout.write(f"Built release in {args.output_dir.resolve()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
