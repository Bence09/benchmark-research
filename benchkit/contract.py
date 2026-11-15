"""Benchmark adapter contract (E3-S1).

Generalizes the oracle explore.py's prepare/run/score into a benchmark-agnostic
protocol. Adding a tool must not touch evaluate(); adding a benchmark must not
touch tool implementations (R5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class PreparedTask:
    """Output of prepare(): a sanitized, hashed task ready to run.

    The agent-visible repo lives at `repo_dir`; gold labels stay host-only under
    `grading_path` (outside any mount). `line_counts` is used both for submission
    validation and as the index-build input.
    """

    instance_id: str
    prepared_dir: Path
    repo_dir: Path
    grading_path: Path
    task: dict
    preparation: dict  # provenance + hashes (R9)


@dataclass
class PromptSpec:
    """Agent prompt pieces. Tool descriptions injected from the resolved policy
    are part of the intervention and recorded separately."""

    system_template: str
    instance_template: str
    extra: dict = field(default_factory=dict)


@dataclass
class EnvSpec:
    """Container spec the adapter owns. The coordinator may append an index mount
    when the policy adds index_query (but never changes the lockdown flags)."""

    image: str
    cwd: str
    run_args: list[str]
    timeout: int = 60
    pull_timeout: int = 120
    interpreter: list[str] = field(default_factory=lambda: ["bash", "-c"])
    mounts: list[dict] = field(default_factory=list)  # {src, dst, readonly}


@dataclass
class Submission:
    """Parsed, validated agent output. `kind` distinguishes region/patch benchmarks."""

    kind: str  # "regions" | "patch" | ...
    payload: Any  # regions list, patch text, ...


@runtime_checkable
class BenchmarkAdapter(Protocol):
    name: str

    def prepare(self, spec: dict) -> PreparedTask: ...

    def required_capabilities(self) -> list[str]: ...

    def agent_prompt(self, task: dict, policy: dict) -> PromptSpec: ...

    def container_spec(self, prepared: PreparedTask, policy: dict) -> EnvSpec: ...

    def parse_submission(self, text: str, prepared: PreparedTask) -> Submission: ...

    def evaluate(self, prepared: PreparedTask, submission: Submission) -> dict: ...
