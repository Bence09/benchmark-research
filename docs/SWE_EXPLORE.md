# SWE-Explore demonstration using the university model

This is an **add-on**, not a replacement for your working SWE-bench runner.
Copy `explore.py` and `test_explore.py` beside your existing `bench.py` in
`/mnt/c/uni/research-bench-starter`. Keep bench.py's evaluator symlink fix.
Use the existing `.venv-agent` (mini 2.4.6 and the installed `mcp` dependency).
The add-on imports the shared model configuration and token accounting from bench.py.

## What this demonstration adds

| Stage | Input | Output |
|---|---|---|
| Prepare | One normalized SWE-Explore record; corresponding source issue/base commit | Fresh source snapshot, sanitized task, host-only grading record |
| Explore | Issue + tools to search/read snapshot | Ranked file/line regions; mini trajectory; API token/runtime metrics |
| Score | Ranked regions + benchmark reference labels | SWE-Explore scores from upstream eval.py |

No repair patch is requested or graded. The final submitted regions are what we
score; they are not a claim to reconstruct every line seen during exploration.
This is a custom mini-based explorer using the official evaluator, not an exact
replication of the paper's explorer configuration.

## 1. Reuse your existing benchmark file

Locate the normalized one-task file from the earlier Kiro experiment:

```bash
find /mnt/c/uni -maxdepth 6 -name 'bench-one-normalized.jsonl' -print
```

Do not reuse the Kiro-modified Xarray checkout. The prepare command downloads a
fresh archive of the historical base commit, with no Git history or gold patch.
It uses the SWE-bench Verified dataset to recover the public issue and base commit.
If the ID is absent, stop and check its source mapping; `--task-dataset` and
`--task-split` let you select the source used by your benchmark's commit map.
Do not substitute a different commit just to make the preparation succeed.

The input is expected to be normalized JSONL with `instance_id` and
`ground_truth.read_core_regions`; `pydata__xarray-4629` is the default ID.
If you use the full benchmark file instead, prepare selects only that instance.

## 2. Prepare

From the project directory, with `.venv-agent` active, substitute the actual path:

```bash
python explore.py prepare \
  --bench /actual/path/to/bench-one-normalized.jsonl
```

This creates `explore-input/xarray-4629/` and refuses to overwrite it.
It records the source revision/fingerprint, archive hash, base commit and input
hashes. When an explicit base commit exists in the Explore record, it must match
the source dataset. Otherwise the source dataset mapping is recorded without
claiming it was cross-checked against an absent Explore commit field.
All core region paths/ranges must exist in the downloaded snapshot.

For a published campaign pin the source dataset with `--task-revision COMMIT_SHA`.
The default is the moving `main` revision, suitable for this first demonstration.
Preparation downloads source/data but makes no model calls.

## 3. Explore with the larger university model

Docker Desktop must be running. Set `OPENAI_API_KEY` in the shell using hidden input
as before; never put it in a file or paste it into logs.

```bash
python explore.py run --model large --top-k 5
```

Default budgets match the repair demonstration where useful: 30 model calls,
2048 output tokens/call, 3600-second agent wall budget, 60-second shell commands.
`top-k` means maximum **regions**, not tokens or unique files. The scorer also
reports metrics at its own 100/300/500 **line budgets**; those are different limits.

The tools-only `python:3.12-slim` image is pulled once; its resolved image ID is
recorded. It is not the SWE-bench test image: exploration does not require installing
Xarray or running its tests. Available tools include bash, find, grep, sed and Python;
do not assume ripgrep is installed. The agent is told to inspect source, not execute it.

Only `explore-input/.../repo` is mounted into the container, read-only at `/repo`.
Gold labels and task metadata files remain on the host outside the mount. Container
networking is disabled; the API connection remains in the host agent process.
The container root filesystem is read-only with a temporary `/tmp`. No host
credentials or Docker socket are forwarded. These are the exploration policy for
this add-on, not the upstream wrapper's default policy.

The agent's wall check happens between calls; an in-flight request can overrun it
until its configured API timeout. The container has a finite lifetime as well.

Copy the printed `runs/...-explore-...` path. It contains:

- `manifest.json`: issue/repo/base revision, budgets, policy, model, image.
- `trajectory.traj.json`: model actions and tool outputs, checkpointed by mini.
- `submission.txt`: raw final RELEVANT_FILES output.
- `regions.json`: validated ranked `{path,start,end}` list.
- `metrics.json`: API usage, actions, format errors, wall time and exit status.

If no valid submission is produced, `regions.json` is absent and the command fails.
Keep the failure trace; do not silently convert it to a successful empty answer.

## 4. Obtain a pinned scorer in a new directory

You can first inspect your existing SWE-Explore checkout. For a separate pinned
copy matching the interface checked while preparing this adapter:

```bash
git clone https://github.com/Qiushao-E/SWE-Explore-Bench.git explore-evaluator-src
git -C explore-evaluator-src checkout 5602f031f2d9562d0a805f83402b536e831a5a11
```

Do not reset your old Kiro experiment checkout or download all submodules.
Only the pure-Python `eval.py` scorer is imported, so this route needs no full
Explore environment installation or `uv sync`. The agent runs in your existing venv.

## 5. Score without another model call

Substitute the actual printed exploration run directory:

```bash
python explore.py score runs/YOUR_EXPLORATION_RUN \
  --evaluator-source explore-evaluator-src
```

`scores.json` records the evaluator file hash and 16 upstream metrics. Their formulas
are those of the pinned source, not reimplemented here. In particular, file hits,
core-line precision/recall, optional-aware context efficiency and ranked-budget
metrics measure different things and should not be treated as interchangeable.
None is a SWE-bench repair success rate.

## 6. What to share for interpretation

Share `task.json` from the preparation folder plus the run's `regions.json`,
`scores.json`, `metrics.json`, `manifest.json` and `trajectory.traj.json`.
We will explain the input, show the ranked output, interpret each score and contrast
this workflow with the completed SWE-bench repair example. No model comparison is
required to demonstrate these benchmark differences.

## Validation and limitations

Six offline tests passed, including real upstream eval.py scoring on a synthetic
record. Tests cover rank preservation, inclusive ranges, invalid paths/ranges,
duplicate rejection, top-k enforcement, exact instance selection and archive safety.
Compilation and CLI help were checked. No university API or Docker execution was
performed in the authoring workspace. This is the next integration to test on WSL,
not a claim of a completed SWE-Explore benchmark result.

Run adapter tests (last test skips without the optional scorer environment variable):

```bash
SWE_EXPLORE_SOURCE="$PWD/explore-evaluator-src" python -m unittest test_explore -v
```

The scorer reads the full selected grading record only after generation. The
generation prompt is constructed solely from the source dataset issue text.
Existing prepared inputs are intended to remain unchanged between run and scoring;
the adapter checks task/grading hashes and preserves the archive hash, but does not
implement continuous filesystem integrity monitoring or full observed-read coverage.

Upstream references inspected:

- https://github.com/Qiushao-E/SWE-Explore-Bench/blob/5602f031f2d9562d0a805f83402b536e831a5a11/README.md
- https://github.com/Qiushao-E/SWE-Explore-Bench/blob/5602f031f2d9562d0a805f83402b536e831a5a11/explorers/mini_swe_agent_explorer.py
- https://github.com/Qiushao-E/SWE-Explore-Bench/blob/5602f031f2d9562d0a805f83402b536e831a5a11/eval.py
