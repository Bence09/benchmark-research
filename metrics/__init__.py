"""metrics (E4): cost/outcome measurement, Prometheus-style.

Pure, offline, post-hoc over artifacts mini already writes (never forks mini,
per PIPELINE.md). Defines metrics cleanly (named, labeled, base units, explicit
null for unknown) so a Prometheus/Grafana-style stack can consume them later;
plotting is out of scope here (optional convenience only).

- read_metrics(trajectory): per-channel delivered read volume (search/read/index/
  other/unattributed), first-program classification, null-safe.
- classify_failure(exit_status, ...): the four failure buckets.
- join_runs(run_dirs): flat named-metric/labeled rows across conditions.
- to_prometheus(rows): text-exposition format.
"""

from .read_metrics import read_metrics, classify_command, Channel
from .failures import classify_failure, FailureCategory
from .join import join_runs, to_prometheus, to_csv

__all__ = [
    "read_metrics", "classify_command", "Channel",
    "classify_failure", "FailureCategory",
    "join_runs", "to_prometheus", "to_csv",
]
