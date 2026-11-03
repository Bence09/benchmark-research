# Coding-agent experiment infrastructure plan

## Research question and experimental unit

Start with two complementary questions:

1. Can the agent resolve an issue? SWE-bench supplies functional patch grading.
2. Does it find useful repository context efficiently? SWE-Explore supplies
   ranked file/line-region evaluation.

The experimental unit is one task, one model, one runner/configuration, one
repository revision and one repetition. The unit is not just a model name.
Do not treat SWE-agent or mini-SWE-agent as separate datasets: they are runners.

## Milestones and acceptance criteria

| Stage | Work | Acceptance criterion |
|---|---|---|
| 0: connection | Probe both models using mini's real parser | One valid action, never executed by probe |
| 1: plumbing | One known Lite task per model | Task starts, trajectory and applicable patch exported; official report retained |
| 2: pilot | Freeze 5–10 task IDs, same budgets, one run/model | Failures categorized, runtime/storage estimated, metrics complete or explicitly missing |
| 3: exploration | One SWE-Explore task, ranked region output and official scoring | Valid path/ranges at correct base revision; scorer returns metrics |
| 4: controlled comparison | Fixed held-out set, repeated runs if feasible | Same tools/budgets/images; raw traces and grading preserved |
| 5: indexing study | Introduce one retrieval/indexing intervention | Compare against unchanged baseline; include index build time/storage |

The familiar Astropy task is a smoke task only. Keep configuration-development
tasks separate from the eventual evaluation set. Do not tune on evaluation labels.

## SWE-bench track: implemented starter

`bench.py` uses pinned mini APIs instead of building another agent loop.
It runs tasks serially in separate worker processes and task Docker containers.
Exact selected dataset records are saved on the host. Only sanitized metadata
enters the agent worker, and only the issue reaches its initial prompt.

Keep the official evaluator in a separate virtual environment. The historical
Kiro notes identify v2.1.7 as the successful evaluator tag, so that is the first
compatibility baseline, not a claim that all newer versions are incompatible.
If moving to a newer harness, validate with the same known patch and task first.

## SWE-Explore track: integration design

The upstream README currently lists a `mini_swe_agent` explorer and endpoint
routing options. Check the checked-out wrapper's dependency/version contract before
reusing it; its name alone does not guarantee compatibility with mini 2.4.6.
Use a separate Python 3.12+ environment for the Explore repository (`uv sync`
upstream), and record its Git SHA. Do not upgrade the agent environment in place.

Suggested first task: `pydata__xarray-4629`, familiar from previous experiments.
Confirm it exists in the chosen current dataset snapshot and has a usable base
commit mapping. Prepare only this instance first, not every repository.

Integration steps:

1. Freeze the released benchmark revision and one task ID.
2. Prepare the repository at its mapped base commit using upstream helpers.
   A repository downloaded as a snapshot may not contain `.git`; record the
   commit and source archive hash externally. Verify line numbering is unchanged.
3. Give the explorer only the issue and repository tools. Keep `ground_truth`,
   reference read steps and grading metadata outside its execution environment.
4. Use the upstream mini wrapper if its contract matches the chosen version;
   otherwise write a small explorer adapter around the same model/environment.
5. Ask for a ranked list of `(path, start, end)` regions with repository-relative
   paths and 1-based inclusive line ranges. Reject missing files, invalid ranges,
   path escapes, and out-of-bounds line numbers before scoring.
6. Pass the ranked list to upstream `ExploreEvaluator`; use its exact formulas.
   Preserve original order and define duplicate handling according to that scorer.
7. Save predictions, scorer output, trajectory, token usage and elapsed time.

The public evaluator contract shown by the upstream README is:

```python
from pathlib import Path
from eval import ExploreEvaluator

evaluator = ExploreEvaluator(
    bench_data_path=Path("bench-one-normalized.jsonl"),
    file_line_counts=None,
)
results = evaluator.evaluate(
    explore_method=my_explorer,  # (issue, instance_id) -> list[(path, start, end)]
    instance_ids=["pydata__xarray-4629"],
    metrics=["precision", "recall", "f1_score", "hit_file_rate", "noise_file_rate"],
)
```

This is an integration contract, not an executable adapter included in this kit.
Start with explicit final ranked regions: reconstructing exact read spans from
arbitrary shell commands is a separate instrumentation problem.

Distinguish two measurements throughout:

- **Submitted context:** the final ranked region list, scored by the benchmark.
- **Observed context:** source text actually delivered to the model during search
  and reads. Log path, revision, range, order, truncation and delivered text size.

A full repair trajectory includes post-edit reads. Do not label those as
pre-edit exploration. For direct exploration, end the task before edits; for a
combined study, define and log an explicit exploration-to-edit boundary.

For strict exploration runs, prefer an allowlisted read-only tool interface and
a read-only repository mount. A shell prompt saying “do not edit” is insufficient
to enforce that policy. This read-only environment is a next-stage implementation,
not something the SWE-bench repair container provides automatically.

## Common run schema

Keep fields in JSON first; export CSV for analysis. Missing measurements are null.

| Field group | Required information |
|---|---|
| Identity | run/task/repetition IDs, benchmark revision, repo base commit |
| Model | exact served ID, endpoint, available server fingerprint/version |
| Agent | package/source SHA, full prompt/tools config, action interface |
| Environment | image tag and digest, dependency snapshot, host resources |
| Budgets | steps, output/call, wall time, context policy, retrieval/top-k budgets |
| Usage | per-call input/output/reasoning if available, peak input, completeness |
| Runtime | setup/image pull, API wait, tools, evaluator, end-to-end separately |
| Outcomes | submission, patch application, official resolved status or exploration metrics |
| Failures | transport, formatting, context exceeded, timeout, setup, invalid patch, test failure |

The starter records a subset of this schema. Image digests, server fingerprint
summary, split timing and line-level read instrumentation are explicit follow-ups.
Raw responses in trajectories can supply additional provider fields where present.

## Controlled model and indexing comparisons

- First compare small vs large with the same runner, action interface, tools and
  a common context budget that fits both. Their advertised context limits differ;
  “each model uses its own maximum” is a separate experiment.
- Fix a task manifest before running. Record temperature, seeds where supported,
  context truncation/summarization policy, output cap and stopping rules.
- Start with sequential workers and confirm the shared server's concurrency policy
  with the professor before scaling. Queue load affects latency comparisons.
- Record token counts and latency separately: API response time includes server
  queueing/prefill/generation, not just model compute. Do not claim measured GPU
  utilization or FLOPs without server telemetry.
- For indexing, compare baseline vs intervention with one change at a time. Include
  build time, index size, refresh policy, query time and context delivered to model.
- Do not build the index using gold patches or gold relevant-file labels.
- A small pilot supports debugging, not broad claims. Use paired task comparisons,
  enough tasks/repetitions for the claim, and report uncertainty and all failures.

## Later benchmarks and resources

SWE-bench variants can be added as dataset/environment adapters after the pilot.
SWE-smith and R2E-Gym are later environment/task-generation or training-related
tracks, requiring their own dependency and task contracts; they are not assumed
to work merely by changing the dataset string in this Lite runner. Scope those
only after deciding whether the research needs task generation, training, or
additional repair evaluation.

No local GPU is required for this architecture. Local CPU/RAM/disk still matter
for repository tools, Docker images, builds, tests and indexing. Measure one cold
and warm task before choosing campaign size; avoid promising a resource footprint
before checking the particular images and repositories.

## First practical session

1. Extract into a new WSL directory; keep previous runs intact.
2. Run `setup.sh`; set the key in the shell.
3. Run one parser probe for each model.
4. If both pass, run one task using the same interface.
5. Grade both patches; inspect full trajectories even if one fails.
6. Capture the local versions/paths and choose the frozen pilot manifest.

Sources and install commands are in README.md. The kit is ready for these local
checks; it has not been installed on your laptop or authenticated to your endpoint.
