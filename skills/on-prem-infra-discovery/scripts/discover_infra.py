#!/usr/bin/env python3
"""Map the on-prem and local infrastructure an agent can use instead of public cloud.

Read-only and standard library only. Discovery runs in three widening rings:

  1. passive   - env vars, ~/.ssh/config, /etc/hosts, Ansible inventories,
                 kubeconfig, docker contexts, package-mirror config, mounts
  2. this host - CPU / memory / accelerators, plus a fixed catalog of
                 well-known service ports on 127.0.0.1
  3. named     - the same port catalog on hosts you pass with --host

It never sweeps a subnet, never accepts a CIDR, range or wildcard, never sends
credentials, and never records secret values: URLs are stripped of userinfo
and query strings, and only hostnames are read from credential-bearing files.

The result is an inventory (JSON via --json / --write) that an agent should
consult before defaulting to a cloud service.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.client
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

SCHEMA = 1
MAX_HOSTS = 32          # explicit --host cap: this is a map-maker, not a scanner
CLI_TIMEOUT = 15.0      # wrapper CLIs are slow: the gcloud-SDK kubectl takes ~5s to print a config
CLI_PROBLEMS: dict[str, str] = {}   # tool -> why its output is unknown (filled by run())
HTTP_MAX_BYTES = 65536
USER_AGENT = "on-prem-infra-discovery/1"

# CLI name -> capability category. Presence only; nothing here is executed
# except the read-only calls in kube_contexts / docker_contexts / host_info.
TOOLS = {
    "docker": "containers", "podman": "containers", "nerdctl": "containers",
    "kubectl": "orchestration", "helm": "orchestration", "k3s": "orchestration",
    "nomad": "orchestration", "consul": "service_discovery", "vault": "secrets",
    "virsh": "virtualization", "pvesh": "virtualization",
    "sinfo": "batch_compute", "sbatch": "batch_compute", "qsub": "batch_compute",
    "ollama": "inference", "nvidia-smi": "accelerator", "rocm-smi": "accelerator",
    "mc": "object_storage", "aws": "object_storage", "s3cmd": "object_storage",
    "ansible": "inventory", "terraform": "inventory",
}

# (port, service, category, identify-kind). A bare open port only *suggests*
# a service; identify-kind names the unauthenticated GET that confirms it.
CATALOG = [
    (11434, "ollama", "inference", "ollama"),
    (8000, "openai-compatible server (vLLM default)", "inference", "openai"),
    (8080, "openai-compatible server (llama.cpp / TGI / LocalAI default)", "inference", "openai"),
    (1234, "lm-studio", "inference", "openai"),
    (30000, "openai-compatible server (SGLang default)", "inference", "openai"),
    (9000, "minio / s3-compatible", "object_storage", "minio"),
    (5000, "container registry", "registry", "registry"),
    (8081, "nexus / artifactory", "package_mirror", None),
    (5432, "postgres", "database", None),
    (3306, "mysql / mariadb", "database", None),
    (27017, "mongodb", "database", None),
    (6379, "redis / valkey", "cache", None),
    (9200, "elasticsearch / opensearch", "search", "elastic"),
    (6333, "qdrant", "vector_db", "qdrant"),
    (19530, "milvus", "vector_db", None),
    (8500, "consul", "service_discovery", "consul"),
    (4646, "nomad", "orchestration", "nomad"),
    (6443, "kubernetes api", "orchestration", None),
    (8006, "proxmox ve", "virtualization", None),
    (9090, "prometheus", "observability", "prometheus"),
    (3000, "grafana", "observability", "grafana"),
    (8200, "vault", "secrets", "vault"),
]

# Environment variables that point at infrastructure. kind: url | path.
ENV_HINTS = [
    ("OLLAMA_HOST", "inference", "url"),
    ("OPENAI_BASE_URL", "inference", "url"),
    ("OPENAI_API_BASE", "inference", "url"),
    ("ANTHROPIC_BASE_URL", "inference", "url"),
    ("HF_ENDPOINT", "model_mirror", "url"),
    ("HF_HOME", "model_cache", "path"),
    ("AWS_ENDPOINT_URL", "object_storage", "url"),
    ("AWS_ENDPOINT_URL_S3", "object_storage", "url"),
    ("S3_ENDPOINT", "object_storage", "url"),
    ("MINIO_ENDPOINT", "object_storage", "url"),
    ("DOCKER_HOST", "containers", "url"),
    ("KUBECONFIG", "orchestration", "path"),
    ("PIP_INDEX_URL", "package_mirror", "url"),
    ("PIP_EXTRA_INDEX_URL", "package_mirror", "url"),
    ("UV_INDEX_URL", "package_mirror", "url"),
    ("NPM_CONFIG_REGISTRY", "package_mirror", "url"),
    ("GOPROXY", "package_mirror", "url"),
    ("MLFLOW_TRACKING_URI", "ml_platform", "url"),
    ("QDRANT_URL", "vector_db", "url"),
    ("DATABASE_URL", "database", "url"),
    ("REDIS_URL", "cache", "url"),
    ("HTTP_PROXY", "network_egress", "url"),
    ("HTTPS_PROXY", "network_egress", "url"),
    ("NO_PROXY", "network_egress", "url"),
]

LOCAL_NAMES = {"localhost", "ip6-localhost", "kubernetes.docker.internal",
               "host.docker.internal"}
CLOUD_SUFFIXES = (
    "amazonaws.com", "amazonaws.com.cn", "ec2.internal", "compute.internal",
    "azmk8s.io", "azure.com", "windows.net", "azurecr.io", "cloudapp.net",
    "googleapis.com", "gcr.io", "pkg.dev", "cloud.google.com",
    "digitaloceanspaces.com", "ondigitalocean.com", "linodeobjects.com",
    "cloudflarestorage.com", "backblazeb2.com", "openai.com", "anthropic.com",
    "cohere.com", "mistral.ai", "huggingface.co", "docker.io", "docker.com",
    "ghcr.io", "quay.io", "pypi.org", "npmjs.org", "golang.org", "github.com",
)
ONPREM_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal",
                   ".intranet", ".corp", ".private", ".localdomain", ".ts.net",
                   ".svc", ".cluster.local")
NETWORK_FS = {"nfs", "nfs4", "cifs", "smbfs", "smb3", "afpfs", "sshfs",
              "fuse.sshfs", "ceph", "glusterfs", "fuse.glusterfs", "lustre",
              "beegfs", "9p", "webdav"}
CORE_CATEGORIES = ["inference", "accelerator", "containers", "orchestration",
                   "object_storage", "database", "vector_db", "registry",
                   "package_mirror", "shared_storage"]


# --- classification & redaction ----------------------------------------------

def classify_host(host: str) -> str:
    """local | on-prem | cloud | unknown. Conservative: unknown beats a guess."""
    h = (host or "").strip().strip("[]").lower().rstrip(".")
    if not h:
        return "unknown"
    if h in LOCAL_NAMES:
        return "local"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback:
            return "local"
        # RFC1918, link-local, CGNAT (Tailscale), ULA - anything not globally routable
        return "unknown" if ip.is_global else "on-prem"
    if any(h == s or h.endswith("." + s) for s in CLOUD_SUFFIXES):
        return "cloud"
    if re.search(r"\.c\.[a-z0-9\-]+\.internal$", h):   # GCP internal DNS
        return "cloud"
    if "." not in h or h.endswith(ONPREM_SUFFIXES):
        return "on-prem"
    return "unknown"


def redact_url(value: str) -> str:
    """Strip userinfo and query strings. Handles comma lists (GOPROXY, NO_PROXY)."""
    parts = []
    for piece in str(value).split(","):
        p = piece.strip()
        if "://" in p:
            try:
                s = urlsplit(p)
                host = s.hostname or ""
                port = s.port
            except ValueError:
                parts.append("<unparseable>")
                continue
            if ":" in host:
                host = f"[{host}]"
            netloc = host + (f":{port}" if port else "")
            if s.username or s.password:
                netloc = "***@" + netloc
            red = urlunsplit((s.scheme, netloc, s.path, "", ""))
            parts.append(red + ("?<removed>" if s.query else ""))
        elif "@" in p:
            parts.append("***@" + p.rsplit("@", 1)[1])
        else:
            parts.append(p)
    return ",".join(parts)


def host_of(value: str) -> str:
    p = str(value).split(",")[0].strip()
    if "://" in p:
        try:
            return urlsplit(p).hostname or ""
        except ValueError:
            return ""
    p = p.rsplit("@", 1)[-1]
    if p.startswith("["):
        return p[1:].split("]")[0]
    return re.split(r"[:/]", p, maxsplit=1)[0]


def tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and path.startswith(home) else path


# --- small IO helpers ---------------------------------------------------------

def run(cmd: list[str]) -> str | None:
    """Run a read-only CLI. A failure is recorded, never swallowed: a tool that
    timed out means "unknown", and the inventory has to say so rather than
    letting silence read as "nothing there"."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=CLI_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        CLI_PROBLEMS[cmd[0]] = f"timed out after {CLI_TIMEOUT:g}s"
        return None
    except OSError as exc:
        CLI_PROBLEMS[cmd[0]] = f"could not be run ({exc.strerror or exc})"
        return None
    if p.returncode != 0:
        CLI_PROBLEMS[cmd[0]] = f"exited {p.returncode}"
        return None
    return p.stdout


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(1_000_000)
    except OSError:
        return ""


def read_json(path: str):
    text = read_text(path)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --- ring 1: passive sources ---------------------------------------------------

def parse_ssh_config(text: str) -> list[dict]:
    """Host aliases + HostName only. No other key is ever read."""
    hosts: list[dict] = []
    current: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        bits = re.split(r"[\s=]+", line, maxsplit=1)
        key, val = bits[0].lower(), (bits[1].strip() if len(bits) > 1 else "")
        if key == "host":
            current = [{"alias": a, "hostname": ""} for a in val.split()
                       if not any(c in a for c in "*?!")]
            hosts.extend(current)
        elif key == "match":
            current = []
        elif key == "hostname":
            for h in current:
                h["hostname"] = val
    for h in hosts:
        h["placement"] = classify_host(h["hostname"] or h["alias"])
    return hosts


def parse_etc_hosts(text: str) -> list[dict]:
    out = []
    for raw in text.splitlines():
        parts = raw.split("#", 1)[0].split()
        if len(parts) < 2:
            continue
        try:
            addr = ipaddress.ip_address(parts[0])
        except ValueError:
            continue
        if addr.is_loopback or addr.is_multicast or addr.is_unspecified \
                or parts[0] == "255.255.255.255":
            continue
        names = [n for n in parts[1:]
                 if n.lower() not in LOCAL_NAMES and n != "broadcasthost"]
        if names:
            out.append({"ip": parts[0], "names": names,
                        "placement": classify_host(parts[0])})
    return out


def parse_ansible_ini(text: str) -> dict[str, list[dict]]:
    """Group -> hosts. Only the host token and ansible_host are read."""
    groups: dict[str, list[dict]] = {}
    group, skip = "ungrouped", False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.match(r"^\[([^\]]+)\]$", line)
        if m:
            group = m.group(1)
            skip = ":" in group           # :vars / :children hold no hosts
            continue
        if skip:
            continue
        token = line.split()[0]
        if "=" in token:
            continue
        entry = {"host": token}
        am = re.search(r"\bansible_host=(\S+)", line)
        if am:
            entry["address"] = am.group(1)
        entry["placement"] = classify_host(entry.get("address", token))
        groups.setdefault(group, []).append(entry)
    return groups


def ansible_inventories(notes: list[str]) -> list[dict]:
    candidates = [p for p in os.environ.get("ANSIBLE_INVENTORY", "").split(",") if p]
    candidates += ["inventory", "inventory.ini", "hosts.ini", "ansible/inventory",
                   "ansible/inventory.ini", "ansible/hosts", "/etc/ansible/hosts"]
    found, seen = [], set()
    for cand in candidates:
        paths = [cand]
        if os.path.isdir(cand):
            paths = [os.path.join(cand, n) for n in ("hosts", "hosts.ini", "inventory.ini")]
        for path in paths:
            real = os.path.realpath(path)
            if real in seen or not os.path.isfile(path):
                continue
            seen.add(real)
            text = read_text(path)
            first = next((ln.strip() for ln in text.splitlines()
                          if ln.strip() and ln.strip()[0] not in "#;"), "")
            if path.endswith((".yml", ".yaml")) or (first.endswith(":") and "[" not in first):
                notes.append(f"YAML Ansible inventory at {tilde(path)} was not parsed "
                             "(standard library only) - read it directly.")
                continue
            groups = parse_ansible_ini(text)
            if groups:
                found.append({"file": tilde(path), "groups": groups})
    return found


def env_hints() -> list[dict]:
    out = []
    for name, category, kind in ENV_HINTS:
        val = os.environ.get(name)
        if not val:
            continue
        if kind == "path":
            out.append({"name": name, "category": category, "kind": kind,
                        "value": tilde(val), "placement": "local"})
        else:
            out.append({"name": name, "category": category, "kind": kind,
                        "value": redact_url(val),
                        "placement": classify_host(host_of(val))})
    return out


def collect_mirrors(home: str) -> list[dict]:
    found: list[dict] = []

    def add(kind: str, url: str, source: str) -> None:
        found.append({"kind": kind, "url": redact_url(url),
                      "placement": classify_host(host_of(url)), "source": tilde(source)})

    for path in (f"{home}/.config/pip/pip.conf", f"{home}/.pip/pip.conf", "/etc/pip.conf"):
        for line in read_text(path).splitlines():
            m = re.match(r"^\s*(?:extra-)?index-url\s*=\s*(\S+)", line)
            if m:
                add("pip", m.group(1), path)
    for line in read_text(f"{home}/.npmrc").splitlines():
        if re.search(r"(?i)auth|token|password|secret", line):
            continue                      # credential lines are skipped, not parsed
        m = re.match(r"^\s*(?:@[\w\-.]+:)?registry\s*=\s*(\S+)", line)
        if m:
            add("npm", m.group(1), f"{home}/.npmrc")
    cfg = read_json(f"{home}/.docker/config.json")
    if isinstance(cfg, dict):
        for registry in (cfg.get("auths") or {}):     # keys only - values hold credentials
            add("container-registry-login", registry, f"{home}/.docker/config.json")
    for path in (f"{home}/.docker/daemon.json", "/etc/docker/daemon.json"):
        daemon = read_json(path)
        if isinstance(daemon, dict):
            for key in ("registry-mirrors", "insecure-registries"):
                for registry in daemon.get(key) or []:
                    add(key, str(registry), path)
    return found


def _mount(fs: str, src: str, mountpoint: str) -> dict:
    entry = {"type": fs.lower(), "source": re.sub(r"//[^/@\s]+@", "//***@", src),
             "mountpoint": mountpoint}
    if mountpoint.startswith("/Volumes/.timemachine"):
        entry["backup_volume"] = True
    return entry


def parse_mounts(text: str) -> list[dict]:
    """Network filesystems from `mount` output (Linux and macOS/BSD formats)."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(?P<src>.+?) on (?P<mp>.+?) type (?P<fs>\S+)", line) or \
            re.match(r"^(?P<src>.+?) on (?P<mp>.+?) \((?P<fs>[^,)\s]+)", line)
        if not m or m.group("fs").lower() not in NETWORK_FS:
            continue
        out.append(_mount(m.group("fs"), m.group("src"), m.group("mp")))
    return out


def parse_proc_mounts(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2].lower() in NETWORK_FS:
            out.append(_mount(parts[2], parts[0], parts[1]))
    return out


def parse_kube_config(cfg: dict) -> list[dict]:
    """From `kubectl config view -o json` (kubectl redacts secrets in that output)."""
    clusters = {c.get("name"): ((c.get("cluster") or {}).get("server") or "")
                for c in cfg.get("clusters") or []}
    current = cfg.get("current-context") or ""
    out = []
    for c in cfg.get("contexts") or []:
        name = c.get("name", "")
        cluster = (c.get("context") or {}).get("cluster", "")
        server = clusters.get(cluster, "")
        out.append({"context": name, "cluster": cluster, "server": redact_url(server),
                    "placement": classify_host(host_of(server)),
                    "current": name == current})
    return out


def kube_contexts(no_cli: bool, notes: list[str]) -> list[dict]:
    if no_cli or not shutil.which("kubectl"):
        return []
    out = run(["kubectl", "config", "view", "-o", "json"])   # local file read; no cluster contact
    if not out:
        return []
    try:
        return parse_kube_config(json.loads(out))
    except (json.JSONDecodeError, AttributeError):
        notes.append("kubectl config view returned output that could not be parsed.")
        return []


def parse_docker_contexts(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        ep = str(d.get("DockerEndpoint", ""))
        entry = {"runtime": "docker", "context": d.get("Name", ""),
                 "endpoint": redact_url(ep), "current": bool(d.get("Current"))}
        if ep.startswith("unix://"):
            entry["placement"] = "local"
            entry["socket_present"] = os.path.exists(ep[len("unix://"):])
        else:
            entry["placement"] = classify_host(host_of(ep))
        out.append(entry)
    return out


def docker_contexts(no_cli: bool) -> list[dict]:
    if no_cli or not shutil.which("docker"):
        return []
    out = run(["docker", "context", "ls", "--format", "{{json .}}"])  # no daemon call
    return parse_docker_contexts(out or "")


# --- ring 2: this host ----------------------------------------------------------

def memory_bytes(no_cli: bool) -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    if not no_cli and platform.system() == "Darwin":
        out = (run(["sysctl", "-n", "hw.memsize"]) or "").strip()
        if out.isdigit():
            return int(out)
    return None


def detect_accelerators(no_cli: bool) -> list[dict]:
    acc = []
    if not no_cli and shutil.which("nvidia-smi"):
        out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
        for line in (out or "").splitlines():
            if line.strip():
                acc.append({"kind": "nvidia", "detail": line.strip()})
    if not acc and os.path.exists("/dev/nvidia0"):
        acc.append({"kind": "nvidia", "detail": "NVIDIA GPU device present (nvidia-smi unavailable)"})
    if os.path.exists("/dev/kfd"):
        acc.append({"kind": "amd-rocm", "detail": "AMD ROCm device present (/dev/kfd)"})
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        chip = "" if no_cli else (run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "").strip()
        acc.append({"kind": "apple-silicon",
                    "detail": f"{chip or 'Apple Silicon'} - Metal GPU, unified memory shared with RAM"})
    return acc


def host_info(no_cli: bool) -> dict:
    mem = memory_bytes(no_cli)
    try:
        free = shutil.disk_usage(os.path.expanduser("~")).free
    except OSError:
        free = None
    return {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "cpu_count": os.cpu_count(),
        "memory_gb": round(mem / 1024**3, 1) if mem else None,
        "disk_free_gb": round(free / 1024**3, 1) if free is not None else None,
        "accelerators": detect_accelerators(no_cli),
    }


# --- rings 2 & 3: endpoint probing ------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):      # never follow a service off-host
        return None


# No proxies (a corporate HTTP_PROXY must not see local probes) and no redirects.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def http_get(host: str, port: int, path: str, timeout: float):
    h = f"[{host}]" if ":" in host else host
    req = urllib.request.Request(f"http://{h}:{port}{path}",
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return (r.status, {k.lower(): v for k, v in r.headers.items()},
                    r.read(HTTP_MAX_BYTES).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, ""
    except (OSError, ValueError, http.client.HTTPException):
        return None


def _json(resp):
    if not resp or not resp[2]:
        return None
    try:
        return json.loads(resp[2])
    except json.JSONDecodeError:
        return None


def identify(kind: str, host: str, port: int, timeout: float) -> tuple[bool, dict]:
    """One unauthenticated GET to confirm what is behind an open port."""
    h = f"[{host}]" if ":" in host else host
    if kind == "ollama":
        data = _json(http_get(host, port, "/api/tags", timeout))
        if isinstance(data, dict) and isinstance(data.get("models"), list):
            names = [m.get("name") for m in data["models"] if isinstance(m, dict)]
            return True, {"models": [n for n in names if n][:25],
                          "base_url": f"http://{h}:{port}",
                          "openai_compatible_base_url": f"http://{h}:{port}/v1"}
    elif kind == "openai":
        resp = http_get(host, port, "/v1/models", timeout)
        data = _json(resp)
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            ids = [m.get("id") for m in data["data"] if isinstance(m, dict)]
            return True, {"models": [i for i in ids if i][:25],
                          "base_url": f"http://{h}:{port}/v1"}
        if resp and resp[0] in (401, 403):
            return False, {"note": "HTTP auth required on /v1/models"}
    elif kind == "minio":
        resp = http_get(host, port, "/minio/health/live", timeout)
        if resp and resp[0] == 200:
            return True, {"endpoint_url": f"http://{h}:{port}"}
    elif kind == "registry":
        resp = http_get(host, port, "/v2/", timeout)
        if resp and resp[0] in (200, 401) and "docker-distribution-api-version" in resp[1]:
            return True, {"registry": f"{h}:{port}", "auth_required": resp[0] == 401}
    elif kind == "elastic":
        resp = http_get(host, port, "/", timeout)
        data = _json(resp)
        if isinstance(data, dict) and isinstance(data.get("version"), dict):
            v = data["version"]
            return True, {"distribution": v.get("distribution", "elasticsearch"),
                          "version": v.get("number")}
        if resp and resp[0] in (401, 403):
            return False, {"note": "HTTP auth required"}
    elif kind == "qdrant":
        data = _json(http_get(host, port, "/", timeout))
        if isinstance(data, dict) and "qdrant" in str(data.get("title", "")).lower():
            return True, {"version": data.get("version")}
    elif kind in ("consul", "nomad"):
        resp = http_get(host, port, "/v1/status/leader", timeout)
        if resp and resp[0] == 200:
            return True, {}
    elif kind == "prometheus":
        resp = http_get(host, port, "/-/ready", timeout)
        if resp and resp[0] == 200:
            return True, {}
    elif kind == "grafana":
        data = _json(http_get(host, port, "/api/health", timeout))
        if isinstance(data, dict) and "database" in data:
            return True, {"version": data.get("version")}
    elif kind == "vault":
        data = _json(http_get(host, port, "/v1/sys/health", timeout))
        if isinstance(data, dict) and "initialized" in data:
            return True, {"sealed": data.get("sealed")}
    return False, {}


def probe_one(host: str, port: int, service: str, category: str,
              kind: str | None, timeout: float) -> dict | None:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError:
        return None
    verified, detail = identify(kind, host, port, max(timeout * 3, 1.5)) if kind else (False, {})
    return {"host": host, "port": port, "service": service, "category": category,
            "placement": classify_host(host), "verified": verified, "detail": detail}


def probe_hosts(hosts: list[str], timeout: float, catalog=None) -> list[dict]:
    jobs = [(h, *entry) for h in hosts for entry in (catalog or CATALOG)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = pool.map(lambda j: probe_one(j[0], j[1], j[2], j[3], j[4], timeout), jobs)
    return [r for r in results if r]


# --- assembly -----------------------------------------------------------------------

def summarize(inv: dict) -> tuple[dict[str, list[str]], list[str], dict[str, list[str]]]:
    """Split evidence into capabilities (confirmed) and hints (unconfirmed).

    A capability is something an agent may plan around: a service that identified
    itself, an endpoint the environment is configured for, a non-cloud kube
    context, a container runtime whose socket exists, a mounted share. An open
    port that did not identify itself, an installed-but-stopped runtime, or a
    backup volume is only a hint - it never fills a gap.
    """
    caps: dict[str, list[str]] = {}
    hints: dict[str, list[str]] = {}

    def add(bucket: dict, cat: str, text: str) -> None:
        bucket.setdefault(cat, []).append(text)

    for e in inv["endpoints"]:
        where = f"{e['host']}:{e['port']}"
        if not e["verified"]:
            why = (e.get("detail") or {}).get("note") or "did not identify itself"
            add(hints, e["category"], f"port {where} is open - {e['service']}? ({why})")
            continue
        models = (e.get("detail") or {}).get("models") or []
        extra = f" - models: {', '.join(models[:5])}{' ...' if len(models) > 5 else ''}" if models else ""
        add(caps, e["category"], f"{e['service']} at {where} (verified){extra}")
    for h in inv["env_hints"]:
        if h.get("kind") == "path" or h["category"] == "network_egress":
            continue                      # a file path or a proxy is not capacity
        if h["placement"] in ("on-prem", "local"):
            add(caps, h["category"], f"{h['name']} -> {h['value']} ({h['placement']}, configured)")
    for k in inv["kubernetes"]:
        if k["placement"] in ("on-prem", "local"):
            add(caps, "orchestration",
                f"kube context '{k['context']}' -> {k['server']} ({k['placement']}, not contacted)")
    for c in inv["container_runtimes"]:
        label = f"{c['runtime']} context '{c['context']}'"
        if c.get("socket_present") is False:
            add(hints, "containers", f"{label}: socket missing - installed, but is the daemon running?")
        elif "socket_present" in c:
            add(caps, "containers", f"{label} (local, socket present)")
        else:
            add(caps, "containers", f"{label} -> {c['endpoint']} ({c['placement']}, reachability not checked)")
    for a_ in inv["host"]["accelerators"]:
        add(caps, "accelerator", a_["detail"])
    for m in inv["mirrors"]:
        if m["placement"] in ("on-prem", "local"):
            cat = "registry" if "registr" in m["kind"] else "package_mirror"
            add(caps, cat, f"{m['kind']}: {m['url']} ({m['placement']})")
    for st in inv["shared_storage"]:
        if st.get("backup_volume"):
            add(hints, "shared_storage", f"backup volume from {st['source']} - a NAS exists, "
                                         "but this mount is a backup target, not a share")
        else:
            add(caps, "shared_storage", f"{st['type']} {st['source']} at {st['mountpoint']}")
    for tool, problem in (inv["scope"].get("cli_problems") or {}).items():
        cat = TOOLS.get(tool) or {"mount": "shared_storage"}.get(tool)
        if cat:
            add(hints, cat, f"{tool} {problem} - result unknown, not absent")
    batch = [t for t, cat in TOOLS.items() if cat == "batch_compute" and t in inv["tools"]]
    if batch:
        add(caps, "batch_compute", f"scheduler CLI present: {', '.join(batch)}")
    return caps, [c for c in CORE_CATEGORIES if c not in caps], hints


def discover(args) -> dict:
    notes: list[str] = []
    CLI_PROBLEMS.clear()
    home = os.path.expanduser("~")
    hosts = [] if args.no_probe else ["127.0.0.1"] + args.hosts

    if args.no_cli:
        storage = parse_proc_mounts(read_text("/proc/mounts"))
    else:
        storage = parse_mounts(run(["mount"]) or "") or parse_proc_mounts(read_text("/proc/mounts"))

    inv = {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": {"passive": True, "cli": not args.no_cli, "probed_hosts": hosts},
        "host": host_info(args.no_cli),
        "tools": {t: shutil.which(t) for t in TOOLS if shutil.which(t)},
        "container_runtimes": docker_contexts(args.no_cli),
        "kubernetes": kube_contexts(args.no_cli, notes),
        "endpoints": probe_hosts(hosts, args.timeout) if hosts else [],
        "env_hints": env_hints(),
        "inventory_sources": {
            "ssh_hosts": parse_ssh_config(read_text(f"{home}/.ssh/config")),
            "etc_hosts": parse_etc_hosts(read_text("/etc/hosts")),
            "ansible": ansible_inventories(notes),
        },
        "mirrors": collect_mirrors(home),
        "shared_storage": storage,
    }
    inv["scope"]["cli_problems"] = dict(CLI_PROBLEMS)
    inv["capabilities"], inv["gaps"], inv["gap_hints"] = summarize(inv)
    if any(not e["verified"] for e in inv["endpoints"]):
        notes.append("Unverified ports are only a hint: 3000/5000/8000/8080 are common dev "
                     "ports (5000 is AirPlay on macOS). Confirm before relying on them.")
    for tool, problem in inv["scope"]["cli_problems"].items():
        notes.append(f"{tool} {problem}: whatever it would have reported is UNKNOWN, not absent. "
                     "Re-run with a higher --cli-timeout, or check it by hand.")
    inv["notes"] = notes
    return inv


def load_if_fresh(path: str, days: float) -> dict | None:
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    try:
        ts = dt.datetime.fromisoformat(str(data["generated_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    age = dt.datetime.now(dt.timezone.utc) - ts
    if age > dt.timedelta(days=days) or age < dt.timedelta(0):
        return None
    data["_cache_age_days"] = round(age.total_seconds() / 86400, 1)
    return data


def write_private(path: str, text: str) -> None:
    """0600: the inventory describes internal topology."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def render(inv: dict) -> str:
    host = inv["host"]
    out = [f"On-prem infrastructure inventory - {host['hostname']}  ({inv['generated_at']})"]
    if "_cache_age_days" in inv:
        out.append(f"(cached inventory, {inv['_cache_age_days']} days old - "
                   "delete the file or lower --if-stale to refresh)")
    probed = inv["scope"]["probed_hosts"]
    out.append(f"Scope: passive sources{'' if inv['scope']['cli'] else ' (no CLIs)'}; "
               f"probed: {', '.join(probed) if probed else 'nothing'}")
    out += ["", "HOST",
            f"  {host['os']} {host['arch']} | {host['cpu_count']} CPUs | "
            f"{host['memory_gb']} GB RAM | {host['disk_free_gb']} GB free"]
    for a in host["accelerators"] or [{"detail": "none detected - CPU only"}]:
        out.append(f"  accelerator: {a['detail']}")

    out += ["", "WHAT YOU CAN USE ON-PREM"]
    if inv["capabilities"]:
        for cat in sorted(inv["capabilities"]):
            for i, line in enumerate(inv["capabilities"][cat]):
                out.append(f"  {cat if i == 0 else '':<17}{line}")
    else:
        out.append("  nothing found")
    if inv["gaps"]:
        out += ["", "NO CONFIRMED ON-PREM OPTION (cloud stays the default for these)",
                "  " + ", ".join(inv["gaps"])]
    if inv.get("gap_hints"):
        out += ["", "HINTS (unconfirmed - check with the user before relying on any of these)"]
        for cat in sorted(inv["gap_hints"]):
            for i, line in enumerate(inv["gap_hints"][cat]):
                out.append(f"  {cat if i == 0 else '':<17}{line}")

    if inv["kubernetes"]:
        out += ["", "KUBERNETES CONTEXTS"]
        for k in inv["kubernetes"]:
            out.append(f"  {'*' if k['current'] else ' '} {k['context']:<28}  {k['server']:<44}  {k['placement']}")
    hints = inv["env_hints"]
    if hints:
        out += ["", "ENV HINTS"]
        for h in hints:
            out.append(f"  {h['name']:<22}{h['value']}  ({h['placement']})")
    src = inv["inventory_sources"]
    if src["ssh_hosts"] or src["etc_hosts"] or src["ansible"]:
        out += ["", "INVENTORY LEADS (listed, NOT probed - pass --host for ones you are authorized to probe)"]
        for h in src["ssh_hosts"]:
            out.append(f"  ssh      {h['alias']:<24}  {h['hostname'] or '-':<28}  {h['placement']}")
        for h in src["etc_hosts"]:
            out.append(f"  hosts    {', '.join(h['names']):<24}  {h['ip']:<28}  {h['placement']}")
        for inv_file in src["ansible"]:
            for group, members in inv_file["groups"].items():
                names = ", ".join(m["host"] for m in members[:8])
                more = f" (+{len(members) - 8})" if len(members) > 8 else ""
                out.append(f"  ansible  [{group}] {names}{more}   ({inv_file['file']})")
    if inv["mirrors"]:
        out += ["", "MIRRORS & REGISTRIES"]
        for m in inv["mirrors"]:
            out.append(f"  {m['kind']:<26}{m['url']}  ({m['placement']}; {m['source']})")
    if inv["tools"]:
        out += ["", "TOOLS PRESENT", "  " + ", ".join(sorted(inv["tools"]))]
    if inv["notes"]:
        out += ["", "NOTES"] + [f"  - {n}" for n in inv["notes"]]
    return "\n".join(out)


def validate_hosts(hosts: list[str]) -> str | None:
    for h in hosts:
        if "/" in h or "*" in h or not re.match(r"^[A-Za-z0-9\[]([A-Za-z0-9.\-:\]]*)$", h):
            return (f"'{h}': one host per --host. CIDRs, ranges and wildcards are "
                    "refused by design - name the hosts you are authorized to probe.")
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}", h):
            try:
                ipaddress.ip_address(h)
            except ValueError:
                return f"'{h}' looks like an address range; ranges are refused by design."
    if len(hosts) > MAX_HOSTS:
        return f"at most {MAX_HOSTS} hosts per run - this tool maps known hosts, it does not scan."
    return None


def main(argv: list[str] | None = None) -> int:
    global CLI_TIMEOUT
    p = argparse.ArgumentParser(
        description="Map on-prem and local infrastructure an agent can use (read-only).")
    p.add_argument("--host", action="append", default=[], dest="hosts", metavar="HOST",
                   help="Also probe this host's well-known ports (repeatable). "
                        "Only hosts you are authorized to probe; no CIDRs or ranges.")
    p.add_argument("--hosts-file", metavar="FILE", help="File of hosts to probe, one per line.")
    p.add_argument("--no-probe", action="store_true", help="Passive sources only; open no sockets.")
    p.add_argument("--no-cli", action="store_true",
                   help="Do not invoke kubectl / docker / nvidia-smi / sysctl / mount.")
    p.add_argument("--timeout", type=float, default=0.4,
                   help="Per-connection timeout in seconds (default 0.4).")
    p.add_argument("--cli-timeout", type=float, default=CLI_TIMEOUT, metavar="SEC",
                   help="Timeout for each read-only CLI call (default 15). "
                        "A timeout is reported as 'unknown', never as 'absent'.")
    p.add_argument("--write", metavar="FILE", help="Write the JSON inventory here (mode 0600).")
    p.add_argument("--if-stale", type=float, metavar="DAYS",
                   help="With --write: reuse FILE if it is younger than DAYS instead of re-discovering.")
    p.add_argument("--json", action="store_true", help="Print JSON instead of the report.")
    args = p.parse_args(argv)

    if args.hosts_file:
        text = read_text(args.hosts_file)
        if not text:
            p.error(f"cannot read --hosts-file {args.hosts_file}")
        args.hosts += [ln.split("#", 1)[0].strip() for ln in text.splitlines()
                       if ln.split("#", 1)[0].strip()]
    problem = validate_hosts(args.hosts)
    if problem:
        p.error(problem)
    if args.no_probe and args.hosts:
        p.error("--no-probe and --host contradict each other")
    if args.if_stale is not None and not args.write:
        p.error("--if-stale needs --write FILE")
    if not 0.05 <= args.timeout <= 5:
        p.error("--timeout must be between 0.05 and 5 seconds")
    if not 1 <= args.cli_timeout <= 120:
        p.error("--cli-timeout must be between 1 and 120 seconds")
    CLI_TIMEOUT = args.cli_timeout

    inv = load_if_fresh(args.write, args.if_stale) if args.if_stale is not None else None
    if inv is None:
        inv = discover(args)
        if args.write:
            write_private(args.write, json.dumps(inv, indent=2) + "\n")

    print(json.dumps(inv, indent=2) if args.json else render(inv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
