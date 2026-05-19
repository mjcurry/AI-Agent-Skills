---
name: eval-harness-scaffolder
description: Generate a minimal, runnable evaluation suite — test cases, a runner, and scoring — so prompt and model changes become measurable instead of guesswork. Use when the user is iterating on a prompt, considering a model switch, asking "did that change make it better," or wants regression coverage for an LLM feature before shipping it.
---

# Eval Harness Scaffolder

Prompt and model changes are unfalsifiable without an eval. This skill drops a small, honest harness into a project so "is this better?" becomes a number instead of a vibe.

## When to use this

- The user is tuning a prompt and judging it by eyeballing a few outputs.
- A model swap is on the table (cost or quality) and needs a before/after.
- An LLM feature is about to ship with zero regression coverage.

## What it scaffolds

Copy `templates/` into the target project (suggested: `evals/`) and adapt:

- **`cases.jsonl`** — one JSON object per line: an `input`, the `scorer` to use, and the expectation. Start with 5–15 cases that cover the golden path, the known failure modes, and the edge cases the user actually cares about.
- **`run_eval.py`** — runs every case against the Claude API, scores it, prints a pass-rate summary, and can write a JSON report for diffing across runs. Standard library plus `anthropic`.
- **`README.md`** — how to run it and how to add cases.

## Scorers available

| `scorer` | Passes when | Use for |
|---|---|---|
| `exact` | normalized output == `expect` | deterministic, single-answer tasks |
| `contains` | `expect` substring in output | "must mention X" |
| `regex` | `expect` pattern matches output | format/shape checks |
| `judge` | an LLM judge rules PASS against `rubric` | open-ended quality where there's no string answer |

## How to drive it

1. **Write cases from real failure modes, not toy inputs.** Ask the user what actually goes wrong; encode those as cases first. A passing eval that doesn't exercise the real risk is worse than none.
2. **Establish a baseline before changing anything.** Run it, record the pass rate, *then* iterate. Always quote before/after.
3. **Prefer cheap scorers.** Reach for `judge` only when no string check captures correctness — it costs an extra call per case and the judge itself can be wrong; keep its rubric narrow and binary.
4. **Keep the suite fast and cheap.** Use `--limit` while iterating; estimate the full run's cost with `token-usage-estimator` before scaling case count.
5. **Treat it as a regression gate.** Commit `cases.jsonl`; re-run on every prompt or model change and don't ship a drop without an explicit reason.

## Honest limitations

- An eval is only as good as its cases — a green suite means "didn't regress *these*," never "correct in general." Say this when reporting results.
- `judge` scoring inherits the judge model's blind spots; spot-check a sample of judge verdicts by hand.
- Small suites have noisy pass rates. Don't over-read a one-case swing.
