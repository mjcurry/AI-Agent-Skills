# Wiring patterns: pointing tools at on-prem endpoints

Load this when you have an inventory and need to actually *use* something in it. Every pattern is a configuration change, not a code rewrite — most cloud SDKs accept an endpoint override.

Conventions: `gpu-box.lan`, `minio.lan`, `registry.lan` stand in for hosts from your inventory. Pass credentials through environment variables or a secrets manager — never hard-code them, and never copy them into the inventory.

## Inference

### OpenAI-compatible servers (vLLM, llama.cpp `llama-server`, LM Studio, SGLang, LocalAI, TGI)

The inventory's `detail.base_url` already ends in `/v1`. Most servers ignore the API key, but SDKs require one to be set.

```python
from openai import OpenAI

client = OpenAI(base_url="http://gpu-box.lan:8000/v1", api_key="not-needed")
reply = client.chat.completions.create(
    model="<an id from detail.models>",
    messages=[{"role": "user", "content": "ping"}],
)
```

Without code changes: `export OPENAI_BASE_URL=http://gpu-box.lan:8000/v1`.

Use a model id **exactly as listed** in `detail.models` — local servers reject unknown names rather than routing them.

### Ollama

Native API on `:11434`, and an OpenAI-compatible surface at `/v1` on the same port.

```bash
export OLLAMA_HOST=http://gpu-box.lan:11434        # for the ollama CLI
curl -s http://gpu-box.lan:11434/api/tags           # what's pulled
```

```python
client = OpenAI(base_url="http://gpu-box.lan:11434/v1", api_key="ollama")
```

Pulling a new model onto someone else's box uses their disk and bandwidth — ask first.

### Hosted frontier models behind an internal gateway

Frontier hosted models are not self-hostable; "on-prem" here means an internal gateway or proxy (LiteLLM, an API management layer) that adds auth, logging, and egress control. SDKs take a base-URL override — for the Anthropic SDK, `ANTHROPIC_BASE_URL` or `Anthropic(base_url=...)`. The data still leaves the premises; the gateway only controls *how*. Say so when it matters.

### Sizing a self-hosted model

Rough memory need ≈ parameters × bytes per weight, plus KV cache and runtime overhead: ~2 GB per billion parameters at 16-bit, ~0.6–0.7 GB at 4-bit. Check it against `host.accelerators` and `host.memory_gb` — on Apple Silicon the GPU shares system RAM, so leave room for everything else running.

## Object storage: S3 → MinIO / Ceph RGW / any S3-compatible store

```bash
export AWS_ENDPOINT_URL=http://minio.lan:9000
aws s3 ls                                   # CLI v2 honors the variable
aws --endpoint-url http://minio.lan:9000 s3 ls   # or per command
```

```python
import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3",
    endpoint_url="http://minio.lan:9000",
    config=Config(s3={"addressing_style": "path"}),   # most on-prem stores need path-style
)
```

Two things bite: virtual-host addressing (`bucket.host`) usually isn't set up on-prem, so use path-style; and a region is still required by the SDK even though the store ignores it.

## Containers and registries

```bash
docker tag app:1.4 registry.lan:5000/team/app:1.4
docker push registry.lan:5000/team/app:1.4
```

A plain-HTTP registry only works if the daemon lists it under `insecure-registries` — that shows up in the inventory's `mirrors`. If it isn't there, the fix is a TLS certificate or a daemon config change by whoever owns the host, not a workaround.

Remote engines: pass `docker --context <name> …` per command. Don't `docker context use`.

## Kubernetes

```bash
kubectl --context homelab get nodes -o wide
kubectl --context homelab -n sandbox apply -f job.yaml
```

Always `--context`, always an explicit namespace; never `kubectl config use-context` — silently changing the user's current context is how commands land on the wrong cluster. Before scheduling real work, check what the cluster can take:

```bash
kubectl --context homelab get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory,GPU:.status.allocatable.nvidia\\.com/gpu
kubectl --context homelab get storageclass,ingressclass
```

## Package, image and model mirrors

| Ecosystem | Override |
|---|---|
| pip / uv | `pip install --index-url https://mirror.lan/simple pkg`, or `PIP_INDEX_URL` / `UV_INDEX_URL` |
| npm | `npm install --registry https://npm.lan/` or `NPM_CONFIG_REGISTRY` |
| Go | `GOPROXY=https://goproxy.lan,direct` |
| Hugging Face Hub | `HF_ENDPOINT=https://hf-mirror.lan`; `HF_HOME` for a shared model cache on local or network storage |
| Container images | `registry-mirrors` in the docker daemon config (owner's change, not yours) |

In an air-gapped network these aren't optimizations — they're the only path. If `env_hints` shows a proxy and mirrors, assume direct internet access is blocked and don't retry public URLs.

## Databases, caches, vector stores

Catalog hits for Postgres, MySQL, Redis and MongoDB are **port-only** (unverified) and tell you nothing about credentials, schemas, or whether the instance is production. Ask for a connection string scoped to a sandbox database. For Qdrant (verified over HTTP), `QdrantClient(url="http://qdrant.lan:6333")`.

## Shared storage

An NFS/SMB/Ceph mount in `shared_storage` is often the right home for datasets, model weights (`HF_HOME`), and build caches — it avoids re-downloading per machine. Check free space and write permissions before pointing a large job at it.

## The on-prem-first fallback pattern

```python
ENDPOINTS = [
    {"name": "on-prem", "base_url": "http://gpu-box.lan:8000/v1", "leaves_premises": False},
    {"name": "cloud",   "base_url": None,                         "leaves_premises": True},
]

def pick(endpoints, allow_egress: bool):
    for ep in endpoints:
        if ep["leaves_premises"] and not allow_egress:
            continue
        if healthy(ep):            # cheap GET /v1/models with a short timeout
            return ep
    raise RuntimeError("no permitted endpoint is reachable")
```

The point is the `allow_egress` flag: falling back to cloud is a *decision the user made in advance*, not something that happens because a box was rebooting. Log which endpoint served each request so `usage-cost-report` can show what the fallback actually cost.
