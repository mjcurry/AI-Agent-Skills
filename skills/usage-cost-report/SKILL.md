---
name: usage-cost-report
description: Turn a JSONL log of API calls (model + token usage, optionally timestamp and tag) into a cost breakdown by model, day, and feature, including what prompt caching actually saved. Use when the user asks where their API spend is going, wants a cost report from logs, needs to attribute spend to features or customers, or wants to check whether caching is paying off.
---

# Usage Cost Report

`token-usage-estimator` forecasts what a run *will* cost. This is the other half: given a log of what actually ran, where did the money go — by model, by day, by feature — and how much did prompt caching save?

## When to use this

- "Our bill went up — which feature did it?"
- Attributing spend to tags, customers, or endpoints from application logs.
- Checking whether a caching change actually reduced cost.
- Producing a cost table for a weekly report.

## How to run it

Standard library only:

```bash
python scripts/usage_report.py usage.jsonl
python scripts/usage_report.py logs/*.jsonl --top 25
cat usage.jsonl | python scripts/usage_report.py - --json
```

| Option | Effect |
|---|---|
| `--pricing FILE` | Per-tier pricing override (same JSON format as `token-usage-estimator`, plus optional `cache_read` / `cache_write` per-MTok rates). |
| `--top N` | Rows per breakdown table (default 15). |
| `--json` | Machine-readable totals and breakdowns. |

## Input format

One JSON object per line. Token fields are read from the top level or from a nested `usage` object — the shape the Messages API returns — so you can log the response's `usage` block as-is:

```json
{"timestamp": "2026-09-01T14:02:11Z", "model": "claude-sonnet-5", "tag": "support-bot",
 "usage": {"input_tokens": 1200, "output_tokens": 340, "cache_read_input_tokens": 900}}
```

- `model` is matched to a pricing tier by substring (`opus` / `sonnet` / `haiku`).
- `tag` (or `label` / `feature` / `endpoint` / `route`) drives the per-feature breakdown.
- `timestamp` (ISO) drives the per-day breakdown. Both are optional.
- `input_tokens` is priced as uncached input; `cache_read_input_tokens` and `cache_creation_input_tokens` are priced at the cache read/write rates. That matches how the API reports them — `input_tokens` already excludes cached tokens.

## How to report results

1. Lead with the total and the single biggest bucket (model or tag) — that's the lever.
2. Show the caching line explicitly: what was saved, and if `cache read` is near zero on a high-volume tag, say that's the first thing to fix (see `api-cost-optimizer`).
3. Any model that fell into "unknown tier, priced at $0" is a gap in the report, not a free model — call it out and offer a `--pricing` entry.
4. Cost figures depend on the pricing table; state that they're computed from last-known rates and should be verified against the bill.

## Honest limitations

- Only as complete as the log. If some calls aren't logged, the report undercounts — say so.
- Tier matching by substring means a custom model alias with no tier name in it will be unpriced until you add it to `--pricing`.
- Pricing constants and cache multipliers are marked to verify; treat dollar figures as estimates until reconciled with the invoice.
