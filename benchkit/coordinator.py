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
    if _policy_adds_index(effective):
        if build_index is None:
            raise RuntimeError(
                "policy adds index_query but no index builder was provided"
            )
        serialized = Path(prepared.prepared_dir) / "index.json"
        index_provenance = build_index(prepared.repo_dir, serialized)
        # Pass the resolved policy's index limits so the in-container tool actually
        # enforces them (recorded limits must also be applied, not just logged).
        idx_tool = next((t for t in effective.get("tools", [])
                         if t.get("name") == "index_query"), {})
        _append_index_mounts(env, serialized, idx_tool.get("limits") or {})

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
    return RunPlan(
        adapter_name=adapter.name,
        condition=condition,
        effective_policy=effective,
        prompt=prompt,
        env=env,
        index_provenance=index_provenance,
        manifest=manifest,
        summary=render_summary(effective),
    )


def _policy_adds_index(effective: dict) -> bool:
    return any(
        t.get("name") == "index_query" and t.get("status") == "added"
        for t in effective.get("tools", [])
    )


def _append_index_mounts(env, serialized_index: Path, limits: dict | None = None) -> None:
    """Add the read-only index artifact + the pure-Python CLI to the container.

    Lockdown flags are untouched, we only add read-only mounts and env vars.
    `limits` (from the resolved policy's index_query tool) are passed into the
    container so the tool ENFORCES them (top_k cap, snippet byte cap), not just
    records them -- otherwise the CLI falls back to its own defaults (top_k 10).
    """
    limits = limits or {}
    cli = IN_CONTAINER_DIR / "index_query.py"
    # Both files are mounted read-only into a --cap-drop=ALL container (no
    # DAC_OVERRIDE), so root inside can only read them if world-readable. index.json
    # is chmod'd by the serializer; ensure the CLI is too (a fresh git checkout may
    # not be world-readable, and /mnt/c's 0777 masks this locally).
    os.chmod(cli, 0o644)
    # Mount the serialized index dir and the CLI, both read-only.
    env.run_args += [
        "--mount",
        f"type=bind,src={serialized_index},dst={INDEX_MOUNT_DST}/index.json,readonly",
        "--mount",
        f"type=bind,src={cli},dst={INDEX_TOOL_DST},readonly",
        "--env",
        f"INDEX_FILE={INDEX_MOUNT_DST}/index.json",
        "--env",
        "INDEX_REPO=/repo",
    ]
    if "top_k" in limits:
        env.run_args += ["--env", f"INDEX_MAX_TOP_K={int(limits['top_k'])}"]
    if "max_snippet_bytes" in limits:
        env.run_args += ["--env", f"INDEX_MAX_SNIPPET={int(limits['max_snippet_bytes'])}"]
    env.mounts += [
        {
            "src": str(serialized_index),
            "dst": f"{INDEX_MOUNT_DST}/index.json",
            "readonly": True,
        },
        {"src": str(cli), "dst": INDEX_TOOL_DST, "readonly": True},
    ]


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

        # Index wiring is benchmark-agnostic, when the policy adds index_query, the
        # coordinator prepares the BASH_ENV shim; the adapter merges index_env into
        # whatever environment it builds.
        index_env = {"env": {}, "run_args": []}
        if _policy_adds_index(plan.effective_policy):
            shim = "/tmp/.index_shim"
            index_env["env"]["BASH_ENV"] = shim
            _write_shim_bootstrap_into(index_env, shim)

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
