---
name: tool-schema-linter
description: Lint LLM tool and function definitions (Anthropic input_schema or OpenAI function-calling format) for the gaps that make models call them wrong — missing or vague descriptions, untyped or generically named parameters, no required list, open object schemas. Use when the user is defining tools for an agent, debugging why a model picks the wrong tool or passes bad arguments, or reviewing a tools JSON before shipping.
---

# Tool Schema Linter

A model decides which tool to call — and with what — from nothing but the name, description, and schema. When those are thin, you get wrong tool choice, invented arguments, and silent misuse. This linter catches the common causes before they reach production.

## When to use this

- Writing or reviewing a set of tool definitions for an agent.
- "The model keeps calling the wrong tool" / "it passes garbage arguments."
- A tools JSON is about to ship and nobody has looked at it as the model would.

## How to run it

Standard library only. Accepts a JSON array of tools or an object with a `tools` key, in either Anthropic (`name` / `description` / `input_schema`) or OpenAI (`type: "function"`, `function: {…, parameters}`) shape:

```bash
python scripts/lint_tools.py tools.json
python scripts/lint_tools.py tools.json --strict   # warnings also fail the exit code
python scripts/lint_tools.py - --json < tools.json
```

## What it checks

| Code | Severity | Why it matters |
|---|---|---|
| `missing-name`, `bad-name`, `duplicate-name` | error | The model can't address the tool reliably. |
| `missing-description` | error | Nothing to decide with — the tool is effectively invisible. |
| `missing-schema`, `schema-not-object`, `required-unknown-property`, `bad-param` | error | Structurally invalid; the API or the model will choke. |
| `short-description`, `description-repeats-name` | warn | "Search" tells the model nothing about *when* or *what it returns*. |
| `no-required-list` | warn | Everything optional usually means the author forgot — the model will omit things you needed. |
| `param-no-description`, `param-no-type` | warn | The model guesses semantics and format. |
| `param-vague-name` | warn | `data`, `value`, `input` — name what it actually holds. |
| `param-object-no-properties`, `param-array-no-items` | warn | Shape is unspecified; expect invented structure. |
| `param-consider-enum` | info | A `mode`/`status`/`format` string without an enum invites made-up values. |
| `additional-properties-open` | info | The model may add keys you never handle. |
| `many-params` | info | Over ~8 parameters, consider splitting the tool. |

Exit code is `1` on any error (or on warnings too with `--strict`), so it drops into CI.

## How to report results

1. Lead with the count by severity and the single tool in the worst shape.
2. For each finding, show the fix as the corrected JSON snippet — a rewritten description, an added `enum`, a filled-in `required` — not just the rule name.
3. Good descriptions answer three things: what the tool does, **when to use it (and when not to)**, and what it returns. Rewrite to that template.
4. If the definitions are clean, say so and stop.

## Honest limitations

- It checks structure and the obvious semantic gaps; it can't judge whether a well-formed description is *accurate*. Pair it with `eval-harness-scaffolder` cases that exercise tool selection.
- Heuristic name checks (`vague`, `enum-ish`) are conservative lists, not a taxonomy — treat those as prompts to think, not verdicts.
