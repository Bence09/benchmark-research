"""Index tool (E2): agent-facing retrieval tool with a swappable payload.

Two seams:
  - ToolBoundary (E2-S1): the frozen agent-facing contract. Parses a call,
    enforces limits, times it, truncates snippets, and logs a structured record.
    Depends only on the IndexPayload protocol, never on a concrete index.
  - IndexPayload (E2-S2): a concrete retrieval implementation (bm25 now, rag/lsp
    later). Opaque `kind`, matching the policy's payload.kind (toolpolicy D4).

Pure/offline: no mini, no Docker, no live agent. Injecting the tool into a
container and wiring the bash call is E3 + run plumbing.

See docs/BACKLOG.md, Epic 2.
"""

from .contract import BuildInfo, IndexPayload, Region, ToolCallRecord
from .boundary import ToolBoundary

__all__ = ["BuildInfo", "IndexPayload", "Region", "ToolCallRecord", "ToolBoundary"]
