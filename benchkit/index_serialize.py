"""Serialized index format (Option B, host side).

At prepare time we build the index on the host (E2 payload) and serialize it to a
single JSON file. That file is mounted read-only into the locked-down container,
where the pure-Python `index_query` CLI reads it with no third-party deps.

Format is payload-kind-tagged so a future RAG/LSP payload can serialize its own
representation under the same contract. For BM25 we serialize the chunk table and
precomputed term frequencies so the container needs no `rank_bm25`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

SERIALIZER_VERSION = 1


def serialize_bm25(payload, build_info, out_path: Path) -> dict:
    """Serialize a built BM25Payload to a self-contained JSON index.

    Stores per-chunk {path,start,end,tokens} and corpus stats so the container-side
    CLI can score BM25 without rank_bm25. Returns the index.json provenance block.
    """
    chunks = payload._chunks  # built chunk table
    tokenized = [c.content.split() for c in chunks]
    doc_len = [len(t) for t in tokenized]
    n = len(tokenized)
    avgdl = (sum(doc_len) / n) if n else 0.0

    # Document frequency per term.
    df: dict[str, int] = {}
    for toks in tokenized:
        for term in set(toks):
            df[term] = df.get(term, 0) + 1

    records = []
    for c, toks in zip(chunks, tokenized):
        tf: dict[str, int] = {}
        for term in toks:
            tf[term] = tf.get(term, 0) + 1
        records.append({"path": c.path, "start": c.start, "end": c.end, "tf": tf,
                         "len": len(toks)})

    index = {
        "serializer_version": SERIALIZER_VERSION,
        "kind": "bm25",
        "params": {"k1": 1.5, "b": 0.75},
        "corpus": {"n": n, "avgdl": avgdl},
        "df": df,
        "chunks": records,
    }
    out_path = Path(out_path)
    out_path.write_text(json.dumps(index, ensure_ascii=False))
    return {**build_info.to_dict(), "serialized_path": out_path.name,
            "serialized_bytes": out_path.stat().st_size,
            "serializer_version": SERIALIZER_VERSION}


def idf(df_t: int, n: int) -> float:
    # BM25 idf with +0.5 smoothing (Okapi).
    return math.log(1 + (n - df_t + 0.5) / (df_t + 0.5))
