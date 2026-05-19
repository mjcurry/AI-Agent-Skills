# AI Agent Skills

A curated collection of reusable **agent skills** - focused, composable capabilities that make an AI coding agent measurably more useful day to day.

Each skill is a self-contained directory with a `SKILL.md` (the instructions an agent loads on demand) plus any supporting scripts. Skills follow the progressive-disclosure model: the agent reads the short description first, and only pulls in the full instructions and tooling when the task actually calls for it.

## Why this exists

Most "AI prompt" collections are a wall of clever one-liners. This repo takes the opposite stance: a small set of skills, each one solving a real, recurring engineering problem — estimating cost before a large run, catching a vague prompt before it wastes a round-trip, keeping context under budget, scaffolding evals so changes are measurable. The bar for inclusion is that I reach for it on actual work.

## Catalog

| Skill | What it does | Ships with |
|---|---|---|
| [`token-usage-estimator`](skills/token-usage-estimator) | Estimate token counts and per-model API cost for text or files before you run them. Heuristic by default, exact counts when an API key is available. | `scripts/estimate_tokens.py` |
| [`prompt-linter`](skills/prompt-linter) | Audit a prompt against a structured rubric — role clarity, task specificity, output spec, ambiguity, conflicts — and return scored findings with concrete rewrites. | rubric only |
| [`api-cost-optimizer`](skills/api-cost-optimizer) | Scan code that calls the Claude API and surface prioritized cost wins: prompt caching, batching, model right-sizing, `max_tokens` tuning. | rubric only |
| [`context-budget-planner`](skills/context-budget-planner) | Given a set of files and a token budget, decide what fits and what to drop, with a ranked include/exclude report. | `scripts/plan_context.py` |
| [`eval-harness-scaffolder`](skills/eval-harness-scaffolder) | Generate a minimal, runnable eval suite (cases + runner + scoring) so prompt and model changes become measurable instead of vibes. | `templates/` |
| [`changelog-from-git`](skills/changelog-from-git) | Turn `git log` between two refs into clean [Keep a Changelog](https://keepachangelog.com) markdown, conventional-commit aware. | `scripts/changelog.py` |

## Using these skills

Clone the repo, then wire up whichever skills you want:

```bash
git clone https://github.com/mjcurry/AI-Agent-Skills.git
```

### With an agent (Claude Code and similar)

Copy a skill directory into a skills path the agent reads:

```bash
# user-level (available everywhere)
cp -r skills/token-usage-estimator ~/.claude/skills/

# or project-level (scoped to one repo)
cp -r skills/token-usage-estimator .claude/skills/
```

The agent will surface the skill by its `description` and load the full `SKILL.md` only when a task matches. Nothing is loaded into context until it's needed.

### Standalone

The scripts are plain Python with no required third-party dependencies for their default modes, so they're useful on their own:

```bash
python skills/token-usage-estimator/scripts/estimate_tokens.py README.md --model sonnet
python skills/context-budget-planner/scripts/plan_context.py "src/**/*.py" --budget 60000
python skills/changelog-from-git/scripts/changelog.py v1.0.0 HEAD
```

Run any script with `--help` for full options.

## Design principles

- **One job each.** A skill that does two things is two skills.
- **Cheap to ignore.** The `description` is enough for an agent to decide relevance without loading the body.
- **Honest defaults.** Estimators say when they're approximating and how to get exact numbers. Pricing tables are clearly marked to verify against current published rates.
- **Degrade gracefully.** Default modes need only the standard library. API-backed precision is opt-in, never required.
- **No magic.** Every script is readable in one sitting and does exactly what the `SKILL.md` says.

## Repository layout

```
skills/
  <skill-name>/
    SKILL.md          # frontmatter (name, description) + instructions
    scripts/ | templates/   # optional supporting files
```

## Contributing

Issues and PRs welcome. A new skill should earn its place: it must solve a recurring problem, do one thing, and degrade gracefully without network or API access. Keep the `SKILL.md` description tight — it's the part an agent reads to decide whether to load anything else.

## Author

Built and maintained by [Mike Curry](https://github.com/mjcurry).

## License

MIT © Mike Curry — see [LICENSE](LICENSE).
