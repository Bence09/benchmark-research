"""BM25 index payload.

Concrete IndexPayload. `rank_bm25` is imported lazily so the rest of indextool
(and its tests) work without it, constructing a BM25Payload without the dependency
raises a clear error, and tests skip accordingly.

Only agent-visible source under repo_root is
chunked, and we assert no grading/gold/held-out artifacts slipped in.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .chunking import chunk_repo
from .contract import BuildInfo, Region

# Builder provenance: bump when the build logic changes.
BUILDER_SOURCE_SHA = "indextool.bm25.v1"

# Paths that must never enter the index (gold labels / held-out tests).
_LEAKAGE_MARKERS = (".grading.", "grading.json", "grading.jsonl")


class BM25Unavailable(RuntimeError):
    """rank_bm25 is not installed."""


def _require_rank_bm25():
    try:
        from rank_bm25 import BM25Okapi
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise BM25Unavailable(
            "rank_bm25 is not installed; `pip install rank-bm25` to use the BM25 payload"
        ) from exc
    return BM25Okapi


class BM25Payload:
    kind = "bm25"

    def __init__(
        self,
        *,
        chunk_size: int = 80,
        chunk_overlap: int = 20,
        max_chunks: int = 3000,
        max_snippet_bytes: int = 2048,
    ) -> None:
        self._BM25Okapi = _require_rank_bm25()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_chunks = max_chunks
        self.max_snippet_bytes = max_snippet_bytes
        self._chunks = []
        self._bm25 = None
        self._repo_root: Path | None = None

    def build(self, repo_root: Path) -> BuildInfo:
        repo_root = Path(repo_root)
        self._repo_root = repo_root
        started = time.monotonic()

        self._chunks = chunk_repo(
            repo_root,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            max_chunks=self.max_chunks,
        )
        leakage_ok = self._leakage_check(self._chunks)

        tokenized = [c.content.split() for c in self._chunks]
        self._bm25 = self._BM25Okapi(tokenized) if tokenized else None

        index_bytes = sum(len(c.content.encode("utf-8")) for c in self._chunks)
        return BuildInfo(
            kind=self.kind,
            build_seconds=time.monotonic() - started,
            index_bytes=index_bytes,
            input_repo_sha256=_hash_repo(repo_root),
            builder_source_sha=BUILDER_SOURCE_SHA,
            leakage_checks_passed=leakage_ok,
            chunk_count=len(self._chunks),
        )

    def query(self, text: str, top_k: int) -> list[Region]:
        if self._bm25 is None or not self._chunks:
            return []
        scores = self._bm25.get_scores(text.split())
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        regions: list[Region] = []
        for idx, score in ranked:
            chunk = self._chunks[idx]
            snippet = chunk.content.encode("utf-8")[: self.max_snippet_bytes].decode(
                "utf-8", errors="ignore"
            )
            regions.append(
                Region(
                    path=chunk.path,
                    start=chunk.start,
                    end=chunk.end,
                    score=float(score),
                    snippet=snippet,
                )
            )
        return regions

    @staticmethod
    def _leakage_check(chunks) -> bool:
        for c in chunks:
            low = c.path.lower()
            if any(m in low for m in _LEAKAGE_MARKERS):
                raise RuntimeError(
                    f"leakage: grading artifact entered the index: {c.path}"
                )
        return True


def _hash_repo(repo_root: Path) -> str:
    """Stable hash of agent-visible source content under repo_root."""
    h = hashlib.sha256()
    from .chunking import iter_source_files

    for p in iter_source_files(repo_root):
        h.update(p.relative_to(repo_root).as_posix().encode("utf-8"))
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            pass
        h.update(b"\0")
    return h.hexdigest()
