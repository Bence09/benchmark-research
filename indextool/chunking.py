"""Overlapping line-window chunking (E2).

Reimplemented in-package (not imported from the oracle explore-evaluator-src) so
indextool has no dependency on reference code. Same shape: 1-based inclusive line
ranges, source-extension filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SOURCE_EXTS = {
    ".py", ".md", ".txt", ".toml", ".cfg", ".ini",
    ".yaml", ".yml", ".json", ".rst",
}


@dataclass
class Chunk:
    path: str  # repo-relative, posix
    start: int  # 1-based
    end: int  # 1-based inclusive
    content: str


def iter_source_files(repo_root: Path) -> Iterable[Path]:
    for p in sorted(repo_root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SOURCE_EXTS:
            yield p


def chunk_repo(
    repo_root: Path,
    *,
    chunk_size: int = 80,
    chunk_overlap: int = 20,
    max_chunks: int = 3000,
) -> list[Chunk]:
    step = max(1, chunk_size - chunk_overlap)
    chunks: list[Chunk] = []
    for p in iter_source_files(repo_root):
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        if not lines:
            continue
        rel = p.relative_to(repo_root).as_posix()
        for i in range(0, len(lines), step):
            end_idx = min(i + chunk_size, len(lines))
            content = "\n".join(lines[i:end_idx])
            if content.strip():
                chunks.append(Chunk(path=rel, start=i + 1, end=end_idx, content=content))
            if end_idx >= len(lines):
                break
            if len(chunks) >= max_chunks:
                return chunks
    return chunks
