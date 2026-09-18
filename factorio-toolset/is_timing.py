"""
Summarize Ingredient Scrap timing markers from factorio-current.log.

Factorio data-stage Lua does not expose a useful high-resolution clock in every
environment, but every log line starts with Factorio's elapsed time. This tool
uses those prefixes to calculate deltas between `[IS][time]` markers.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


DEFAULT_LOG = Path(r"F:/Games/Factorio_ModTest/factorio-current.log")
LINE_RE = re.compile(
    r"^\s*(?P<time>[0-9.]+)\s+.*?\[IS\]\[time\]\[(?P<stage>[^\]]+)\]\[(?P<step>[^\]]+)\](?P<rest>.*)$"
)
COUNT_RE = re.compile(r"\bcount=(?P<count>\S+)")


def read_markers(log_path: Path) -> list[dict[str, object]]:
    """Read Ingredient Scrap timing markers from a Factorio log file."""
    markers: list[dict[str, object]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        count_match = COUNT_RE.search(match.group("rest"))
        markers.append(
            {
                "time": float(match.group("time")),
                "stage": match.group("stage"),
                "step": match.group("step"),
                "count": count_match.group("count") if count_match else "",
            }
        )
    return markers


def print_timeline(markers: list[dict[str, object]]) -> None:
    """Print markers in log order with delta from the previous marker."""
    if not markers:
        print("No [IS][time] markers found.")
        return

    first = float(markers[0]["time"])
    previous = first
    print("Timeline:")
    for marker in markers:
        current = float(marker["time"])
        delta = current - previous
        total = current - first
        previous = current
        count = f" count={marker['count']}" if marker["count"] else ""
        print(
            f"at={current:8.3f}s delta={delta:8.3f}s total={total:8.3f}s "
            f"{marker['stage']}/{marker['step']}{count}"
        )


def print_slowest(markers: list[dict[str, object]], limit: int) -> None:
    """Print the largest gaps between adjacent markers."""
    if len(markers) < 2:
        return

    rows = []
    previous = float(markers[0]["time"])
    for marker in markers[1:]:
        current = float(marker["time"])
        rows.append((current - previous, marker))
        previous = current

    print()
    print("Slowest gaps:")
    for delta, marker in sorted(rows, key=lambda row: row[0], reverse=True)[:limit]:
        count = f" count={marker['count']}" if marker["count"] else ""
        print(f"{delta:8.3f}s at={float(marker['time']):8.3f}s {marker['stage']}/{marker['step']}{count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Ingredient Scrap timing markers from Factorio logs")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="Path to factorio-current.log")
    parser.add_argument("--limit", type=int, default=12, help="Number of slowest gaps to print")
    args = parser.parse_args()

    if not args.log.exists():
        print(f"ERROR: log file not found: {args.log}")
        return 2

    markers = read_markers(args.log)
    print_timeline(markers)
    print_slowest(markers, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
