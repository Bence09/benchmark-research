"""Agent-facing retrieval tool with a swappable payload.

Two seams:
  - ToolBoundary: the frozen agent-facing contract. Parses a call,
    enforces limits, times it, truncates snippets, and logs a structured record.
    Depends only on the IndexPayload protocol, not on a concrete index.
  - IndexPayload: a concrete retrieval implementation (bm25 now, might be something else later).
  Opaque `kind`, matching the policy's payload.kind.
"""

from .boundary import ToolBoundary
from .contract import BuildInfo, IndexPayload, Region, ToolCallRecord

__all__ = ["BuildInfo", "IndexPayload", "Region", "ToolBoundary", "ToolCallRecord"]
