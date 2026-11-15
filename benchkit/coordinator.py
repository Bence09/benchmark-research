"""Experiment coordinator (E3).

Benchmark-agnostic lifecycle. The offline-verifiable core is `plan_run`: it
resolves the policy (E1), validates the adapter's required capabilities, builds and
serializes the index when the policy adds `index_query` (E2 → Option B artifact),
and produces a RunPlan (prompt + container spec incl. the index mount + manifest
fields). The live `execute()` that calls mini in Docker is wired but not run here.

Adding a tool never touches the adapter's evaluate(); adding a benchmark never
touches the index tool (R5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from toolpolicy import resolve, render_summary

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


def plan_run(adapter, prepared, policy_file: str | dict, condition: str | None,
             *, top_k: int = 5, build_index=None) -> RunPlan:
    """Resolve policy, validate caps, build+serialize index if added, plan the run.

    `build_index(repo_dir, serialized_out) -> provenance_dict` is injected so the
    coordinator stays independent of a concrete payload (tests pass a fake; the
    live path passes the BM25 builder). None means no index build attempted.
    """
    effective = resolve(policy_file, condition,
                        required_capabilities=adapter.required_capabilities())

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
        _append_index_mounts(env, serialized)

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
    return any(t.get("name") == "index_query" and t.get("status") == "added"
               for t in effective.get("tools", []))


def _append_index_mounts(env, serialized_index: Path) -> None:
    """Add the read-only index artifact + the pure-Python CLI to the container.

    Lockdown flags are untouched (R11); we only add read-only mounts and env vars.
    """
    cli = IN_CONTAINER_DIR / "index_query.py"
    # Mount the serialized index dir and the CLI, both read-only.
    env.run_args += [
        "--mount", f"type=bind,src={serialized_index},dst={INDEX_MOUNT_DST}/index.json,readonly",
        "--mount", f"type=bind,src={cli},dst={INDEX_TOOL_DST},readonly",
        "--env", f"INDEX_FILE={INDEX_MOUNT_DST}/index.json",
        "--env", "INDEX_REPO=/repo",
    ]
    env.mounts += [
        {"src": str(serialized_index), "dst": f"{INDEX_MOUNT_DST}/index.json", "readonly": True},
        {"src": str(cli), "dst": INDEX_TOOL_DST, "readonly": True},
    ]


class Coordinator:
    """Thin wrapper; the live execute() is wired but not exercised offline."""

    def __init__(self, adapter):
        self.adapter = adapter

    def plan(self, prepared, policy_file, condition, **kw) -> RunPlan:
        return plan_run(self.adapter, prepared, policy_file, condition, **kw)

    def execute(self, plan: RunPlan, prepared):  # pragma: no cover - live Docker/API
        raise NotImplementedError(
            "Live execution: construct mini DockerEnvironment from plan.env, run "
            "DefaultAgent with plan.prompt, parse via adapter.parse_submission, "
            "evaluate via adapter.evaluate. Mirrors oracle explore.py run(); needs "
            "Docker + OPENAI_API_KEY (E3 live step, not run in authoring)."
        )
