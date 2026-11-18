"""benchkit: benchmark-agnostic adapter contract + coordinator.

The coordinator drives the lifecycle (resolve policy -> validate required caps ->
build+serialize index when the policy adds it -> run -> parse -> evaluate -> persist)
without knowing a benchmark's grading semantics. A BenchmarkAdapter owns task
preparation, required capabilities, prompt/container specs, submission parsing and
evaluator invocation.

Index-in-container closing: the BM25 index is built on the host at prepare
time, serialized, and mounted read-only into the locked-down container alongside a
pure-Python `index_query` CLI that reads it locally.
Tool-call logging is reconstructed from the trajectory afterward.
"""

from .contract import (
    BenchmarkAdapter,
    EnvSpec,
    PreparedTask,
    PromptSpec,
    Submission,
)
from .coordinator import Coordinator, plan_run

__all__ = [
    "BenchmarkAdapter",
    "Coordinator",
    "EnvSpec",
    "PreparedTask",
    "PromptSpec",
    "Submission",
    "plan_run",
]
