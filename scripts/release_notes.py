#!/usr/bin/env python
"""Extract one release section from CHANGELOG.md."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANGELOG = ROOT / "CHANGELOG.md"
_HEADING = re.compile(r"^## \[([^\]]+)\](?: - .+)?$")


def extract_section(changelog: str, version: str) -> str:
    """Return the Markdown body below the exact version heading."""
    lines = changelog.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match and match.group(1) == version:
            start = index + 1
            break
    if start is None:
        raise ValueError(f"CHANGELOG.md 缺少 [{version}] 小节")

    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    body = "\n".join(lines[start:end]).strip()
    if not body:
        raise ValueError(f"CHANGELOG.md 的 [{version}] 小节为空")
    return body + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_notes.py", description="提取版本发布说明")
    parser.add_argument("--version", required=True, help="不带 v 的版本号，例如 0.2.1")
    parser.add_argument("--changelog", default=str(DEFAULT_CHANGELOG))
    parser.add_argument("--out", default="", help="输出文件；省略时写 stdout")
    args = parser.parse_args(argv)

    try:
        notes = extract_section(
            Path(args.changelog).read_text(encoding="utf-8"),
            args.version,
        )
    except (OSError, ValueError) as exc:
        print(f"提取发布说明失败：{exc}", file=sys.stderr)
        return 2

    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(notes, encoding="utf-8", newline="\n")
        print(f"发布说明：{output}")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
