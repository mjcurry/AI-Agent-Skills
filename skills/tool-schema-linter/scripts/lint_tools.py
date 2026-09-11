#!/usr/bin/env python3
"""Lint LLM tool/function definitions for the mistakes that make models misuse them.

Accepts Anthropic-style ({name, description, input_schema}) or OpenAI-style
({type: "function", function: {name, description, parameters}}) definitions,
given as a JSON array or an object with a top-level "tools" key.
Standard library only.

A model only sees the name, description, and schema — if those are vague,
the tool gets called wrong or not at all. This flags the usual causes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
VAGUE_PARAMS = {"data", "value", "input", "arg", "args", "param", "params",
                "obj", "object", "str", "string", "x", "y", "val", "item", "thing"}
ENUMISH_NAMES = {"type", "mode", "status", "kind", "format", "unit", "level",
                 "category", "sort", "order", "direction", "priority"}
MIN_DESCRIPTION = 40
MAX_PARAMS = 8

SEVERITY_RANK = {"error": 2, "warn": 1, "info": 0}


@dataclass
class Finding:
    tool: str
    severity: str
    code: str
    message: str


def normalize(raw) -> list[dict]:
    if isinstance(raw, dict) and "tools" in raw:
        raw = raw["tools"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        sys.exit("expected a list of tool definitions (or an object with a 'tools' key)")

    tools = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            tools.append({"index": i, "name": None, "description": None,
                          "schema": None, "malformed": True})
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            f = t["function"]
            tools.append({"index": i, "name": f.get("name"),
                          "description": f.get("description"),
                          "schema": f.get("parameters")})
        else:
            tools.append({"index": i, "name": t.get("name"),
                          "description": t.get("description"),
                          "schema": t.get("input_schema", t.get("parameters"))})
    return tools


def lint_tool(t: dict, seen_names: set[str]) -> list[Finding]:
    label = t["name"] or f"<tool #{t['index']}>"
    out: list[Finding] = []

    def add(sev: str, code: str, msg: str) -> None:
        out.append(Finding(label, sev, code, msg))

    if t.get("malformed"):
        add("error", "malformed", "entry is not an object")
        return out

    name = t["name"]
    if not name:
        add("error", "missing-name", "tool has no name")
    else:
        if not NAME_RE.match(name):
            add("error", "bad-name",
                "name must be 1-64 chars of letters, digits, '_' or '-'")
        if name in seen_names:
            add("error", "duplicate-name", "another tool already uses this name")
        seen_names.add(name)

    desc = (t["description"] or "").strip()
    if not desc:
        add("error", "missing-description",
            "no description — the model has nothing to decide with")
    else:
        if len(desc) < MIN_DESCRIPTION:
            add("warn", "short-description",
                f"description is {len(desc)} chars; say what it does, when to use it, "
                "and what it returns")
        if name and re.sub(r"[\s_\-]+", " ", desc.lower()).strip(". ") == \
                name.replace("_", " ").replace("-", " ").lower():
            add("warn", "description-repeats-name",
                "description just restates the name")

    schema = t["schema"]
    if schema is None:
        add("error", "missing-schema", "no input schema / parameters object")
        return out
    if not isinstance(schema, dict):
        add("error", "schema-not-object", "schema must be a JSON object")
        return out
    if schema.get("type") != "object":
        add("error", "schema-not-object", "top-level schema type must be \"object\"")

    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        add("error", "bad-properties", "'properties' must be an object")
        props = {}

    required = schema.get("required")
    if props and required is None:
        add("warn", "no-required-list",
            "no 'required' array — every parameter is optional; is that intended?")
    if isinstance(required, list):
        for r in required:
            if r not in props:
                add("error", "required-unknown-property",
                    f"'required' names '{r}' which is not in properties")

    if schema.get("additionalProperties") is not False:
        add("info", "additional-properties-open",
            "additionalProperties is not false — the model may invent extra keys")
    if len(props) > MAX_PARAMS:
        add("info", "many-params",
            f"{len(props)} parameters; consider splitting into smaller tools")

    for pname, spec in props.items():
        if not isinstance(spec, dict):
            add("error", "bad-param", f"'{pname}' spec must be an object")
            continue
        if pname.lower() in VAGUE_PARAMS:
            add("warn", "param-vague-name",
                f"'{pname}' is too generic to guide the model — name what it holds")
        if not (spec.get("description") or "").strip():
            add("warn", "param-no-description", f"'{pname}' has no description")
        ptype = spec.get("type")
        if ptype is None and not any(k in spec for k in ("enum", "anyOf", "oneOf", "$ref")):
            add("warn", "param-no-type", f"'{pname}' has no type")
        if ptype == "object" and not spec.get("properties"):
            add("warn", "param-object-no-properties",
                f"'{pname}' is an object with no properties — the model must guess its shape")
        if ptype == "array" and not spec.get("items"):
            add("warn", "param-array-no-items", f"'{pname}' is an array with no 'items' schema")
        if ptype == "string" and "enum" not in spec and pname.lower() in ENUMISH_NAMES:
            add("info", "param-consider-enum",
                f"'{pname}' looks categorical — an enum would stop invented values")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Lint LLM tool definitions.")
    p.add_argument("file", help="JSON file with tool definitions ('-' for stdin).")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero on warnings too, not just errors.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    try:
        raw = json.load(sys.stdin) if args.file == "-" else json.load(
            open(args.file, "r", encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"cannot read {args.file}: {exc}")

    tools = normalize(raw)
    seen: set[str] = set()
    findings: list[Finding] = []
    for t in tools:
        findings.extend(lint_tool(t, seen))

    counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_RANK}

    if args.json:
        print(json.dumps({"tools": len(tools), "counts": counts,
                          "findings": [f.__dict__ for f in findings]}, indent=2))
    else:
        print(f"{len(tools)} tool(s) — "
              f"{counts['error']} error, {counts['warn']} warn, {counts['info']} info\n")
        by_tool: dict[str, list[Finding]] = {}
        for f in findings:
            by_tool.setdefault(f.tool, []).append(f)
        for tool, items in by_tool.items():
            print(tool)
            for f in sorted(items, key=lambda f: -SEVERITY_RANK[f.severity]):
                print(f"  [{f.severity:<5}] {f.code:<32} {f.message}")
            print()
        if not findings:
            print("Clean — descriptions, types, and required lists all present.")

    threshold = 1 if args.strict else 2
    return 1 if any(SEVERITY_RANK[f.severity] >= threshold for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
