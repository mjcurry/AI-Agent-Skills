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

# test a specific system prompt and model, save a report to diff later
python run_eval.py --system prompts/support.txt --model claude-haiku-4-5 --out runs/haiku.json
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
