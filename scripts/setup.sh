#!/usr/bin/env bash
# One-shot, idempotent reproducer for the whole local environment:
#   - .venv-agent : pinned mini-SWE-agent runner + adapters (+ fuzzywuzzy scorer dep)
#   - .venv-eval  : official SWE-bench harness (known-good tag v2.1.7)
#   - evaluator-src / explore-evaluator-src / repobench-src : pinned vendored sources
#
# Safe to re-run: existing venvs and clones are detected and left in place.
# Makes NO model calls and needs NO Docker. Dataset `prepare` steps and any
# --execute runs are deliberately left out; they are per-experiment and need
# network datasets and/or the BME endpoint key. See the printed next steps.
set -euo pipefail

# Anchor at the repository root (this script lives in scripts/).
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$ROOT"

# Pins (keep in sync with README.md / docs).
EXPLORE_SHA="5602f031f2d9562d0a805f83402b536e831a5a11"
EVALUATOR_TAG="v2.1.7"
FUZZYWUZZY_VERSION="0.18.0"

log() { printf '\n=== %s ===\n' "$1"; }

# --- 1. Agent virtual environment -------------------------------------------
log "Agent venv (.venv-agent)"
if [ -d .venv-agent ]; then
  echo ".venv-agent already exists; reusing it."
else
  python3 -m venv .venv-agent
fi
.venv-agent/bin/python -m pip install --upgrade pip
.venv-agent/bin/python -m pip install -r requirements/requirements-agent.txt
# RepoBench scorer dependency. Pinned; do NOT add python-Levenshtein, which would
# change fuzzywuzzy's similarity backend away from the recorded difflib behaviour.
.venv-agent/bin/python -m pip install "fuzzywuzzy==${FUZZYWUZZY_VERSION}"
.venv-agent/bin/python -m pip freeze > installed-agent.txt
echo "Agent environment ready."

# --- 2. Official SWE-bench evaluator + its venv -----------------------------
log "Evaluator source (evaluator-src @ ${EVALUATOR_TAG})"
if [ -d evaluator-src ]; then
  echo "evaluator-src already exists; reusing it."
else
  git clone --branch "${EVALUATOR_TAG}" --depth 1 \
    https://github.com/SWE-bench/SWE-bench.git evaluator-src
fi

log "Evaluator venv (.venv-eval)"
if [ -d .venv-eval ]; then
  echo ".venv-eval already exists; reusing it."
else
  python3 -m venv .venv-eval
fi
.venv-eval/bin/python -m pip install --upgrade pip
.venv-eval/bin/python -m pip install -e ./evaluator-src
.venv-eval/bin/python -m pip freeze > installed-evaluator.txt
echo "Evaluator environment ready."

# --- 3. Pinned scorer sources (no install; imported by file) ----------------
log "SWE-Explore scorer source (explore-evaluator-src @ ${EXPLORE_SHA})"
if [ -d explore-evaluator-src ]; then
  echo "explore-evaluator-src already exists; reusing it."
else
  git clone https://github.com/Qiushao-E/SWE-Explore-Bench.git explore-evaluator-src
  git -C explore-evaluator-src checkout "${EXPLORE_SHA}"
fi
test -f explore-evaluator-src/eval.py \
  && echo "explore-evaluator-src/eval.py present." \
  || { echo "ERROR: explore-evaluator-src/eval.py missing" >&2; exit 1; }

log "RepoBench source (repobench-src)"
if [ -d repobench-src ]; then
  echo "repobench-src already exists; reusing it."
else
  git clone https://github.com/Leolty/repobench.git repobench-src
fi
for f in run.py data/utils.py evaluation/metrics.py; do
  test -f "repobench-src/$f" \
    && echo "repobench-src/$f present." \
    || { echo "ERROR: repobench-src/$f missing" >&2; exit 1; }
done

# --- Done -------------------------------------------------------------------
log "Setup complete"
cat <<'NEXT'
Environments and sources are ready. To verify offline (no endpoint, no Docker):

  export SWE_EXPLORE_SOURCE="$PWD/explore-evaluator-src"
  export REPOBENCH_SOURCE="$PWD/repobench-src"
  export LITELLM_LOCAL_MODEL_COST_MAP=True
  .venv-agent/bin/python -m unittest discover -s tests -v

Per-experiment steps NOT run by this script (need network datasets / a key):

  # Regenerate benchmark inputs (network only; no key, no Docker):
  .venv-agent/bin/python repobench_api.py prepare --source repobench-src
  .venv-agent/bin/python explore.py prepare --bench /path/to/bench-one-normalized.jsonl

  # Live runs need OPENAI_API_KEY + the BME endpoint, and Docker for bench/explore.

Activate the agent environment with:  source .venv-agent/bin/activate
NEXT
