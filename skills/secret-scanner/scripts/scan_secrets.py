#!/usr/bin/env python3
"""Scan files or staged changes for credentials before they get committed.

Standard library only. Pattern-based, with a Shannon-entropy and placeholder
filter on generic `key = "..."` assignments to keep false positives down.
The exit code is non-zero when findings at or above --fail-on are present,
so it drops into a pre-commit hook or CI step unchanged.

Suppress a known-safe line by adding the comment  secret-scanner: ignore
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass

IGNORE_MARK = "secret-scanner: ignore"
SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build"}
MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str
    pattern: re.Pattern
    entropy_check: bool = False      # entropy + placeholder filter on group(1)
    placeholder_check: bool = False  # placeholder filter only on group(1)


RULES: list[Rule] = [
    Rule("private-key-block", "critical",
         re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    Rule("aws-access-key-id", "high", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    Rule("anthropic-api-key", "high", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    Rule("openai-api-key", "high",
         re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    Rule("github-token", "high",
         re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    Rule("slack-token", "high", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    Rule("google-api-key", "high", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    Rule("stripe-key", "high", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    Rule("url-with-password", "high",
         re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:([^@\s/]{4,})@"),
         placeholder_check=True),
    Rule("jwt", "medium",
         re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    Rule("generic-secret-assignment", "medium",
         re.compile(
             r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|"
             r"auth[_-]?token|token|password|passwd)\b\s*[:=]\s*['\"]([^'\"\s]{12,})['\"]"
         ),
         entropy_check=True),
]

PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:x+|\*+|<.*>|\$\{.*\}|\{\{.*\}\}|your[_-].*|changeme|replace[_-]?me|"
    r"example|dummy|placeholder|todo|none|null|redacted|\.\.\.|"
    r"pass(?:word)?|pwd|secret|user(?:name)?)$"
)


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    severity: str
    masked: str


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def is_placeholder(value: str) -> bool:
    if PLACEHOLDER_RE.match(value):
        return True
    if "${" in value or "{{" in value or value.startswith("<"):
        return True
    return any(k in value for k in ("os.environ", "process.env", "getenv"))


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-2:]}"


def scan_line(path: str, line_no: int, text: str) -> list[Finding]:
    if IGNORE_MARK in text:
        return []
    found: list[Finding] = []
    seen: set[str] = set()
    for rule in RULES:
        for m in rule.pattern.finditer(text):
            captured = rule.entropy_check or rule.placeholder_check
            value = m.group(1) if captured else m.group(0)
            if captured and is_placeholder(value):
                continue
            if rule.entropy_check and shannon_entropy(value) < 3.0:
                continue
            if rule.name in seen:
                continue
            seen.add(rule.name)
            found.append(Finding(path, line_no, rule.name, rule.severity, mask(value)))
    return found


def scan_text(path: str, text: str, start_line: int = 1) -> list[Finding]:
    out: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start_line):
        out.extend(scan_line(path, i, line))
    return out


def scan_file(path: str) -> list[Finding]:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return []
        with open(path, "rb") as fh:
            head = fh.read(1024)
            if b"\x00" in head:
                return []
            data = head + fh.read()
    except OSError:
        return []
    return scan_text(path, data.decode("utf-8", errors="replace"))


def expand(targets: list[str], excludes: list[str]) -> list[str]:
    files: dict[str, None] = {}

    def wanted(p: str) -> bool:
        return not any(fnmatch.fnmatch(p, pat) for pat in excludes)

    for t in targets:
        if os.path.isdir(t):
            for root, dirs, names in os.walk(t):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for n in names:
                    p = os.path.normpath(os.path.join(root, n))
                    if wanted(p):
                        files[p] = None
        else:
            hits = glob.glob(t, recursive=True) or ([t] if os.path.isfile(t) else [])
            for h in hits:
                if os.path.isfile(h) and wanted(h):
                    files[os.path.normpath(h)] = None
    return list(files)


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_staged() -> list[Finding]:
    """Scan only the *added* lines of the staged diff, with real line numbers."""
    try:
        diff = subprocess.run(
            ["git", "diff", "--cached", "--unified=0", "--no-color", "--diff-filter=AM"],
            capture_output=True, text=True, check=True,
        ).stdout
    except FileNotFoundError:
        sys.exit("git not found on PATH")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"git diff failed: {exc.stderr.strip()}")

    findings: list[Finding] = []
    path = ""
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:]
            path = "" if target == "/dev/null" else target[2:] if target.startswith("b/") else target
        elif raw.startswith("@@"):
            m = HUNK_RE.match(raw)
            line_no = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            if path:
                findings.extend(scan_line(path, line_no, raw[1:]))
            line_no += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
    return findings


def render(findings: list[Finding], scanned: str) -> str:
    if not findings:
        return f"No secrets found ({scanned})."
    order = sorted(findings, key=lambda f: (-SEVERITY_RANK[f.severity], f.path, f.line))
    lines = [f"{len(findings)} finding(s) ({scanned})", ""]
    lines.append(f"{'SEVERITY':<10}{'RULE':<28}{'LOCATION':<42}MATCH")
    lines.append("-" * 94)
    for f in order:
        loc = f"{f.path}:{f.line}"
        if len(loc) > 40:  # keep the tail — the filename and line are what matter
            loc = "…" + loc[-39:]
        lines.append(f"{f.severity:<10}{f.rule:<28}{loc:<42}{f.masked}")
    lines.append("")
    lines.append("Rotate anything real. Suppress a false positive with: "
                 f"# {IGNORE_MARK}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Scan files or staged changes for credentials.",
        epilog="With no paths, scans the staged git diff (pre-commit mode).",
    )
    p.add_argument("paths", nargs="*", help="Files, directories, or globs to scan.")
    p.add_argument("--staged", action="store_true",
                   help="Scan added lines in the staged diff (default when no paths).")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                   help="Path glob to skip (repeatable).")
    p.add_argument("--fail-on", choices=list(SEVERITY_RANK), default="high",
                   help="Minimum severity that makes the exit code non-zero (default: high).")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.staged or not args.paths:
        findings = scan_staged()
        scanned = "staged changes"
    else:
        files = expand(args.paths, args.exclude)
        if not files:
            print("No files matched.", file=sys.stderr)
            return 0
        findings = [f for path in files for f in scan_file(path)]
        scanned = f"{len(files)} file(s)"

    if args.json:
        print(json.dumps({"scanned": scanned, "findings": [f.__dict__ for f in findings]},
                         indent=2))
    else:
        print(render(findings, scanned))

    threshold = SEVERITY_RANK[args.fail_on]
    blocking = any(SEVERITY_RANK[f.severity] >= threshold for f in findings)
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
