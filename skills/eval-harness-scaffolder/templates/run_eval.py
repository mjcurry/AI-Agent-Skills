#!/usr/bin/env python3
"""Minimal Claude eval runner.

Runs every case in a JSONL file against the Claude API, scores it, prints a
pass-rate summary, and optionally writes a JSON report. Pass a previous
report as --baseline to see exactly which cases regressed or improved.

Requires: pip install anthropic   (and ANTHROPIC_API_KEY in the environment)

Case format (one JSON object per line in cases.jsonl):

  {"id": "greet-1", "input": "Say hi in French.",
   "scorer": "contains", "expect": "bonjour"}

  {"id": "tone", "input": "Decline politely.",
   "scorer": "judge", "rubric": "The reply declines and stays courteous."}

Scorers: exact | contains | regex | judge
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

DEFAULT_MODEL = "claude-sonnet-5"


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def load_cases(path: str) -> list[dict]:
    cases = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                sys.exit(f"{path}:{n}: invalid JSON — {exc}")
    if not cases:
        sys.exit(f"{path}: no cases found")
    return cases


def load_baseline(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"--baseline: cannot read {path}: {exc}")
    return {
        "pass_rate": data.get("pass_rate"),
        "by_id": {r["id"]: bool(r.get("passed")) for r in data.get("results", [])},
    }


def make_client():
    try:
        import anthropic
    except ImportError:
        sys.exit("This runner needs the `anthropic` package: pip install anthropic")
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY


def run_case(client, model: str, system: str, text: str, max_tokens: int) -> str:
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": text}],
    }
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    return "".join(block.text for block in resp.content if block.type == "text").strip()


def judge(client, judge_model: str, rubric: str, output: str) -> tuple[bool, str]:
    prompt = (
        "You are a strict evaluator. Given a RUBRIC and an OUTPUT, decide if "
        "the output satisfies the rubric. Reply with JSON only: "
        '{"verdict": "PASS" | "FAIL", "reason": "<one sentence>"}.\n\n'
        f"RUBRIC:\n{rubric}\n\nOUTPUT:\n{output}"
    )
    raw = run_case(client, judge_model, "", prompt, 256)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return False, f"judge returned non-JSON: {raw[:120]}"
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return False, f"judge returned bad JSON: {raw[:120]}"
    return data.get("verdict") == "PASS", data.get("reason", "")


def score(client, case: dict, output: str, judge_model: str) -> tuple[bool, str]:
    scorer = case.get("scorer", "exact")
    if scorer == "exact":
        return normalize(output) == normalize(str(case["expect"])), "exact match"
    if scorer == "contains":
        return normalize(str(case["expect"])) in normalize(output), "substring"
    if scorer == "regex":
        return bool(re.search(case["expect"], output, re.DOTALL)), "regex"
    if scorer == "judge":
        return judge(client, judge_model, case["rubric"], output)
    return False, f"unknown scorer '{scorer}'"


def evaluate(client, case: dict, cid: str, model: str, system: str,
             max_tokens: int, judge_model: str) -> dict:
    """Run + score one case. Never raises — one bad case shouldn't abort a run."""
    try:
        output = run_case(client, model, system, case["input"], max_tokens)
        passed, detail = score(client, case, output, judge_model)
        return {"id": cid, "passed": passed, "detail": detail, "output": output}
    except Exception as exc:  # noqa: BLE001
        return {"id": cid, "passed": False, "detail": f"error: {exc}", "output": ""}


def compare(results: list[dict], baseline: dict) -> dict:
    prev = baseline["by_id"]
    return {
        "baseline_pass_rate": baseline["pass_rate"],
        "regressions": [r["id"] for r in results if prev.get(r["id"]) is True and not r["passed"]],
        "improvements": [r["id"] for r in results if prev.get(r["id"]) is False and r["passed"]],
        "new_cases": [r["id"] for r in results if r["id"] not in prev],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run a Claude eval suite.")
    p.add_argument("--cases", default="cases.jsonl")
    p.add_argument("--system", help="Path to a system-prompt file (optional).")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--judge-model", default=DEFAULT_MODEL)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--limit", type=int, help="Run only the first N cases.")
    p.add_argument("--workers", type=int, default=1,
                   help="Concurrent cases (default: 1). Mind your rate limit.")
    p.add_argument("--baseline", metavar="REPORT",
                   help="Previous --out report to diff against.")
    p.add_argument("--out", help="Write a JSON report to this path.")
    p.add_argument("--json", action="store_true", help="Print JSON, not a table.")
    args = p.parse_args(argv)

    if args.workers < 1:
        p.error("--workers must be >= 1")

    system = ""
    if args.system:
        with open(args.system, "r", encoding="utf-8") as fh:
            system = fh.read()

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]
    ids = [c.get("id") or f"case-{i}" for i, c in enumerate(cases, 1)]

    baseline = load_baseline(args.baseline) if args.baseline else None

    client = make_client()

    def work(pair):
        case, cid = pair
        return evaluate(client, case, cid, args.model, system,
                        args.max_tokens, args.judge_model)

    if args.workers == 1:
        results = [work(pair) for pair in zip(cases, ids)]
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(work, zip(cases, ids)))  # preserves order

    n = len(results)
    n_pass = sum(1 for r in results if r["passed"])
    rate = n_pass / n if n else 0.0

    report = {
        "model": args.model,
        "total": n,
        "passed": n_pass,
        "pass_rate": round(rate, 4),
        "results": results,
    }
    if baseline:
        report["comparison"] = compare(results, baseline)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\nModel: {args.model}")
        print(f"Pass rate: {n_pass}/{n}  ({rate:.0%})\n")
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  [{mark}] {r['id']} — {r['detail']}")
            if not r["passed"] and r["output"]:
                print(f"         output: {r['output'][:100]!r}")
        if baseline:
            cmp = report["comparison"]
            prev_rate = cmp["baseline_pass_rate"]
            delta = (
                f"{rate - prev_rate:+.0%}" if isinstance(prev_rate, (int, float)) else "n/a"
            )
            print(f"\nvs baseline: {delta}")
            for label, key in (("Regressed", "regressions"),
                               ("Improved", "improvements"),
                               ("New", "new_cases")):
                if cmp[key]:
                    print(f"  {label}: {', '.join(cmp[key])}")
        print()
        print("Green means these cases didn't regress — not 'correct in general'.")

    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
