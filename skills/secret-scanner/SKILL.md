---
name: secret-scanner
description: Scan files, directories, or the staged git diff for API keys, tokens, private keys, and passwords before they get committed. Use before any commit that touches config or code an agent generated, when the user asks "did I leak a key," when setting up a pre-commit hook, or when reviewing a repo for accidentally committed credentials.
---

# Secret Scanner

Agents write config files and example code all day, and the fastest way to burn an API key is to commit one. This scans for credentials *before* they land — as a pre-commit gate on the staged diff, or across a whole tree after the fact.

## When to use this

- Right before committing anything that touches `.env`, config, fixtures, or generated code.
- "Did I just commit a key?" / "Is there anything sensitive in this repo?"
- Wiring a pre-commit hook or CI step that blocks credential leaks.

## How to run it

Standard library only:

```bash
# pre-commit mode: scan only the ADDED lines of the staged diff (default with no paths)
python scripts/scan_secrets.py

# scan paths / dirs / globs
python scripts/scan_secrets.py src/ config/ "**/*.env*"
```

| Option | Effect |
|---|---|
| `--staged` | Scan added lines in the staged diff, with real line numbers (default when no paths are given). |
| `--exclude GLOB` | Skip matching paths (repeatable), e.g. `--exclude "**/fixtures/**"`. |
| `--fail-on {critical,high,medium}` | Minimum severity that makes the exit code non-zero (default: `high`). |
| `--json` | Machine-readable findings. |

Suppress a known-safe line by adding the comment `secret-scanner: ignore` to it.

As a git hook, `.git/hooks/pre-commit`:

```bash
#!/bin/sh
exec python path/to/scan_secrets.py --staged
```

## What it detects

| Rule | Severity |
|---|---|
| Private key blocks (`-----BEGIN … PRIVATE KEY-----`) | critical |
| AWS access key IDs, Anthropic / OpenAI / Google / Stripe API keys, GitHub and Slack tokens | high |
| Connection URLs embedding a password (`scheme://user:pass@host`) | high |
| JWTs | medium |
| Generic `api_key = "…"`, `secret`, `token`, `password` assignments — only when the value has high entropy and isn't an obvious placeholder | medium |

Matches are always printed **masked** (first 4 and last 2 characters) so the report itself is safe to paste into an issue.

## How to report results

1. Lead with the count and the highest severity. A single critical finding is the headline, not item 4.
2. List each finding as `path:line`, rule, masked value — never the full match.
3. For anything that looks real, say so plainly: **rotate it now**, then remove it from history if it was already committed. Removing the line in a new commit does not un-leak it.
4. For false positives (test fixtures, docs examples), suggest the inline ignore comment or `--exclude` rather than lowering `--fail-on`.

## Honest limitations

- Pattern-based: it catches well-known key formats and high-entropy assignments, not every possible secret. A clean scan lowers risk; it doesn't prove absence.
- The generic-assignment rule trades some recall for a low false-positive rate (entropy ≥ 3.0, placeholder filter). Tune `--fail-on medium` if you'd rather be noisier.
- Skips binaries and files over 5 MB. Scans the working tree or the staged diff — not git history. For history, run it over `git show <ref>:<path>` output or use a dedicated history tool.
