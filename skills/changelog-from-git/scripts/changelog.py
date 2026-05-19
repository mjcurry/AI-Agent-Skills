#!/usr/bin/env python3
"""Generate Keep a Changelog markdown from git history.

Conventional-commit aware. Reads history only — never writes to the repo.
Standard library; must be run inside the target git repository.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys

FIELD = "\x1f"  # between fields of one commit
RECORD = "\x1e"  # between commits

# Conventional Commit subject: type(scope)!: description
CC_RE = re.compile(r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s+(?P<desc>.+)$")

TYPE_SECTION = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Changed",
    "refactor": "Changed",
    "docs": "Documentation",
    "revert": "Removed",
}
NOISE_TYPES = {"chore", "test", "ci", "build", "style"}

# Order sections appear in the output.
SECTION_ORDER = ["Added", "Changed", "Fixed", "Removed", "Documentation", "Other"]


def git(args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        sys.exit("git not found on PATH")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"git {' '.join(args)} failed: {exc.stderr.strip()}")
    return out.stdout


def collect(rev_range: str) -> list[dict]:
    fmt = FIELD.join(["%h", "%s", "%b"]) + RECORD
    raw = git(["log", rev_range, f"--pretty=format:{fmt}"])
    commits = []
    for chunk in raw.split(RECORD):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        short, subject, body = (chunk.split(FIELD) + ["", "", ""])[:3]
        commits.append({"hash": short, "subject": subject, "body": body})
    return commits


def classify(commit: dict, include_all: bool, strict: bool):
    """Return (section, text, breaking) or None to drop the commit."""
    m = CC_RE.match(commit["subject"])
    breaking = "BREAKING CHANGE" in commit["body"]

    if not m:
        if strict:
            return None
        return "Other", commit["subject"], breaking

    ctype = m.group("type").lower()
    if ctype in NOISE_TYPES and not include_all:
        return None

    breaking = breaking or bool(m.group("bang"))
    scope = m.group("scope")
    desc = m.group("desc")
    text = f"**{scope}:** {desc}" if scope else desc
    section = TYPE_SECTION.get(ctype, "Other")
    return section, text, breaking


def build(commits, version, date, repo_url, include_all, strict) -> str:
    buckets: dict[str, list[str]] = {s: [] for s in SECTION_ORDER}
    for c in commits:
        result = classify(c, include_all, strict)
        if result is None:
            continue
        section, text, breaking = result
        link = (
            f"([{c['hash']}]({repo_url.rstrip('/')}/commit/{c['hash']}))"
            if repo_url
            else f"(`{c['hash']}`)"
        )
        prefix = "**BREAKING** " if breaking else ""
        entry = f"- {prefix}{text} {link}"
        # Breaking changes float to the top of their section.
        buckets[section].insert(0, entry) if breaking else buckets[section].append(entry)

    lines = [f"## [{version}] - {date}", ""]
    any_content = False
    for section in SECTION_ORDER:
        entries = buckets[section]
        if not entries:
            continue
        any_content = True
        lines.append(f"### {section}")
        lines.extend(entries)
        lines.append("")

    if not any_content:
        return f"## [{version}] - {date}\n\n_No notable changes in this range._\n"
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate Keep a Changelog markdown from git history."
    )
    p.add_argument("from_ref", help="Start ref (exclusive), e.g. v1.4.0")
    p.add_argument("to_ref", nargs="?", default="HEAD", help="End ref (default HEAD)")
    p.add_argument("--version", default="Unreleased")
    p.add_argument("--date", default=datetime.date.today().isoformat())
    p.add_argument("--repo-url", default="")
    p.add_argument("--include-all", action="store_true")
    p.add_argument("--strict", action="store_true")
    args = p.parse_args(argv)

    # Validate refs up front for a clear error instead of a git stack trace.
    for ref in (args.from_ref, args.to_ref):
        git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])

    rev_range = f"{args.from_ref}..{args.to_ref}"
    commits = collect(rev_range)
    if not commits:
        print(
            f"No commits in {rev_range}.", file=sys.stderr
        )
        return 1

    print(
        build(
            commits,
            args.version,
            args.date,
            args.repo_url,
            args.include_all,
            args.strict,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
