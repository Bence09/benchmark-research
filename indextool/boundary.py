"""Agent-facing tool boundary (E2-S1).

The frozen contract the agent calls. Parses one `index_query` call, clamps top_k
to the policy limit, runs the payload, truncates delivered snippet bytes, and
returns a (observation_text, ToolCallRecord) pair. Depends only on IndexPayload.

Call grammar (what the agent emits, parsed here):
    index_query "<query text>"            # default top_k
    index_query "<query text>" --top-k N  # clamped to the policy limit
"""

from __future__ import annotations

import shlex
import time

from .contract import IndexPayload, Region, ToolCallRecord

TOOL_NAME = "index_query"


class ToolBoundary:
    def __init__(self, payload: IndexPayload, *, max_top_k: int = 10,
                 max_snippet_bytes: int = 2048) -> None:
        self.payload = payload
        self.max_top_k = max_top_k
        self.max_snippet_bytes = max_snippet_bytes

    def handle(self, raw_call: str) -> tuple[str, ToolCallRecord]:
        """Handle one tool call. Never raises for user/query errors; records them."""
        query, requested_k, parse_error = self._parse(raw_call)
        rec = ToolCallRecord(tool=TOOL_NAME, query=query, top_k=None)

        if parse_error is not None:
            rec.error = parse_error
            return self._render_error(parse_error), rec

        top_k = min(requested_k or self.max_top_k, self.max_top_k)
        rec.top_k = top_k

        started = time.monotonic()
        try:
            regions = self.payload.query(query, top_k)
        except Exception as exc:  # payload failure is an observation, not a crash
            rec.latency_seconds = time.monotonic() - started
            rec.error = f"{type(exc).__name__}: {exc}"
            return self._render_error(rec.error), rec
        rec.latency_seconds = time.monotonic() - started

        regions = self._truncate(regions)
        rec.returned_regions = [
            {"path": r.path, "start": r.start, "end": r.end, "score": round(r.score, 4)}
            for r in regions
        ]
        rec.bytes_delivered = sum(len((r.snippet or "").encode("utf-8")) for r in regions)
        return self._render(regions), rec

    def _parse(self, raw_call: str):
        try:
            parts = shlex.split(raw_call)
        except ValueError as exc:
            return None, None, f"could not parse call: {exc}"
        if not parts or parts[0] != TOOL_NAME:
            return None, None, f"not an {TOOL_NAME} call"
        parts = parts[1:]
        top_k = None
        query_tokens = []
        i = 0
        while i < len(parts):
            if parts[i] in ("--top-k", "-k"):
                if i + 1 >= len(parts):
                    return None, None, "--top-k requires a value"
                try:
                    top_k = int(parts[i + 1])
                except ValueError:
                    return None, None, f"--top-k value not an integer: {parts[i + 1]!r}"
                if top_k < 1:
                    return None, None, "--top-k must be >= 1"
                i += 2
            else:
                query_tokens.append(parts[i])
                i += 1
        query = " ".join(query_tokens).strip()
        if not query:
            return None, None, "empty query"
        return query, top_k, None

    def _truncate(self, regions: list[Region]) -> list[Region]:
        for r in regions:
            if r.snippet is not None:
                b = r.snippet.encode("utf-8")
                if len(b) > self.max_snippet_bytes:
                    r.snippet = b[: self.max_snippet_bytes].decode("utf-8", errors="ignore")
        return regions

    def _render(self, regions: list[Region]) -> str:
        if not regions:
            return "index_query: no results."
        out = []
        for i, r in enumerate(regions, 1):
            out.append(f"[{i}] {r.path}:{r.start}-{r.end}  (score={r.score:.4f})")
            if r.snippet:
                out.append(r.snippet)
        return "\n".join(out)

    def _render_error(self, msg: str) -> str:
        return f"index_query error: {msg}"
