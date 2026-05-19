---
name: token-usage-estimator
description: Estimate token counts and per-model API cost for text, files, or a planned prompt before running it. Use when the user asks how many tokens something is, how much an API call or batch will cost, whether content fits a context window, or wants to compare model costs for the same workload.
---

# Token Usage Estimator

Estimate how many tokens a piece of content is and what it will cost across Claude models — *before* spending the call.

## When to use this

- "How many tokens is this file / this prompt?"
- "What will it cost to run this over 10k records?"
- "Will this fit in the context window?"
- "Is Haiku cheap enough here, or do we need Sonnet?"

## How to run it

The bundled script needs only the Python standard library for its default (heuristic) mode:

```bash
python scripts/estimate_tokens.py <path-or-->  [options]
```

| Option | Effect |
|---|---|
| `--model {opus,sonnet,haiku}` | Price against a specific model (default: all three). |
| `--output-tokens N` | Assume `N` output tokens per call when costing (default: 0). |
| `--runs N` | Multiply the cost by `N` calls (e.g. a batch). |
| `--exact` | Use the Anthropic token-counting API for an exact input count. Requires `anthropic` installed and `ANTHROPIC_API_KEY` set. |
| `--json` | Emit machine-readable JSON instead of a table. |

Examples:

```bash
# Heuristic estimate of a file, priced on all models
python scripts/estimate_tokens.py prompt.txt

# Cost of running a 1,200-token prompt with ~400 output tokens, 5,000 times, on Haiku
echo "..." | python scripts/estimate_tokens.py - --model haiku --output-tokens 400 --runs 5000

# Exact input count via the API
python scripts/estimate_tokens.py prompt.txt --exact
```

## How to report results

1. State the token estimate and **whether it is heuristic or exact**. Never present a heuristic as exact.
2. Give the cost per call and the total for the requested number of runs.
3. If the content is close to or over a model's context window, say so explicitly and name the limit.
4. If the user is cost-sensitive, note the cheapest model that plausibly fits the task — but flag that a smaller model may need quality validation (point them at `eval-harness-scaffolder`).

## Accuracy notes

- The heuristic blends a character-based and a word-based estimate. It is typically within ~10–15% for English prose and is rougher for code, non-Latin scripts, and heavy markup. Always label it an estimate.
- For anything where being wrong costs real money (large batches, budget approvals), use `--exact`.
- The pricing table in the script is a constant clearly marked to verify against current published Anthropic pricing. Treat it as "last known," not authoritative — confirm rates before quoting hard numbers to anyone.
