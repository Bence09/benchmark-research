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

    # prepare() and evaluate() run live; see prepare_support / the oracle explore.py.
    def prepare(self, spec: dict) -> PreparedTask:  # pragma: no cover - live path
        raise NotImplementedError("Live prepare: reuse explore.py prepare logic (E3 live step)")

    def evaluate(self, prepared: PreparedTask, submission: Submission) -> dict:  # pragma: no cover
        raise NotImplementedError("Live evaluate: reuse explore.py score logic (E3 live step)")


def _tool_descriptions(policy: dict) -> str:
    """Describe only tools the resolved policy added (part of the intervention)."""
    added = [t for t in policy.get("tools", []) if t.get("status") == "added"]
    if not any(t["name"] == "index_query" for t in added):
        return ""
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
