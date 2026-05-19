---
name: api-cost-optimizer
description: Scan code that calls the Claude API and surface prioritized, concrete cost reductions — prompt caching on stable prefixes, Message Batches for non-interactive work, model right-sizing, max_tokens and streaming tuning, and eliminating redundant calls. Use when the user wants to lower their Anthropic bill, asks why API costs are high, or is reviewing Claude integration code before scaling it up.
---

# API Cost Optimizer

Find where money is being left on the table in code that calls the Claude API, and report fixes ranked by expected savings × ease.

## When to use this

- "Our Anthropic bill is too high — where's it going?"
- "Review this before we turn it on for all users."
- Any Claude integration about to scale from prototype to production volume.

## What to look for

Inspect every call site and the prompts they build. Check, in roughly descending order of typical impact:

1. **Prompt caching not used on a stable prefix.** A long system prompt, tool schema, or document repeated across calls should carry `cache_control` so the prefix is billed at the cached rate. This is usually the single biggest win for high-volume apps. Look for large constant strings concatenated into every request.
2. **Interactive API for non-interactive work.** Bulk/offline jobs (classification, enrichment, evals, backfills) that don't need a synchronous response should use the **Message Batches API**, which is billed at a discount. Flag loops that fire one blocking request per row.
3. **Model oversized for the task.** Opus on extraction/classification/routing that Sonnet or Haiku handles well is pure overspend. Flag the model choice and recommend the smallest model that plausibly fits — paired with an eval (see `eval-harness-scaffolder`) to confirm quality before switching.
4. **`max_tokens` set far above real output length.** It doesn't bill unused tokens, but an oversized ceiling hides runaway generations and breaks cost forecasting. Recommend a ceiling tied to the actual expected output.
5. **Redundant or repeated calls.** The same prompt sent twice, per-item calls that could be one batched prompt, retries without backoff, no memoization of deterministic results.
6. **Unbounded context growth.** Whole files/histories pasted in when a slice would do. Cross-reference `context-budget-planner` and `token-usage-estimator`.
7. **Missing stop conditions / streaming.** No stop sequences where output has a natural terminator; no streaming where early-exit on the consumer side could cut generated tokens.
8. **Re-sending tool definitions or system prompts that never change** without caching them.

## Output format

```
API COST REVIEW

Summary: <biggest lever in one line, with a rough magnitude if estimable>

Findings (ranked by savings × ease)
  [P1] <title>  — file:line
       Now:    <what the code does, with a short snippet>
       Change: <the fix, as a code snippet or precise instruction>
       Why:    <mechanism of the saving + rough size: order-of-magnitude, %, or "verify with token-usage-estimator">
  [P2] ...

Validate before shipping
  <which changes need an eval or A/B to confirm quality is preserved>
```

## Rules of engagement

- **Cite file:line and show the code.** No generic advice that isn't tied to this codebase.
- **Quantify when possible, label when not.** Use `token-usage-estimator` for concrete per-call numbers; otherwise give an order-of-magnitude and say it's an estimate.
- **Never trade silent quality loss for savings.** Any model downgrade or context trim is paired with an explicit "validate this with an eval" note.
- **Rank by impact.** Caching a 20k-token prefix on a hot path beats shaving `max_tokens` — order the report that way.
- **Verify pricing assumptions** against current published Anthropic rates before quoting dollar figures.
