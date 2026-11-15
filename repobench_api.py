#!/usr/bin/env python3
"""RepoBench v1.1 completion demonstration through an OpenAI-compatible chat API."""
import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

DATASET = "tianyang/repobench_python_v1.1"
MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"
SYSTEM = "Complete the next line of the Python file at the end of the supplied context. Return only that single code line, with no explanation and no Markdown fences."


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def functions(path, names, namespace=None):
    """Execute only named official function definitions, avoiding GPU/CodeBLEU imports."""
    tree = ast.parse(Path(path).read_text())
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in selected} != set(names):
        raise ValueError(f"Official functions missing in {path}")
    scope = dict(namespace or {})
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), scope)
    return scope


def prepare(args):
    from datasets import load_dataset
    import re
    src = Path(args.source).absolute()
    builder = functions(src / "data/utils.py", ["construct_prompt"], {"re": re})["construct_prompt"]
    ds = load_dataset(DATASET, split=args.split, revision=args.revision, streaming=True)
    chosen = None
    eligible = 0
    for index, row in enumerate(ds):
        if row["level"] != args.level:
            continue
        if eligible == args.index:
            chosen = (index, row)
            break
        eligible += 1
    if chosen is None:
        raise ValueError("Selected sample not found")
    index, row = chosen
    # Allowlist inputs: gold next_line, gold_snippet_index and all_code are excluded.
    inputs = {k: row[k] for k in ["repo_name", "file_path", "context", "import_statement", "cropped_code"]}
    prompt = builder(inputs, language="python", tokenizer=None, max_token_nums=None)
    out = Path(args.output).absolute()
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "input.json", inputs)
    (out / "prompt.txt").write_text(prompt)
    dump(out / "reference.grading.json", {"next_line": row["next_line"]})
    commit = subprocess.check_output(["git", "-C", str(src), "rev-parse", "HEAD"], text=True).strip()
    dump(out / "preparation.json", {"dataset": DATASET, "requested_revision": args.revision,
        "split": args.split, "level": args.level, "index_within_level": args.index,
        "source_row_index": index, "selection": "fixed ordinal, no answer/output filtering",
        "repo_name": row["repo_name"], "file_path": row["file_path"],
        "dataset_token_num": row["token_num"], "context_snippets": len(row["context"]),
        "source_dir": str(src), "source_commit": commit,
        "prompt_builder_sha256": sha(src / "data/utils.py"), "extraction_source_sha256": sha(src / "run.py"),
        "metrics_source_sha256": sha(src / "evaluation/metrics.py"),
        "prompt_sha256": sha(out / "prompt.txt"), "reference_sha256": sha(out / "reference.grading.json"),
        "cropping": "none; full supplied context and cropped_code; demonstration limited to 2k by default"})
    print("Prepared:", out)
    print("Repository:", row["repo_name"], "File:", row["file_path"])
    print("Context snippets:", len(row["context"]), "No model calls made.")
    return 0


def run(args):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("Set OPENAI_API_KEY in this shell first.")
    prepared = Path(args.prepared).absolute()
    prep = json.loads((prepared / "preparation.json").read_text())
    if sha(prepared / "prompt.txt") != prep["prompt_sha256"]:
        raise ValueError("Prepared prompt changed")
    body = {"model": args.model, "messages": [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": (prepared / "prompt.txt").read_text()}],
            "temperature": 0, "max_tokens": args.output_tokens}
    out = Path(__file__).absolute().parent / "runs" / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-repobench-" + uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    dump(out / "request.json", body)
    dump(out / "manifest.json", {"benchmark": "RepoBench v1.1 Python completion", "protocol": "chat API adaptation; no tools or agent loop",
        "prepared_dir": str(prepared), "preparation": prep,
        "model": args.model, "endpoint": os.environ.get("BME_API_BASE", "https://xrlab-ie318-3.tmit.bme.hu/v1"),
        "output_tokens": args.output_tokens, "temperature": 0})
    base = os.environ.get("BME_API_BASE", "https://xrlab-ie318-3.tmit.bme.hu/v1").rstrip("/")
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        dump(out / "metrics.json", {"status": "HTTPError", "http_status": exc.code, "wall_seconds": time.monotonic() - start})
        raise SystemExit(f"API failed with HTTP {exc.code}. Run directory: {out}")
    dump(out / "response.json", data)
    choice = data["choices"][0]
    text = choice["message"].get("content")
    dump(out / "metrics.json", {"wall_seconds": time.monotonic() - start, "api_calls": 1,
        "usage": data.get("usage"), "finish_reason": choice.get("finish_reason"),
        "system_fingerprint": data.get("system_fingerprint"), "content_present": bool(text), "monetary_cost": None})
    print("Run directory:", out)
    if not text:
        raise SystemExit("No content returned. Raw response saved; reasoning is not used as the completion.")
    (out / "completion.raw.txt").write_text(text)
    if choice.get("finish_reason") == "length":
        raise SystemExit("Generation truncated. Raw response retained; score only after reviewing this failure.")
    return 0


def score(args):
    from fuzzywuzzy import fuzz
    out = Path(args.run_dir).absolute()
    if json.loads((out / "metrics.json").read_text()).get("finish_reason") == "length":
        raise ValueError("Refusing to score a truncated response as a completed run")
    manifest = json.loads((out / "manifest.json").read_text())
    prep = manifest["preparation"]
    prepared = Path(manifest["prepared_dir"])
    src = Path(prep["source_dir"])
    for key, path in [("reference_sha256", prepared / "reference.grading.json"),
                      ("extraction_source_sha256", src / "run.py"),
                      ("metrics_source_sha256", src / "evaluation/metrics.py")]:
        if sha(path) != prep[key]:
            raise ValueError(f"Source/input changed: {path}")
    extractor = functions(src / "run.py", ["get_first_line_not_comment"])["get_first_line_not_comment"]
    raw = (out / "completion.raw.txt").read_text()
    # Strip a fence only when it encloses the entire response; preserve raw response.
    text = raw
    stripped = raw.strip()
    lines = stripped.splitlines()
    fenced = len(lines) >= 3 and lines[0] in ("```", "```python") and lines[-1] == "```"
    if fenced:
        text = "\n".join(lines[1:-1])
    pred = extractor(text, language="python") or ""
    gt = json.loads((prepared / "reference.grading.json").read_text())["next_line"]
    metric = functions(src / "evaluation/metrics.py", ["exact_match_score", "edit_similarity_score"], {"fuzz": fuzz})
    result = {"exact_match": metric["exact_match_score"]([pred], [gt]),
        "edit_similarity_0_to_100": metric["edit_similarity_score"]([pred], [gt]),
        "codebleu": None, "codebleu_status": "not computed in this demonstration",
        "prediction": pred, "reference": gt, "fences_removed": fenced,
        "extraction": "upstream get_first_line_not_comment after optional whole-response fence removal",
        "fuzzywuzzy_version": importlib.metadata.version("fuzzywuzzy"),
        "similarity_backend": fuzz.SequenceMatcher.__module__, "metrics_source_sha256": prep["metrics_source_sha256"]}
    dump(out / "scores.json", result)
    dump(out / "prediction.json", {"idx": prep["source_row_index"], "level": prep["level"], "pred": pred, "gt": gt})
    (out / (prep["split"] + ".jsonl")).write_text(json.dumps({"idx": prep["source_row_index"], "level": prep["level"], "pred": pred, "gt": gt}) + "\n")
    print(json.dumps(result, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("prepare")
    q.add_argument("--source", default="repobench-src")
    q.add_argument("--split", choices=("cross_file_first", "cross_file_random", "in_file"), default="cross_file_first")
    q.add_argument("--level", default="2k")
    q.add_argument("--index", type=int, default=0)
    q.add_argument("--revision", default="main")
    q.add_argument("--output", default="repobench-input/first-2k")
    q = sub.add_parser("run")
    q.add_argument("--prepared", default="repobench-input/first-2k")
    q.add_argument("--model", default=MODEL)
    q.add_argument("--output-tokens", type=int, default=2048)
    q = sub.add_parser("score")
    q.add_argument("run_dir")
    args = p.parse_args()
    if getattr(args, "index", 0) < 0 or getattr(args, "output_tokens", 1) <= 0:
        p.error("Index must be nonnegative and output tokens positive")
    return globals()[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
