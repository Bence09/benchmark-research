#!/usr/bin/env python3
"""Live demo runner for the tool-policy benchmark (E3 execute + E4 join).

Runs one or both conditions on the already-prepared SWE-Explore task, then joins
the run dirs into Prometheus + CSV metrics. Needs Docker running and
OPENAI_API_KEY set in the shell.

Examples:
    # one condition
    python run_demo.py run --condition baseline
    python run_demo.py run --condition baseline_plus_index

    # both conditions back to back, then emit joined metrics
    python run_demo.py demo

    # join existing run dirs only (no model calls)
    python run_demo.py join runs/<dir-a> runs/<dir-b> --out runs/sweep
"""

import argparse
from pathlib import Path

from benchkit import Coordinator
from benchkit.load_prepared import load_prepared, bm25_build_index
from benchkit.swe_explore_adapter import SweExploreAdapter

ROOT = Path(__file__).resolve().parent
DEFAULT_PREPARED = ROOT / "explore-input" / "xarray-4629"
DEFAULT_POLICY = ROOT / "conditions" / "config.yaml"


def _run_one(condition, args):
    prepared = load_prepared(args.prepared, top_k=args.top_k)
    coord = Coordinator(SweExploreAdapter())
    plan = coord.plan(prepared, str(args.policy), condition,
                      top_k=args.top_k, build_index=bm25_build_index)
    return coord.execute(plan, prepared, model=args.model, steps=args.steps,
                         output_tokens=args.output_tokens, wall_seconds=args.wall_seconds)


def cmd_run(args):
    out = _run_one(args.condition, args)
    print("Done:", out)
    return 0


def cmd_demo(args):
    dirs = []
    for condition in ("baseline", "baseline_plus_index"):
        print(f"\n=== condition: {condition} ===")
        dirs.append(_run_one(condition, args))
    return _emit(dirs, ROOT / "runs" / "sweep")


def cmd_join(args):
    return _emit([Path(d) for d in args.run_dirs], Path(args.out))


def _emit(run_dirs, out_prefix):
    from metrics import join_runs, to_prometheus, to_csv

    rows = join_runs(run_dirs)
    out_prefix = Path(out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    (out_prefix.with_suffix(".prom")).write_text(to_prometheus(rows))
    (out_prefix.with_suffix(".csv")).write_text(to_csv(rows))
    print("Wrote:", out_prefix.with_suffix(".prom"), "and", out_prefix.with_suffix(".csv"))
    for r in rows:
        cond = r.get("condition") or "(unlabeled)"
        print(f"  {cond:20} score={r.get('native_score')} "
              f"lines_read={r.get('lines_read_total')} "
              f"index_build_s={r.get('index_build_seconds')} "
              f"failure={r.get('failure_category')}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--prepared", default=str(DEFAULT_PREPARED))
        sp.add_argument("--policy", default=str(DEFAULT_POLICY))
        sp.add_argument("--model", choices=("small", "large"), default="large")
        sp.add_argument("--top-k", type=int, default=5)
        sp.add_argument("--steps", type=int, default=30)
        sp.add_argument("--output-tokens", type=int, default=2048)
        sp.add_argument("--wall-seconds", type=int, default=3600)

    r = sub.add_parser("run", help="run a single condition")
    r.add_argument("--condition", default="baseline")
    common(r)
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("demo", help="run baseline then baseline_plus_index, then join")
    common(d)
    d.set_defaults(func=cmd_demo)

    j = sub.add_parser("join", help="join existing run dirs into metrics (no model calls)")
    j.add_argument("run_dirs", nargs="+")
    j.add_argument("--out", default="runs/sweep")
    j.set_defaults(func=cmd_join)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
