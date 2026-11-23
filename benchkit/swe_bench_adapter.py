"""SWE-bench Lite adapter.

The agent edits the installed repo inside the task's SWE-bench image and submits a patch,
grading runs the official hidden tests via the pinned harness (swebench 2.1.7 inside .venv-eval).

  - container comes from mini's get_sb_environment (task-specific installed image)
  - evaluate() shells out to swebench.harness.run_evaluation
  - gold fields (patch/test_patch/FAIL_TO_PASS) stay host-only, never in task.json.

NOTE: the index is built at prepare time and is read-only,
a fixing-agent edits files, so the index can go stale mid-run. First cut treats the
index as pre-edit "find the bug" only, churn/staleness deferred.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from .contract import EnvSpec, PreparedTask, PromptSpec, Submission

DATASET = "princeton-nlp/SWE-bench_Lite"
SPLIT = "test"
# Pinned official evaluator env (swebench 2.1.7). Property of SWE-bench,
# independent of our run environment.
DEFAULT_EVALUATOR_PYTHON = (
    Path(__file__).resolve().parent.parent / ".venv-eval" / "bin" / "python"
)

# Repair submission protocol. The agent writes its git diff to patch.txt, verifies it, then finishes with
# the exact sentinel command. mini detects COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT in the
# output, marks the run Submitted, and the submission stdout is the patch (from the real
# `git diff`, not hand-typed). Sentinel is the one mini's model already recognizes.
SYSTEM = """You are a software engineer fixing a bug in a repository checked out at /testbed.
Use the bash tool to inspect, edit and verify code. Make the minimal change that fixes
the issue described. Do not edit tests. {{tool_descriptions}}

When your fix is complete, submit in three separate commands (do NOT combine with &&):
  1. Write your patch:    cd /testbed && git add -A && git diff --cached > patch.txt
  2. Verify it:           cat patch.txt      (confirm only your intended changes, with
                          `--- a/` and `+++ b/` headers)
  3. Submit (EXACT):      echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt

The first stdout line of the submit command must be the sentinel. Submit before your
step budget runs out. You cannot continue working after submitting.
"""

INSTANCE_TEMPLATE = "ISSUE:\n{{task}}\nFix it within {{step_limit}} model calls."


class SweBenchAdapter:
    name = "SWE-bench"

    def required_capabilities(self) -> list[str]:
        # Repair floor: search + read to locate, edit to change, run to verify,
        # submit (patch) to finish.
        return ["grep", "read", "edit", "run", "submit"]

    def agent_prompt(self, task: dict, policy: dict) -> PromptSpec:
        from .swe_explore_adapter import _tool_descriptions

        system = SYSTEM.replace("{{tool_descriptions}}", _tool_descriptions(policy))
        return PromptSpec(system_template=system, instance_template=INSTANCE_TEMPLATE)

    def container_spec(self, prepared: PreparedTask, policy: dict) -> EnvSpec:
        # The image is resolved by get_sb_environment from the instance; recorded for
        # provenance. The real container is built in make_environment.
        return EnvSpec(
            image="(resolved by get_sb_environment from instance)",
            cwd="/testbed",
            run_args=[],
            timeout=120,
            pull_timeout=600,
        )

    def make_environment(
        self,
        prepared: PreparedTask,
        plan,
        cfg: dict,
        *,
        wall_seconds: int,
        index_env: dict,
    ):
        """Build the repair container via mini's get_sb_environment (task image).

        Merges the coordinator's index_env (BASH_ENV shim + mounts) into the mini
        environment config so index_query works identically.
        Returns (env, env_manifest).
        """
        from minisweagent.run.benchmarks.swebench import (
            get_sb_environment,
            get_swebench_docker_image_name,
        )

        instance = prepared.task  # instance_id/repo/base_commit/problem_statement
        image_name = get_swebench_docker_image_name(instance)

        # mini reads environment overrides from cfg["environment"]; merge index wiring.
        env_cfg = dict(cfg.get("environment", {}))
        env_cfg["env"] = {**env_cfg.get("env", {}), **index_env.get("env", {})}
        if index_env.get("run_args"):
            env_cfg["run_args"] = list(env_cfg.get("run_args", [])) + list(
                index_env["run_args"]
            )
        env_cfg["container_timeout"] = f"{wall_seconds + 300}s"
        merged_cfg = {**cfg, "environment": env_cfg}

        env = get_sb_environment(merged_cfg, instance)
        return env, {
            "image": image_name,
            "cwd": "/testbed",
            "mounts": index_env.get("run_args", []),
        }

    def parse_submission(self, text: str, prepared: PreparedTask) -> Submission:
        """Extract the unified diff from the submit output.

        The submit command is `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat
        patch.txt`, so the submission is the sentinel line followed by the patch
        (produced by a real `git diff`, not hand-typed). Strip the sentinel; the
        remainder is the patch. An empty patch is a recorded failure, not a crash.
        """
        raw = text or ""
        SENTINEL = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        if SENTINEL in raw:
            raw = raw.split(SENTINEL, 1)[1]
        if "diff --git" not in raw:
            raise ValueError(
                "Submission contains no unified diff (empty or invalid patch)."
            )
        # Keep from the first real diff header onward (drop any leading prose/sentinel).
        patch = raw[raw.index("diff --git") :].strip("\n")
        return Submission(kind="patch", payload=patch + "\n")

    def prepare(self, spec: dict) -> PreparedTask:  # pragma: no cover - live path
        """Select one Lite instance; keep gold fields host-only, save filtered task.

        `spec` = {"instance_id": ..., "output_dir": ..., optional "dataset_revision"}.
        """
        from datasets import load_dataset

        instance_id = spec["instance_id"]
        out = Path(spec["output_dir"]).expanduser().absolute()
        out.mkdir(parents=True, exist_ok=False)

        ds = load_dataset(
            DATASET, split=SPLIT, revision=spec.get("dataset_revision", "main")
        )
        rows = [r for r in ds if r["instance_id"] == instance_id]
        if len(rows) != 1:
            raise SystemExit(
                f"Expected exactly one {instance_id} in {DATASET}; found {len(rows)}."
            )
        row = rows[0]

        # Host-only grading record (gold patch, tests, FAIL_TO_PASS): never mounted on docker container.
        (out / "selected-task.grading.json").write_text(json.dumps(row) + "\n")
        # Agent-visible task: issue + repo + base only.
        task = {
            k: row[k]
            for k in ("instance_id", "repo", "base_commit", "problem_statement")
            if k in row
        }
        for opt in ("image_name", "docker_image"):
            if row.get(opt):
                task[opt] = row[opt]
        (out / "task.json").write_text(json.dumps(task, indent=2) + "\n")

        # Download the base-commit source snapshot into a clean repo/ subdir.
        # The agent still runs against the image's /testbed, this host snapshot
        # exists only so the index can be built over real source with no grading in
        # the indexed tree (grading stays in out/, outside repo/).
        from .prepare_support import download_repo_snapshot

        snap = download_repo_snapshot(row["repo"], row["base_commit"], out / "repo")

        import hashlib

        prep = {
            "benchmark": self.name,
            "dataset": DATASET,
            "split": SPLIT,
            "dataset_revision": spec.get("dataset_revision", "main"),
            "dataset_fingerprint": ds._fingerprint,
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "archive_sha256": snap["archive_sha256"],
            "skipped_symlinks": snap["skipped_symlinks"],
            "grading_sha256": hashlib.sha256(
                (out / "selected-task.grading.json").read_bytes()
            ).hexdigest(),
            "task_sha256": hashlib.sha256((out / "task.json").read_bytes()).hexdigest(),
        }
        (out / "preparation.json").write_text(json.dumps(prep, indent=2) + "\n")
        return PreparedTask(
            instance_id=instance_id,
            prepared_dir=out,
            repo_dir=out
            / "repo",  # clean source snapshot (no grading); index builds here
            grading_path=out / "selected-task.grading.json",
            task=task,
            preparation=prep,
        )

    def evaluate(
        self,
        prepared: PreparedTask,
        submission: Submission,
        *,
        evaluator_python=None,
        run_dir=None,
    ) -> dict:
        """Grade a patch with the official SWE-bench harness.

        Writes predictions.json, runs swebench.harness.run_evaluation in the pinned
        eval venv with a fresh run id, returns the official resolved status read from
        the harness report (success != resolved).
        """
        if submission.kind != "patch":
            raise ValueError(
                f"SWE-bench expects a patch submission, got {submission.kind!r}"
            )
        run_dir = Path(run_dir or prepared.prepared_dir).expanduser().absolute()
        py = Path(evaluator_python or DEFAULT_EVALUATOR_PYTHON).expanduser().absolute()
        if not py.exists():
            raise FileNotFoundError(
                f"Evaluator python not found: {py} (set up .venv-eval)."
            )

        model_name = "benchkit-swebench"
        predictions = [
            {
                "instance_id": prepared.instance_id,
                "model_name_or_path": model_name,
                "model_patch": submission.payload,
            }
        ]
        (run_dir / "predictions.json").write_text(json.dumps(predictions) + "\n")

        run_id = run_dir.name + "-eval-" + uuid.uuid4().hex[:8]
        cmd = [
            str(py),
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            DATASET,
            "--split",
            SPLIT,
            "--predictions_path",
            str(run_dir / "predictions.json"),
            "--max_workers",
            "1",
            "--run_id",
            run_id,
        ]
        (run_dir / (run_id + ".command.json")).write_text(
            json.dumps(cmd, indent=2) + "\n"
        )
        with (run_dir / (run_id + ".log")).open("w") as log:
            proc = subprocess.run(
                cmd, check=False, cwd=run_dir, stdout=log, stderr=subprocess.STDOUT
            )

        resolved = _read_resolved(run_dir, model_name, prepared.instance_id)
        return {
            "instance_id": prepared.instance_id,
            "resolved": resolved,
            "evaluator_exit_code": proc.returncode,
            "run_id": run_id,
            "scored_artifact": "official SWE-bench hidden-test result",
        }


def _read_resolved(run_dir: Path, model_name: str, instance_id: str):
    """Read resolved/unresolved from the harness report. None if not found."""
    # Harness writes <model_name>.<run_id>.json; find the newest report for this model.
    reports = sorted(
        run_dir.glob(f"{model_name.replace('/', '__')}*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for rep in reports:
        try:
            data = json.loads(rep.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        resolved_ids = data.get("resolved_ids") or []
        if instance_id in resolved_ids:
            return True
        if instance_id in (data.get("unresolved_ids") or []) or instance_id in (
            data.get("error_ids") or []
        ):
            return False
    return None
