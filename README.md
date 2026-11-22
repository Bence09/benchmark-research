# Configurable tool-policy benchmarking for coding agents

A research framework for measuring how an agent's **available tool set** (especially
repository search and retrieval) affects its task performance and resource cost,
under otherwise controlled conditions. It runs existing coding benchmarks through a
pluggable adapter, exposes tools to the agent by a declared YAML policy, and records
outcome and per-channel cost for comparison.

Prepared for BME API + Windows/WSL2 + Docker. No university API key is included.
This is active research tooling, not a finished product: interfaces are stabilizing
but still evolve, and "verified live" vs "offline only" is called out explicitly.

## What it is now

The framework wraps a pinned mini-SWE-agent (2.4.6) and a locked-down container.
A run is: **resolve a tool policy → prepare a task → run the agent → parse its
submission → grade with the benchmark's official evaluator → record cost × outcome.**

The only thing that changes between the arms of a comparison is the declared tool
policy (e.g. baseline search vs baseline + an index tool). Everything else — model,
budgets, image, prompt scaffold — is held fixed and recorded.

Two benchmarks are wired in and verified end to end through the common framework:

| Benchmark | Task | Submission | Grading |
|---|---|---|---|
| SWE-Explore | locate relevant code for an issue | ranked `path:start-end` regions | upstream region scorer |
| SWE-bench Lite | repair a real bug | a patch (`git diff`) | official hidden-test harness (resolved/unresolved) |

Adding a benchmark is one adapter + YAML conditions + one registry entry; the policy,
index-tool, and metrics layers are untouched (this reusability was proven by wiring
SWE-bench in after SWE-Explore).

## Repository layout

```
run_demo.py        Example driver: pick a benchmark + condition, run/score/join.
conditions/        config.yaml — named tool-policy conditions (YAML anchors share
                   the common tool vocabulary; per-benchmark baselines differ only
                   in their required tool floor).
benchkit/          Coordinator, BenchmarkAdapter contract, the two adapters,
                   model_config (model/budget config + token accounting),
                   Option-B index serialisation + in-container index_query CLI.
toolpolicy/        Declared YAML policy -> resolved, inspectable, hashed effective
                   policy (per-tool enforcement status: enforced/best-effort).
indextool/         Frozen index_query tool contract + a swappable payload (BM25 now;
                   RAG/LSP later) + chunking.
metrics/           Per-channel read attribution (search/read/index/other/
                   unattributed), failure buckets, and a join that emits
                   Prometheus-text + CSV.
oracle/            Legacy/reference scripts from the project's sandbox origin
                   (bench.py, explore.py, repobench_api.py). Not maintained, not
                   imported by the framework; kept as worked references.
docs/              Design notes and the working backlog.
runs/              Per-run outputs (gitignored).
```

## Isolation and label hygiene

- The agent sees only the issue and tool outputs — never reference patches, gold
  regions, hidden tests or FAIL_TO_PASS. Gold data is written to host-only
  `*.grading.*` files, outside any container mount.
- Containers: `--network=none --cap-drop=ALL --security-opt=no-new-privileges`;
  SWE-Explore mounts the repo read-only, SWE-bench repair uses the task's installed
  image with a writable `/testbed`. No API key forwarding, no Docker socket mount.
- Tool restrictions are **best-effort / record-only** in the first cut: a policy
  records which tools are available and whether a restriction is enforced; it does
  not claim a shell agent cannot bypass a missing binary. `network: deny` is the one
  genuinely enforced restriction (container netns).

## 1. Install (WSL)

Keep the repo on the WSL Linux filesystem (not `/mnt/c`) if you can; `/mnt/c` write
errors under heavy per-step writes are a known nuisance (see run outputs below).
Docker/Podman must be running.

```bash
make setup          # create .venv-agent and vendored evaluator sources (scripts/setup.sh)
make requirements   # install pinned agent deps + the fuzzywuzzy scorer dep
source .venv-agent/bin/activate
docker info
```

The agent is pinned to mini-SWE-agent 2.4.6. Preserve `installed-agent.txt` and each
run's `packages.txt` as the dependency snapshot across experiments.

## 2. Development commands

```bash
make build    # compile all sources (benchkit indextool metrics oracle toolpolicy tests)
make test     # offline unittest suite (mocked APIs, synthetic fixtures, no Docker)
make lint     # ruff lint
make ruff     # ruff format --check (no changes)
make format   # apply ruff formatting
make check    # ruff format-check + lint + test
make help     # list targets
```

The offline suite needs no API, Docker, or live grading. A few tests self-skip
without the optional evaluator sources (`explore-evaluator-src`, `repobench-src`).

## 3. Set the API key (kept out of shell history)

```bash
read -rsp 'BME API key: ' OPENAI_API_KEY
echo
export OPENAI_API_KEY
export BME_API_BASE='https://xrlab-ie318-3.tmit.bme.hu/v1'
```

Never put the key in config, prompts, or logs.

## 4. Run a condition

`run_demo.py` is an example driver, not the framework itself (the framework is the
`benchkit`/`toolpolicy`/`indextool`/`metrics` packages). The benchmark is a parameter.

```bash
# SWE-Explore localization (default benchmark)
python run_demo.py run  --benchmark swe-explore --condition baseline
python run_demo.py run  --benchmark swe-explore --condition baseline_plus_index_encouraged

# SWE-bench repair
python run_demo.py run  --benchmark swe-bench --condition swebench_baseline
```

Defaults: `large` model, 30 steps, 2048 output tokens/call, temperature 0, 3600 s
wall budget. On `/mnt/c`, add `--runs-root ~/bench-runs` to write run outputs to the
Linux filesystem and avoid intermittent write errors.

Each run directory contains `manifest.json` (model, budgets, resolved policy,
image, index provenance, hashes), `trajectory.traj.json`, `submission.txt` +
`submission.parsed.json`, and `metrics.json` (tokens, tool actions, per-channel
read attribution, failure category, wall time).

## 5. Score and join

Grading is a separate step (no model calls). SWE-Explore uses its upstream region
scorer; SWE-bench shells out to the official harness in a dedicated venv.

```bash
python run_demo.py score <run-dir> --benchmark swe-bench
python run_demo.py join  <run-a> <run-b> --out runs/sweep
```

`join` emits `sweep.prom` (Prometheus text exposition) and `sweep.csv`, joining
native outcome with per-condition cost. The metrics are named/labelled for a
Prometheus/Grafana stack; actual dashboards are a later scope.

`demo` runs several conditions, scores each, and joins in one command:

```bash
python run_demo.py demo --benchmark swe-bench \
  --conditions swebench_baseline swebench_plus_index --runs-root ~/bench-runs
```

## 6. The official evaluators

SWE-bench grading uses the official harness pinned to tag `v2.1.7` in a separate
venv (`.venv-eval`); the harness is a property of SWE-bench, independent of the run
environment. SWE-Explore uses the pinned upstream region scorer in
`explore-evaluator-src`. An evaluator exiting 0 is **not** evidence a task resolved —
read the official report; the framework records `resolved: null` when no report is
produced rather than inventing a pass.

**Grading needs a Docker daemon the Python Docker SDK can reach.** The SWE-bench
harness calls `docker.from_env()`, which talks to a daemon over a socket — not the
`docker` CLI. On Podman-rootless the CLI works (so agent *runs* are fine) but the SDK
has no socket by default, so **grading** fails with
`DockerException: ... PermissionError(13, 'Permission denied')` and you get
`resolved: null`. This is an environment setup step, not something the framework
handles. If you see that error, expose the Podman API socket and point the SDK at it:

```bash
systemctl --user enable --now podman.socket
export DOCKER_HOST=unix:///run/user/$(id -u)/podman/podman.sock
```

Then re-run the `score` step. With real Docker Desktop/daemon this is already satisfied.

## Tool policy and the index tool

Conditions are declared in `conditions/config.yaml`, additively: a baseline names the
task-essential tools; `*_plus_index` conditions add the `index_query` retrieval tool.
The index is a swappable payload behind a frozen tool contract (BM25 now; built on
the host at prepare time, serialised, and read inside the locked container by a
pure-stdlib CLI — no network, no host callback). A recorded `description_style`
(neutral vs encouraged) controls how assertively the prompt offers the index, so
prompt-nudge strength is part of the recorded intervention, not hidden drift.

## Sources

- https://mini-swe-agent.com/latest/usage/swebench/
- https://github.com/SWE-agent/mini-swe-agent/tree/04d809ceab9df28f9adaed044884180159172930
- https://github.com/Qiushao-E/SWE-Explore-Bench
- https://www.swebench.com/SWE-bench/guides/evaluation/
