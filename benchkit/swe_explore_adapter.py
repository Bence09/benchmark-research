"""SWE-Explore benchmark adapter (E3-S2).

First conforming BenchmarkAdapter. The pure parts (required_capabilities, prompt,
container_spec, parse_submission) are offline-testable; prepare() and evaluate()
reuse the oracle explore.py's proven download/validate/score logic and run live.

The region grammar in parse_submission matches the oracle's parse_regions exactly
(RELEVANT_FILES, 1-based inclusive, path-escape + duplicate rejection) so behavior
is identical to the verified baseline.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from .contract import EnvSpec, PreparedTask, PromptSpec, Submission

# Pinned upstream SWE-Explore scorer location (vendored; see docs/SWE_EXPLORE.md).
ROOT_EVALUATOR_SRC = Path(__file__).resolve().parent.parent / "explore-evaluator-src"

# The 16 upstream metrics, in the oracle's order (explore.py). native_score uses f1.
EXPLORE_METRICS = [
    "precision", "recall", "f1_score", "hit_file_rate", "noise_file_rate",
    "hit_region_rate", "noise_region_rate", "weighted_core_coverage",
    "context_efficiency", "recall_at_100", "recall_at_300", "recall_at_500",
    "ndcg_at_100", "ndcg_at_300", "ndcg_at_500", "first_useful_hit",
]

SYSTEM = """You explore source code to locate context relevant to an issue.
Use the bash tool to search and read files in /repo. The repository is read-only.
Do not fix the issue, install packages, run repository code, or try network access.
Use short, targeted commands such as find, grep, nl, and sed -n.
{{tool_descriptions}}Return at most {{top_k}} regions ranked most relevant first.
Use repository-relative paths and exact 1-based inclusive line numbers. Finish with
a bash tool call that prints this format (substitute actual paths/ranges):
cat <<'EOF'
COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
RELEVANT_FILES:
- package/module.py:10-40
EOF
The first stdout line must be the submit sentinel. Submit before your step budget.
"""

INSTANCE_TEMPLATE = "ISSUE:\n{{task}}\nReturn up to {{top_k}} regions within {{step_limit}} model calls."


class SweExploreAdapter:
    name = "SWE-Explore"

    def required_capabilities(self) -> list[str]:
        # Baseline floor for a region-finding task (E0 decision 2).
        return ["grep", "read", "submit"]

    def agent_prompt(self, task: dict, policy: dict) -> PromptSpec:
        tool_desc = _tool_descriptions(policy)
        system = SYSTEM.replace("{{tool_descriptions}}", tool_desc)
        return PromptSpec(system_template=system, instance_template=INSTANCE_TEMPLATE)

    def container_spec(self, prepared: PreparedTask, policy: dict) -> EnvSpec:
        # Same lockdown the oracle proved: read-only repo, no network, dropped caps.
        run_args = [
            "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--mount",
            f"type=bind,src={prepared.repo_dir},dst=/repo,readonly",
            "--tmpfs", "/tmp:rw,nosuid,size=64m",
        ]
        mounts = [{"src": str(prepared.repo_dir), "dst": "/repo", "readonly": True}]
        return EnvSpec(image="python:3.12-slim", cwd="/repo", run_args=run_args,
                       timeout=60, pull_timeout=120, mounts=mounts)

    def parse_submission(self, text: str, prepared: PreparedTask) -> Submission:
        counts = prepared.preparation.get("line_counts")
        if counts is None:
            from .prepare_support import line_counts
            counts = line_counts(prepared.repo_dir)
        top_k = prepared.preparation.get("top_k", 5)
        regions = _parse_regions(text, counts, top_k)
        return Submission(kind="regions",
                          payload=[{"path": p, "start": s, "end": e} for p, s, e in regions])

    # prepare() runs live; see explore.py prepare logic (reused, not reimplemented).
    def prepare(self, spec: dict) -> PreparedTask:  # pragma: no cover - live path
        raise NotImplementedError("Live prepare: reuse explore.py prepare logic (E3 live step)")

    def evaluate(self, prepared: PreparedTask, submission: Submission,
                 *, evaluator_source=None) -> dict:
        """Grade submitted regions with the pinned upstream SWE-Explore scorer.

        Mirrors the oracle explore.py score() (verified path): load eval.py, revalidate
        the persisted regions, run ExploreEvaluator over the host-only grading record.
        Returns the 16-metric dict + evaluator file hash; never merges cost in (R8, §6).
        """
        import hashlib
        import importlib.util
        import json
        from pathlib import Path as _Path

        if submission.kind != "regions":
            raise ValueError(f"SWE-Explore expects region submissions, got {submission.kind!r}")

        src = _Path(evaluator_source or (ROOT_EVALUATOR_SRC)).expanduser().absolute()
        evaluator_file = src / "eval.py"
        if not evaluator_file.exists():
            raise FileNotFoundError(f"SWE-Explore evaluator not found at {evaluator_file}")

        grading = prepared.grading_path
        grading_sha = prepared.preparation.get("grading_sha256")
        if grading_sha and _sha256_file(grading) != grading_sha:
            raise RuntimeError("Grading record changed since prepare; refusing to score.")

        counts = prepared.preparation.get("line_counts")
        if counts is None:
            from .prepare_support import line_counts
            counts = line_counts(prepared.repo_dir)
        top_k = prepared.preparation.get("top_k", 5)

        # Revalidate persisted predictions through the same grammar before scoring.
        regions = submission.payload
        text = "RELEVANT_FILES:\n" + "\n".join(
            f"- {r['path']}:{r['start']}-{r['end']}" for r in regions)
        predictions = _parse_regions(text, counts, top_k)

        spec = importlib.util.spec_from_file_location("swe_explore_official_evaluator", evaluator_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        iid = prepared.instance_id
        evaluator = module.ExploreEvaluator(bench_data_path=grading, file_line_counts={iid: counts})
        scores = evaluator.evaluate(
            explore_method=lambda issue, instance: predictions,
            instance_ids=[iid], metrics=EXPLORE_METRICS)
        return {
            "instance_id": iid,
            "scores": scores[iid],
            "evaluator_file_sha256": hashlib.sha256(evaluator_file.read_bytes()).hexdigest(),
            "scored_artifact": "submitted ranked regions; not all observed reads",
        }


def _sha256_file(path) -> str:
    import hashlib
    from pathlib import Path as _Path
    return hashlib.sha256(_Path(path).read_bytes()).hexdigest()


def _tool_descriptions(policy: dict) -> str:
    """Describe only tools the resolved policy added (part of the intervention).

    The index tool's wording varies by its recorded `description_style`:
      - neutral    : offered as one option among grep/read (no preference expressed)
      - encouraged : the agent is asked to try the index first (a recorded nudge)
    Both are captured in the effective policy / manifest, so prompt strength is never
    silent drift. This encourages use; it does not force it (the task still needs
    normal reads — the index is a shortcut, not a replacement).
    """
    added = [t for t in policy.get("tools", []) if t.get("status") == "added"]
    idx = next((t for t in added if t["name"] == "index_query"), None)
    if idx is None:
        return ""
    style = idx.get("description_style", "neutral")
    if style == "encouraged":
        return (
            "You have an `index_query` tool: run `index_query \"<query>\" --top-k N`\n"
            "to retrieve ranked candidate regions with snippets across the whole repo.\n"
            "Use at least once a command starting with `index_query` to locate relevant code quickly, then\n"
            "confirm with grep/sed as needed. It is a fast way to find where to look.\n"
        )
    return (
        "You also have an `index_query` tool: run `index_query \"<query>\" --top-k N`\n"
        "to retrieve ranked candidate regions with snippets. It is optional; you may\n"
        "use grep instead or as well.\n"
    )


def _parse_regions(text, counts, top_k):
    """Identical grammar to the oracle explore.py parse_regions (verified baseline)."""
    if "RELEVANT_FILES:" not in text:
        raise ValueError("Submission lacks RELEVANT_FILES:")
    regions = []
    for line in text.split("RELEVANT_FILES:", 1)[1].splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\s*-\s+(.+?):(\d+)-(\d+)\s*", line)
        if not match:
            raise ValueError("Expected '- relative/path.py:start-end' for each region")
        path, start, end = match.group(1), int(match.group(2)), int(match.group(3))
        parts = PurePosixPath(path)
        if parts.is_absolute() or ".." in parts.parts or "\\" in path or path not in counts:
            raise ValueError(f"Invalid repository-relative path: {path}")
        if not 1 <= start <= end <= counts[path]:
            raise ValueError(f"Invalid line range for {path}")
        region = (path, start, end)
        if region in regions:
            raise ValueError("Duplicate submitted region")
        regions.append(region)
    if not 1 <= len(regions) <= top_k:
        raise ValueError(f"Expected 1 to {top_k} ranked regions")
    return regions
