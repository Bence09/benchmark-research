# Backlog — configurable tool-policy layer for coding-agent benchmarking

Working document • created 2026-11-15 • this is the load-bearing plan we build from.

**Supersedes for planning purposes:**
- `docs/tool_policy_research_plan.md` — the rationale and requirements (R1–R12) this
  backlog traces to. Still the reference for *why*; this doc is the *what/when*.
- `docs/PIPELINE.md` — its **generator, repository-size knob and churn** are parked
  (see Epic 5 / Non-goals). Its **line-level read instrumentation** design is pulled
  forward into Epic 4 and is the only part built now.
- `docs/PLAN.md` — original research framing; unchanged, still useful background.

The existing `bench.py`, `explore.py`, `repobench_api.py` and their tests/runs are
treated as an **oracle / reference implementation**: proof the moving parts are
reachable and a source of worked examples, **not** a codebase we extend. The new
implementation starts from scratch with this scope and plan.

---

## Scope

Build a **benchmark-agnostic tool-policy layer** for coding-agent experiments:

> A declared YAML configuration resolves into an effective, inspectable, logged
> toolset for a coding agent. An existing benchmark runs underneath through a
> pluggable adapter. Outcome and cost are measured across tool conditions.

The product is the **tool-policy layer and its measurement**, not another benchmark
runner. The benchmark underneath is pluggable; SWE-Explore is the first adapter.

### Settled decisions (from planning discussion)

1. **Modified tool *environment*, not modified benchmark *content***. We change the
   agent's toolset around an unmodified benchmark task + grading.
2. **Additive conditions.** Baseline = task-scoped search (grep) + essential
   read/submit operations. Other conditions *add* capabilities (index, symbols, …).
3. **Restriction is expressible but not strictly enforced** in the first cut. We
   record tool availability and actual use; strict removal (e.g. grep off `PATH`)
   is a later, optional condition, not a first-cut requirement. (tool_policy R3, §13-2)
4. **Retrieval tool = stable contract, swappable payload.** The agent-facing tool
   interface (inputs, output format, logging) is frozen; what sits behind it is
   interchangeable — a reference/demo index now (BM25 over a demo repo), real
   index/RAG later — with no interface change.
5. **Benchmark-agnostic by design.** SWE-Explore is the first adapter; SWE-bench is
   a later adapter that proves the abstraction is reusable (tool_policy R12).
6. **Existing code is oracle/reference only.** New code is written fresh.

### Deliverable

A tutorial on coding-agent benchmarking for the E/// presentation, demonstrated by
**baseline vs. baseline+index** runs on SWE-Explore showing the tool policy
measurably changes cost and/or outcome.

---

## Non-goals (parked — see Epic 5)

Explicitly out of near-term scope. Parking them is a decision, not an oversight.

- Repository-size knob, churn / stale-index experiments.
- Benchmark-**content** generation (all of PIPELINE.md's generator, stage G).
- Strict tool removal / enforced "index-only" conditions.
- Real index/RAG or structural/LSP implementations (only a reference payload now).
- Multi-language generation; multi-agent; training; a GUI or distributed scheduler.
- Replacing official benchmark evaluators.

---

## Requirements trace

Acceptance criteria below reference requirement IDs from
`docs/tool_policy_research_plan.md` §5 (R1–R12). Kept here for quick lookup:

| ID | Requirement (abbrev.) |
|---|---|
| R1 | Declare tool config separately from benchmark task data |
| R2 | Support additive tool configurations |
| R3 | Represent restrictions explicitly (enforced / best-effort / unsupported) |
| R4 | Preserve task-essential operations (validate before running) |
| R5 | Separate benchmark / agent / environment integration |
| R6 | Make effective configuration inspectable (pre-run summary) |
| R7 | Record tool interactions (available / called / returned / error / denied) |
| R8 | Preserve benchmark evaluation through its designated evaluator |
| R9 | Record reproducibility info (versions, policy, tools, env, index provenance) |
| R10 | Fail clearly on incompatible combinations (never silently weaken) |
| R11 | Preserve existing behavior when no intervention is selected |
| R12 | Support extension through documented integration contracts |

---

## Epic overview

| Epic | Title | Goal | Status |
|---|---|---|---|
| E0 | Decide-first spikes | Settle the three decisions that shape everything else | first |
| E1 | Tool-policy config layer | YAML → resolved, inspectable, logged effective policy | core |
| E2 | Agent tool interface + swappable index payload | Frozen tool contract; reference BM25 behind it | core |
| E3 | Benchmark adapter contract + SWE-Explore adapter | Pluggable benchmark; first conforming adapter | core |
| E4 | Measurement + demo comparison | Outcome × cost across conditions; the tutorial chart | core |
| E5 | Parked / later research | SWE-bench adapter, strict removal, size, churn, real index | parked |

Dependency order: **E0 → E1 → (E2, E3 in parallel) → E4**. E5 depends on E1–E4.

---

## Epic 0 — Decide-first spikes

Resolve before writing production code. Output is written decisions, not software.
Maps to tool_policy §13 (decisions 1–3) and §14 ("one complete example on paper").

### E0 — Settled decisions (2026-11-15)

Resolved in planning discussion. These are the inputs E1+ build against.

| # | Decision | Settled value |
|---|---|---|
| 1 | Demo task | `pydata__xarray-4629`, already prepared under `explore-input/xarray-4629/`. |
| 2 | Baseline essential tool set | `{ search = grep, read, submit }`; network off. Additive from here. |
| 3 | Model + budgets | `large` (Qwen3.6-35B); 30 steps / 2048 output tokens per call / 3600 s wall / temp 0. Headroom chosen to avoid resource-exhaustion failures in the demo. |
| 4 | Enforcement strength | Soft / best-effort, **record-only**. Log available / called / denied; do not physically prevent bypass. Strict removal + Docker-image shaping parked to E5-B. Consequence: claims may say "index available and used N times", never "agent could only use the index". |
| 5 | Retrieval-tool result shape | `index_query` returns **pointers + snippet**: `{path, start, end, score, snippet}`. Richer for the agent; cost-attribution handled by persistence, not by restricting output. |

**Persistence requirement (follows from decision 5).** Source text reaches the
model through two channels — `index_query` snippets and explicit `read`. Run
persistence must tag each delivered observation with its channel so reads are
attributable per channel. E4 reports both combined and per-channel lines/bytes
read; no information is discarded at collection time ("capture richly, interpret
later"). This keeps the grep-vs-index cost comparison honest rather than
double-counting snippet text as a repo read.

### Still open (resolve during E0 execution, not blocking E1 design)

- Which demo repo backs the BM25 reference index — the xarray task's own repo, or
  a separate toy repo for a pure plumbing demo (E0-S3 / E2-S2).
- Exact bar-chart axes for the tutorial (E4-S2).

### E0-S1 — Describe one complete example on paper
As the researcher, I can point to a single fully-specified example run so the build
targets something concrete.
**Subtasks**
- Pick the one SWE-Explore task for the demo (candidate: `pydata__xarray-4629`,
  already prepared under `explore-input/`; confirm it is still the intended one).
- Write out: the task input, required operations, submission format, evaluator.
- Write out: the exact baseline tool list and the one additive (+index) config.
- Write out: fixed variables, intended differences, known limitations.
**Acceptance**
- A one-page example exists covering tool_policy §14's seven checklist items.
- The baseline tool list names every search/read/submit op explicitly (R4, §13-3).

### E0-S2 — Settle enforcement strength for the first cut
**Subtasks**
- Confirm the first cut records availability + use only (no strict removal).
- Define what "available" vs "used" vs "denied" mean in the trajectory (R3, R7).
- State which claims (if any) would later require strict enforcement, and defer them.
**Acceptance**
- A decision note states: first cut = best-effort/record-only; strict removal parked.
- The enforcement status vocabulary (enforced / best-effort / unsupported) is fixed.

### E0-S3 — Define the retrieval-tool contract (payload-independent)
**Subtasks**
- Specify the agent-facing tool: name, inputs, accessible paths, output format,
  output cap, timeout, logging fields.
- Confirm the contract says nothing about *how* the index is built (BM25/LSP/RAG),
  so the payload is swappable (decision 4).
**Acceptance**
- A written tool contract exists that a reference BM25 payload and a future RAG
  payload could both satisfy without interface change (R12).

---

## Epic 1 — Tool-policy config layer

Realize the YAML config surface: declared policy → resolved effective policy,
inspectable and logged. Benchmark-independent.

### E1 — Settled schema design (2026-11-15)

**Reality it respects.** The agent (mini, tools interface) acts through bash;
there is no toggleable tool registry. `grep` is a binary on `PATH`, `read` is
`sed`/`cat`, `submit` is a protocol convention, an added tool is an installed
command + an injected prompt description. So a directive touches up to three
layers — environment (binary on PATH), prompt (tool described), logging (use
recorded) — and the schema is honest about which layer it can actually affect
under soft enforcement. The oracle's `bench.config()` TODO ("implement offline/
online indexes, rags, just grep") is the hook this policy feeds into; it produces
input to mini's `cfg`, it does not replace it.

**File shape — named conditions, one selected per run:**

```yaml
version: 1
conditions:
  baseline:
    description: "Task-scoped search: grep + read + submit. Network off."
    tools:
      grep:    { directive: allow }
      read:    { directive: allow }
      submit:  { directive: allow }
      network: { directive: deny }           # enforced via container netns=none
  baseline_plus_index:
    extends: baseline                         # additive: inherits + states delta only
    description: "Baseline plus an index_query retrieval tool."
    tools:
      index_query:
        directive: add
        payload: { kind: bm25, index_ref: null, stale: false }   # opaque to policy layer
        limits: { top_k: 10, max_snippet_bytes: 2048, timeout_seconds: 30 }
        result_shape: pointers_plus_snippet   # from E0-S5
```

**Settled design decisions:**

- **D1** One file, many named conditions; a run selects one, recorded in its manifest.
- **D2** `extends` expresses additivity (R2): a child inherits a parent and states
  only the delta, so "differ only in the declared addition" is structurally true.
- **D3** Four directives, full grammar now:
  - `allow` — task-essential, expected present (grep/read/submit).
  - `add` — inject a new tool (install + prompt description + logging).
  - `deny` — declared off **and** really enforced (network via `netns=none`).
  - `remove` — declared off, **best-effort only** (prompt drops it; binary may
    remain). Documented-but-parked; strict removal is E5-B.
  First cut actively uses `allow`/`add`/`deny`; `remove` is in the grammar, flagged
  unsupported-for-strict.
- **D4** `payload` is **opaque** to the policy layer (R12, E0-S5): it validates a
  `payload` block with a `kind` exists, never interprets `kind`. BM25-now vs
  RAG-later is entirely an E2 concern; swapping needs no schema change.
- **D5** Enforcement status is **derived, not declared**. Author writes `directive`;
  the resolver computes `enforcement: enforced | best-effort | unsupported` from
  the directive + what the environment can back. `deny network` → enforced;
  `remove grep` → best-effort. Authors cannot misstate enforcement.
- **D6** Validation (R4/R10): every tool in an adapter's `required_capabilities`
  must resolve present or the run is rejected before the model starts; unknown
  directive, `add` without `payload.kind`, or a child that both `add`s and
  `remove`s a tool → hard error; omitting the policy → untouched default (R11).

**Resolver output (feeds E1-S3 summary + manifest), both declared and resolved
blocks hashed and saved (R9):**

```
effective_policy:
  condition: baseline_plus_index
  tools:
    - { name: grep,    source: baseline, status: present, enforcement: n/a }
    - { name: network, source: baseline, status: denied,  enforcement: enforced }
    - { name: index_query, source: baseline_plus_index, status: added,
        enforcement: best-effort, payload_kind: bm25, index_ref_sha256: <hash>, limits: {...} }
  declared_sha256: <...>
  resolved_sha256: <...>
```

### E1-S1 — YAML schema for tool policies
Express `unrestricted` / `index_augmented` and the general additive shape.
**Subtasks**
- Define schema: named conditions, per-tool directive (`allow` / `add` /
  `remove`), tool-specific params (e.g. `index: {tool, stale}`).
- Validation: unknown tools, missing required params, contradictory directives.
- Fixtures: at least the baseline and baseline+index conditions.
**Acceptance**
- A policy is declared independently of benchmark task data (R1).
- Baseline and baseline+index differ *only* in the declared addition (R2).
- An invalid/contradictory policy is rejected with a clear error (R10).
- Offline unit tests cover valid parse, additive diff, and each rejection path.

### E1-S2 — Resolve declared policy into an effective policy
**Subtasks**
- Resolver: declared directives → concrete effective tool list + limits +
  enforcement status per tool (enforced / best-effort / unsupported) (R3).
- Carry provenance: which directive produced each effective entry.
**Acceptance**
- Each effective tool records its enforcement status (R3).
- Omitting a policy yields the untouched default environment (R11).
- Tests assert resolved output for baseline vs +index and the no-policy default.

### E1-S3 — Inspectable pre-run summary
**Subtasks**
- Render a human-readable summary: actual tools, limits, repo snapshot, runtime
  settings, declared vs resolved policy.
- Persist both declared and resolved policy into the run record.
**Acceptance**
- A pre-run summary shows the effective configuration before the model runs (R6).
- Declared and resolved policy are both saved for reproducibility (R9).

---

## Epic 2 — Agent tool interface + swappable index payload

Frozen tool contract (from E0-S3); a reference BM25 payload proves plumbing.

### E2 — Built and verified (2026-11-15)

Package `indextool/` (sibling to `toolpolicy/`), pure/offline. Oracle untouched.

- `contract.py` — frozen types: `Region`, `BuildInfo` (provenance → `index.json`),
  `ToolCallRecord` (per-call log, channel-tagged), `IndexPayload` protocol (the
  swap point; `kind` opaque, matches policy `payload.kind`).
- `chunking.py` — in-package overlapping line-window chunker (reimplemented, not
  imported from the oracle; 1-based inclusive ranges, source-ext filter).
- `bm25.py` — `BM25Payload`: `rank_bm25` imported lazily (optional dep), build
  provenance + leakage guard (rejects any `*.grading.*` artifact entering the index).
- `boundary.py` — `ToolBoundary`: parses `index_query "<q>" [--top-k N]`, clamps
  top_k to the policy limit, times the call, truncates snippet to
  `max_snippet_bytes`, tags delivered bytes `channel="index_query"` (E0 persistence
  requirement), returns `(observation, ToolCallRecord)`. Payload errors become
  observations, never crashes. Depends only on `IndexPayload`.

**Decisions settled here:** chunker reimplemented in-package (import-independent);
`indextool/` is a separate package from `toolpolicy/`; `rank-bm25==0.2.2` added to
`.venv-agent`, BM25 behind optional import so tests skip cleanly if absent.
**Open item #1 resolved:** the demo index is backed by the SWE-Explore task's own
repo (`explore-input/xarray-4629/repo`).

**Verification:**
- `tests/test_indextool.py` — 13 tests. Boundary (fake payload): clamp, truncate,
  empty/bad-arg recorded-not-crashed, payload-exception-as-observation, record
  serialization. Chunker: 1-based ranges, binary skip, max-chunks cap. BM25:
  build+query returns the on-topic file, leakage check rejects a grading artifact.
- Full discovery: **56 tests OK (5 oracle env-gated skips)**, no regressions.
- Real-repo sanity: BM25 over xarray-4629 (1946 chunks, ~5 MB, ~7.8 s build,
  leakage passed) returns `xarray/core/combine.py` as top hits for a concat/attrs
  query — plausible, on-topic regions, not noise.

**Still E3's job (not built here):** injecting `index_query` into mini's container
and wiring the bash call; that needs the environment + benchmark adapter.

### E2-S1 — Agent tool boundary with interaction logging
**Subtasks**
- Expose the resolved toolset to the agent through the supported boundary
  (consult `explore.py` as oracle for the mini tool wiring).
- Log per call: tool, arguments, returned observation, error, denied status,
  latency (R7).
- Capture delivered-observation volume (bytes/chars after truncation) (§10).
**Acceptance**
- Available / requested / returned / errored / denied calls are distinguishable
  in the saved trajectory (R7).
- A denied/absent tool call is recorded, not silently dropped.
- Offline tests over synthetic trajectories cover each interaction category.

### E2-S2 — Retrieval tool: reference BM25 payload
**Subtasks**
- Build a BM25 index over a demo repository snapshot (reference:
  `explore-evaluator-src/explorers/bm25.py`, oracle only).
- Implement the retrieval tool satisfying the E0-S3 contract; index built only
  from permitted repo inputs — no gold labels / held-out tests (§6).
- Record index provenance: builder version, input snapshot hash, build time,
  on-disk size (R9, §10).
**Acceptance**
- The retrieval tool conforms to the frozen contract; swapping the payload needs
  no interface change (R12, decision 4).
- Index provenance (hashed inputs, build time, size) is recorded (R9).
- A leakage check confirms no gold/held-out content entered the index (§6).
- Tests: reproducible build from the frozen snapshot; leakage check; contract shape.

---

## Epic 3 — Benchmark adapter contract + SWE-Explore adapter

Make the solution benchmark-agnostic: define the contract first, then one adapter.

### E3 — Built and verified (2026-11-15)

Package `benchkit/`. Oracle untouched. Offline-verified; live Docker/API wired
but not run (matches repo convention).

- `contract.py` — `BenchmarkAdapter` protocol + `PreparedTask` / `PromptSpec` /
  `EnvSpec` / `Submission`. Generalizes the oracle's prepare/run/score.
- `swe_explore_adapter.py` — `SweExploreAdapter`: `required_capabilities()` =
  `["grep","read","submit"]` (E0 decision 2); `parse_submission` reuses the
  oracle's exact region grammar (RELEVANT_FILES, 1-based inclusive, path-escape +
  duplicate rejection); `container_spec` carries the proven lockdown
  (`--network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges`,
  read-only `/repo`); prompt injects the index tool description only when policy
  adds it. `prepare`/`evaluate` are the live steps (reuse oracle logic).
- `coordinator.py` — `plan_run` (offline core): resolve policy (E1) → validate
  required caps → build+serialize index **when policy adds `index_query`** → append
  read-only index + CLI mounts (lockdown flags untouched) → RunPlan + manifest.
  `execute()` (live mini-in-Docker) wired, not run.
- `index_serialize.py` + `in_container/index_query.py` — **Option B seam**: host
  builds BM25 (E2) and serializes to self-contained JSON; a pure-stdlib CLI reads
  it inside the locked container (no `rank_bm25`, no network, no host callback) and
  reads snippets from read-only `/repo`. Tool-call logging reconstructed from the
  trajectory afterward (R7).

**Decisions settled here:** Option B for the index-in-container seam; adapter owns
`container_spec`, coordinator appends the index mount; offline verification only.

**Verification (`tests/test_benchkit.py`, 13 tests):**
- Adapter conforms to protocol; required caps correct.
- `parse_submission`: valid regions + 4 rejection paths (sentinel, escape, range,
  duplicate) — identical behavior to the verified oracle grammar.
- Coordinator: baseline plans with **no** index mount and **no** index mention in
  the prompt; `baseline_plus_index` without a builder raises; with a builder it
  builds, mounts `/index/index.json` + the CLI read-only, and adds the tool
  description; all lockdown flags preserved alongside the index mount.
- **Option B round trip**: host-serialized BM25 index scored by the in-container
  CLI returns the same on-topic file.
- Real-repo consistency check: serialized-index CLI ranks xarray-4629 **identically
  (same top-3, same order)** to the live payload; only absolute scores differ
  (explicit k1/b vs rank_bm25 defaults), ranking unaffected.
- Full discovery: **69 tests OK (5 oracle skips)**, no regressions.

**Still the live step (not run here):** `Coordinator.execute()` — construct mini
`DockerEnvironment` from `plan.env`, run `DefaultAgent` with `plan.prompt`, parse
via `adapter.parse_submission`, grade via `adapter.evaluate`. Needs Docker +
`OPENAI_API_KEY`.

### E3-S1 — Benchmark adapter contract
**Subtasks**
- Define the contract: `prepare` (snapshot + sanitize + hash), `required_capabilities`,
  `submission_format`, `evaluate` (invoke designated evaluator) (R5, R8).
- Specify how an adapter declares task-essential operations so E1 can validate
  policy compatibility (R4).
**Acceptance**
- Adding a tool needs no change to grading logic; adding a benchmark needs no
  change to tool implementations (R5).
- The contract is documented well enough for a second adapter later (R12).

### E3-S2 — SWE-Explore adapter (first conforming implementation)
**Subtasks**
- Implement the adapter against the contract (oracle: `explore.py` prepare/run/score).
- Keep gold labels / reference regions host-only, outside any agent mount (§6).
- Wire the official upstream scorer as the designated evaluator (R8).
**Acceptance**
- The same task runs under baseline and baseline+index without touching task or
  grading data (R1, R8).
- Native SWE-Explore scores are produced by the designated evaluator, unmerged
  with cost metrics (R8, §6).
- Config validation flags a policy missing a task-essential op *before* running (R4).

---

## Epic 4 — Measurement + demo comparison

Join outcome × cost across conditions; produce the tutorial artifact. Pulls
PIPELINE.md's read instrumentation forward.

### E4 — Built and verified (2026-11-16)

Package `metrics/`. Pure/offline post-hoc over artifacts mini already writes (never
forks mini). **Scope shift agreed:** result handling targets a Prometheus/Grafana
style — define metrics cleanly, emit them consumable; plotting (MATLAB/matplotlib)
is explicitly *not* the main path. Grafana dashboards = separate later scope.

- `read_metrics.py` — per-channel read attribution. Classifies each command by the
  **first program in its pipeline** (`grep/rg/find`→search, `sed/nl/cat/head/tail`
  →read, `index_query`→index, known-non-read→other, **unknown leader→unattributed**).
  Attributes the paired `tool` observation's delivered bytes/lines to that channel.
  Unattributed volume is reported separately, never folded into a read channel or
  zeroed (§10). This is the per-channel split the E0 snippet-channel decision was
  built for: index reads are separable from grep/sed reads.
- `failures.py` — four §6 buckets: `infra` / `policy_setup` / `output_limit` /
  `valid_but_wrong` (+`ok`). A valid-but-low-scoring submission is a *result*, not
  a harness failure.
- `join.py` — `join_runs(run_dirs)` → flat labeled rows (outcome kept distinct from
  cost; index build cost reported separately). Emitters: `to_prometheus` (text
  exposition, **Prometheus naming**: `bench_*`, `_total`/`_bytes`/`_seconds`,
  labels `{benchmark,condition,instance,channel}`, **nulls omitted — absent ≠ zero**)
  and `to_csv` (channel-labeled columns). Missing measurements stay null.

**Decisions settled here:** first-program channel classification, unknown→
unattributed; Prometheus-convention metric naming from day one; plotting/Grafana
parked as separate scope once the core system runs.

**Verification (`tests/test_metrics.py`, 18 tests):**
- Classification: search/read/index leaders, first-program-in-pipeline wins
  (`grep … | head`→search, `nl … | sed`→read), unknown leader→unattributed,
  env-prefix skipped.
- Attribution: per-channel line counts on synthetic trajectories; unattributed not
  counted as a read; empty trajectory → 0.
- **Real trajectory**: the oracle explore run attributes to 4 search calls (51
  lines) + 5 read calls (218 lines) = **269 lines read, 0 unattributed** — every
  real command classified cleanly, validating the first-program rule on real agent
  behavior. (That run: 269 lines read → 0.09 f1, the baseline cost×outcome point.)
- Failure buckets; join reports index build cost separately (null for baseline);
  Prometheus omits null metrics (no phantom zero index cost on baseline); CSV has
  channel columns.
- Full discovery: **87 tests OK (5 oracle skips)**, no regressions.

**Still the live step (E4-S2 real numbers):** the actual baseline vs +index runs
need Docker + API (same as E3 `execute()`). The join/emitters are verified offline
on synthetic + real artifacts; they will consume the live run dirs unchanged.


### E4-S1 — Cost/outcome metrics (incl. line-level reads)
**Subtasks**
- Collect per run: native outcome, input/output/reasoning tokens, model-call
  count, tool-call counts, wall time with environment-prep time separated (§10).
- Port the `read_metrics(trajectory)` idea from PIPELINE.md: attribute
  lines/bytes read from delivered observations, truncation-aware; unattributable
  → `null`, never zero.
- Separate failure categories: infra / policy-setup / output-limit / valid-but-wrong (§6).
**Acceptance**
- Metrics record tokens, time and tool use separately; index setup cost is
  reported separately and (optionally) amortized with a stated run count (§10).
- Missing measurements are `null`, not estimated or zeroed (§6, §10).
- Tests: read attribution incl. null cases; failure-category separation.

### E4-S2 — Run the demo comparison and render the artifact
**Subtasks**
- Run baseline and baseline+index on the chosen SWE-Explore task under identical
  model/budgets/image, differing only by the declared addition (§6).
- Join outcome × cost per condition; render the comparison (bars: tokens /
  tool-combination, tool-calls / tool-combination, lines-read / tool-combination).
- Record warm/cold conditions and all failures.
**Acceptance**
- A reproducible comparison shows the tool policy's effect on cost and/or outcome.
- Paired conditions match on everything except the declared addition; unavoidable
  differences are recorded (§6).
- Stated honestly as an integration demonstration, not a broad accuracy claim (§6).

---

## Epic 5 — Parked / later research

Not started until E1–E4 land. Listed so scope does not silently drift back.

- **E5-A** SWE-bench adapter — second conforming adapter; proves reusability (R12).
  **Promoted to an active epic below (2026-11-16).**
- **E5-B** Strict enforcement condition — real tool removal (derived image / `offline_only`),
  with enforcement status honestly recorded (R3); note arbitrary shell can bypass
  a removed binary (§8).
- **E5-C** Real index/RAG and structural/LSP payloads behind the frozen contract.
- **E5-D** Repository-size knob (PIPELINE.md generator, stage G).
- **E5-E** Churn / stale-index experiments and staleness measurement.
- **E5-F** Multi-task sets with repetitions and uncertainty for comparative claims.

---

## Epic 5-A — SWE-bench repair adapter (second conforming adapter)

Wire real SWE-bench repair (patch + hidden-test grading) into the framework as a
second `BenchmarkAdapter`. The point is to prove the core (policy layer, index tool,
coordinator, metrics) carries a structurally different benchmark without change
(R12) — not to bend SWE-bench into localization.

**Settled scope (2026-11-16):**
- Full repair: submission is a patch; grading runs the official SWE-bench hidden
  tests (resolved / unresolved). Not a localization mode.
- Reuse the existing official evaluator as-is: the oracle's `.venv-eval` + harness
  tag `v2.1.7` (proven on `astropy__astropy-12907`). The harness is a property of
  SWE-bench itself, independent of our run environment; the adapter's `evaluate()`
  shells out to it (mirrors oracle `bench.evaluate()`), treating it as the
  designated grader (R8). Verify it still runs before assuming compatibility.
- Demo task: reuse `astropy__astropy-12907` (proven image + grading; an existing
  resolved oracle run dir confirms the baseline). Goal is a real end-to-end repair
  run driven through *our* framework, baseline vs +index.

**Key differences from the SWE-Explore adapter (why this is real work, not a swap):**
- Container is the task's *installed* image, and the repo is *writable* (the agent
  edits + the harness runs tests) — the SWE-Explore `--read-only` lockdown
  assumption changes. Crib from oracle `bench.py` (image mapping, patch extraction).
- `evaluate()` runs Docker-based test execution, not a pure-Python scorer.
- Index staleness: the Option-B index is built at prepare time and read-only; a
  repair agent edits files, so the index can go stale mid-run. First cut: index is
  for *finding* the bug (pre-edit); record the assumption, defer churn (E5-E).

### E5-A1 — Adapter: prepare + required_capabilities + container_spec
Crib the task-image + installed-repo setup from oracle `bench.py`; repo is writable.
**Acceptance**
- `required_capabilities()` names the repair floor (search, read, edit, run-tests,
  submit-patch); config validation flags a policy missing any before running (R4).
- `container_spec` uses the task image; lockdown reflects a writable repair repo,
  honestly different from SWE-Explore's read-only spec (recorded).
- No change required to `toolpolicy`, `indextool`, `metrics`, `coordinator`.

### E5-A2 — parse_submission (patch) + evaluate (official harness)
**Acceptance**
- `parse_submission` returns a `Submission(kind="patch", ...)`; empty/invalid patch
  is a recorded failure, not a crash.
- `evaluate()` writes `predictions.json`, invokes the reused `.venv-eval`/v2.1.7
  harness with a fresh run id, and returns the official resolved/unresolved result
  (R8); harness success != resolved (read the report, not the exit code).

### E5-A3 — Metrics + failure mapping for repair
**Acceptance**
- `read_metrics` works unchanged (trajectory-based); verified on a repair trajectory.
- Repair outcomes map to the E4 buckets: resolved→ok, valid-patch-but-unresolved→
  valid_but_wrong, empty/invalid patch→output_limit/infra as appropriate.

### E5-A4 — Index behavior in a writable repair container + live run
**Acceptance**
- Decision recorded: index is pre-edit find-only for the first cut; staleness
  deferred to E5-E.
- One real end-to-end repair run through the framework on `astropy-12907`, baseline
  vs +index, with official grading and a joined sweep.

**Reusability check (the epic's real acceptance, R12):** adding SWE-bench required
zero changes to `toolpolicy`, `indextool`, `metrics`, and `benchkit.coordinator`.
If that holds, the abstraction is proven; if not, record exactly where it leaked.

---

## Open questions still to resolve during E0

- Which agent/model for the first demo, and fixed budgets (steps, output cap,
  wall, temperature 0)? (tool_policy §13-6)
- Which single demo repo for the BM25 reference index (the SWE-Explore task's own
  repo, or a separate toy repo for the plumbing demo)?
- Exact bar-chart set for the tutorial (which tool-combination axes to show).
