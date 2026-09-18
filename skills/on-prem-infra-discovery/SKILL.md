---
name: on-prem-infra-discovery
description: Discover and map the on-prem and local infrastructure available to an agent — local model servers and GPUs, container and Kubernetes contexts, S3-compatible storage, databases, internal registries and package mirrors, shared storage, and host inventories — then use that map to place work on-prem instead of defaulting to public cloud. Use when the user asks what infrastructure is available, wants to avoid or reduce cloud usage, mentions on-prem, homelab, air-gapped, data residency, or self-hosted models, or before provisioning any cloud resource for a task that existing infrastructure might already cover.
---

# On-Prem Infra Discovery

Agents reach for public cloud by default because cloud is the only infrastructure they know exists. The GPU box under the desk, the MinIO cluster, the internal registry, the homelab Kubernetes context — none of it is visible unless something goes and looks. This skill is that look: a read-only discovery pass that produces a durable **inventory**, and the rules for using it to decide where work should run.

## When to use this

- "What do we have on-prem / locally that could run this?"
- Before provisioning *any* cloud resource, or calling a hosted API with data that may not be allowed to leave.
- The user mentions on-prem, homelab, air-gapped, data residency, self-hosted models, or cutting cloud spend.
- Start of work in an unfamiliar environment: build the map once, consult it for the rest of the session.

## The loop: discover → record → consult → place

1. **Discover** — run the script. It widens in three rings and stops where authorization stops:
   passive sources (env vars, `~/.ssh/config`, `/etc/hosts`, Ansible inventories, kubeconfig, docker contexts, mirror config, mounts) → this host (CPU, memory, accelerators, well-known ports on `127.0.0.1`) → hosts **you name** with `--host`.
2. **Record** — `--write` saves the inventory as JSON (mode `0600`). It is the knowledge layer: later tasks read it instead of rediscovering.
3. **Consult** — before choosing a cloud service, check `capabilities` for a confirmed on-prem option, `gaps` for where cloud genuinely remains the default, and `gap_hints` for things worth asking the user about.
4. **Place** — apply the rubric below, state the placement and the reason, and wire up using [`references/wiring-patterns.md`](references/wiring-patterns.md).

## How to run it

Standard library only, read-only:

```bash
# the one command to run first: discover, or reuse the inventory if it's under 14 days old
python scripts/discover_infra.py --write .agent/infra-inventory.json --if-stale 14

# also probe hosts the user has confirmed are in scope
python scripts/discover_infra.py --host gpu-box.lan --host 10.0.0.12 --write .agent/infra-inventory.json

# strictest mode: read config only — opens no sockets, runs no CLIs
python scripts/discover_infra.py --no-probe --no-cli --json
```

| Option | Effect |
|---|---|
| `--host HOST` | Also probe this host's catalog ports (repeatable, max 32). CIDRs, ranges and wildcards are **refused by design**. |
| `--hosts-file FILE` | Same, one host per line. |
| `--no-probe` | Passive sources only; no sockets opened. |
| `--no-cli` | Don't invoke `kubectl` / `docker` / `nvidia-smi` / `sysctl` / `mount`. |
| `--cli-timeout SEC` | Timeout per CLI call (default 15 — wrapper CLIs such as the gcloud-SDK `kubectl` are slow). A timeout is reported as *unknown*, never as *absent*. |
| `--write FILE` | Save the JSON inventory (`0600`). Keep it out of public repos — add it to `.gitignore`. |
| `--if-stale DAYS` | With `--write`: reuse the file if younger than `DAYS`. |
| `--timeout SEC`, `--json` | Per-connection timeout (default 0.4s); JSON to stdout. |

## Reading the inventory

| Section | What it tells you | What it does **not** prove |
|---|---|---|
| `capabilities` / `gaps` | **Confirmed** on-prem options per need — a service that identified itself, an endpoint the environment is configured for, a non-cloud kube context, a runtime whose socket exists, a mounted share — and the needs with none. | That you're allowed to use them, or that they have spare capacity. |
| `gap_hints` | Unconfirmed evidence, kept apart on purpose: open ports that didn't identify themselves, an installed-but-stopped runtime, a backup volume. A hint never fills a gap. | Anything. Raise it with the user as a question, not a finding. |
| `scope` | What was actually examined: hosts probed, whether CLIs ran, and `cli_problems` — any CLI that failed or timed out. | A missing finding only means something where `scope` says the tool looked. A `cli_problems` entry means **unknown**, not absent. |
| `endpoints` | Open catalog ports. `verified: true` means one unauthenticated GET confirmed the service (and lists models for inference servers). | `verified: false` is only an open port — 3000/5000/8000/8080 are common dev ports. Never treat it as the named service. |
| `kubernetes`, `container_runtimes` | Contexts with `placement`: `local`, `on-prem`, `cloud`, or `unknown`. No cluster is contacted. | That the context still works or that you have rights in it. |
| `env_hints`, `mirrors` | Endpoints the environment is already configured for — often the strongest signal of what the team actually uses. | — |
| `inventory_sources` | Hosts from SSH config, `/etc/hosts`, Ansible. These are **leads, not permission**: listed, never probed automatically. | Anything about what runs there. |
| `host.accelerators` | Whether this machine can self-host a model at all (NVIDIA / ROCm / Apple Silicon unified memory). | Which model sizes fit — check memory against the model. |

`placement: unknown` is deliberate: a public IP or an unrecognized domain could be either. Ask rather than guess.

## Placement rubric: on-prem or cloud?

Work through these in order — the first two are constraints, the rest are trade-offs.

1. **Hard constraints first.** Air-gapped, data-residency, or "this data may not leave" → on-prem only. If no on-prem option exists in `capabilities`, **stop and tell the user**; don't quietly fall back to cloud.
2. **Capability bar.** Does the on-prem option actually do the job? A local 8B model is not a frontier model. When swapping a hosted model for a self-hosted one, prove it with `eval-harness-scaffolder` instead of assuming.
3. **Ownership and capacity.** A discovered service has an owner. Confirm you may use it and that it has headroom before sending load — especially anything that looks like production.
4. **Cost shape.** Steady, high-volume work favors capacity you already own; spiky or one-off work favors cloud. Put numbers on it with `token-usage-estimator` / `usage-cost-report`.
5. **Latency and data gravity.** Run the work next to the data it reads.

| Favors on-prem | Favors cloud |
|---|---|
| Sensitive or regulated data; no-egress networks | Frontier model quality; managed services with no local equivalent |
| Steady high-volume workloads on owned, idle capacity | Bursty or short-lived demand; elastic scale |
| Large local datasets (data gravity), low-latency LAN access | Global reach, SLAs, no team to operate the thing |

**Hybrid is normal:** on-prem first with an explicit cloud fallback. Whenever the fallback would send data off-premises, say so and get a yes before it happens.

## How to report results

1. Lead with `capabilities` and `gaps` in plain language: what can run on-prem today, and what can't.
2. Separate **verified** from **unverified** findings, and leads from confirmed resources. Never upgrade a guess.
3. For the task at hand, state the recommended placement, the rubric step that decided it, and the fallback.
4. Give the exact wiring (base URL, endpoint override, kube context) from `references/wiring-patterns.md`.

## Rules of engagement

- **Authorization first.** Probe only `127.0.0.1` and hosts the user names or confirms. Inventory listings are leads; ask before turning one into a `--host`.
- **Read-only.** Discovery never creates, deploys, or changes anything. Deploying onto discovered infrastructure is a separate step that needs an explicit go-ahead.
- **No sweeps, no secrets.** The script refuses ranges and never records credentials; don't work around either. If you go deeper by hand, hold the same line.
- **Don't flip global state.** Never change the current kube or docker context — pass `--context` per command.
- **Treat the inventory as sensitive.** It describes internal topology: keep it `0600`, git-ignored, and out of tickets and public logs.
- **On-prem is not automatically compliant.** Placement satisfies a policy only if the user confirms it does.

## Going deeper

- [`references/wiring-patterns.md`](references/wiring-patterns.md) — pointing SDKs and tools at on-prem endpoints: OpenAI-compatible servers, Ollama, S3 → MinIO, registries, kube contexts, package and model mirrors, and the on-prem-first fallback pattern.
- [`references/discovery-sources.md`](references/discovery-sources.md) — where infrastructure announces itself beyond what the script covers (Consul, DNS-SD, Slurm, Proxmox, libvirt, in-cluster capacity), what each signal proves, and how to query it once authorized.

## Honest limitations

- A map of *signals*, not a CMDB. It finds what is configured on, or reachable from, this machine — not everything the organization owns.
- Fixed port catalog, plain-HTTP identification only. Services on non-default ports, behind TLS, or requiring auth show as unverified or not at all.
- YAML Ansible inventories and SSH `Include` files are noted or skipped, not parsed (standard library only).
- Evidence can fail to arrive. A slow or broken CLI is recorded in `scope.cli_problems` and surfaces as a hint on the affected gap — read it as "couldn't find out," and say so.
- Staleness is real: infrastructure changes. Re-run past `--if-stale`, and re-verify an endpoint before depending on it.
