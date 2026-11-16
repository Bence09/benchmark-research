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
                         output_tokens=args.output_tokens, wall_seconds=args.wall_seconds,
                         runs_root=getattr(args, "runs_root", None))


def cmd_run(args):
    out = _run_one(args.condition, args)
    print("Done:", out)
    return 0


def cmd_demo(args):
    dirs = []
    for condition in args.conditions:
        print(f"\n=== condition: {condition} ===")
        run_dir = _run_one(condition, args)
        # Score each run before joining so the sweep carries native_score.
        # Best-effort: a run that did not submit validly is still joined (null score).
        try:
            _score_run(run_dir, args.evaluator_source)
        except Exception as exc:
            print(f"  (scoring skipped for {run_dir.name}: {exc})")
        dirs.append(run_dir)
    sweep_root = Path(args.runs_root).expanduser() if args.runs_root else (ROOT / "runs")
    return _emit(dirs, sweep_root / "sweep")


def cmd_join(args):
    return _emit([Path(d) for d in args.run_dirs], Path(args.out))


def _score_run(run_dir, evaluator_source):
    """Grade a finished run's parsed submission; write scores.json. Returns the result."""
    import json

    from benchkit.contract import Submission

    run_dir = Path(run_dir)
    parsed_path = run_dir / "submission.parsed.json"
    if not parsed_path.exists():
        raise SystemExit(f"No parsed submission in {run_dir} (run did not submit validly).")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    prepared = load_prepared(manifest["prepared_dir"], top_k=manifest.get("top_k", 5))
    parsed = json.loads(parsed_path.read_text())
    sub = Submission(kind=parsed["kind"], payload=parsed["payload"])

    result = SweExploreAdapter().evaluate(prepared, sub, evaluator_source=evaluator_source)
    (run_dir / "scores.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Scored:", run_dir / "scores.json")
    print("  f1_score =", result["scores"].get("f1_score"),
          " precision =", result["scores"].get("precision"),
          " recall =", result["scores"].get("recall"))
    return result


def cmd_score(args):
    """Grade a finished run's parsed submission with the pinned SWE-Explore scorer."""
    _score_run(args.run_dir, args.evaluator_source)
    return 0


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
        sp.add_argument("--runs-root", default=None,
                        help="where to write run dirs; use a WSL Linux path (e.g. ~/bench-runs) "
                             "to avoid /mnt/c write errors (Errno 5). Default: ./runs")

    r = sub.add_parser("run", help="run a single condition")
    r.add_argument("--condition", default="baseline")
    common(r)
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("demo", help="run conditions, score each, then join into a sweep")
    d.add_argument("--conditions", nargs="+",
                   default=["baseline", "baseline_plus_index"],
                   help="condition names to run in order (default: baseline, baseline_plus_index)")
    d.add_argument("--evaluator-source", default=None,
                   help="path to the pinned SWE-Explore scorer (default: explore-evaluator-src)")
    common(d)
    d.set_defaults(func=cmd_demo)

    j = sub.add_parser("join", help="join existing run dirs into metrics (no model calls)")
    j.add_argument("run_dirs", nargs="+")
    j.add_argument("--out", default="runs/sweep")
    j.set_defaults(func=cmd_join)

    s = sub.add_parser("score", help="grade a finished run's submission (no model calls)")
    s.add_argument("run_dir")
    s.add_argument("--evaluator-source", default=None,
                   help="path to the pinned SWE-Explore scorer (default: explore-evaluator-src)")
    s.set_defaults(func=cmd_score)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
