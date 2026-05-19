#!/usr/bin/env python3
"""Estimate token counts and Claude API cost for text or files.

Default mode is heuristic and needs only the standard library. Pass --exact
for an API-backed exact input count (requires the `anthropic` package and
ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass

# --- Pricing & limits --------------------------------------------------------
# USD per 1,000,000 tokens. VERIFY against current published Anthropic pricing
# before quoting hard numbers — these are "last known" constants, not a feed.
@dataclass(frozen=True)
class Model:
    key: str
    label: str
    input_per_mtok: float
    output_per_mtok: float
    context_window: int


MODELS: dict[str, Model] = {
    "opus": Model("opus", "Claude Opus 4.x", 15.0, 75.0, 200_000),
    "sonnet": Model("sonnet", "Claude Sonnet 4.x", 3.0, 15.0, 200_000),
    "haiku": Model("haiku", "Claude Haiku 4.x", 1.0, 5.0, 200_000),
}


def heuristic_tokens(text: str) -> int:
    """Blend a char-based and word-based estimate.

    Claude's tokenizer averages roughly 3.5-4 characters per token for English
    prose; a word is a touch over one token. Averaging the two estimators is
    more stable across prose, code, and markup than either alone. Still an
    estimate — typically within ~10-15% for English, rougher otherwise.
    """
    if not text:
        return 0
    char_estimate = len(text) / 3.8
    words = re.findall(r"\S+", text)
    word_estimate = len(words) * 1.3
    return max(1, round((char_estimate + word_estimate) / 2))


def exact_tokens(text: str) -> int:
    """Exact input token count via the Anthropic token-counting API."""
    try:
        import anthropic
    except ImportError:
        sys.exit(
            "--exact needs the `anthropic` package: pip install anthropic"
        )
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    resp = client.messages.count_tokens(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": text}],
    )
    return resp.input_tokens


def cost(model: Model, in_tokens: int, out_tokens: int, runs: int) -> float:
    per_run = (
        in_tokens / 1_000_000 * model.input_per_mtok
        + out_tokens / 1_000_000 * model.output_per_mtok
    )
    return per_run * runs


def read_input(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    with open(source, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def fmt_usd(amount: float) -> str:
    if amount and amount < 0.01:
        return f"${amount:.6f}"
    return f"${amount:,.4f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Estimate token counts and Claude API cost.",
        epilog="Pass '-' as the path to read from stdin.",
    )
    parser.add_argument("path", help="File to measure, or '-' for stdin.")
    parser.add_argument(
        "--model",
        choices=sorted(MODELS),
        help="Price against one model (default: all).",
    )
    parser.add_argument(
        "--output-tokens",
        type=int,
        default=0,
        help="Assumed output tokens per call (default: 0).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of calls, e.g. a batch size (default: 1).",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="Exact input count via the Anthropic API.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of a table."
    )
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be >= 1")
    if args.output_tokens < 0:
        parser.error("--output-tokens must be >= 0")

    text = read_input(args.path)
    in_tokens = exact_tokens(text) if args.exact else heuristic_tokens(text)
    method = "exact (API)" if args.exact else "heuristic (~10-15%)"

    chosen = (
        [MODELS[args.model]] if args.model else list(MODELS.values())
    )

    rows = []
    for m in chosen:
        total_in = in_tokens * args.runs
        total_out = args.output_tokens * args.runs
        rows.append(
            {
                "model": m.label,
                "input_tokens": in_tokens,
                "output_tokens_per_run": args.output_tokens,
                "runs": args.runs,
                "cost_per_run_usd": round(
                    cost(m, in_tokens, args.output_tokens, 1), 6
                ),
                "total_cost_usd": round(
                    cost(m, in_tokens, args.output_tokens, args.runs), 6
                ),
                "fits_context": (in_tokens + args.output_tokens)
                <= m.context_window,
                "context_window": m.context_window,
                "_total_in": total_in,
                "_total_out": total_out,
            }
        )

    if args.json:
        print(
            json.dumps(
                {"method": method, "estimates": rows},
                indent=2,
                default=str,
            )
        )
        return 0

    print(f"Input tokens: {in_tokens:,}  ({method})")
    if args.runs > 1:
        print(f"Runs: {args.runs:,}   Output tokens/run: {args.output_tokens:,}")
    print()
    print(f"{'Model':<20}{'Cost/run':>14}{'Total':>16}{'Fits ctx':>12}")
    print("-" * 62)
    for r in rows:
        fits = "yes" if r["fits_context"] else "NO — over limit"
        print(
            f"{r['model']:<20}"
            f"{fmt_usd(r['cost_per_run_usd']):>14}"
            f"{fmt_usd(r['total_cost_usd']):>16}"
            f"{fits:>12}"
        )
    print()
    print("Heuristic mode is an estimate; use --exact for batch/budget calls.")
    print("Verify pricing against current published Anthropic rates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
