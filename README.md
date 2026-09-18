# AI Agent Skills

A curated collection of reusable **agent skills** — focused, composable capabilities that make an AI coding agent measurably more useful day to day.

Each skill is a self-contained directory with a `SKILL.md` (the instructions an agent loads on demand) plus any supporting scripts. Skills follow the progressive-disclosure model: the agent reads the short description first, and only pulls in the full instructions and tooling when the task actually calls for it.

## Why this exists

Most "AI prompt" collections are a wall of clever one-liners. This repo takes the opposite stance: a small set of skills, each one solving a real, recurring engineering problem — estimating cost before a large run and attributing it afterward, catching a vague prompt or a sloppy tool definition before it wastes a round-trip, keeping context under budget, stopping a leaked key at the pre-commit line, scaffolding evals so changes are measurable, knowing what on-prem capacity already exists before reaching for another cloud service. The bar for inclusion is that I reach for it on actual work.

## Catalog

### Cost & tokens

| Skill | What it does | Ships with |
|---|---|---|
| [`token-usage-estimator`](skills/token-usage-estimator) | Estimate token counts and per-tier API cost for text or files *before* you run them. Heuristic by default, exact counts when an API key is available, pricing overridable from a JSON file. | `scripts/estimate_tokens.py` |
| [`usage-cost-report`](skills/usage-cost-report) | The other half: turn a JSONL log of what actually ran into a cost breakdown by model, day, and tag — including what prompt caching saved. | `scripts/usage_report.py` |
| [`api-cost-optimizer`](skills/api-cost-optimizer) | Scan code that calls the Claude API and surface prioritized cost wins: prompt caching, batching, model right-sizing, `max_tokens` tuning. | rubric only |

### Context & prompts

| Skill | What it does | Ships with |
|---|---|---|
| [`context-budget-planner`](skills/context-budget-planner) | Given a set of files and a token budget, decide what fits and what to drop, with a ranked include/exclude report. | `scripts/plan_context.py` |
| [`prompt-linter`](skills/prompt-linter) | Audit a prompt against a structured rubric — role clarity, task specificity, output spec, ambiguity, conflicts — and return scored findings with concrete rewrites. | rubric only |
| [`tool-schema-linter`](skills/tool-schema-linter) | Lint LLM tool/function definitions for the gaps that make models misuse them — missing descriptions, vague params, no `required`, open schemas. Anthropic and OpenAI formats. | `scripts/lint_tools.py` |

### Quality & safety

| Skill | What it does | Ships with |
|---|---|---|
| [`eval-harness-scaffolder`](skills/eval-harness-scaffolder) | Generate a minimal, runnable eval suite (cases + runner + scoring) with baseline comparison, so prompt and model changes become measurable instead of vibes. | `templates/` |
| [`secret-scanner`](skills/secret-scanner) | Catch API keys, tokens, and private keys in files or the staged diff before they're committed. Pattern + entropy based, pre-commit ready. | `scripts/scan_secrets.py` |

### Infrastructure

| Skill | What it does | Ships with |
|---|---|---|
| [`on-prem-infra-discovery`](skills/on-prem-infra-discovery) | Map the on-prem and local infrastructure an agent can use — model servers and GPUs, container and Kubernetes contexts, S3-compatible storage, mirrors, host inventories — and place work there instead of defaulting to public cloud. Read-only, refuses subnet sweeps, never records secrets. | `scripts/discover_infra.py`, `references/` |

### Release tooling

| Skill | What it does | Ships with |
|---|---|---|
| [`changelog-from-git`](skills/changelog-from-git) | Turn `git log` between two refs into clean [Keep a Changelog](https://keepachangelog.com) markdown, conventional-commit aware. This repo's own [CHANGELOG](CHANGELOG.md) is generated with it. | `scripts/changelog.py` |

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
python skills/secret-scanner/scripts/scan_secrets.py --staged
python skills/tool-schema-linter/scripts/lint_tools.py tools.json
python skills/usage-cost-report/scripts/usage_report.py usage.jsonl
python skills/on-prem-infra-discovery/scripts/discover_infra.py --write .agent/infra-inventory.json --if-stale 14
python skills/changelog-from-git/scripts/changelog.py v1.0.0 HEAD
```

Run any script with `--help` for full options.

## Design principles

- **One job each.** A skill that does two things is two skills.
- **Cheap to ignore.** The `description` is enough for an agent to decide relevance without loading the body.
- **Honest defaults.** Estimators say when they're approximating and how to get exact numbers. Pricing lives in constants that are clearly marked to verify — and can be overridden from a file rather than edited in code.
- **Degrade gracefully.** Default modes need only the standard library. API-backed precision is opt-in, never required.
- **No magic.** Every script is readable in one sitting and does exactly what the `SKILL.md` says.
- **Safe to paste.** Anything that reports on secrets masks them; nothing writes to your repo unless the skill says so.

## Repository layout

```
skills/
  <skill-name>/
    SKILL.md                # frontmatter (name, description) + instructions
    scripts/ | templates/   # optional supporting files
    references/             # optional deep-dive docs, loaded only when the task needs them
CHANGELOG.md                # generated by skills/changelog-from-git
```

## Contributing

Issues and PRs welcome. A new skill should earn its place: it must solve a recurring problem, do one thing, and degrade gracefully without network or API access. Keep the `SKILL.md` description tight — it's the part an agent reads to decide whether to load anything else.

## Author

Built and maintained by [Mike Curry](https://github.com/mjcurry).

## License

MIT © Mike Curry — see [LICENSE](LICENSE).
