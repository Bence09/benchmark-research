#!/usr/bin/env python3
"""In-container `index_query` CLI.

Pure stdlib. Mounted read-only into the locked-down container next to a serialized
BM25 index. The agent calls:

    index_query "<query text>" [--top-k N]

It scores chunks against the serialized index, reads snippet text from the mounted
read-only /repo, truncates to the snippet byte cap, and prints the same format the
host-side ToolBoundary renders. The host reconstructs ToolCallRecords from the
trajectory afterward; this script itself has no host callback and no network.

Environment (set by the container spec):
    INDEX_FILE          path to serialized index JSON (read-only mount)
    INDEX_REPO          path to the read-only repo root (default /repo)
    INDEX_MAX_TOP_K     policy top_k limit
    INDEX_MAX_SNIPPET   snippet byte cap
"""

import argparse
import json
import math
import os
from pathlib import Path


def _idf(df_t, n):
    return math.log(1 + (n - df_t + 0.5) / (df_t + 0.5))


def load_index(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def score(index, query_tokens):
    n = index["corpus"]["n"]
    avgdl = index["corpus"]["avgdl"] or 1.0
    k1 = index["params"]["k1"]
    b = index["params"]["b"]
    df = index["df"]
    q = set(query_tokens)
    results = []
    for i, ch in enumerate(index["chunks"]):
        s = 0.0
        dl = ch["len"] or 1
        for term in q:
            tf = ch["tf"].get(term)
            if not tf:
                continue
            s += (
                _idf(df.get(term, 0), n)
                * (tf * (k1 + 1))
                / (tf + k1 * (1 - b + b * dl / avgdl))
            )
        if s > 0:
            results.append((i, s))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def read_snippet(repo_root, path, start, end, max_bytes):
    f = Path(repo_root) / path
    try:
        lines = f.read_text(errors="ignore").splitlines()
    except OSError:
        return ""
    chunk = "\n".join(lines[start - 1 : end])
    return chunk.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="index_query", add_help=True)
    ap.add_argument("query")
    ap.add_argument("--top-k", "-k", type=int, default=None)
    args = ap.parse_args(argv)

    index_file = os.environ.get("INDEX_FILE", "/index/index.json")
    repo_root = os.environ.get("INDEX_REPO", "/repo")
    max_top_k = int(os.environ.get("INDEX_MAX_TOP_K", "10"))
    max_snippet = int(os.environ.get("INDEX_MAX_SNIPPET", "2048"))

    query = args.query.strip()
    if not query:
        print("index_query error: empty query")
        return 2
    top_k = min(args.top_k or max_top_k, max_top_k)
    if top_k < 1:
        print("index_query error: --top-k must be >= 1")
        return 2

    index = load_index(index_file)
    ranked = score(index, query.split())[:top_k]
    if not ranked:
        print("index_query: no results.")
        return 0

    for rank, (i, s) in enumerate(ranked, 1):
        ch = index["chunks"][i]
        print(f"[{rank}] {ch['path']}:{ch['start']}-{ch['end']}  (score={s:.4f})")
        snippet = read_snippet(
            repo_root, ch["path"], ch["start"], ch["end"], max_snippet
        )
        if snippet:
            print(snippet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
