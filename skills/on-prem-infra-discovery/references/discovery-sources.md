# Discovery sources: where on-prem infrastructure announces itself

Load this when the script's inventory isn't enough — you need to go a level deeper, or you're on a machine where the script can't run. Everything here is **read-only**. Everything past the first section needs the user to confirm the target is in scope.

The ordering is deliberate: cheapest and least intrusive first. Stop as soon as you know enough to place the work.

## 1. Already covered by the script

| Source | Signal | Proves | Doesn't prove |
|---|---|---|---|
| Env vars (`OPENAI_BASE_URL`, `AWS_ENDPOINT_URL`, `PIP_INDEX_URL`, `HTTP_PROXY`, …) | The environment is configured for an internal endpoint | Someone set this up on purpose — the strongest "this is what we use" signal | That the endpoint is up |
| `kubectl config view` | Contexts and API server addresses | Which clusters this user has config for | Access rights, cluster health |
| `docker context ls` | Local and remote engines | Configured engines | That the daemon is running |
| `~/.ssh/config`, `/etc/hosts`, Ansible INI | Named hosts | The user works with these machines | What runs on them; permission to touch them |
| pip / npm / docker config | Mirrors and private registries | Where packages and images are expected to come from | — |
| `mount` | NFS / SMB / Ceph / Lustre mounts | Shared storage reachable right now | Free space, write access |
| Catalog ports on `127.0.0.1` and `--host` | Open ports, HTTP-identified services | `verified: true` → that service answered | `verified: false` → only that *something* listens |

## 2. Ask the systems of record (needs an endpoint and usually a token)

These are authoritative where they exist. Prefer them over inference.

- **Consul catalog** — `curl -s http://consul.lan:8500/v1/catalog/services` lists every registered service; `/v1/health/service/<name>?passing=true` gives healthy instances with addresses and ports.
- **Nomad** — `nomad node status`, `nomad job status`.
- **DNS service discovery** — `dig +short SRV _ldap._tcp.corp.example` (or any `_service._proto` the site uses). On the local link, mDNS browsing shows what machines advertise: `dns-sd -B _services._dns-sd._udp` (macOS), `avahi-browse -at` (Linux). Browsing is passive listening, not probing.
- **CMDB / IPAM** — NetBox (`/api/dcim/devices/`, `/api/virtualization/virtual-machines/`), or whatever the organization runs. If one exists, it outranks everything on this page; ask the user.
- **Terraform** — provider blocks tell you what kind of estate it is: `vsphere`, `proxmox`, `libvirt`, `openstack`, `maas`, `nutanix` all mean on-prem. `terraform state list` enumerates managed resources. State can hold secrets — list, don't dump.

## 3. Compute platforms

- **Kubernetes**, once a context is approved:
  `kubectl --context X get nodes -o wide` · allocatable CPU / memory / `nvidia.com/gpu` per node · `get storageclass,ingressclass` · `get ns`. Check `kubectl auth can-i --list -n <ns>` before assuming you can deploy.
- **Slurm / PBS** — the classic on-prem compute most agents never think to look for. `sinfo -o "%P %a %l %D %G"` shows partitions, limits and GPUs (GRES); `squeue -u $USER` shows your queue; `sacctmgr show assoc user=$USER` shows what you may submit to. PBS: `qstat -Q`, `pbsnodes -a`. If `sinfo` or `qsub` is on the PATH, there is a cluster.
- **Proxmox VE** — `pvesh get /cluster/resources --type vm` and `--type node` (or the same over the API on `:8006`).
- **libvirt / KVM** — `virsh list --all`, `virsh nodeinfo`.
- **OpenStack** — `openstack server list`, `openstack flavor list`, `openstack quota show`.
- **VMware** — `govc ls`, `govc host.info` where `govc` is configured.

## 4. Accelerators, precisely

- NVIDIA: `nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv` — the *used* columns matter; an "available" GPU at 95% memory isn't.
- AMD: `rocm-smi --showmeminfo vram`.
- Apple Silicon: unified memory — the GPU budget is system RAM minus everything else running.
- In Kubernetes, GPUs appear as the `nvidia.com/gpu` (or `amd.com/gpu`) allocatable resource on nodes.

## 5. What a finding is worth

Rank evidence before acting on it:

1. **A system of record says so** (CMDB, Consul, the scheduler) — act on it.
2. **The environment is configured for it** (env vars, mirrors, kube contexts) — very likely in use; confirm it's up.
3. **The service identified itself** (`verified: true`) — it exists and answers; ownership still unknown.
4. **A port is open** — a hint. Do not name the service in your report as if it were confirmed.
5. **A hostname appears in a list** — a lead. Nothing more.

Levels 3–5 always need the same two questions answered by a human before any load is sent: *whose is it*, and *may we use it for this*.

## Lines that don't move

- No subnet sweeps, no port ranges, no brute-forced paths. If you can't name the host, you don't probe it.
- No credential discovery. Reading `~/.aws/credentials`, token files, kube secrets, or Terraform state values to "see what we have access to" is out of scope, full stop.
- No writes. `apply`, `create`, `pull`, `submit` belong to a later step the user explicitly starts.
- If the network is someone else's (a client site, a shared lab), get the go-ahead for active probing in writing terms the user is comfortable with — passive sources only until then (`--no-probe`).
