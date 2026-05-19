#!/usr/bin/env python3
"""Plan which files fit an agent context budget.

Estimates the token cost of a candidate file set and produces a ranked
include/exclude plan that fits a budget. Standard library only.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from dataclasses import dataclass, field


def heuristic_tokens(text: str) -> int:
    """Blended char + word estimator (see token-usage-estimator)."""
    if not text:
        return 0
    char_estimate = len(text) / 3.8
    word_estimate = len(re.findall(r"\S+", text)) * 1.3
    return max(1, round((char_estimate + word_estimate) / 2))


def looks_binary(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(1024)
    except OSError:
        return False


@dataclass
class FileEntry:
    path: str
    tokens: int = 0
    skipped: str = ""  # non-empty = reason it was excluded outright


@dataclass
class Plan:
    included: list[FileEntry] = field(default_factory=list)
    excluded: list[FileEntry] = field(default_factory=list)
    skipped: list[FileEntry] = field(default_factory=list)
    budget: int = 0
    reserve: int = 0

    @property
    def used(self) -> int:
        return sum(f.tokens for f in self.included)


def expand(patterns: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for pat in patterns:
        for hit in sorted(glob.glob(pat, recursive=True)):
            if os.path.isfile(hit) and hit not in seen:
                seen[hit] = None
    return list(seen)


def measure(paths: list[str]) -> list[FileEntry]:
    entries = []
    for p in paths:
        if looks_binary(p):
            entries.append(FileEntry(p, skipped="binary"))
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                entries.append(FileEntry(p, tokens=heuristic_tokens(fh.read())))
        except OSError as exc:
            entries.append(FileEntry(p, skipped=f"unreadable ({exc.strerror})"))
    return entries


def order_entries(
    entries: list[FileEntry], order: str, priority: list[str]
) -> list[FileEntry]:
    by_path = {e.path: e for e in entries}
    ranked: list[FileEntry] = []
    used: set[str] = set()

    for want in priority:
        e = by_path.get(want) or by_path.get(os.path.normpath(want))
        if e and e.path not in used:
            ranked.append(e)
            used.add(e.path)

    rest = [e for e in entries if e.path not in used]
    if order == "smallest":
        rest.sort(key=lambda e: e.tokens)
    elif order == "largest":
        rest.sort(key=lambda e: e.tokens, reverse=True)
    # "given" keeps expansion order

    return ranked + rest


def build_plan(
    entries: list[FileEntry], budget: int, reserve: int
) -> Plan:
    plan = Plan(budget=budget, reserve=reserve)
    effective = budget - reserve
    running = 0
    for e in entries:
        if e.skipped:
            plan.skipped.append(e)
            continue
        if running + e.tokens <= effective:
            plan.included.append(e)
            running += e.tokens
        else:
            plan.excluded.append(e)
    return plan


def render(plan: Plan) -> str:
    out: list[str] = []
    effective = plan.budget - plan.reserve
    n_fit = len(plan.included)
    n_total = n_fit + len(plan.excluded)

    out.append(
        f"Fits {n_fit}/{n_total} files — "
        f"{plan.used:,} / {effective:,} usable tokens "
        f"({plan.budget:,} budget − {plan.reserve:,} reserved)"
    )
    out.append("")

    if plan.included:
        out.append("INCLUDE                                          tokens   running")
        out.append("-" * 70)
        run = 0
        for e in plan.included:
            run += e.tokens
            out.append(f"{e.path[:46]:<46}{e.tokens:>10,}{run:>10,}")
        out.append("")

    if plan.excluded:
        out.append("EXCLUDE (over budget)                            tokens")
        out.append("-" * 58)
        for e in plan.excluded:
            out.append(f"{e.path[:46]:<46}{e.tokens:>10,}")
        out.append("")

    if plan.skipped:
        out.append("SKIPPED")
        for e in plan.skipped:
            out.append(f"  {e.path} — {e.skipped}")
        out.append("")

    if plan.excluded:
        out.append(
            "Excluded files: summarize, reference by path, or split the task."
        )
    if plan.included and plan.included[0].tokens > effective:
        out.append(
            "WARNING: even the top-priority file exceeds the budget — "
            "chunk it or do a summary pass instead of truncating."
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan which files fit a context token budget."
    )
    parser.add_argument("patterns", nargs="+", help="Glob(s); ** is recursive.")
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument(
        "--order",
        choices=["smallest", "largest", "given"],
        default="smallest",
    )
    parser.add_argument("--priority-file")
    parser.add_argument("--reserve", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.budget <= 0:
        parser.error("--budget must be > 0")
    if args.reserve < 0 or args.reserve >= args.budget:
        parser.error("--reserve must be >= 0 and < --budget")

    priority: list[str] = []
    if args.priority_file:
        try:
            with open(args.priority_file, "r", encoding="utf-8") as fh:
                priority = [ln.strip() for ln in fh if ln.strip()]
        except OSError as exc:
            parser.error(f"cannot read --priority-file: {exc}")

    paths = expand(args.patterns)
    if not paths:
        print("No files matched.", file=sys.stderr)
        return 1

    entries = measure(paths)
    ordered = order_entries(entries, args.order, priority)
    plan = build_plan(ordered, args.budget, args.reserve)

    if args.json:
        print(
            json.dumps(
                {
                    "budget": plan.budget,
                    "reserve": plan.reserve,
                    "used": plan.used,
                    "included": [
                        {"path": e.path, "tokens": e.tokens}
                        for e in plan.included
                    ],
                    "excluded": [
                        {"path": e.path, "tokens": e.tokens}
                        for e in plan.excluded
                    ],
                    "skipped": [
                        {"path": e.path, "reason": e.skipped}
                        for e in plan.skipped
                    ],
                },
                indent=2,
            )
        )
    else:
        print(render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
