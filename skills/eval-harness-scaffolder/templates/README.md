# Eval suite

A small regression harness for an LLM feature. Run it before and after any
prompt or model change and compare the pass rate.

## Setup

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-...
```

## Run

```bash
# whole suite against the default model
python run_eval.py

# iterate fast: first 3 cases only
python run_eval.py --limit 3

# record a baseline, then see exactly what a change regressed or improved
python run_eval.py --system prompts/v1.txt --out runs/baseline.json
python run_eval.py --system prompts/v2.txt --baseline runs/baseline.json

# try a cheaper model, 4 cases at a time
python run_eval.py --model claude-haiku-4-5 --workers 4 --baseline runs/baseline.json
```

Exit code is `0` only if every case passes, so this drops straight into CI.

## Adding cases

One JSON object per line in `cases.jsonl`:

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Stable, unique — it's how you track a case across runs. |
| `input` | yes | The user message sent to the model. |
| `scorer` | yes | `exact` \| `contains` \| `regex` \| `judge` |
| `expect` | for exact/contains/regex | The expected value or pattern. |
| `rubric` | for judge | A narrow, binary pass condition. |

Write cases from real failure modes, not toy inputs. A green suite means
"didn't regress these cases" — never "correct in general."
