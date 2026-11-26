#!/usr/bin/env python3
"""Summarize Bedrock Evaluations per-record scores.

    python summarize_eval.py eval_results

Searches the downloaded results folder for the job's output JSONL file(s),
matches each record to its test id in harness-tests.json (by prompt), and
writes eval_scores.md: a per-record table you can quote in README.md.
"""

import json
import sys
from pathlib import Path


def find_records(root: Path):
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "automatedEvaluationResult" in rec:
                yield rec


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "eval_results")
    tests = json.loads(Path("harness-tests.json").read_text(encoding="utf-8"))["tests"]
    id_by_prompt = {t["prompt"].strip(): t["id"] for t in tests}

    rows = []
    for rec in find_records(root):
        prompt = (rec.get("inputRecord", {}).get("prompt") or rec.get("prompt") or "").strip()
        scores = rec["automatedEvaluationResult"].get("scores", [])
        for s in scores:
            rows.append({
                "id": id_by_prompt.get(prompt, "?"),
                "metric": s.get("metricName", "?"),
                "score": s.get("result"),
                "explanation": " ".join(str(s.get("evaluatorDetails", [{}])[0].get("explanation", "")).split())
                if s.get("evaluatorDetails") else "",
            })

    if not rows:
        sys.exit(f"No evaluation records found under {root}. Is the job Completed?")

    rows.sort(key=lambda r: r["id"])
    numeric = [r["score"] for r in rows if isinstance(r["score"], (int, float))]
    avg = sum(numeric) / len(numeric) if numeric else float("nan")

    lines = ["# Bedrock Evaluations - per-record scores", "",
             f"Records: {len(rows)}  |  Average {rows[0]['metric']}: {avg:.2f}", "",
             "| Test id | Metric | Score | Judge explanation (truncated) |",
             "|---|---|---|---|"]
    for r in rows:
        expl = r["explanation"][:220].replace("|", "/")
        lines.append(f"| {r['id']} | {r['metric']} | {r['score']} | {expl} |")
    Path("eval_scores.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for r in rows:
        print(f"{r['id']:<34} {r['score']}")
    print(f"\nAverage: {avg:.2f}  ->  wrote eval_scores.md")


if __name__ == "__main__":
    main()
