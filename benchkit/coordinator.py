"""Run coordinator.

Benchmark-agnostic lifecycle. The core function resolves the policy,
validates the adapter's required capabilities, builds and
serializes the index when the policy adds `index_query`,
and produces a RunPlan (prompt + container spec including the index mount + manifest
fields).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from toolpolicy import render_summary, resolve

IN_CONTAINER_DIR = Path(__file__).resolve().parent / "in_container"
INDEX_MOUNT_DST = "/index"
INDEX_TOOL_DST = "/opt/index_query.py"


@dataclass
class RunPlan:
    adapter_name: str
    condition: str | None
    effective_policy: dict
    prompt: object  # PromptSpec
    env: object  # EnvSpec (with index mount appended when applicable)
    index_provenance: dict | None
    manifest: dict
    summary: str
    index_spec: dict | None = None  # {serialized, limits} when policy adds index_query


def plan_run(
    adapter,
    prepared,
    policy_file: str | dict,
    condition: str | None,
    *,
    top_k: int = 5,
    build_index=None,
) -> RunPlan:
    """Resolve policy, validate caps, build+serialize index if added, plan the run.

    `build_index(repo_dir, serialized_out) -> provenance_dict` is injected so the
    coordinator stays independent of a concrete payload. None means no index build attempted.
    """
    effective = resolve(
        policy_file, condition, required_capabilities=adapter.required_capabilities()
    )

    prompt = adapter.agent_prompt(prepared.task, effective)
    env = adapter.container_spec(prepared, effective)

    index_provenance = None
    index_spec = None
    if _policy_adds_index(effective):
        if build_index is None:
            raise RuntimeError(
                "policy adds index_query but no index builder was provided"
            )
        serialized = Path(prepared.prepared_dir) / "index.json"
        index_provenance = build_index(prepared.repo_dir, serialized)
        # Carry the serialized index path + the resolved policy's index limits so
        # execute() can wire them into index_env.
        # NOTE: do not append to EnvSpec.run_args -- SWE-bench builds its container via
        # get_sb_environment and ignores EnvSpec.run_args, which stranded the mounts.
        idx_tool = next(
            (t for t in effective.get("tools", []) if t.get("name") == "index_query"),
            {},
        )
        index_spec = {
            "serialized": str(serialized),
            "limits": idx_tool.get("limits") or {},
        }

    manifest = {
        "benchmark": adapter.name,
        "instance_id": prepared.instance_id,
        "condition": condition,
        "effective_policy": effective,
        "required_capabilities": adapter.required_capabilities(),
        "top_k": top_k,
        "index_provenance": index_provenance,
        "mounts": env.mounts,
        "image": env.image,
    }
    plan = RunPlan(
        adapter_name=adapter.name,
        condition=condition,
        effective_policy=effective,
        prompt=prompt,
        env=env,
        index_provenance=index_provenance,
        manifest=manifest,
        summary=render_summary(effective),
    )
    plan.index_spec = index_spec
    return plan


def _policy_adds_index(effective: dict) -> bool:
    return any(
        t.get("name") == "index_query" and t.get("status") == "added"
        for t in effective.get("tools", [])
    )


def _build_index_env(index_env: dict, index_spec: dict, repo_in_container: str) -> None:
    """Populate index_env with everything the in-container index tool needs.

    Writes into index_env (run_args + env), the single channel every adapter's
    make_environment merges. Mounts the serialized index read-only,
    defines the index_query shell shim via BASH_ENV,
    sets INDEX_REPO to the container's actual repo path, and
    passes the policy limits so the tool enforces them.
    """
    from pathlib import Path as _Path

    serialized = _Path(index_spec["serialized"])
    limits = index_spec.get("limits") or {}
    cli = IN_CONTAINER_DIR / "index_query.py"
    # Read-only mounts into a --cap-drop=ALL container require world-readable files.
    os.chmod(cli, 0o644)

    shim = "/tmp/.index_shim"
    _write_shim_bootstrap_into(index_env, shim)

    index_env["run_args"] += [
        "--mount",
        f"type=bind,src={serialized},dst={INDEX_MOUNT_DST}/index.json,readonly",
        "--mount",
        f"type=bind,src={cli},dst={INDEX_TOOL_DST},readonly",
    ]
    index_env["env"].update(
        {
            "BASH_ENV": shim,
            "INDEX_FILE": f"{INDEX_MOUNT_DST}/index.json",
            "INDEX_REPO": repo_in_container,
        }
    )
    if "top_k" in limits:
        index_env["env"]["INDEX_MAX_TOP_K"] = str(int(limits["top_k"]))
    if "max_snippet_bytes" in limits:
        index_env["env"]["INDEX_MAX_SNIPPET"] = str(int(limits["max_snippet_bytes"]))


class Coordinator:
    """Plans runs offline; `execute()` drives mini in Docker."""

    def __init__(self, adapter):
        self.adapter = adapter

    def plan(self, prepared, policy_file, condition, **kw) -> RunPlan:
        return plan_run(self.adapter, prepared, policy_file, condition, **kw)

    def execute(
        self,
        plan: RunPlan,
        prepared,
        *,
        model="large",
        steps=30,
        output_tokens=2048,
        wall_seconds=3600,
        runs_root=None,
    ):
        """Run one plan end-to-end: mini in Docker -> parse -> grade -> persist.

        Needs Docker running and OPENAI_API_KEY set. Returns the run directory.
        """
        import argparse
        import json
        import subprocess
        import time
        import uuid

        from minisweagent.agents.default import DefaultAgent
        from minisweagent.models import get_model

        from benchkit import model_config
        from metrics import read_metrics

        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("Set OPENAI_API_KEY in your shell first.")

        subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, check=True)

        root = (
            Path(runs_root).expanduser()
            if runs_root
            else (Path(__file__).resolve().parent.parent / "runs")
        )
        label = (
            time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            + "-"
            + (plan.condition or "default")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        out = root / label
        out.mkdir(parents=True)

        # Shared model/connection config + token accounting.
        settings = argparse.Namespace(
            interface="tools", model=model, steps=steps, output_tokens=output_tokens
        )
        cfg = model_config.config(settings)
        agent_cfg = cfg["agent"]
        agent_cfg.update(
            system_template=plan.prompt.system_template,
            instance_template=plan.prompt.instance_template,
            output_path=out / "trajectory.traj.json",
            wall_time_limit_seconds=wall_seconds,
        )

        # Everything the index tool needs goes
        # into index_env, which the adapter's make_environment merges into whatever
        # container it builds.
        index_env = {"env": {}, "run_args": []}
        if _policy_adds_index(plan.effective_policy) and plan.index_spec:
            # repo path differs per benchmark: /repo (Explore) vs /testbed (SWE-bench).
            repo_in_container = getattr(plan.env, "cwd", "/repo")
            _build_index_env(index_env, plan.index_spec, repo_in_container)

        # The adapter owns environment construction.
        env, env_manifest = self.adapter.make_environment(
            prepared, plan, cfg, wall_seconds=wall_seconds, index_env=index_env
        )

        manifest = {
            **plan.manifest,
            **env_manifest,
            "model": model_config.MODEL_NAMES[model],
            "steps": steps,
            "output_tokens_per_call": output_tokens,
            "wall_time_limit_seconds": wall_seconds,
            "prepared_dir": str(prepared.prepared_dir),
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print("Run directory:", out, flush=True)
        print(plan.summary, flush=True)

        agent = None
        started = time.monotonic()
        info = {"exit_status": "SetupError", "submission": ""}
        try:
            agent = DefaultAgent(get_model(config=cfg["model"]), env, **agent_cfg)
            info = agent.run(
                prepared.task["problem_statement"], top_k=plan.manifest.get("top_k", 5)
            )
            (out / "submission.txt").write_text(info.get("submission") or "")
            if info.get("exit_status") in ("Submitted", "Completed"):
                sub = self.adapter.parse_submission(info["submission"], prepared)
                (out / "submission.parsed.json").write_text(
                    json.dumps({"kind": sub.kind, "payload": sub.payload}, indent=2)
                    + "\n"
                )
            else:
                print("No valid submission:", info.get("exit_status"))
        finally:
            if agent is not None:
                traj = agent.save(
                    out / "trajectory.traj.json",
                    {"info": info, "instance_id": prepared.instance_id},
                )
                merged = {
                    **model_config.metrics(traj),
                    **read_metrics(traj),
                    "exit_status": info.get("exit_status"),
                    "wall_seconds": time.monotonic() - started,
                    "valid_regions_saved": (out / "submission.parsed.json").exists(),
                }
                (out / "metrics.json").write_text(json.dumps(merged, indent=2) + "\n")
            if env is not None:
                cleanup = getattr(env, "cleanup", None)
                if cleanup:
                    cleanup()
        return out


def _write_shim_bootstrap_into(index_env: dict, shim_path: str) -> None:
    """Write the `index_query` shell-function bootstrap and add its read-only mount
    to index_env. The adapter merges index_env["run_args"]/["env"] into whatever
    environment it builds.
    """
    import tempfile

    bootstrap = (
        f'index_query() {{ python3 {INDEX_TOOL_DST} "$@"; }}\nexport -f index_query\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".index_shim", delete=False) as fd:
        fd.write(bootstrap)
        shim_file = fd.name
    # NamedTemporaryFile creates 0600 files owned by the host user. The container
    # runs with --cap-drop=ALL (no DAC_OVERRIDE), so root inside cannot read it
    # unless it is world-readable.
    os.chmod(shim_file, 0o644)
    index_env["run_args"] += [
        "--mount",
        f"type=bind,src={shim_file},dst={shim_path},readonly",
    ]
