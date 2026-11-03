# Indexing-cost benchmarking pipeline

Design document, not a completed implementation. It describes a six-stage
pipeline that measures the impact of code indexing on a coding agent's cost and
efficiency, layered on top of the existing `bench.py` orchestrator. No pipeline
code exists yet; this plan defines the stages, contracts and acceptance criteria
before any is written. The tone and conventions follow `docs/PLAN.md`.

## Problem statement and novel contribution

Four external benchmarks were studied at source level: SWE-bench (fixed real
issues + PRs, hidden-test oracle), SWE-MERA (dynamic harvest of post-cutoff
GitHub issues + PRs with a clean single-PR mapping filter, Harbor `task.toml`
format), R2E-Gym (SWE-GEN: mines commits and synthesizes issue text, Docker
red→green validation), and SWE-Explore (region-relevance scoring). Three are
construction recipes; one is an exploration scorer.

Verified finding: **none of them provides controlled repository size or
controlled churn.** They offer selection and filtering, not generation of a
repository whose size or churn is an independent, dialable variable. Those two
variables are exactly what this research needs to measure online-vs-offline
indexing cost and stale-index cost. A benchmark-generation layer that supplies
them is therefore the novel contribution; the external projects are borrowable
machinery around it.

The experiment exposes an **index as a tool the agent may choose to call** (grep
baseline vs lexical vs structural/LSP vs semantic/RAG), competing with plain
grep, and measures **outcome (hidden tests pass) × cost (lines read + tokens)**
swept over the repository size knob. The headline deliverable for the Ericsson
tutorial is a chart showing an indexed agent reads fewer lines and spends fewer
tokens than grep-everything as repository size grows, without losing resolution.

## Architectural constraints (settled)

- `bench.py` stays the orchestrator/brain. New capabilities enter as adapters
  that import its pure functions (`config`, `metrics`) or consume its artifacts,
  exactly as `explore.py` already does. Nothing rewrites the agent loop.
- mini-SWE-agent is pinned (2.4.6). Instrumentation is captured from trajectory
  artifacts and tool wiring, never by forking mini.
- Generated instances emit a Harbor `task.toml` (schema v1.4) sidecar so the
  wider research group can run their own agents against the same instances.
- Run conventions are inherited verbatim: `runs/<UTC-timestamp>-<label>-<uuid8>/`,
  `manifest.json`, `*.grading.*` kept outside any agent mount, sha256 provenance
  on every host-frozen artifact, `null` (not zero) for unknown measurements, and
  one task is never reported as a success rate.
- Docker isolation mirrors `explore.py`: `--network=none --read-only
  --cap-drop=ALL --security-opt=no-new-privileges`, read-only repo bind mount,
  tmpfs `/tmp`, no socket or credential forwarding.
- One variable at a time; identical budgets/images/tool allowlist across arms;
  record uncertainty and all failures.

## Six stages and acceptance criteria

Within a comparison the only experimental variable is the index tool (the
"arm") or the size knob, never both at once.

| Stage | Work | Acceptance criterion |
|---|---|---|
| G: Generate | Acquire one real solved instance (reuse a SWE-bench Lite/Verified record handled by `bench.py`); build a synthetic scaffold of controllable size N around the fixed needle; emit a Harbor `task.toml` sidecar | Needle identical across sizes (same patch target, same hidden tests); scaffold deterministic from a seed; distractors import and never satisfy FAIL_TO_PASS; `task.toml` validates against schema v1.4 |
| P: Prepare | Snapshot/sanitize the generated repo at a pinned base state; strict label hygiene; hashing | Agent-visible repo contains issue + code only; hidden tests, gold patch, FAIL_TO_PASS and size/churn ground truth live in host-only `*.grading.*`; archive-safety checks pass; hashes recorded |
| I: Index | Build the index-under-test over the prepared repo; record build time, on-disk size and (phase 2) staleness after churn | Index builds reproducibly from the frozen snapshot with no gold-label leakage; build time/size/query time recorded; grep arm records an explicit null/zero-build baseline |
| R: Run | mini-SWE-agent via `bench.py` with the index exposed as one allowlisted tool competing with grep | Same budgets/image/tool set across arms; index tool calls appear in the trajectory; agent free to use or ignore the index; read events instrumented |
| E: Evaluate | Hidden-test oracle (official SWE-bench harness, separate venv) + optional SWE-Explore region scoring | Official resolved/unresolved status retained; region scores, if produced, kept distinct and never conflated with resolution |
| M: Measure/report | Join cost × outcome across the size (phase 1) and churn (phase 2) sweeps; emit the headline chart | Per-arm, per-size rows with lines-read, tokens, resolved status and index build cost; uncertainty and all failure categories reported; chart reproducible from the joined data |

## Stage contracts

Each stage consumes the prior stage's frozen artifacts and emits its own,
hashing anything that must stay stable downstream.

- **G → P** — generation directory `generated/<needle-id>/N<size>/`:
  - `repo/` synthetic + needle source tree (agent-visible later).
  - `needle.grading.json` — gold patch, FAIL_TO_PASS/PASS_TO_PASS, hidden test
    files (host-only).
  - `scaffold.json` — seed, size N, distractor manifest (module list, synthetic
    call-graph edges), generator source SHA.
  - `task.toml` — Harbor v1.4 sidecar (network policy none).
  - `generation.json` — needle provenance (source dataset/revision/fingerprint,
    base commit) and scaffold hashes.
- **P → I/R** — prepared directory mirroring `explore.py`'s layout: sanitized
  `repo/`, `task.json` (issue/repo/base only), host-only `*.grading.*`,
  `preparation.json` with `repo_sha256`, `grading_sha256`, `task_sha256`, size N
  and seed.
- **I → R** — `index/<arm>/` plus `index.json` =
  `{arm, build_seconds, index_bytes, query_tool_spec, builder_source_sha,
  input_repo_sha256, leakage_checks_passed}`. The grep arm sets
  `build_seconds=0`, `index_bytes=0` and notes that no artifact is prebuilt.
- **R** — standard `runs/<label>/` with `manifest.json` extended by
  `{needle_id, size_N, churn_id|null, index_arm, index_sha|null, tool_allowlist,
  prepared_dir, scaffold_seed}`; per-task `trajectory.traj.json`, `patch.diff`,
  `prediction.json`, `metrics.json` (with read instrumentation, below) and
  `process.json`.
- **E** — official `*.report.json` + log retained in the run dir (unchanged
  `bench.evaluate` path); optional `scores.json` for regions (unchanged
  `explore.py` scorer path).
- **M** — `sweep.json` / `sweep.csv` with one row per `(needle_id, size_N,
  churn_id, index_arm, repetition)` carrying `{resolved, input_tokens,
  output_tokens, lines_read, files_read, bytes_read, index_build_seconds,
  index_bytes, wall_seconds, failure_category}`; plus `headline.(csv|png)` for
  the size sweep.

## New instrumentation: line-level read tracking

`bench.metrics()` today counts API responses, tokens, tool actions and format
errors from the trajectory but has no notion of lines read. The design keeps
mini untouched:

1. **Source of truth is the trajectory.** mini already records each tool
   action's command and its observation in `messages[*].extra`. Add a post-hoc
   pure function (e.g. `read_metrics(trajectory)`) alongside `metrics()`; do not
   change mini. It replays recorded commands/observations and attributes read
   volume.
2. **Make reads attributable by constraining the tool interface.** As `explore.py`
   already does by steering the agent toward `nl`/`sed -n`, prefer a small
   allowlisted read interface over free-form shell so reads are machine-parseable:
   explicit tools (`open path start end`, `grep pattern`, `index_query ...`)
   whose observations carry structured `{path, start, end, lines_returned,
   bytes_returned}`. The index tool logs what it returned.
3. **Count delivered context, not intent.** Attribute `lines_read`/`bytes_read`
   from the observation actually returned to the model (truncation-aware),
   matching the PLAN's "observed context" definition and its warning against
   reconstructing read spans from arbitrary shell. Genuinely ambiguous spans are
   recorded as `null`/"unattributed", never zero.
4. **Where it lands.** `read_metrics()` output merges into the per-task
   `metrics.json` dict (same `{**metrics(traj), ...}` pattern), so `summarize()`
   and the sweep join pick it up without further change.
5. **Non-breaking guarantee.** All of this reads artifacts mini already writes;
   the only runtime change is the tool allowlist/config passed through
   `bench.config`, already a supported extension point.

## Experimental-control requirements

Carried from `docs/PLAN.md`:

- Identical model, action interface, budgets (steps, output tokens, wall),
  Docker image tag + digest, and tool allowlist across all arms of a comparison.
  The only difference between arms is the available index tool (or, for the size
  sweep, N).
- Fix the task manifest and scaffold seed before running. Record temperature (0),
  seeds where supported, truncation policy, output cap and stopping rules.
- The index must never be built from gold patches or gold relevant-file labels;
  record a leakage check in `index.json`.
- Record tokens and latency separately; do not claim GPU/FLOPs without telemetry.
- Sequential workers first; confirm the shared server's concurrency policy before
  scaling.
- A pilot supports debugging, not broad claims: paired comparisons, enough
  repetitions for any claim, uncertainty and every failure category reported.

## Phased task breakdown

Each task is a working, demoable increment; later tasks wire into earlier ones.

### Phase 1 — minimal end-to-end slice

Single needle, two sizes (small vs big), grep baseline vs one index, line-read
instrumentation, headline outcome × cost chart.

1. **Line-read instrumentation (foundation).** Add pure `read_metrics(trajectory)`
   and a structured read-tool allowlist config; merge output into `metrics.json`.
   Tests: offline unit tests over synthetic trajectories (hits, truncation,
   unattributable → null). Demo: existing astropy smoke task now reports
   `lines_read`/`files_read` with nulls where unattributable.
2. **Size-controlled generator.** Given one already-solved SWE-bench instance,
   emit `generated/<id>/N<size>/` with deterministic distractor modules and a
   synthetic call graph; needle untouched; hidden tests to host-only grading.
   Tests: determinism from seed, needle invariance across sizes, distractors
   import and do not satisfy FAIL_TO_PASS. Demo: same needle at small and big N,
   diff shows only the haystack grew.
3. **Prepare for generated repos.** Extend the prepare path (reuse `explore.py`
   snapshot/sanitize/hash/archive-safety patterns) to consume a generation dir.
   Tests: label hygiene (no grading in `repo/`), hash cross-checks. Demo:
   prepared small/big dirs with recorded hashes.
4. **Harbor `task.toml` emission.** Write a schema-v1.4-valid sidecar (network
   policy none) per instance. Tests: schema validation offline. Demo: both sizes
   validate.
5. **Index stage: grep baseline + one real index.** Build and record one index
   arm (recommend lexical/BM25 first; upstream pure code in
   `explore-evaluator-src/explorers/bm25.py` is a reference) plus the null-cost
   grep arm; expose each as the agent read tool via `bench.config`. Tests:
   reproducible build, no gold leakage, `index.json` fields present. Demo:
   `index/grep/` and `index/<arm>/` with recorded build time/size.
6. **Run both arms at both sizes.** Orchestrate 2 arms × 2 sizes through
   `bench.py` with identical budgets/image/allowlist. Demo: four run dirs with
   trajectories, patches and read-instrumented metrics.
7. **Evaluate via official oracle.** Reuse `bench.evaluate` to grade all four
   runs. Demo: resolved/unresolved per run from the official report.
8. **Measure/report headline chart.** Join cost × outcome into `sweep.csv` and
   render outcome × (lines-read, tokens) vs size for grep vs index. Tests: join
   correctness, null propagation. Demo: the tutorial chart.

### Phase 2 — churn knob

Add a deterministic churn operator over the haystack (churn defined as a measured
edit rate / graph-distance from base), index-staleness measurement in
`index.json`, and a churn sweep in Measure. Depends on Phase 1 tasks 2, 5, 8.

### Phase 3 — more index arms

Add structural/LSP and semantic/RAG arms (RAG scaffolding references
`explore-evaluator-src/explorers/rag*.py`). One arm at a time; same controls.

### Phase 4 — more needles and multi-language

Generalize beyond one Python needle; add Groovy, bash and Erlang as separate
generator/language contracts (not assumed to work by string-swapping).

## Validation and testing expectations

Follow the existing offline `tests/` style: `unittest`, mocked `datasets`/APIs,
synthetic tar/trajectory fixtures, LiteLLM bundled cost map, no live endpoint or
Docker during authoring. Minimum coverage: `read_metrics` attribution including
null cases; generator determinism and needle invariance; prepare label hygiene
and hash cross-checks; `task.toml` schema; index build reproducibility and
leakage check; sweep-join null propagation. A `docs/VALIDATION.md`-style record
should state what passed offline and explicitly what was not exercised (real
endpoint, Docker startup, official grading).

## Open risks and decisions to resolve

1. **Needle validity inside a synthetic scaffold.** Embedding a real solved
   instance so its hidden tests still pass at base and still flip red→green with
   the gold patch, when surrounded by generated modules. Risk: import
   path/namespacing collisions. Needs a validated red→green check on the
   scaffolded repo (R2E-Gym's Docker validation is the reference pattern) before
   any arm runs.
2. **Contamination-safe, realistic distractors.** Generated distractor code must
   be plausible (realistic call graphs) yet not memorized by the model and not
   accidentally satisfy FAIL_TO_PASS. Decision: procedural generation from a seed
   vs sampling real unrelated code (licensing/provenance implications).
3. **Churn definition and measurement.** Define churn precisely (fraction of
   lines/files changed? call-graph edit distance? commits replayed?) and how the
   staleness of each index type is quantified after churn.
4. **Structural indexing tooling.** Whether the structural/LSP arm reuses
   existing LSP tooling or a new language-server integration; affects Phase 3
   scope and the Docker image.
5. **Read attribution under free-form shell.** How strictly to constrain the tool
   interface. A locked structured read interface gives clean `lines_read` but
   diverges from a realistic repair agent; free-form shell is realistic but
   yields unattributable reads (recorded null). Decision needed, consistent with
   PLAN's observed-vs-submitted-context caution.
6. **Fairness of exposing an index as an optional tool.** The agent may ignore
   the index; ensure prompt/tool parity so the comparison measures the index's
   value, not prompt nudging.
7. **Image/budget parity across arms** when a real index arm needs extra runtime
   dependencies — keep the agent image identical and build indexes out-of-band so
   the Run image/tool surface stays constant.

## Sources inspected

- SWE-MERA: `github.com/MERA-Evaluation/repotest`, `.../SWE-MERA-CHALLENGE`,
  `huggingface.co/datasets/MERA-evaluation/SWE-MERA`.
- R2E-Gym: `github.com/R2E-Gym/R2E-Gym` (`docs/ENV_GENERATION.md`,
  `src/r2egym/repo_analysis/`, `src/r2egym/agenthub/tools/`), arXiv 2504.07164.
- SWE-Explore and SWE-bench: as integrated in this workspace (`explore.py`,
  `bench.py`, `docs/SWE_EXPLORE.md`, `docs/PLAN.md`).

This is a design document. No authenticated run, Docker execution, generator or
instrumentation code exists yet; those are the Phase 1 deliverables.
