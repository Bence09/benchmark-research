"""Prepare-time helpers shared by adapters (in-package, not imported from oracle)."""

from __future__ import annotations

from pathlib import Path


def line_counts(repo: Path) -> dict[str, int]:
    """Map repo-relative posix path -> line count. Skips .git, binaries, symlinks."""
    repo = Path(repo).resolve()
    result: dict[str, int] = {}
    for path in repo.rglob("*"):
        if path.is_file() and not path.is_symlink() and ".git" not in path.relative_to(repo).parts:
            try:
                result[path.relative_to(repo).as_posix()] = len(
                    path.read_text(encoding="utf-8").splitlines()
                )
            except (UnicodeError, OSError):
                pass
    return result
