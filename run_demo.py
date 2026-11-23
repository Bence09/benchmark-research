#!/usr/bin/env python3
"""Example demo runner for the tool-policy framework.

Shows the intended framework usage: pick a benchmark, pick a condition, run.
The benchmark is a parameter (--benchmark); everything else (policy resolution,
index build, execution, scoring, metrics join) is benchmark-agnostic. Needs Docker
running and OPENAI_API_KEY set in the shell.

Examples:
    # SWE-Explore localization (default)
    python run_demo.py run  --benchmark swe-explore --condition baseline
    python run_demo.py demo --benchmark swe-explore

    # SWE-bench repair
    python run_demo.py run  --benchmark swe-bench --condition swebench_baseline
    python run_demo.py demo --benchmark swe-bench \\
        --conditions swebench_baseline swebench_plus_index

    # join / score existing run dirs (no model calls)
    python run_demo.py join <dir-a> <dir-b> --out runs/sweep
    python run_demo.py score <run-dir> --benchmark swe-bench
"""

import argparse
import json
import time
from pathlib import Path

from benchkit import Coordinator
from benchkit.contract import Submission
from benchkit.load_prepared import bm25_build_index, load_prepared
from benchkit.swe_bench_adapter import SweBenchAdapter
from benchkit.swe_explore_adapter import SweExploreAdapter

ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY = ROOT / "conditions" / "config.yaml"


# --- per-benchmark registry: the only place benchmarks differ in this runner ---


def _prepare_swe_explore(args):
    # Pre-prepared on disk (explore.py prepare). Loaded into a PreparedTask.
    return load_prepared(
        args.prepared or (ROOT / "explore-input" / "xarray-4629"), top_k=args.top_k
    )


def _prepare_swe_bench(args):
    # prepare from an instance_id (downloads record, host-only grading, filtered task).
    out = (
        Path(args.prepared)
        if args.prepared
        else (ROOT / "swebench-input" / args.instance)
    )
    if out.exists():
        # already prepared: load task + preparation without re-downloading
        return _load_swebench_prepared(out)
    return SweBenchAdapter().prepare(
        {"instance_id": args.instance, "output_dir": str(out)}
    )


def _load_swebench_prepared(out: Path):
    from benchkit.contract import PreparedTask

    task = json.loads((out / "task.json").read_text())
    prep = json.loads((out / "preparation.json").read_text())
    return PreparedTask(
        instance_id=task["instance_id"],
        prepared_dir=out,
        repo_dir=out / "repo",  # clean source snapshot
        grading_path=out / "selected-task.grading.json",
        task=task,
        preparation=prep,
    )


def _score_swe_explore(run_dir, prepared, sub, args):
    res = SweExploreAdapter().evaluate(
        prepared, sub, evaluator_source=args.evaluator_source
    )
    print(
        "  f1 =",
        res["scores"].get("f1_score"),
        " precision =",
        res["scores"].get("precision"),
        " recall =",
        res["scores"].get("recall"),
    )
    return res


def _score_swe_bench(run_dir, prepared, sub, args):
    res = SweBenchAdapter().evaluate(
        prepared, sub, evaluator_python=args.evaluator_python, run_dir=run_dir
    )
    print(
        "  resolved =", res.get("resolved"), " exit =", res.get("evaluator_exit_code")
    )
    return res


BENCHMARKS = {
    "swe-explore": {
        "adapter": SweExploreAdapter,
        "prepare": _prepare_swe_explore,
        "score": _score_swe_explore,
        "default_conditions": ["baseline", "baseline_plus_index"],
    },
    "swe-bench": {
        "adapter": SweBenchAdapter,
        "prepare": _prepare_swe_bench,
        "score": _score_swe_bench,
        "default_conditions": ["swebench_baseline", "swebench_plus_index"],
    },
}


# --- benchmark-agnostic command logic ---


def _run_one(condition, args, prepared):
    coord = Coordinator(BENCHMARKS[args.benchmark]["adapter"]())
    plan = coord.plan(
        prepared,
        str(args.policy),
        condition,
        top_k=args.top_k,
        build_index=bm25_build_index,
    )
    return coord.execute(
        plan,
        prepared,
        model=args.model,
        steps=args.steps,
        output_tokens=args.output_tokens,
        wall_seconds=args.wall_seconds,
        runs_root=getattr(args, "runs_root", None),
    )


def cmd_run(args):
    prepared = BENCHMARKS[args.benchmark]["prepare"](args)
    out = _run_one(args.condition, args, prepared)
    # Score so the pool row carries a native_score, then append the
    # run to the shared sweep pool - devery run, single or demo, lands in one CSV.
    try:
        _score_run(out, args, prepared)
    except Exception as exc:  # noqa: BLE001 - scoring failure must not drop the run from the pool
        print(f"  (scoring skipped for {Path(out).name}: {exc})")
    sweep_root = (
        Path(args.runs_root).expanduser() if args.runs_root else (ROOT / "runs")
    )
    _emit(
        [out],
        sweep_root / "sweep",
        sweep_id=getattr(args, "sweep_id", None),
        append=True,
    )
    print("Done:", out)
    return 0


def cmd_demo(args):
    bm = BENCHMARKS[args.benchmark]
    prepared = bm["prepare"](args)
    conditions = args.conditions or bm["default_conditions"]
    dirs = []
    for condition in conditions:
        print(f"\n=== {args.benchmark} / {condition} ===")
        run_dir = _run_one(condition, args, prepared)
        try:
            _score_run(run_dir, args, prepared)
        except Exception as exc:  # noqa: BLE001 - a scoring failure must not stop the sweep.
            print(f"  (scoring skipped for {run_dir.name}: {exc})")
        dirs.append(run_dir)
    sweep_root = (
        Path(args.runs_root).expanduser() if args.runs_root else (ROOT / "runs")
    )
    return _emit(
        dirs, sweep_root / "sweep", sweep_id=args.sweep_id, append=not args.overwrite
    )


def _score_run(run_dir, args, prepared=None):
    run_dir = Path(run_dir)
    parsed_path = run_dir / "submission.parsed.json"
    if not parsed_path.exists():
        raise SystemExit(
            f"No parsed submission in {run_dir} (run did not submit validly)."
        )
    if prepared is None:
        manifest = json.loads((run_dir / "manifest.json").read_text())
        prepared = BENCHMARKS[args.benchmark]["prepare"](
            argparse.Namespace(
                prepared=manifest.get("prepared_dir"),
                instance=manifest.get("instance_id"),
                top_k=manifest.get("top_k", 5),
            )
        )
    parsed = json.loads(parsed_path.read_text())
    sub = Submission(kind=parsed["kind"], payload=parsed["payload"])
    res = BENCHMARKS[args.benchmark]["score"](run_dir, prepared, sub, args)
    (run_dir / "scores.json").write_text(json.dumps(res, indent=2) + "\n")
    print("Scored:", run_dir / "scores.json")
    return res


def cmd_score(args):
    _score_run(args.run_dir, args)
    return 0


def cmd_join(args):
    return _emit(
        [Path(d) for d in args.run_dirs],
        Path(args.out),
        sweep_id=args.sweep_id,
        append=not args.overwrite,
    )


def _emit(run_dirs, out_prefix, sweep_id=None, append=True):
    from metrics import join_runs, to_prometheus, write_csv

    out_prefix = Path(out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    # sweep_id groups the rows of this comparison; default to the output stem +
    # a timestamp so repeated sweeps into the same pool stay distinguishable.
    if sweep_id is None:
        sweep_id = (
            out_prefix.name + "-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        )

    rows = join_runs(run_dirs, sweep_id=sweep_id)

    # Pool CSV accumulates one row per run (append); .prom reflects this sweep only.
    csv_path = out_prefix.with_suffix(".csv")
    write_csv(rows, csv_path, append=append)
    (out_prefix.with_suffix(".prom")).write_text(to_prometheus(rows))
    print(f"Sweep {sweep_id}")
    print(f"  pool CSV ({'appended' if append else 'written'}):", csv_path)
    print("  prom (this sweep):", out_prefix.with_suffix(".prom"))
    for r in rows:
        cond = r.get("condition") or "(unlabeled)"
        print(
            f"  [{r.get('repetition')}] {cond:30} run={r.get('run_key')} "
            f"score={r.get('native_score')} lines_read={r.get('lines_read_total')} "
            f"failure={r.get('failure_category')}"
        )
    return 0


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--benchmark", choices=tuple(BENCHMARKS), default="swe-explore")
        sp.add_argument(
            "--prepared", default=None, help="prepared dir (default: per-benchmark)"
        )
        sp.add_argument(
            "--instance",
            default="astropy__astropy-12907",
            help="instance id for benchmarks that prepare from a dataset (swe-bench)",
        )
        sp.add_argument("--policy", default=str(DEFAULT_POLICY))
        sp.add_argument("--model", choices=("small", "large"), default="large")
        sp.add_argument("--top-k", type=int, default=5)
        sp.add_argument("--steps", type=int, default=30)
        sp.add_argument("--output-tokens", type=int, default=2048)
        sp.add_argument("--wall-seconds", type=int, default=3600)
        sp.add_argument(
            "--runs-root",
            default=None,
            help="where to write run dirs; use a WSL Linux path (e.g. ~/bench-runs) "
            "to avoid /mnt/c write errors. Default: ./runs",
        )
        sp.add_argument(
            "--evaluator-source",
            default=None,
            help="SWE-Explore scorer path (default: explore-evaluator-src)",
        )
        sp.add_argument(
            "--evaluator-python",
            default=None,
            help="SWE-bench harness python (default: .venv-eval/bin/python)",
        )

    r = sub.add_parser("run", help="run a single condition")
    r.add_argument("--condition", default="baseline")
    r.add_argument(
        "--sweep-id",
        default=None,
        help="grouping label for this run's pool row (default: auto)",
    )
    common(r)
    r.set_defaults(func=cmd_run)

    d = sub.add_parser(
        "demo", help="run conditions, score each, then join into a sweep"
    )
    d.add_argument(
        "--conditions",
        nargs="*",
        default=None,
        help="condition names (default: per-benchmark baseline + index)",
    )
    d.add_argument(
        "--sweep-id",
        default=None,
        help="grouping label for this comparison's rows (default: auto)",
    )
    d.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite the pool CSV instead of appending",
    )
    common(d)
    d.set_defaults(func=cmd_demo)

    j = sub.add_parser(
        "join", help="join existing run dirs into metrics (no model calls)"
    )
    j.add_argument("run_dirs", nargs="+")
    j.add_argument("--out", default="runs/sweep")
    j.add_argument(
        "--sweep-id",
        default=None,
        help="grouping label for this comparison's rows (default: auto)",
    )
    j.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite the pool CSV instead of appending",
    )
    j.set_defaults(func=cmd_join)

    s = sub.add_parser(
        "score", help="grade a finished run's submission (no model calls)"
    )
    s.add_argument("run_dir")
    common(s)
    s.set_defaults(func=cmd_score)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
