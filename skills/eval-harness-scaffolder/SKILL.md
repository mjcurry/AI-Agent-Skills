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
- **`run_eval.py`** — runs every case against the Claude API, scores it, prints a pass-rate summary, and writes a JSON report. Pass a previous report as `--baseline` and it names exactly which cases regressed or improved. Standard library plus `anthropic`.
- **`README.md`** — how to run it and how to add cases.

## Scorers available

| `scorer` | Passes when | Use for |
|---|---|---|
| `exact` | normalized output == `expect` | deterministic, single-answer tasks |
| `contains` | `expect` substring in output | "must mention X" |
| `regex` | `expect` pattern matches output | format/shape checks |
| `judge` | an LLM judge rules PASS against `rubric` | open-ended quality where there's no string answer |

## Runner options

| Option | Effect |
|---|---|
| `--cases FILE` | Cases file (default `cases.jsonl`). |
| `--system FILE` | System prompt to test. |
| `--model ID`, `--judge-model ID` | Model under test / model used for `judge` scoring. |
| `--limit N` | First N cases only — for fast iteration. |
| `--workers N` | Run N cases concurrently (default 1; mind your rate limit). |
| `--baseline REPORT` | A previous `--out` report; prints regressions, improvements, new cases, and the pass-rate delta. |
| `--out FILE`, `--json` | Write / print the JSON report. |

## How to drive it

1. **Write cases from real failure modes, not toy inputs.** Ask the user what actually goes wrong; encode those as cases first. A passing eval that doesn't exercise the real risk is worse than none.
2. **Establish a baseline before changing anything.** Run with `--out runs/baseline.json`, *then* iterate, and re-run with `--baseline runs/baseline.json`. The report names each regressed case by id — quote those, not just the aggregate.
3. **Prefer cheap scorers.** Reach for `judge` only when no string check captures correctness — it costs an extra call per case and the judge itself can be wrong; keep its rubric narrow and binary.
4. **Keep the suite fast and cheap.** Use `--limit` while iterating and `--workers` for the full run; estimate the full run's cost with `token-usage-estimator` before scaling case count.
5. **Treat it as a regression gate.** Commit `cases.jsonl`; re-run on every prompt or model change and don't ship a drop without an explicit reason.

## Honest limitations

- An eval is only as good as its cases — a green suite means "didn't regress *these*," never "correct in general." Say this when reporting results.
- `judge` scoring inherits the judge model's blind spots; spot-check a sample of judge verdicts by hand.
- Small suites have noisy pass rates. Don't over-read a one-case swing.
