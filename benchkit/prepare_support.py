"""Prepare-time helpers shared by adapters."""

from __future__ import annotations

import io
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath


def download_repo_snapshot(repo: str, commit: str, dest: Path) -> dict:
    """Download a GitHub tarball at `commit` into `dest` (a clean repo/ dir).

    Safe unpack: strips the archive root, rejects path traversal and absolute
    members, and skips relative symlinks (like icon links in Django).
    Returns provenance incl. archive hash and
    the list of skipped links. `dest` must not already exist.
    """
    import hashlib

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False)
    url = f"https://api.github.com/repos/{repo}/tarball/{commit}"
    req = urllib.request.Request(url, headers={"User-Agent": "benchkit-prepare"})
    data = urllib.request.urlopen(req, timeout=180).read()

    skipped: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for m in archive.getmembers():
            parts = PurePosixPath(m.name).parts
            if not parts or parts[0] in ("/", "..") or ".." in parts:
                raise ValueError(f"unsafe archive member: {m.name}")
            if len(parts) < 2:
                continue  # the single root dir
            target = dest / Path(*parts[1:])
            if m.issym() or m.islnk():
                if PurePosixPath(m.linkname).is_absolute():
                    raise ValueError(f"unsafe absolute link: {m.name} -> {m.linkname}")
                skipped.append(m.name)  # relative link, not needed for indexing
                continue
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(m) as src:
                    target.write_bytes(src.read())
            else:
                raise ValueError(f"unsupported archive member: {m.name}")
    return {
        "archive_sha256": hashlib.sha256(data).hexdigest(),
        "skipped_symlinks": skipped,
    }


def line_counts(repo: Path) -> dict[str, int]:
    """Map repo-relative posix path -> line count. Skips .git, binaries, symlinks."""
    repo = Path(repo).resolve()
    result: dict[str, int] = {}
    for path in repo.rglob("*"):
        if (
            path.is_file()
            and not path.is_symlink()
            and ".git" not in path.relative_to(repo).parts
        ):
            try:
                result[path.relative_to(repo).as_posix()] = len(
                    path.read_text(encoding="utf-8").splitlines()
                )
            except (UnicodeError, OSError):
                pass
    return result
