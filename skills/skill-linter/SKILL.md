---
name: skill-linter
description: Lint Agent Skills (SKILL.md files) against the Agent Skills specification — name and description rules, frontmatter YAML pitfalls, body size budgets, broken or nested file references, and files nothing points to. Use when writing or reviewing a skill, before publishing or committing one, when a skill fails to load or never triggers, or to audit a whole skills directory in CI.
---

# Skill Linter

A skill that breaks the spec usually fails silently: it doesn't load, or loads and never triggers, and nothing tells you why. This checks a skill the way a loader would — and then the way an agent would, asking whether the description can actually be matched to a task and whether every bundled file can be found.

## When to use this

- Writing a new skill, or reviewing someone else's, before it's committed or published.
- "My skill isn't being picked up" / "it loads but never fires."
- Auditing a whole `skills/` directory, or gating one in CI.

## How to run it

Standard library only:

```bash
python scripts/lint_skill.py path/to/my-skill      # one skill
python scripts/lint_skill.py skills/               # every SKILL.md underneath
python scripts/lint_skill.py skills/ --strict      # warnings fail the exit code too
python scripts/lint_skill.py skills/ --json
```

Exit code is `1` on any error (or any warning with `--strict`), `2` if no `SKILL.md` was found.

## What it checks

**Errors — the [specification](https://agentskills.io/specification) says "must":**

| Code | Rule |
|---|---|
| `missing-skill-md`, `missing-frontmatter` | A skill is a directory with a `SKILL.md` that opens with YAML frontmatter. |
| `missing-name`, `name-too-long`, `bad-name` | `name` is required, 1–64 characters, lowercase `a-z`, `0-9` and hyphens; no leading, trailing or consecutive hyphens. |
| `name-directory-mismatch` | `name` must equal the parent directory name. |
| `missing-description`, `description-too-long` | `description` is required, 1–1024 characters. |
| `compatibility-too-long`, `bad-metadata`, `bad-allowed-tools` | `compatibility` ≤ 500 characters; `metadata` is a string→string map; `allowed-tools` is one space-separated string. |
| `unquoted-colon`, `frontmatter-syntax`, `duplicate-field` | YAML that a real parser rejects — most often a description containing `: ` without quotes. |
| `broken-link` | A relative link in `SKILL.md` to a file that isn't there. |

**Warnings — the spec says "should", or the value will be mangled:**

| Code | Rule |
|---|---|
| `short-description`, `no-trigger` | The description must say what the skill does *and when to use it*. It is the only thing an agent reads when deciding whether to load the skill. |
| `body-too-long`, `body-too-heavy`, `empty-body` | Keep the body under 500 lines and roughly 5,000 tokens; it all loads on activation. |
| `deep-reference`, `link-outside-skill` | Keep references one level deep, and inside the skill so it survives being copied. |
| `unquoted-hash`, `unquoted-special`, `metadata-non-string` | Unquoted values YAML will truncate at ` #`, parse as syntax, or turn into a number. |

**Info — not in the spec, but they decide whether the skill works:**

| Code | Rule |
|---|---|
| `heavy-metadata` | `name` + `description` load for *every* task; the spec budgets about 100 tokens. |
| `unreferenced-file` | A bundled file `SKILL.md` never mentions is a file no agent will ever open. |
| `reference-chain` | A reference that links onward to a file `SKILL.md` doesn't mention. |
| `unknown-field`, `long-license` | Fields the spec doesn't define may be ignored; put custom data under `metadata`. |

## How to report results

1. Lead with the count by severity and whether the skill would load at all — errors first.
2. For each finding, give the fix as the corrected text: the quoted description, the renamed directory, the added `Use when …` clause.
3. Treat `no-trigger` and `short-description` as the highest-value warnings. A skill that loads but is never selected is as broken as one that doesn't load.
4. If everything is clean, say so and stop.

## Honest limitations

- Frontmatter is read by a small built-in parser covering what skills actually use — scalars, block scalars, one-level maps and lists. Anything beyond that is reported as a syntax problem rather than guessed at; for a second opinion, the spec's reference tool is `skills-ref validate`.
- Token counts are estimates (the same heuristic as `token-usage-estimator`).
- It checks form, not truth: a well-formed description can still describe the wrong thing. Whether a skill *triggers* on real tasks is a question for `eval-harness-scaffolder`.
- A clean lint says nothing about whether a skill is safe to load — that is `hidden-instruction-scanner`'s job.
