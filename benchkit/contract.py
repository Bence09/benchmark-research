"""Benchmark adapter contract.

Generalizes the explore.py's prepare/run/score into the benchmark-agnostic
protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

RUN_TIMEOUT = 60  # seconds to wait for a container to run
PULL_TIMEOUT = 120  # seconds to wait for a container image to pull


@dataclass
class PreparedTask:
    """Output of prepare(): a sanitized, hashed task ready to run.

    The agent-visible repo lives at `repo_dir`, gold labels (which means the correct answers)
    stay host-only under `grading_path` (outside any mount). `line_counts` is used both for submission
    validation and as the index-build input.
    """

    instance_id: str  # unique task identifier (hash of the task spec)
    prepared_dir: Path  # host-only directory with the prepared task
    repo_dir: Path  # agent-visible repo (mounted into the container)
    grading_path: Path  # gold labels (host-only)
    task: dict  # original task spec
    preparation: dict  # metadata about the preparation process (e.g. index build)


@dataclass
class PromptSpec:
    """Agent prompt pieces.
    Tool descriptions injected from the resolved policy is recorded separately.
    """

    system_template: str
    instance_template: str
    extra: dict = field(default_factory=dict)


@dataclass
class EnvSpec:
    """Container spec the adapter owns. The coordinator may append an index mount
    when the policy adds index_query."""

    image: str
    cwd: str
    run_args: list[str]
    timeout: int = RUN_TIMEOUT
    pull_timeout: int = PULL_TIMEOUT
    interpreter: list[str] = field(default_factory=lambda: ["bash", "-c"])
    mounts: list[dict] = field(default_factory=list)  # {src, dst, readonly}


@dataclass
class Submission:
    """Parsed, validated agent output."""

    kind: str  #  different benchmark submission types (e.g. "patch", "regions")
    payload: Any  # regions list, patch text, ...


@runtime_checkable
class BenchmarkAdapter(Protocol):
    name: str

    def prepare(self, spec: dict) -> PreparedTask: ...

    def required_capabilities(self) -> list[str]: ...

    def agent_prompt(self, task: dict, policy: dict) -> PromptSpec: ...

    def container_spec(self, prepared: PreparedTask, policy: dict) -> EnvSpec: ...

    def make_environment(
        self,
        prepared: PreparedTask,
        plan,
        cfg: dict,
        *,
        wall_seconds: int,
        index_env: dict,
    ) -> tuple:
        """Construct and return (mini_environment, env_manifest_dict).

        Each benchmark owns how its container is built.
        SWE-Explore builds a locked-down DockerEnvironment from its EnvSpec.
        SWE-bench calls mini's get_sb_environment to resolve the task's
        installed image. `index_env` carries the index mounts/BASH_ENV which
        the coordinator prepared when the policy added index_query.
        """
        ...

    def parse_submission(self, text: str, prepared: PreparedTask) -> Submission: ...

    def evaluate(self, prepared: PreparedTask, submission: Submission) -> dict: ...
