---
name: context-budget-planner
description: Given a set of files and a token budget, decide what to include in an agent's context and what to drop, returning a ranked include/exclude report with running totals. Use when assembling context for a task would overflow the window, when the user asks "what should I show the model," or when trimming a large set of files down to fit a budget.
---

# Context Budget Planner

Deciding *what not to send* is as important as the prompt itself. This skill estimates the token cost of a candidate file set and produces a defensible include/exclude plan that fits a budget.

## When to use this

- The files relevant to a task clearly won't all fit in the window.
- "Which of these should I actually put in context?"
- Preparing a focused context bundle before a large or expensive run.

## How to run it

Standard library only:

```bash
python scripts/plan_context.py "<glob>" ["<glob>" ...] --budget <tokens> [options]
```

| Option | Effect |
|---|---|
| `--budget N` | Token budget to fit within (required). |
| `--order {smallest,largest,given}` | Selection order. `smallest` (default) maximizes how many files fit; `given` preserves argument/glob order; `largest` front-loads big files. |
| `--priority-file FILE` | A file of paths (one per line, most important first). Listed files are considered before everything else, in that order. |
| `--reserve N` | Hold back `N` tokens for the prompt/instructions and response (default: 0). |
| `--json` | Machine-readable output. |

Examples:

```bash
# Fit as many source files as possible into 60k tokens, keeping 8k for the prompt
python scripts/plan_context.py "src/**/*.py" --budget 60000 --reserve 8000

# Honor an explicit relevance ranking
python scripts/plan_context.py "**/*.md" --budget 30000 --priority-file relevant.txt
```

## How to use the result

1. Lead with the verdict: how many of N files fit, total tokens used vs. budget.
2. Show the **included** set with running totals, then the **excluded** set with what each would have cost.
3. For excluded-but-important files, suggest the fallback: summarize instead of inline, reference by path, or split the task.
4. If even the highest-priority single file blows the budget, say so plainly and recommend chunking or a summary pass rather than silently truncating.

## Notes

- Token counts are heuristic (same blended estimator as `token-usage-estimator`) — within ~10-15% for text, rougher for dense code or non-Latin scripts. Good enough for planning; use `token-usage-estimator --exact` if a precise pre-flight number is needed.
- Likely-binary files (null bytes in the head) are skipped and reported separately rather than silently estimated.
- The planner never edits or sends anything — it only produces a plan for you to act on.
