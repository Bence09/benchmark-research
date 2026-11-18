#!/usr/bin/env python3
"""Research orchestration around pinned mini-SWE-agent; no agent reimplementation."""

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_NAMES = {"small": "small_model", "large": "Qwen/Qwen3.6-35B-A3B-FP8"}
BASE_URL = "https://xrlab-ie318-3.tmit.bme.hu/v1"
DATASET = "princeton-nlp/SWE-bench_Lite"
DEFAULT_TASK = "astropy__astropy-12907"


def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def config(args):
    import yaml
    from minisweagent.config import builtin_config_dir

    name = "swebench.yaml" if args.interface == "tools" else "swebench_xml.yaml"
    cfg = yaml.safe_load((builtin_config_dir / "benchmarks" / name).read_text())
    cfg["agent"].update(step_limit=args.steps, cost_limit=0)
    # Unknown university prices must not be reported as a measured zero cost.
    cfg["model"].update(
        model_name="openai/" + MODEL_NAMES[args.model],
        model_class="litellm" if args.interface == "tools" else "litellm_textbased",
        cost_tracking="ignore_errors",
        model_kwargs={
            "api_base": os.environ.get("BME_API_BASE", BASE_URL),
            "temperature": 0,
            "max_tokens": args.output_tokens,
            "timeout": 600,
            "num_retries": 0,
        },
    )
    cfg["environment"].update(
        environment_class="docker",
        timeout=120,
        env={**cfg["environment"].get("env", {}), "BASH_ENV": "/root/.bashrc"},
    )
    return cfg


def check_key():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "Set OPENAI_API_KEY in this WSL shell first; do not put it in config files."
        )


def probe(args):
    """Test mini's actual parser. This never executes a returned command."""
    check_key()
    from minisweagent.models import get_model

    cfg = config(args)
    model = get_model(config=cfg["model"])
    instruction = (
        "Use the bash tool exactly once with command 'echo API_ACTION_OK'. Do not explain."
        if args.interface == "tools"
        else "Reply exactly: <mswea_bash_command>echo API_ACTION_OK</mswea_bash_command>"
    )
    try:
        msg = model.query([{"role": "user", "content": instruction}])
        actions = msg.get("extra", {}).get("actions", [])
        passed = len(actions) == 1 and actions[0].get("command") == "echo API_ACTION_OK"
    except Exception as exc:  # noqa: BLE001 - provider errors vary by backend.
        print("Compatibility check FAILED:", type(exc).__name__)
        print(
            "Check response fields, server parser configuration, and the selected action interface."
        )
        return 1
    print("Compatibility check", "PASSED" if passed else "FAILED: unexpected action")
    print("No shell command was executed.")
    return 0 if passed else 1


def metrics(trajectory):
    """Sum per-response usage once. Unknown usage remains null, not zero."""
    calls = actions = format_errors = 0
    usages = []
    for msg in trajectory.get("messages", []):
        extra = msg.get("extra", {})
        response = extra.get("response")
        if isinstance(response, dict):
            calls += 1
            if isinstance(response.get("usage"), dict):
                usages.append(response["usage"])
        actions += len(extra.get("actions", []))
        format_errors += extra.get("interrupt_type") == "FormatError"

    def total(key):
        values = [u.get(key) for u in usages]
        return (
            sum(values) if values and all(isinstance(v, int) for v in values) else None
        )

    prompts = [
        u["prompt_tokens"] for u in usages if isinstance(u.get("prompt_tokens"), int)
    ]
    return {
        "recorded_api_responses": calls,
        "responses_with_usage": len(usages),
        "usage_complete": calls > 0 and len(usages) == calls,
        "input_tokens_recorded": total("prompt_tokens"),
        "output_tokens_recorded": total("completion_tokens"),
        "max_input_tokens_recorded": max(prompts) if prompts else None,
        "tool_actions": actions,
        "format_errors": format_errors,
        "monetary_cost": None,
    }


def worker(args):
    # This process sees only issue/repo preparation metadata, not grading labels.
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.models import get_model
    from minisweagent.run.benchmarks.swebench import get_sb_environment

    task = json.loads(Path(args.task).read_text())
    cfg = config(args)
    cfg["agent"]["output_path"] = Path(args.output) / "trajectory.traj.json"
    env = agent = None
    info = {"exit_status": "SetupError", "submission": ""}
    started = time.monotonic()
    failure = None
    try:
        env = get_sb_environment(cfg, task)
        agent = DefaultAgent(get_model(config=cfg["model"]), env, **cfg["agent"])
        info = agent.run(task["problem_statement"])
    except Exception as exc:  # noqa: BLE001 - record any task-run failure.
        failure = type(exc).__name__
        info = {"exit_status": failure, "submission": ""}
        # Avoid logging exception messages that may contain provider credentials.
        print("Run failed:", failure, file=sys.stderr)
    finally:
        out = Path(args.output)
        try:
            if agent is not None:
                agent.save(
                    out / "trajectory.traj.json",
                    {"info": info, "instance_id": task["instance_id"]},
                )
            patch = info.get("submission") or ""
            (out / "patch.diff").write_text(patch)
            dump(
                out / "prediction.json",
                {
                    "instance_id": task["instance_id"],
                    "model_name_or_path": MODEL_NAMES[args.model],
                    "model_patch": patch,
                },
            )
            traj = (
                json.loads((out / "trajectory.traj.json").read_text())
                if (out / "trajectory.traj.json").exists()
                else {}
            )
            dump(
                out / "metrics.json",
                {
                    **metrics(traj),
                    "instance_id": task["instance_id"],
                    "exit_status": info.get("exit_status"),
                    "worker_seconds": time.monotonic() - started,
                },
            )
        finally:
            if env is not None:
                cleanup = getattr(env, "cleanup", None)
                if cleanup:
                    cleanup()
    return 1 if failure else 0


def select_tasks(args):
    from datasets import load_dataset

    ds = load_dataset(DATASET, split="test", revision=args.dataset_revision)
    requested = args.instances.split(",")
    if len(set(requested)) != len(requested):
        raise SystemExit("Duplicate instance IDs are not allowed.")
    lookup = {row["instance_id"]: row for row in ds if row["instance_id"] in requested}
    if set(lookup) != set(requested):
        raise SystemExit(
            "Missing dataset instance IDs: " + str(set(requested) - set(lookup))
        )
    return [lookup[i] for i in requested], ds._fingerprint


def run(args):
    if args.execute:
        check_key()
    if args.execute and probe(args):
        return 1
    if args.execute:
        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, check=True)
    rows, fingerprint = select_tasks(args)
    label = (
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        + "-"
        + args.model
        + "-"
        + uuid.uuid4().hex[:8]
    )
    out = ROOT / "runs" / label
    out.mkdir(parents=True, exist_ok=False)
    dump(out / "selected-tasks.grading.json", rows)
    packages = {}
    for p in ("mini-swe-agent", "litellm", "datasets", "openai"):
        try:
            packages[p] = importlib.metadata.version(p)
        except importlib.metadata.PackageNotFoundError:
            packages[p] = None
    manifest = {
        "run_id": label,
        "model": MODEL_NAMES[args.model],
        "interface": args.interface,
        "dataset": DATASET,
        "split": "test",
        "requested_revision": args.dataset_revision,
        "dataset_fingerprint": fingerprint,
        "instances": [r["instance_id"] for r in rows],
        "packages": packages,
        "python": sys.version,
        "budgets": {
            "steps": args.steps,
            "output_tokens_per_call": args.output_tokens,
            "wall_seconds_per_task_including_setup": args.wall_seconds,
        },
        "endpoint": os.environ.get("BME_API_BASE", BASE_URL),
        "executed": args.execute,
        "selected_tasks_sha256": hashlib.sha256(
            (out / "selected-tasks.grading.json").read_bytes()
        ).hexdigest(),
    }
    dump(out / "manifest.json", manifest)
    with (out / "packages.txt").open("w") as package_file:
        subprocess.run(
            [sys.executable, "-m", "pip", "freeze"], stdout=package_file, check=True
        )
    predictions = []
    for row in rows:
        task_dir = out / row["instance_id"]
        task_dir.mkdir()
        # Never feed patch, test_patch, FAIL_TO_PASS or ground-truth fields to agent.
        task = {
            k: row[k]
            for k in (
                "instance_id",
                "repo",
                "base_commit",
                "problem_statement",
                "image_name",
                "docker_image",
            )
            if k in row
        }
        dump(task_dir / "task.json", task)
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "worker",
            "--task",
            str(task_dir / "task.json"),
            "--output",
            str(task_dir),
            "--model",
            args.model,
            "--interface",
            args.interface,
            "--steps",
            str(args.steps),
            "--output-tokens",
            str(args.output_tokens),
        ]
        print("Task:", row["instance_id"], "->", task_dir)
        if not args.execute:
            dump(task_dir / "planned-command.json", cmd)
            continue
        started = time.monotonic()
        with (task_dir / "agent.log").open("w") as log:
            process = subprocess.Popen(
                cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
            )
            try:
                code = process.wait(timeout=args.wall_seconds)
                status = "completed" if code == 0 else "worker_failed"
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                code, status = process.returncode, "wall_timeout"
        dump(
            task_dir / "process.json",
            {
                "status": status,
                "exit_code": code,
                "wall_seconds": time.monotonic() - started,
            },
        )
        prediction_path = task_dir / "prediction.json"
        predictions.append(
            json.loads(prediction_path.read_text())
            if prediction_path.exists()
            else {
                "instance_id": row["instance_id"],
                "model_name_or_path": MODEL_NAMES[args.model],
                "model_patch": "",
            }
        )
    if args.execute:
        dump(out / "predictions.json", predictions)
    print("Run directory:", out)
    print(
        "Predictions generated; not yet evaluated."
        if args.execute
        else "Plan only: no agent commands executed."
    )
    return 0


def evaluate(args):
    run_dir = Path(args.run_dir).resolve()
    if not (run_dir / "predictions.json").is_file():
        raise SystemExit("No predictions.json: execute a run first.")
    run_id = run_dir.name + "-eval-" + uuid.uuid4().hex[:8]
    cmd = [
        str(Path(args.evaluator_python).expanduser().absolute()),
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        DATASET,
        "--split",
        "test",
        "--predictions_path",
        str(run_dir / "predictions.json"),
        "--max_workers",
        "1",
        "--run_id",
        run_id,
    ]
    dump(run_dir / (run_id + ".command.json"), cmd)
    # Evaluator report/log locations are rooted in this run directory.
    with (run_dir / (run_id + ".log")).open("w") as log:
        result = subprocess.run(
            cmd, check=False, cwd=run_dir, stdout=log, stderr=subprocess.STDOUT
        )
    print("Evaluator exit code:", result.returncode)
    print("Inspect report and log in:", run_dir)
    return result.returncode


def summarize(args):
    run_dir = Path(args.run_dir)
    rows = []
    for task in sorted(run_dir.iterdir()):
        if task.is_dir() and (task / "task.json").exists():
            m = (
                json.loads((task / "metrics.json").read_text())
                if (task / "metrics.json").exists()
                else {}
            )
            p = (
                json.loads((task / "process.json").read_text())
                if (task / "process.json").exists()
                else {}
            )
            rows.append({"instance_id": task.name, **m, **p})
    if not rows:
        raise SystemExit("No task outputs found.")
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with (run_dir / "summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    print(run_dir / "summary.csv")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("probe", "run", "worker"):
        p = sub.add_parser(name)
        p.add_argument("--model", choices=MODEL_NAMES, default="small")
        p.add_argument("--interface", choices=("tools", "text"), default="tools")
        p.add_argument("--steps", type=int, default=30)
        p.add_argument("--output-tokens", type=int, default=2048)
        if name == "run":
            p.add_argument(
                "--instances", default=DEFAULT_TASK, help="Comma-separated exact IDs"
            )
            p.add_argument("--dataset-revision", default="main")
            p.add_argument("--wall-seconds", type=int, default=3600)
            p.add_argument("--execute", action="store_true")
        if name == "worker":
            p.add_argument("--task", required=True)
            p.add_argument("--output", required=True)
    p = sub.add_parser("evaluate")
    p.add_argument("run_dir")
    p.add_argument("--evaluator-python", required=True)
    p = sub.add_parser("summarize")
    p.add_argument("run_dir")
    args = parser.parse_args()
    for name in ("steps", "output_tokens", "wall_seconds"):
        if hasattr(args, name) and getattr(args, name) <= 0:
            parser.error(name + " must be positive")
    return globals()[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
