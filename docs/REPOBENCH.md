# RepoBench v1.1 Python completion: one-example demonstration

Copy repobench_api.py beside bench.py. Existing runners and results are unchanged.
Use your existing .venv-agent environment. No Docker or mini-SWE-agent tool loop
is required: this task sends the supplied code context and predicts one next line.

## Prepare the same first 2k sample you inspected

```bash
python repobench_api.py prepare --source repobench-src
```

This streams the published Python v1.1 cross_file_first split and selects the
first 2k row in dataset order. No answer-based filtering is used. Output is
repobench-input/first-2k/; preparation refuses to overwrite an existing directory.
Use --revision DATASET_COMMIT_SHA to pin the dataset for a later reported campaign.
The selected inputs, reference, source checkout SHA and file hashes are saved.

The first sample's cropped_code is mostly a license header. It is valid: the
official prompt also supplies imports and three cross-file context snippets.
Keep this as the first smoke example rather than selecting by model success.

Files:

- input.json: allowlisted context fields only.
- prompt.txt: official construct_prompt output, inspectable before API use.
- reference.grading.json: target next line, excluded from API input.
- preparation.json: dataset selection, source revision and integrity hashes.

The official builder is loaded from data/utils.py with tokenizer=None: no prompt
cropping is applied. Stay with the 2k demo initially. The dataset's 2k level is based
on its own tokenization convention, not a guarantee about Qwen's token count.
Neither next_line, gold_snippet_index nor all_code is passed to the prompt builder.

## Generate through the university API

Set OPENAI_API_KEY using hidden shell input as before; do not print the key.
The default model is Qwen/Qwen3.6-35B-A3B-FP8.

```bash
python repobench_api.py run
```

The runner makes one nonstreaming chat-completion request, with temperature=0
and a 2048-token generation cap to allow for server reasoning. The task asks for
only the next code line. It writes a new runs/...-repobench-... directory containing
manifest.json, request.json (no authorization header), response.json,
completion.raw.txt and metrics.json. The actual API prompt contains no reference.
It never promotes reasoning_content into a prediction. Null content or generation
truncation is an explicit failure, with available response/usage retained.

This is a **chat-API adaptation of RepoBench completion**, not an exact reproduction
of the upstream causal-model generation protocol. The code-context prompt matches
the official builder without cropping; the added system instruction, chat template,
temperature and output limit differ from upstream run.py's defaults. Record this
distinction in the presentation rather than claiming a published comparable score.

## Score the saved completion, without another API call

One lightweight scorer dependency is needed:

```bash
python -m pip install 'fuzzywuzzy==0.18.0'
python repobench_api.py score runs/YOUR_REPOBENCH_RUN
```

The runner executes the exact named function definitions from the checked-out
official run.py and evaluation/metrics.py, using AST extraction to avoid importing
Transformers, loading a local GPU model, or requiring CodeBLEU for EM/ES scoring.
Hashes are checked before scoring. Whole-response Markdown fences are removed
when present; this is recorded. It then uses upstream get_first_line_not_comment.
Raw output is always preserved. Leading prose is not manually repaired into a hit.

Outputs:

- prediction.json: extracted line and reference.
- cross_file_first.jsonl: official idx/level/pred/gt record format (or selected split).
- scores.json: exact match and edit similarity, extraction details, dependency/backend.

Official Exact Match compares pred.split() with gt.split(), so whitespace differences
are ignored. A one-sample result is 0 or 1, not a broad accuracy estimate.
Official Edit Similarity uses fuzzywuzzy.fuzz.ratio and is on a 0–100 scale, despite
the upstream function's docstring describing 0–1. The backend is recorded because
installing python-Levenshtein can change similarity behavior. Keep the same backend
for any later comparison.
CodeBLEU is explicitly **not computed**, not replaced with 0. The one-line demo
uses EM and ES; adding CodeBLEU requires a separately checked dependency setup.
Text similarity does not establish functional correctness or passing repository tests.

## What this adds to the benchmark comparison

| Benchmark example | Agent/model input | Main submitted output | Grading |
|---|---|---|---|
| SWE-bench | Issue and repository tools | Repair patch | Required tests and resolved status |
| SWE-Explore | Issue and repository tools | Ranked line regions | Reference relevance/coverage |
| RepoBench v1.1 completion | Code prefix, imports and cross-file snippets | Next code line | Reference-line match/similarity |

This add-on does not implement the original RepoBench-R retrieval task or
RepoBench-P pipeline, and does not grant repository tools to the completion model.
Those are separate task adapters if needed for the next presentation example.

## Validation

The runner was checked offline against the fetched official prompt builder,
first-line extractor and EM/ES functions on synthetic examples. No authenticated
university generation or full benchmark campaign was performed here. Run the actual
one-sample experiment on WSL and preserve the inputs, raw response and scores.

Sources:

- https://github.com/Leolty/repobench/blob/main/run.py
- https://github.com/Leolty/repobench/blob/main/data/utils.py
- https://github.com/Leolty/repobench/blob/main/evaluation/metrics.py
- https://huggingface.co/datasets/tianyang/repobench_python_v1.1
