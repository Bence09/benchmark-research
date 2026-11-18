"""metrics: cost/outcome measurement, Prometheus-style.

Pure, offline, post-hoc over artifacts mini already writes.
Defines metrics (named, labeled, base units, explicit
null for unknown) so a Prometheus/Grafana-style stack can consume them later.

- read_metrics(trajectory): per-channel delivered read volume (search/read/index/
  other/unattributed), first-program classification, null-safe.
- classify_failure(exit_status, ...): the four failure buckets.
- join_runs(run_dirs): flat named-metric/labeled rows across conditions.
- to_prometheus(rows): text-exposition format.
"""

from .failures import FailureCategory, classify_failure
from .join import join_runs, to_csv, to_prometheus, write_csv
from .read_metrics import Channel, classify_command, read_metrics

__all__ = [
    "Channel",
    "FailureCategory",
    "classify_command",
    "classify_failure",
    "join_runs",
    "read_metrics",
    "to_csv",
    "to_prometheus",
    "write_csv",
]
