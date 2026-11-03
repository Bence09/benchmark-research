# University model benchmark starter

Prepared 2026-11-02 for BME API + Windows/WSL2 + Docker.

This is a starter implementation, not a completed benchmark campaign. The code
uses mini-SWE-agent's model, agent loop, SWE-bench environment preparation and
serialization. It adds task selection, process isolation, budgets, run records,
prediction export and measurement summaries. No university API key is included.

## First scope

1. Validate API action compatibility for each model.
2. Run one SWE-bench Lite task end to end.
3. Evaluate the generated patch with the official harness.
4. Repeat with the other model, then a fixed small task list.
5. Add SWE-Explore using the same model and measurement conventions.

See PLAN.md for research design, exploration integration and later benchmarks.
SWE-Explore is a documented integration stage here, not a completed adapter.

## Architecture

| Component | Location | Responsibility |
|---|---|---|
| University inference server | University | Generate model responses through `/v1` |
| This runner + mini-SWE-agent | WSL | Manage history, API calls and tools |
| Task container | Local Docker | Repository, edits, dependencies and agent tests |
| Official evaluator | Separate WSL virtual environment + Docker | Grade patch |
| Run outputs | Local `runs/` directory | Audit, compare and analyze |

The agent gets only the issue prompt and tool outputs, not reference patches or
grading labels. Task containers have no host workspace mounts, API key forwarding,
or Docker socket mounts in this configuration. Container networking is not
disabled: this is not a strict offline evaluation policy. If researching restricted
retrieval, enforce the same network and tool policy in every experimental arm.

## 1. Install in WSL

Keep your existing experiments unchanged. Extract this folder into a new directory,
preferably `~/uni/research-bench-starter` on WSL's Linux filesystem.
Python 3.12 is a convenient common version for the planned Explore integration.
Docker Desktop must be running with WSL integration enabled.

```bash
bash setup.sh
source .venv-agent/bin/activate
docker info
```

The agent is pinned to upstream commit
`04d809ceab9df28f9adaed044884180159172930`, reporting version 2.4.6. It is
deliberately a separate environment from older mini-SWE-agent installations.
Transitive dependencies are resolved at install time; preserve `installed-agent.txt`
and each run's `packages.txt` and use that dependency snapshot across experiments.

## 2. Set the API key without putting it into command history

```bash
read -rsp 'BME API key: ' OPENAI_API_KEY
echo
export OPENAI_API_KEY
export BME_API_BASE='https://xrlab-ie318-3.tmit.bme.hu/v1'
```

Do not put keys in model configuration, task prompts or uploaded logs.

## 3. Test action compatibility

```bash
python bench.py probe --model small --interface tools
python bench.py probe --model large --interface tools
```

These calls ask the model for one harmless action, parse it through mini's actual
model interface and verify it. They **do not execute** the returned shell command.
If structured calls fail, independently test the text-action interface:

```bash
python bench.py probe --model small --interface text
```

For a controlled model comparison, use the same passing interface for both models.
If content remains null and actions appear only in reasoning_content, stop and
check the server's chat template/reasoning/tool parser configuration. This starter
does not promote reasoning into executable output. A successful prose response
alone does not establish action compatibility.

## 4. Prepare and run the known smoke task

Plan only (downloads/selects dataset, writes metadata, does not run an agent):

```bash
python bench.py run --model small --interface tools
```

Execute (also reruns the compatibility check):

```bash
python bench.py run --model small --interface tools --execute
```

Defaults: `astropy__astropy-12907`, 30 model steps, 2048 output tokens per
call, 120 seconds per shell command, 3600 seconds total worker timeout including
image startup. This is a debugging budget, not an optimized performance setting.
At the professor's approximate 20 generated tokens/second, 2048 generated tokens
alone take about 102 seconds per call. Thirty full outputs could take roughly
51 minutes before adding prompt processing, tools and setup.

Use the printed run directory for the following commands. It contains:

- `manifest.json`: model, interface, budgets, dataset fingerprint, package versions.
- `selected-tasks.grading.json`: host-only snapshot of full task records.
- `packages.txt`: dependency snapshot.
- Per task: sanitized `task.json`, trajectory, patch, prediction, metrics,
  process status and log.
- `predictions.json`: official-evaluator input after execution.

The full task snapshot is never mounted into the agent container or sent as a
prompt. A new timestamp/UUID directory is created for every run.

For strict reproducibility, replace the moving dataset default with a Hugging Face
dataset commit: `--dataset-revision COMMIT_SHA`. The exact selected records are
saved and hashed even when `main` is used. Pin Docker images by digest before a
reported campaign; this starter uses mini's upstream task image mapping/tags.

## 5. Evaluate separately

Your saved Kiro run notes document using SWE-bench tag `v2.1.7` after a schema
problem with a newer harness. Reuse that known evaluator first if it is still
available. Otherwise create a dedicated one:

```bash
git clone --branch v2.1.7 --depth 1 https://github.com/SWE-bench/SWE-bench.git evaluator-src
python3 -m venv .venv-eval
.venv-eval/bin/python -m pip install -e ./evaluator-src
.venv-eval/bin/python -m pip freeze > installed-evaluator.txt
```

Then, while the agent environment is active:

```bash
python bench.py evaluate runs/YOUR_RUN_DIRECTORY \
  --evaluator-python "$PWD/.venv-eval/bin/python"
python bench.py summarize runs/YOUR_RUN_DIRECTORY
```

The harness runs in the selected run directory. Each evaluation receives a fresh
run ID to avoid stale cached grading. Preserve its report, evaluation log,
evaluator Git revision, dependency snapshot and image digests.
An evaluator process exiting successfully is **not** evidence the issue resolved;
read the official resolved/unresolved result. CSV summaries do not yet merge
official grading, which remains authoritative in the harness report.

## 6. Expand cautiously

```bash
python bench.py run --model large --interface tools --execute
python bench.py run --model small --interface tools \
  --instances astropy__astropy-12907,ANOTHER_VALID_INSTANCE_ID --execute
```

Replace the second placeholder with a real instance ID. Use the same instances,
budgets and interface for both models. Run serially initially; do not assume the
shared endpoint can handle parallel workers or an unrestricted experiment load.

## Measurement limitations

- Recorded token totals include reasoning when the provider includes it in
  completion_tokens. They sum usage across responses; context is a per-call limit.
- Missing usage is null/flagged, not zero. Failed transport attempts or provider
  retries may not appear as completed responses. Counts are not server billing.
- Monetary cost is unknown, even if mini's internal missing-price fallback says 0.
- The largest recorded input is not a token-budget enforcement mechanism. Server
  context errors remain possible. Per-call output, step and wall limits are enforced.
- Timed-out workers may have partial trajectories. The agent saves after each step;
  inspect the last saved state. A container killed before cleanup may remain until
  its configured lifetime ends. Inspect `docker ps` before another large run.
- Tool-action counts are not line-level exploration metrics. Arbitrary shell reads
  require instrumentation or a defined exploration output contract (PLAN.md).
- One successful task validates plumbing; it is not a benchmark success-rate claim.

## Validation included

`python -m unittest discover -s tests -v` checks token aggregation (including format
failures and missing data), task selection, CSV export and evaluator invocation.
These offline tests do not substitute for API, Docker-image or live grading checks.

## Sources

- https://mini-swe-agent.com/latest/usage/swebench/
- https://mini-swe-agent.com/latest/models/local_models/
- https://mini-swe-agent.com/latest/advanced/environments/
- https://github.com/SWE-agent/mini-swe-agent/tree/04d809ceab9df28f9adaed044884180159172930
- https://github.com/Qiushao-E/SWE-Explore-Bench
- https://www.swebench.com/SWE-bench/guides/evaluation/

Upstream source was inspected when creating this starter. No authenticated
university run or local Docker evaluation has been performed from this workspace.
