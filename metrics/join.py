"""Join runs into flat named/labeled metric rows + emitters.

Prometheus-convention naming: snake_case, base-unit suffixes (_bytes, _seconds,
_total), labels {benchmark, condition, instance, channel}. CSV + Prometheus
text-exposition emitted always.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .failures import classify_failure
from .read_metrics import read_metrics

METRIC_PREFIX = "bench"


def row_from_run(run_dir: Path) -> dict:
    """Build one labeled row from a run directory's manifest + metrics + scores.

    Tolerant of partial runs: missing measurements stay null not zeroed.
    """
    run_dir = Path(run_dir)
    manifest = _load(run_dir / "manifest.json")
    metrics = _load(run_dir / "metrics.json")
    scores = _load(run_dir / "scores.json")
    traj = _load(run_dir / "trajectory.traj.json")

    reads = (
        read_metrics(traj)
        if traj
        else {"channels": {}, "lines_read_total": None, "bytes_read_total": None}
    )

    resolved = None
    native_score = None
    if scores:
        s = scores.get("scores", scores)
        native_score = s.get("f1_score") if isinstance(s, dict) else None
        resolved = scores.get("resolved")

    failure = classify_failure(
        metrics.get("exit_status") if metrics else None,
        has_valid_submission=bool(metrics.get("valid_regions_saved"))
        if metrics
        else False,
        resolved=resolved,
    )

    index_prov = (manifest.get("index_provenance") or {}) if manifest else {}

    return {
        "benchmark": (manifest or {}).get("benchmark"),
        "condition": (manifest or {}).get("condition"),
        "instance": (manifest or {}).get("instance_id")
        or (manifest or {}).get("task", {}).get("instance_id"),
        "failure_category": failure.value,
        # outcome
        "native_score": native_score,
        # cost: tokens
        "input_tokens": (metrics or {}).get("input_tokens_recorded"),
        "output_tokens": (metrics or {}).get("output_tokens_recorded"),
        "model_calls": (metrics or {}).get("recorded_api_responses"),
        "tool_actions": (metrics or {}).get("tool_actions"),
        # cost: per-channel reads
        "channels": reads.get("channels", {}),
        "lines_read_total": reads.get("lines_read_total"),
        "bytes_read_total": reads.get("bytes_read_total"),
        "unattributed_calls": reads.get("unattributed_calls"),
        # index build cost reported separately (not hidden in a cached env)
        "index_build_seconds": index_prov.get("build_seconds"),
        "index_bytes": index_prov.get("index_bytes"),
        "wall_seconds": (metrics or {}).get("wall_seconds"),
    }


def join_runs(run_dirs) -> list[dict]:
    return [row_from_run(d) for d in run_dirs]


# ---- emitters ----


def to_csv(rows: list[dict]) -> str:
    """Flat CSV; per-channel reads expanded to channel-labeled columns."""
    flat = [_flatten(r) for r in rows]
    if not flat:
        return ""
    fields = sorted({k for r in flat for k in r})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in flat:
        w.writerow(r)
    return buf.getvalue()


def to_prometheus(rows: list[dict]) -> str:
    """Prometheus text-exposition."""
    lines: list[str] = []
    emitted_help: set[str] = set()

    def emit(metric, value, labels, help_text=None, mtype="gauge"):
        if value is None:
            return
        full = f"{METRIC_PREFIX}_{metric}"
        if full not in emitted_help:
            if help_text:
                lines.append(f"# HELP {full} {help_text}")
            lines.append(f"# TYPE {full} {mtype}")
            emitted_help.add(full)
        lbl = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items() if v is not None)
        lines.append(f"{full}{{{lbl}}} {_num(value)}")

    for r in rows:
        base = {
            "benchmark": r.get("benchmark"),
            "condition": r.get("condition"),
            "instance": r.get("instance"),
        }
        emit(
            "native_score",
            r.get("native_score"),
            base,
            "Native benchmark score (outcome).",
        )
        emit(
            "input_tokens",
            r.get("input_tokens"),
            base,
            "Recorded input tokens.",
            "counter",
        )
        emit(
            "output_tokens",
            r.get("output_tokens"),
            base,
            "Recorded output tokens.",
            "counter",
        )
        emit(
            "model_calls_total",
            r.get("model_calls"),
            base,
            "Model API calls.",
            "counter",
        )
        emit(
            "tool_actions_total",
            r.get("tool_actions"),
            base,
            "Tool actions.",
            "counter",
        )
        emit(
            "lines_read_total",
            r.get("lines_read_total"),
            base,
            "Lines delivered via read channels.",
            "counter",
        )
        emit(
            "bytes_read_total",
            r.get("bytes_read_total"),
            base,
            "Bytes delivered via read channels.",
            "counter",
        )
        emit(
            "unattributed_calls_total",
            r.get("unattributed_calls"),
            base,
            "Calls with unattributed read volume.",
            "counter",
        )
        emit(
            "index_build_seconds",
            r.get("index_build_seconds"),
            base,
            "Index build time (reported separately).",
        )
        emit("index_bytes", r.get("index_bytes"), base, "Serialized index size.")
        emit("wall_seconds", r.get("wall_seconds"), base, "End-to-end wall time.")
        for ch, vals in (r.get("channels") or {}).items():
            clabels = {**base, "channel": ch}
            emit(
                "channel_calls_total",
                vals.get("calls"),
                clabels,
                "Calls per channel.",
                "counter",
            )
            emit(
                "channel_lines_total",
                vals.get("lines"),
                clabels,
                "Lines delivered per channel.",
                "counter",
            )
            emit(
                "channel_bytes_total",
                vals.get("bytes"),
                clabels,
                "Bytes delivered per channel.",
                "counter",
            )
    return "\n".join(lines) + ("\n" if lines else "")


# ---- helpers ----


def _flatten(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k != "channels"}
    for ch, vals in (r.get("channels") or {}).items():
        for metric, v in vals.items():
            out[f"channel_{ch}_{metric}"] = v
    return out


def _load(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def _num(v):
    return int(v) if isinstance(v, bool) is False and isinstance(v, int) else v
