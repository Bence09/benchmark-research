"""Frozen contract types for the index tool.

The ToolBoundary depends only on these, concrete payloads (BM25, RAG, ...) must
satisfy IndexPayload without the boundary knowing which one it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class Region:
    """One retrieved region. 1-based inclusive line range, repo-relative path."""

    path: str
    start: int
    end: int
    score: float
    snippet: str | None = None


@dataclass
class BuildInfo:
    """Provenance recorded when an index is built. Serialized to index.json."""

    kind: str
    build_seconds: float
    index_bytes: int
    input_repo_sha256: str
    builder_source_sha: str
    leakage_checks_passed: bool
    chunk_count: int = 0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "build_seconds": self.build_seconds,
            "index_bytes": self.index_bytes,
            "input_repo_sha256": self.input_repo_sha256,
            "builder_source_sha": self.builder_source_sha,
            "leakage_checks_passed": self.leakage_checks_passed,
            "chunk_count": self.chunk_count,
        }


@dataclass
class ToolCallRecord:
    """One structured log record per tool call.

    Distinguishes available / called / returned / errored / denied, and tags the
    delivered bytes with channel='index_query' so reads via the index are
    separable from explicit `read` reads.
    """

    tool: str
    query: str | None
    top_k: int | None
    returned_regions: list[dict] = field(default_factory=list)
    bytes_delivered: int = 0
    channel: str = "index_query"
    latency_seconds: float | None = None
    error: str | None = None
    denied: bool = False

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "query": self.query,
            "top_k": self.top_k,
            "returned_regions": self.returned_regions,
            "bytes_delivered": self.bytes_delivered,
            "channel": self.channel,
            "latency_seconds": self.latency_seconds,
            "error": self.error,
            "denied": self.denied,
        }


@runtime_checkable
class IndexPayload(Protocol):
    """A swappable retrieval implementation behind the frozen tool interface."""

    kind: str

    def build(self, repo_root: Path) -> BuildInfo: ...

    def query(self, text: str, top_k: int) -> list[Region]: ...
