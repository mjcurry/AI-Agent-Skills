#!/usr/bin/env python3
"""Lint Agent Skills (SKILL.md) against the Agent Skills specification.

Checks the hard rules from https://agentskills.io/specification (errors), its
stated guidance (warnings), and a few habits that decide whether a skill ever
gets triggered or its files ever get found (info). Standard library only -
the frontmatter is read by a small purpose-built parser, so anything it
cannot understand is reported rather than guessed at.

Pass skill directories, SKILL.md files, or a parent directory to walk.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

# --- limits taken from the specification -------------------------------------
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
MAX_BODY_LINES = 500          # "Keep your main SKILL.md under 500 lines."
MAX_BODY_TOKENS = 5000        # "< 5000 tokens recommended"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KNOWN_FIELDS = {"name", "description", "license", "compatibility", "metadata",
                "allowed-tools"}

# --- heuristics that are ours, not the spec's ---------------------------------
MIN_DESCRIPTION = 50          # the spec's own poor example is "Helps with PDFs."
METADATA_TOKENS_SOFT = 200    # spec says name+description load at "~100 tokens"
WHEN_RE = re.compile(
    r"(?i)\buse (?:this |it )?(?:skill )?(?:when|whenever|for|before|after|if|to)\b"
    r"|\bwhen (?:the user|you|a |an |asked|working)"
    r"|\btriggers? (?:on|when)\b")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
BLOCK_INDICATORS = {">", "|", ">-", "|-", ">+", "|+"}
SEVERITY_RANK = {"error": 2, "warn": 1, "info": 0}


@dataclass
class Finding:
    skill: str
    severity: str
    code: str
    message: str


def estimate_tokens(text: str) -> int:
    """Blended char + word heuristic (same estimator as token-usage-estimator)."""
    if not text:
        return 0
    return max(1, round((len(text) / 3.8 + len(re.findall(r"\S+", text)) * 1.3) / 2))


def unquote(value: str) -> tuple[str, bool]:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            return inner.replace('\\"', '"').replace("\\n", "\n"), True
        return inner.replace("''", "'"), True
    return value, False


def parse_frontmatter(lines: list[str]) -> tuple[dict, list[tuple[str, str, str]]]:
    """Parse the YAML subset skills actually use: scalars, block scalars,
    one-level maps and lists. Returns (fields, problems)."""
    meta: dict = {}
    problems: list[tuple[str, str, str]] = []
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw[0] in " \t":
            problems.append(("error", "frontmatter-syntax",
                             f"frontmatter line {i + 1}: unexpected indentation"))
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):(?:\s+(.*))?$", raw)
        if not m:
            problems.append(("error", "frontmatter-syntax",
                             f"frontmatter line {i + 1}: not a 'key: value' pair"))
            i += 1
            continue
        key, val = m.group(1), (m.group(2) or "").strip()
        if key in meta:
            problems.append(("error", "duplicate-field", f"'{key}' appears more than once"))

        j = i + 1                         # gather indented continuation lines
        block: list[str] = []
        while j < len(lines) and (not lines[j].strip() or lines[j][0] in " \t"):
            block.append(lines[j])
            j += 1
        items = [b.strip() for b in block if b.strip() and not b.strip().startswith("#")]

        if val in BLOCK_INDICATORS:
            joiner = " " if val[0] == ">" else "\n"
            meta[key] = joiner.join(b.strip() for b in block if b.strip() or val[0] == "|").strip()
        elif not val and items:
            if all(it == "-" or it.startswith("- ") for it in items):
                meta[key] = [unquote(it[1:].strip())[0] for it in items]
            else:
                sub: dict[str, str] = {}
                for it in items:
                    sm = re.match(r"^([^:]+):\s*(.*)$", it)
                    if not sm:
                        problems.append(("error", "frontmatter-syntax",
                                         f"'{key}': cannot read nested entry '{it[:40]}'"))
                        continue
                    sv, quoted = unquote(sm.group(2).strip())
                    if key == "metadata" and not quoted and \
                            re.match(r"^(?:-?\d+(?:\.\d+)?|true|false|null|~|yes|no)$", sv, re.I):
                        problems.append(("warn", "metadata-non-string",
                                         f"metadata.{sm.group(1).strip()}: {sv} is not a string in YAML - "
                                         "quote it (the spec defines metadata as string -> string)"))
                    sub[sm.group(1).strip()] = sv
                meta[key] = sub
        else:
            v, quoted = unquote(val)
            if not quoted:
                if items:                 # plain scalar continued on following lines
                    v = " ".join([v] + items)
                if re.search(r":\s", v) or v.endswith(":"):
                    problems.append(("error", "unquoted-colon",
                                     f"'{key}' contains ': ' but is not quoted - YAML parsers reject "
                                     "or truncate this; wrap the whole value in quotes"))
                if re.search(r"\s#", v):
                    problems.append(("warn", "unquoted-hash",
                                     f"'{key}' contains ' #' unquoted - YAML treats the rest as a "
                                     "comment and drops it; quote the value"))
                if v[:1] in "[{*&!|>%@`":
                    problems.append(("warn", "unquoted-special",
                                     f"'{key}' starts with '{v[:1]}', which is YAML syntax - quote the value"))
            meta[key] = v
        i = j
    return meta, problems


def mask_code(text: str) -> str:
    """Blank out fenced blocks and inline code so example links aren't checked."""
    out, fence = [], ""
    for line in text.split("\n"):
        s = line.lstrip()
        if not fence and (s.startswith("```") or s.startswith("~~~")):
            fence = s[:3]
            out.append("")
        elif fence:
            if s.startswith(fence):
                fence = ""
            out.append("")
        else:
            out.append(re.sub(r"`[^`\n]*`", "", line))
    return "\n".join(out)


def lint_skill(skill_dir: str) -> list[Finding]:
    label = os.path.basename(os.path.normpath(os.path.abspath(skill_dir)))
    out: list[Finding] = []

    def add(sev: str, code: str, msg: str) -> None:
        out.append(Finding(label, sev, code, msg))

    path = os.path.join(skill_dir, "SKILL.md")
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read().replace("\r\n", "\n")
    except OSError:
        add("error", "missing-skill-md", "no SKILL.md in this directory")
        return out

    lines = text.split("\n")
    end = next((n for n in range(1, len(lines)) if lines[n].strip() == "---"), None)
    if lines[0].strip() != "---" or end is None:
        add("error", "missing-frontmatter",
            "SKILL.md must start with YAML frontmatter delimited by '---' lines")
        return out
    meta, problems = parse_frontmatter(lines[1:end])
    for sev, code, msg in problems:
        add(sev, code, msg)
    body = "\n".join(lines[end + 1:])

    # --- name ---------------------------------------------------------------
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        add("error", "missing-name", "frontmatter has no 'name'")
    else:
        if len(name) > MAX_NAME:
            add("error", "name-too-long", f"name is {len(name)} characters; the limit is {MAX_NAME}")
        if not NAME_RE.match(name):
            add("error", "bad-name",
                f"'{name}': lowercase a-z, 0-9 and single hyphens only; "
                "no leading, trailing or consecutive hyphens")
        if name != label:
            add("error", "name-directory-mismatch",
                f"name '{name}' must match the parent directory '{label}'")

    # --- description --------------------------------------------------------
    desc = meta.get("description")
    if not isinstance(desc, str) or not desc.strip():
        add("error", "missing-description", "frontmatter has no 'description'")
        desc = ""
    else:
        if len(desc) > MAX_DESCRIPTION:
            add("error", "description-too-long",
                f"description is {len(desc)} characters; the limit is {MAX_DESCRIPTION}")
        if len(desc) < MIN_DESCRIPTION:
            add("warn", "short-description",
                f"description is {len(desc)} characters - say what the skill does and when to use it, "
                "with the keywords a matching task would contain")
        elif not WHEN_RE.search(desc):
            add("warn", "no-trigger",
                "description says what the skill does but not when to use it - "
                "add a 'Use when ...' clause; this field alone decides whether the skill loads")
        tokens = estimate_tokens(f"{name or ''} {desc}")
        if tokens > METADATA_TOKENS_SOFT:
            add("info", "heavy-metadata",
                f"name + description is ~{tokens} tokens and loads for every task "
                "(the spec budgets ~100) - trim it")

    # --- optional fields ----------------------------------------------------
    compat = meta.get("compatibility")
    if compat is not None:
        if not isinstance(compat, str) or not compat:
            add("error", "bad-compatibility", "compatibility must be a non-empty string")
        elif len(compat) > MAX_COMPATIBILITY:
            add("error", "compatibility-too-long",
                f"compatibility is {len(compat)} characters; the limit is {MAX_COMPATIBILITY}")
    if "metadata" in meta and not isinstance(meta["metadata"], dict):
        add("error", "bad-metadata", "metadata must be a mapping of string keys to string values")
    if "allowed-tools" in meta and not isinstance(meta["allowed-tools"], str):
        add("error", "bad-allowed-tools",
            "allowed-tools must be one space-separated string, not a list")
    lic = meta.get("license")
    if isinstance(lic, str) and len(lic) > 200:
        add("info", "long-license", "license is long - the spec recommends a short name or file reference")
    for key in meta:
        if key not in KNOWN_FIELDS:
            add("info", "unknown-field",
                f"'{key}' is not a field the spec defines - agents may ignore it "
                "(put custom data under 'metadata')")

    # --- body ---------------------------------------------------------------
    body_lines = body.strip("\n").split("\n") if body.strip() else []
    if not body_lines:
        add("warn", "empty-body", "SKILL.md has no instructions after the frontmatter")
    if len(body_lines) > MAX_BODY_LINES:
        add("warn", "body-too-long",
            f"body is {len(body_lines)} lines; keep SKILL.md under {MAX_BODY_LINES} and move detail to references/")
    body_tokens = estimate_tokens(body)
    if body_tokens > MAX_BODY_TOKENS:
        add("warn", "body-too-heavy",
            f"body is ~{body_tokens} tokens; under {MAX_BODY_TOKENS} is recommended since it all loads on activation")

    # --- file references ----------------------------------------------------
    linked: set[str] = set()
    for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", mask_code(body)):
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|#|//)", target, re.I):
            continue                                  # URL, mailto, in-page anchor
        rel = target.split("#", 1)[0].split("?", 1)[0]
        if not rel:
            continue
        norm = os.path.normpath(rel)
        if norm.startswith("..") or os.path.isabs(rel):
            add("warn", "link-outside-skill",
                f"'{target}' points outside the skill directory - it breaks when the skill is copied elsewhere")
            continue
        linked.add(norm)
        if not os.path.exists(os.path.join(skill_dir, norm)):
            add("error", "broken-link", f"'{target}' does not exist")
        elif norm.count(os.sep) > 1:
            add("warn", "deep-reference",
                f"'{target}' is nested more than one level below SKILL.md - keep references one level deep")

    for norm in sorted(linked):                       # reference chains
        full = os.path.join(skill_dir, norm)
        if not norm.endswith((".md", ".markdown")) or not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                ref_text = mask_code(fh.read())
        except OSError:
            continue
        for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", ref_text):
            if re.match(r"^(?:[a-z][a-z0-9+.-]*:|#|//)", target, re.I):
                continue
            rel = target.split("#", 1)[0]
            if not rel:
                continue
            chained = os.path.normpath(os.path.join(os.path.dirname(norm), rel))
            if os.path.isfile(os.path.join(skill_dir, chained)) and chained not in linked \
                    and chained != "SKILL.md":
                add("info", "reference-chain",
                    f"{norm} links to {chained}, which SKILL.md never mentions - "
                    "avoid chains; link it from SKILL.md directly")

    # --- files nothing points at -----------------------------------------------
    for root, dirs, names in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), skill_dir)
            if rel == "SKILL.md" or n.startswith("."):
                continue
            top = rel.split(os.sep)[0]
            mentioned = rel in body or n in body or (os.sep in rel and f"{top}/" in body)
            if not mentioned:
                add("info", "unreferenced-file",
                    f"{rel} is never mentioned in SKILL.md - an agent has no way to know it exists")
    return out


def discover(paths: list[str]) -> list[str]:
    found: dict[str, None] = {}
    for p in paths:
        if os.path.isfile(p) and os.path.basename(p) == "SKILL.md":
            found[os.path.dirname(os.path.abspath(p))] = None
        elif os.path.isfile(os.path.join(p, "SKILL.md")):
            found[os.path.abspath(p)] = None
        elif os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                if "SKILL.md" in names:
                    found[os.path.abspath(root)] = None
                    dirs[:] = []                      # a skill does not contain skills
    return list(found)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Lint Agent Skills against the specification.")
    p.add_argument("paths", nargs="+",
                   help="Skill directories, SKILL.md files, or a directory to search.")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero on warnings too, not just errors.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    skills = discover(args.paths)
    if not skills:
        print("No SKILL.md found under the given paths.", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    for s in skills:
        findings.extend(lint_skill(s))
    counts = {sev: sum(1 for f in findings if f.severity == sev) for sev in SEVERITY_RANK}

    if args.json:
        print(json.dumps({"skills": len(skills), "counts": counts,
                          "findings": [f.__dict__ for f in findings]}, indent=2))
    else:
        print(f"{len(skills)} skill(s) - {counts['error']} error, "
              f"{counts['warn']} warn, {counts['info']} info\n")
        by_skill: dict[str, list[Finding]] = {}
        for f in findings:
            by_skill.setdefault(f.skill, []).append(f)
        for skill, items in by_skill.items():
            print(skill)
            for f in sorted(items, key=lambda f: -SEVERITY_RANK[f.severity]):
                print(f"  [{f.severity:<5}] {f.code:<26} {f.message}")
            print()
        clean = len(skills) - len(by_skill)
        if clean:
            print(f"{clean} skill(s) clean.")

    threshold = 1 if args.strict else 2
    return 1 if any(SEVERITY_RANK[f.severity] >= threshold for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
