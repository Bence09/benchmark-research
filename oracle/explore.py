#!/usr/bin/env python3
"""SWE-Explore adapter for the research-bench-starter's mini-SWE-agent setup."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
INSTANCE = "pydata__xarray-4629"
EVALUATOR_SHA = "5602f031f2d9562d0a805f83402b536e831a5a11"
METRICS = ["precision", "recall", "f1_score", "hit_file_rate", "noise_file_rate",
           "hit_region_rate", "noise_region_rate", "weighted_core_coverage",
           "context_efficiency", "recall_at_100", "recall_at_300", "recall_at_500",
           "ndcg_at_100", "ndcg_at_300", "ndcg_at_500", "first_useful_hit"]


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_record(path, instance):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    found = [r for r in rows if r.get("instance_id") == instance]
    if len(found) != 1:
        raise ValueError(f"Expected one {instance} record, found {len(found)}")
    row = found[0]
    if not isinstance(row.get("ground_truth"), dict) or not row["ground_truth"].get("read_core_regions"):
        raise ValueError("Expected normalized SWE-Explore JSONL with nonempty ground_truth.read_core_regions")
    return row


def unpack_snapshot(data, destination):
    """Strip archive root; reject traversal, links, and special files."""
    destination = Path(destination)
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] in ("/", "..") or ".." in parts:
                raise ValueError("Unsafe archive member")
            if len(parts) < 2:
                continue
            relative = Path(*parts[1:])
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source:
                    target.write_bytes(source.read())
            elif member.issym() or member.islnk():
                raise ValueError("Repository archive contains links; prepare manually after reviewing them")
            else:
                raise ValueError("Unsupported archive member")


def prepare(args):
    from datasets import load_dataset
    record = select_record(args.bench, args.instance)
    # Source metadata supplies only issue/repo/base commit, never its gold patch.
    dataset = load_dataset(args.task_dataset, split=args.task_split, revision=args.task_revision)
    found = [r for r in dataset if r.get("instance_id") == args.instance]
    if len(found) != 1:
        raise SystemExit("Task not found in source dataset. Supply the correct --task-dataset/--task-split.")
    source = found[0]
    commit = source["base_commit"]
    meta = record.get("meta") or {}
    stated_commit = record.get("base_commit") or meta.get("base_commit")
    if stated_commit and stated_commit != commit:
        raise SystemExit("Explore and source dataset base commits differ; resolve mapping before running.")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise SystemExit("Expected a full 40-character base commit.")
    repo = source["repo"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise SystemExit("Invalid repository name")
    out = Path(args.output).expanduser().absolute()
    out.mkdir(parents=True, exist_ok=False)
    # Grading record stays outside the mounted repository.
    (out / "benchmark.grading.jsonl").write_text(json.dumps(record) + "\n")
    task = {k: source[k] for k in ("instance_id", "repo", "base_commit", "problem_statement")}
    dump(out / "task.json", task)
    url = f"https://api.github.com/repos/{repo}/tarball/{commit}"
    request = urllib.request.Request(url, headers={"User-Agent": "research-bench-explore"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    unpack_snapshot(data, out / "repo")
    # Validate grading path/ranges at this revision before any API calls.
    counts = line_counts(out / "repo")
    for region in record["ground_truth"]["read_core_regions"]:
        path, start, end = region_values(region)
        if path not in counts or start < 1 or (end != -1 and not start <= end <= counts[path]):
            raise ValueError(f"Ground-truth region does not match snapshot: {path}:{start}-{end}")
    dump(out / "preparation.json", {"instance_id": args.instance, "repo": repo,
        "base_commit": commit, "base_commit_cross_checked_in_explore_record": bool(stated_commit),
        "source_dataset": args.task_dataset, "source_revision": args.task_revision,
        "source_fingerprint": dataset._fingerprint, "archive_sha256": hashlib.sha256(data).hexdigest(),
        "grading_sha256": digest(out / "benchmark.grading.jsonl"),
        "task_sha256": digest(out / "task.json")})
    print("Prepared:", out)
    print("No model calls made. Gold labels stay outside the repository mount.")


def line_counts(repo):
    repo = Path(repo).resolve()
    result = {}
    for path in repo.rglob("*"):
        if path.is_file() and not path.is_symlink() and ".git" not in path.relative_to(repo).parts:
            try:
                result[path.relative_to(repo).as_posix()] = len(path.read_text(encoding="utf-8").splitlines())
            except (UnicodeError, OSError):
                pass
    return result


def region_values(region):
    return (region["path"], region["start"], region["end"]) if isinstance(region, dict) else tuple(region)


def parse_regions(text, counts, top_k):
    if "RELEVANT_FILES:" not in text:
        raise ValueError("Submission lacks RELEVANT_FILES:")
    regions = []
    for line in text.split("RELEVANT_FILES:", 1)[1].splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*-\s+(.+?):(\d+)-(\d+)\s*", line)
        if not match:
            raise ValueError("Expected '- relative/path.py:start-end' for each region")
        path, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        parts = PurePosixPath(path)
        if parts.is_absolute() or ".." in parts.parts or "\\" in path or path not in counts:
            raise ValueError(f"Invalid repository-relative path: {path}")
        if not 1 <= start <= end <= counts[path]:
            raise ValueError(f"Invalid line range for {path}")
        region = (path, start, end)
        if region in regions:
            raise ValueError("Duplicate submitted region")
        regions.append(region)
    if not 1 <= len(regions) <= top_k:
        raise ValueError(f"Expected 1 to {top_k} ranked regions")
    return regions


SYSTEM = """You explore source code to locate context relevant to an issue.
Use the bash tool to search and read files in /repo. The repository is read-only.
Do not fix the issue, install packages, run repository code, or try network access.
Use short, targeted commands such as find, grep, nl, and sed -n.
Return at most {{top_k}} regions ranked most relevant first. Use repository-relative
paths and exact 1-based inclusive line numbers. Finish with a bash tool call that
prints this format (substitute actual paths/ranges):
cat <<'EOF'
COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
RELEVANT_FILES:
- package/module.py:10-40
- package/other.py:50-80
EOF
The first stdout line must be the submit sentinel. Submit before your step budget.
"""


def run(args):
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY in your shell first.")
    import bench
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models import get_model
    prepared = Path(args.prepared).expanduser().absolute()
    task = json.loads((prepared / "task.json").read_text())
    prep = json.loads((prepared / "preparation.json").read_text())
    if digest(prepared / "task.json") != prep["task_sha256"]:
        raise SystemExit("Prepared task changed; prepare again.")
    subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, check=True)
    # Use a dedicated lightweight tools image; no project dependencies needed.
    subprocess.run(["docker", "pull", args.image], check=True)
    image = subprocess.check_output(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True).strip()
    label = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-explore-" + uuid.uuid4().hex[:8]
    out = ROOT / "runs" / label
    out.mkdir(parents=True)
    # Reuse connection settings and accounting, but replace the repair prompt.
    settings = argparse.Namespace(interface="tools", model=args.model, steps=args.steps, output_tokens=args.output_tokens)
    cfg = bench.config(settings)
    agent_cfg = cfg["agent"]
    agent_cfg.update(system_template=SYSTEM, instance_template="ISSUE:\n{{task}}\nReturn up to {{top_k}} regions within {{step_limit}} model calls.",
                     output_path=out / "trajectory.traj.json", wall_time_limit_seconds=args.wall_seconds)
    env_cfg = dict(image=image, cwd="/repo", timeout=60, pull_timeout=120, interpreter=["bash", "-c"],
        run_args=["--rm", "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                  "--mount", f"type=bind,src={prepared / 'repo'},dst=/repo,readonly",
                  "--tmpfs", "/tmp:rw,nosuid,size=64m"], container_timeout=f"{args.wall_seconds + 300}s")
    dump(out / "manifest.json", {"benchmark": "SWE-Explore", "task": task,
        "preparation": prep, "prepared_dir": str(prepared), "model": bench.MODEL_NAMES[args.model],
        "top_k_regions": args.top_k, "steps": args.steps, "output_tokens_per_call": args.output_tokens,
        "image": image, "wall_time_limit_seconds": args.wall_seconds,
        "policy": "read-only repository; no container network; submitted ranked regions"})
    print("Run directory:", out, flush=True)
    env = agent = None
    start = time.monotonic()
    info = {"exit_status": "SetupError", "submission": ""}
    try:
        env = DockerEnvironment(**env_cfg)
        agent = DefaultAgent(get_model(config=cfg["model"]), env, **agent_cfg)
        info = agent.run(task["problem_statement"], top_k=args.top_k)
        (out / "submission.txt").write_text(info.get("submission") or "")
        if info.get("exit_status") == "Submitted":
            regions = parse_regions(info["submission"], line_counts(prepared / "repo"), args.top_k)
            dump(out / "regions.json", [dict(zip(("path", "start", "end"), r)) for r in regions])
        else:
            print("No valid submission:", info.get("exit_status"))
    finally:
        if agent is not None:
            trajectory = agent.save(out / "trajectory.traj.json", {"info": info, "instance_id": task["instance_id"]})
            dump(out / "metrics.json", {**bench.metrics(trajectory), "exit_status": info.get("exit_status"),
                 "wall_seconds": time.monotonic() - start, "valid_regions_saved": (out / "regions.json").exists()})
        if env is not None:
            env.cleanup()
    return 0 if (out / "regions.json").exists() else 1


def score(args):
    run_dir = Path(args.run_dir).absolute()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    prepared = Path(manifest["prepared_dir"])
    grading = prepared / "benchmark.grading.jsonl"
    if digest(grading) != manifest["preparation"]["grading_sha256"]:
        raise SystemExit("Grading record changed; refusing to score.")
    evaluator_file = Path(args.evaluator_source).expanduser().absolute() / "eval.py"
    spec = importlib.util.spec_from_file_location("swe_explore_official_evaluator", evaluator_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    iid = manifest["task"]["instance_id"]
    regions = json.loads((run_dir / "regions.json").read_text())
    counts = line_counts(prepared / "repo")
    # Revalidate persisted predictions before passing them to the scorer.
    submission = "RELEVANT_FILES:\n" + "\n".join(f"- {r['path']}:{r['start']}-{r['end']}" for r in regions)
    predictions = parse_regions(submission, counts, manifest["top_k_regions"])
    evaluator = module.ExploreEvaluator(bench_data_path=grading, file_line_counts={iid: counts})
    scores = evaluator.evaluate(explore_method=lambda issue, instance: predictions, instance_ids=[iid], metrics=METRICS)
    result = {"instance_id": iid, "scores": scores[iid], "evaluator_file_sha256": digest(evaluator_file),
              "scored_artifact": "submitted ranked regions; not all observed reads"}
    dump(run_dir / "scores.json", result)
    print(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--bench", required=True, help="Existing normalized SWE-Explore JSONL")
    p.add_argument("--instance", default=INSTANCE)
    p.add_argument("--output", default="explore-input/xarray-4629")
    p.add_argument("--task-dataset", default="princeton-nlp/SWE-bench_Verified")
    p.add_argument("--task-split", default="test")
    p.add_argument("--task-revision", default="main")
    p = sub.add_parser("run")
    p.add_argument("--prepared", default="explore-input/xarray-4629")
    p.add_argument("--model", choices=("small", "large"), default="large")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--steps", type=int, default=30)
    p.add_argument("--output-tokens", type=int, default=2048)
    p.add_argument("--wall-seconds", type=int, default=3600)
    p.add_argument("--image", default="python:3.12-slim")
    p = sub.add_parser("score")
    p.add_argument("run_dir")
    p.add_argument("--evaluator-source", required=True)
    args = parser.parse_args()
    for key in ("top_k", "steps", "output_tokens", "wall_seconds"):
        if hasattr(args, key) and getattr(args, key) <= 0:
            parser.error(f"{key} must be positive")
    return globals()[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
