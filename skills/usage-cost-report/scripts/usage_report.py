#!/usr/bin/env python3
"""Turn a JSONL log of API usage into a cost breakdown by model, day, and tag.

The companion to token-usage-estimator: that one forecasts before a run,
this one reports what actually happened. Standard library only.

Each line is a JSON object. Token fields are read from the top level or from
a nested "usage" object (the shape the Messages API returns):

  {"timestamp": "2026-09-01T14:02:11Z", "model": "claude-sonnet-5",
   "tag": "support-bot",
   "usage": {"input_tokens": 1200, "output_tokens": 340,
             "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}}

Note: in API responses, input_tokens already EXCLUDES cached tokens — the
cache_* fields are separate buckets, and this script prices them that way.

Pricing is per tier (opus / sonnet / haiku, matched by substring of the model
ID). Override with --pricing pricing.json, same format as token-usage-estimator
plus optional "cache_read" / "cache_write" per-MTok rates:

  {"sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}}
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

# USD per 1,000,000 tokens. VERIFY against current published Anthropic pricing.
# Cache multipliers are the published defaults: reads at 10% of the input
# rate, writes at 125%. Override any of these with --pricing.
CACHE_READ_MULT = 0.10
CACHE_WRITE_MULT = 1.25

DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "opus": {"input": 15.0, "output": 75.0},
    "sonnet": {"input": 3.0, "output": 15.0},
    "haiku": {"input": 1.0, "output": 5.0},
}

TAG_FIELDS = ("tag", "label", "feature", "endpoint", "route")


@dataclass
class Bucket:
    calls: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    savings: float = 0.0  # what cache reads would have cost at full price, minus paid

    def add(self, other: "Bucket") -> None:
        for k in ("calls", "input", "output", "cache_read", "cache_write", "cost", "savings"):
            setattr(self, k, getattr(self, k) + getattr(other, k))


def load_pricing(path: str | None) -> dict[str, dict[str, float]]:
    pricing = {k: dict(v) for k, v in DEFAULT_PRICING.items()}
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            sys.exit(f"--pricing: cannot read {path}: {exc}")
        if not isinstance(data, dict):
            sys.exit("--pricing: top level must be an object keyed by tier")
        for tier, spec in data.items():
            if not isinstance(spec, dict):
                sys.exit(f"--pricing: entry '{tier}' must be an object")
            pricing.setdefault(tier, {"input": 0.0, "output": 0.0})
            for k in ("input", "output", "cache_read", "cache_write"):
                if k in spec:
                    try:
                        pricing[tier][k] = float(spec[k])
                    except (TypeError, ValueError):
                        sys.exit(f"--pricing: '{tier}.{k}' must be a number")
    for spec in pricing.values():
        spec.setdefault("cache_read", spec["input"] * CACHE_READ_MULT)
        spec.setdefault("cache_write", spec["input"] * CACHE_WRITE_MULT)
    return pricing


def tier_of(model: str, pricing: dict) -> str | None:
    m = (model or "").lower()
    for tier in pricing:
        if tier in m:
            return tier
    return None


def parse_record(rec: dict) -> tuple[str, dict[str, int], str | None, str | None]:
    usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else rec
    tokens = {}
    for key, aliases in (
        ("input", ("input_tokens", "prompt_tokens")),
        ("output", ("output_tokens", "completion_tokens")),
        ("cache_read", ("cache_read_input_tokens",)),
        ("cache_write", ("cache_creation_input_tokens",)),
    ):
        val = next((usage[a] for a in aliases if a in usage), 0)
        tokens[key] = int(val or 0)
    ts = rec.get("timestamp") or rec.get("created_at") or rec.get("time")
    day = str(ts)[:10] if ts else None
    tag = next((str(rec[f]) for f in TAG_FIELDS if rec.get(f) is not None), None)
    return str(rec.get("model", "")), tokens, day, tag


def price(tokens: dict[str, int], spec: dict[str, float] | None) -> tuple[float, float]:
    if spec is None:
        return 0.0, 0.0
    m = 1_000_000
    cost = (tokens["input"] / m * spec["input"]
            + tokens["output"] / m * spec["output"]
            + tokens["cache_read"] / m * spec["cache_read"]
            + tokens["cache_write"] / m * spec["cache_write"])
    savings = tokens["cache_read"] / m * (spec["input"] - spec["cache_read"])
    return cost, savings


def read_lines(paths: list[str]):
    for p in paths:
        fh = sys.stdin if p == "-" else open(p, "r", encoding="utf-8")
        with fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if line:
                    yield p, n, line


def fmt_usd(v: float) -> str:
    return f"${v:,.4f}" if v >= 0.01 or v == 0 else f"${v:.6f}"


def render_group(title: str, groups: dict[str, Bucket], top: int) -> list[str]:
    lines = [title, f"  {'':<28}{'calls':>8}{'cost':>14}{'input':>12}{'output':>10}"
             f"{'cached':>10}"]
    ranked = sorted(groups.items(), key=lambda kv: -kv[1].cost)
    for key, b in ranked[:top]:
        name = key if len(key) <= 28 else "…" + key[-27:]
        lines.append(f"  {name:<28}{b.calls:>8,}{fmt_usd(b.cost):>14}"
                     f"{b.input:>12,}{b.output:>10,}{b.cache_read:>10,}")
    if len(ranked) > top:
        lines.append(f"  … {len(ranked) - top} more (raise --top)")
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Cost breakdown from a JSONL usage log.")
    p.add_argument("files", nargs="+", help="JSONL file(s), or '-' for stdin.")
    p.add_argument("--pricing", metavar="FILE", help="Per-tier pricing override JSON.")
    p.add_argument("--top", type=int, default=15, help="Rows per breakdown (default 15).")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    pricing = load_pricing(args.pricing)
    total = Bucket()
    by_model: dict[str, Bucket] = {}
    by_day: dict[str, Bucket] = {}
    by_tag: dict[str, Bucket] = {}
    unknown_models: dict[str, int] = {}
    bad_lines = 0

    for path, n, line in read_lines(args.files):
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
            model, tokens, day, tag = parse_record(rec)
        except (ValueError, TypeError):
            bad_lines += 1
            continue
        tier = tier_of(model, pricing)
        if tier is None:
            unknown_models[model or "<missing>"] = unknown_models.get(model or "<missing>", 0) + 1
        cost, savings = price(tokens, pricing.get(tier) if tier else None)
        b = Bucket(1, tokens["input"], tokens["output"], tokens["cache_read"],
                   tokens["cache_write"], cost, savings)
        total.add(b)
        by_model.setdefault(model or "<missing>", Bucket()).add(b)
        if day:
            by_day.setdefault(day, Bucket()).add(b)
        if tag:
            by_tag.setdefault(tag, Bucket()).add(b)

    if total.calls == 0:
        print("No usable records found.", file=sys.stderr)
        return 1

    if args.json:
        def dump(groups):
            return {k: v.__dict__ for k, v in groups.items()}
        print(json.dumps({
            "total": total.__dict__,
            "by_model": dump(by_model),
            "by_day": dump(by_day),
            "by_tag": dump(by_tag),
            "unknown_models": unknown_models,
            "skipped_lines": bad_lines,
        }, indent=2))
        return 0

    out = [f"Usage report — {total.calls:,} calls",
           f"Total cost: {fmt_usd(total.cost)}   "
           f"(prompt caching saved {fmt_usd(total.savings)})",
           f"Tokens: input {total.input:,} · output {total.output:,} · "
           f"cache read {total.cache_read:,} · cache write {total.cache_write:,}", ""]
    out += render_group("By model", by_model, args.top)
    if by_day:
        out += render_group("By day", dict(sorted(by_day.items())), args.top)
    if by_tag:
        out += render_group("By tag", by_tag, args.top)
    if unknown_models:
        names = ", ".join(f"{m} ({c})" for m, c in sorted(unknown_models.items()))
        out.append(f"Unknown tier, priced at $0: {names}")
        out.append("  Add them to a --pricing file to include them.")
    if bad_lines:
        out.append(f"Skipped {bad_lines} unparseable line(s).")
    out.append("Verify pricing against current published Anthropic rates.")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
