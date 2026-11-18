"""Load an existing prepared dir into a PreparedTask + BM25 builder.

BM25 builder wraps the payload and the serializer for the coordinator.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contract import PreparedTask
from .prepare_support import line_counts


def load_prepared(prepared_dir, *, top_k: int = 5) -> PreparedTask:
    prepared_dir = Path(prepared_dir).expanduser().absolute()
    task = json.loads((prepared_dir / "task.json").read_text())
    prep = json.loads((prepared_dir / "preparation.json").read_text())
    repo_dir = prepared_dir / "repo"
    grading = prepared_dir / "benchmark.grading.jsonl"
    # line_counts + top_k feed submission validation (adapter.parse_submission).
    prep = {**prep, "line_counts": line_counts(repo_dir), "top_k": top_k}
    return PreparedTask(
        instance_id=task["instance_id"],
        prepared_dir=prepared_dir,
        repo_dir=repo_dir,
        grading_path=grading,
        task=task,
        preparation=prep,
    )


def bm25_build_index(repo_dir, serialized_out) -> dict:
    """Coordinator build_index hook: build BM25 on the host, serialize it"""
    from indextool.bm25 import BM25Payload

    from .index_serialize import serialize_bm25

    payload = BM25Payload()
    info = payload.build(Path(repo_dir))
    return serialize_bm25(payload, info, Path(serialized_out))
