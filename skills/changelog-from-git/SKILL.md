---
name: changelog-from-git
description: Turn git history between two refs into clean, grouped Keep a Changelog markdown, conventional-commit aware. Use when the user is cutting a release, asks for a changelog or release notes, wants to summarize what changed since a tag, or needs commit history rewritten into something a human can read.
---

# Changelog from Git

Release notes assembled by hand drift from what actually shipped. This generates the changelog straight from commits, grouped the way [Keep a Changelog](https://keepachangelog.com) expects.

## When to use this

- Cutting a release and need notes for the tag / GitHub release.
- "What changed since v1.4.0?"
- Converting a messy commit range into a readable summary.

## How to run it

Standard library, must be run inside the git repo:

```bash
python scripts/changelog.py <from-ref> [<to-ref>] [options]
```

| Option | Effect |
|---|---|
| `<from-ref> <to-ref>` | Range to summarize. `to-ref` defaults to `HEAD`. |
| `--version LABEL` | Heading version, e.g. `1.5.0` (default: `Unreleased`). |
| `--date YYYY-MM-DD` | Heading date (default: the commit date of `<to-ref>`, so cutting a historical tag gets the right date). |
| `--repo-url URL` | Base repo URL; short hashes become commit links. |
| `--include-all` | Also include `chore/test/ci/build/style` commits (default: dropped). |
| `--strict` | Drop commits that don't follow Conventional Commits instead of bucketing them under "Other". |

Examples:

```bash
python scripts/changelog.py v1.4.0 HEAD --version 1.5.0
python scripts/changelog.py v1.4.0 --repo-url https://github.com/acme/widget
```

## Type → section mapping

| Commit type | Changelog section |
|---|---|
| `feat` | Added |
| `fix` | Fixed |
| `perf`, `refactor` | Changed |
| `docs` | Documentation |
| `revert` | Removed |
| `!` / `BREAKING CHANGE` | flagged **BREAKING** at the top of its section |
| `chore`/`test`/`ci`/`build`/`style` | omitted unless `--include-all` |
| anything else | Other (omitted under `--strict`) |

## How to use the result

1. Print the generated markdown for the requested range.
2. **Treat it as a strong draft, not final copy.** Commit subjects are written for other developers; before publishing, rewrite user-facing entries in user language and lead with the highest-impact change.
3. Call out every **BREAKING** entry explicitly to the user — those need a migration note the commits won't contain.
4. If the range has no commits, or none survive filtering, say so plainly rather than emitting an empty section.

## Notes

- Quality tracks commit hygiene: a repo with clean Conventional Commits gets a clean changelog; a noisy history gets a noisy draft (that's a signal worth mentioning to the user).
- The script only reads git history — it never writes tags, commits, or files.
