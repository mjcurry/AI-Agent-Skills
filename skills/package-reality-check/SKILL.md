---
name: package-reality-check
description: Check that the dependencies named in requirements files, pyproject.toml, or package.json actually exist on PyPI or npm and look established, flagging hallucinated names and freshly registered or barely used packages. Use before installing any package an AI suggested, when an agent adds or changes dependencies, when reviewing a manifest diff, or when the user asks whether a package is real.
---

# Package Reality Check

Models invent dependencies that sound right. In a USENIX Security 2025 study of 16 code models across 576,000 samples, 19.7% of recommended packages did not exist; 43% of the invented names came back on every one of ten re-runs, which is what makes them worth registering; and only 13.4% were within two edits of a real package, so typo detection misses most of them. A 2026 replication on newer models still measured 4.6–6.1%. (Figures as summarized in [Socket's slopsquatting reference](https://socket.dev/glossary/slopsquatting).) Registering a name a model reliably invents, and waiting, is called slopsquatting.

So the question before `pip install` or `npm install` is two questions: **does this name exist**, and if it does, **does it look like something people have actually been using** — or like something registered last month.

## When to use this

- Before installing a package that an AI — including you — suggested from memory.
- An agent has added or changed dependencies in a manifest.
- Reviewing a pull request that touches `requirements.txt`, `pyproject.toml`, or `package.json`.
- "Is this package real?"

## How to run it

Standard library only. Needs network access to the registry:

```bash
python scripts/check_packages.py requirements.txt package.json
python scripts/check_packages.py pyproject.toml --skip 'acme-*' --skip '@acme/*'
python scripts/check_packages.py --pypi requests fastapi --npm react zod
```

| Option | Effect |
|---|---|
| `--pypi NAME …`, `--npm NAME …` | Check names directly, without a manifest. |
| `--skip GLOB` | Names never sent to any registry — use it for internal packages (repeatable). |
| `--min-age-days N` | Younger than this is `suspicious` (default 90). |
| `--min-weekly-downloads N` | npm: fewer weekly downloads than this is `suspicious` (default 1000). |
| `--pypi-url`, `--npm-url`, `--npm-downloads-url` | Point at an internal mirror that serves the same JSON APIs. |
| `--timeout SEC`, `--workers N` | Per-request timeout (default 15s) and concurrency (default 8). |
| `--ca-file FILE` | CA bundle to verify TLS against. By default Python's trust store is used; if Python has none (common with python.org installers on macOS) the system bundle is used instead and the report says so. Verification is never turned off. |
| `--strict` | Exit `1` for `suspicious` packages too, not only `not-found`. |
| `--json` | Machine-readable results. |

Exit code: `1` if anything is `not-found` (or `suspicious`, with `--strict`); `3` if nothing was worse but some lookups came back `unknown` — a check that couldn't run is not a pass; `0` otherwise.

Reads `requirements*.txt` (and any other pip-style file), `pyproject.toml` (PEP 621, dependency groups, Poetry) and `package.json`. Direct URL, path, git and workspace dependencies are skipped — there is no registry name to verify.

## Reading the result

| Status | Meaning | What to do |
|---|---|---|
| `not-found` | The registry has no such package. | **Do not install, and do not "fix" it by guessing a similar name** — that is exactly the mistake the attack relies on. Find the real name in the project's own documentation, or drop the dependency. |
| `suspicious` | It exists, but is under 90 days old or (npm) barely downloaded. | Stop and show the user. Look at the publisher, the repository link, and what the package contains before it is installed. |
| `ok` | Exists and looks established. | Proceed — this is not a security audit. |
| `unknown` | The registry couldn't be reached. | **Unverified.** Say so; never report it as checked. |
| `skipped` | Matched `--skip`. | Nothing was sent anywhere. |

## How to report results

1. Lead with any `not-found` names and where each was declared. Those block the install.
2. For `suspicious`, give the evidence — age, weekly downloads, release count — and ask before going further. A new package is often legitimate; the user is the one who can tell.
3. Never auto-correct a name. If you believe you know the intended package, say which and why, and let the user confirm.
4. State what was *not* checked: skipped names, unreachable registries, and direct-URL dependencies.

## Rules of engagement

- **Package names leave the machine.** They are sent to the public registry. Use `--skip` for internal names — an internal name leaked to a public index is itself the setup for a dependency-confusion attack.
- **Read-only.** It never installs, downloads an archive, or runs package code.
- **Check before the install command, not after.** A verdict is only useful while it can still stop something.

## Honest limitations

- **Existing is not the same as safe.** An attacker who registered the name early enough passes the age test. This catches the invented and the freshly squatted; it does not review code, maintainers, or install scripts. Pair it with lockfiles and a dependency cooldown.
- PyPI's API has no download counts, so Python packages are judged on age and release history only.
- A scoped npm name that is `not-found` may simply be private.
- Thresholds are heuristics. A brand-new release of a real project will be flagged; that's the cost of looking.
