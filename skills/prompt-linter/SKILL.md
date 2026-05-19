---
name: prompt-linter
description: Audit a prompt or instruction set for the issues that make LLM output unreliable — vague task, missing context, no output spec, conflicting rules, untestable success criteria — and return scored findings with concrete rewrites. Use when the user shares a prompt and asks for review, says their prompt "isn't working," or wants a prompt tightened before it goes into production.
---

# Prompt Linter

A structured review pass for prompts. The goal is not style polish — it is to find the specific weaknesses that cause an LLM to produce inconsistent, off-target, or unverifiable output, and to propose exact fixes.

## When to use this

- "Review this prompt."
- "Why is this prompt giving inconsistent results?"
- "Tighten this before we ship it."
- Any time a prompt is about to be run at scale (cost and quality both compound).

## The rubric

Score each dimension **Pass / Weak / Fail** and justify the score with a direct quote from the prompt.

1. **Role & framing** — Is the model's role and the task's domain stated? A prompt that opens with the task and no framing usually underperforms one that sets context first.
2. **Task specificity** — Is the actual ask unambiguous? Flag verbs like "handle," "process," "improve," "make better" with no definition of done.
3. **Context sufficiency** — Does the prompt include everything the model needs, or does it assume knowledge it never provides (a schema, prior turn, file, domain convention)?
4. **Output contract** — Is the exact output shape specified: format, fields, length, ordering, what to do on empty/error? Unspecified output is the most common cause of "inconsistent" results.
5. **Examples** — For non-trivial formatting or judgment tasks, is there at least one worked example? Note where one is missing and would disproportionately help.
6. **Constraints & guardrails** — Are limits explicit (length, scope, "do not invent," what to do when unsure)?
7. **Conflicts & redundancy** — Do any instructions contradict each other or repeat in ways that dilute priority? Quote both sides of any conflict.
8. **Testability** — Could you write a pass/fail check for the output from the prompt alone? If not, the success criteria are underspecified.
9. **Robustness to input variation** — If the input is empty, huge, malformed, or adversarial, does the prompt say what to do?

## Output format

```
PROMPT LINT REPORT

Overall: <one-line verdict + the single highest-leverage fix>

Scores
  Role & framing            [Pass|Weak|Fail]  — "<quote>" → <why>
  Task specificity          [Pass|Weak|Fail]  — ...
  ... (all 9 dimensions)

Top fixes (ranked by impact)
  1. <problem> → <concrete rewrite, shown as before/after>
  2. ...

Revised prompt
  <full rewritten prompt incorporating the accepted fixes>
```

## Rules of engagement

- **Always quote.** Every finding cites the specific text it refers to. No vague "could be clearer."
- **Rank by impact, not by order.** A missing output contract outranks a stylistic nit every time.
- **Show, don't tell.** Each fix includes the rewritten text, not just a description of what to change.
- **Preserve intent.** The revised prompt must do what the author wanted — surface assumptions instead of silently changing the goal.
- **Don't pad.** If the prompt is genuinely solid, say so and stop. A short clean report is a valid outcome.
