"""benchkit (E3): benchmark-agnostic adapter contract + experiment coordinator.

The coordinator drives the lifecycle (resolve policy → validate required caps →
build+serialize index when the policy adds it → run → parse → evaluate → persist)
without knowing a benchmark's grading semantics. A BenchmarkAdapter owns task
preparation, required capabilities, prompt/container specs, submission parsing and
evaluator invocation — never tool implementations or the agent loop (R5).

Index-in-container seam = Option B: the BM25 index is built on the host at prepare
time, serialized, and mounted read-only into the locked-down container alongside a
pure-Python `index_query` CLI that reads it locally (no network, no host callback).
Tool-call logging is reconstructed from the trajectory afterward (R7).

Offline-verifiable: the live `agent.run()` in Docker is wired but not executed here.
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
    "EnvSpec",
    "PreparedTask",
    "PromptSpec",
    "Submission",
    "Coordinator",
    "plan_run",
]
