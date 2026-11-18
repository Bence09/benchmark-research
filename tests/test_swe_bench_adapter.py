"""Offline tests for the SWE-bench repair adapter (E5-A). No Docker, no API.

Covers protocol conformance, required capabilities, patch parsing, prompt wiring,
and repair outcome -> E4 failure-bucket mapping. prepare()/evaluate()/
make_environment() are live paths (Docker + eval venv), exercised on WSL.
"""

import unittest
from pathlib import Path

from benchkit import BenchmarkAdapter
from benchkit.contract import PreparedTask
from benchkit.swe_bench_adapter import SweBenchAdapter
from metrics import FailureCategory, classify_failure

FIXTURE_POLICY = Path(__file__).resolve().parent.parent / "conditions" / "config.yaml"

SAMPLE_PATCH = """diff --git a/astropy/modeling/separable.py b/astropy/modeling/separable.py
index a308e2729..45bea3608 100755
--- a/astropy/modeling/separable.py
+++ b/astropy/modeling/separable.py
@@ -242,7 +242,7 @@ def _cstack(left, right):
-        cright[-right.shape[0]:, -right.shape[1]:] = 1
+        cright[-right.shape[0]:, -right.shape[1]:] = right
"""


def _prepared():
    return PreparedTask(
        instance_id="astropy__astropy-12907",
        prepared_dir=Path("/tmp/none"),
        repo_dir=Path("/tmp/none"),
        grading_path=Path("/tmp/none/grading.json"),
        task={
            "instance_id": "astropy__astropy-12907",
            "repo": "astropy/astropy",
            "base_commit": "d16bfe",
            "problem_statement": "bug",
        },
        preparation={},
    )


class TestConformance(unittest.TestCase):
    def test_conforms(self):
        self.assertIsInstance(SweBenchAdapter(), BenchmarkAdapter)

    def test_required_capabilities_is_repair_floor(self):
        self.assertEqual(
            SweBenchAdapter().required_capabilities(),
            ["grep", "read", "edit", "run", "submit"],
        )


class TestPatchParsing(unittest.TestCase):
    def setUp(self):
        self.a = SweBenchAdapter()

    def test_valid_patch(self):
        sub = self.a.parse_submission(SAMPLE_PATCH, _prepared())
        self.assertEqual(sub.kind, "patch")
        self.assertIn("diff --git", sub.payload)

    def test_sentinel_prefixed_submission_is_stripped(self):
        # Realistic submit output: `echo SENTINEL && cat patch.txt`
        raw = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n" + SAMPLE_PATCH
        sub = self.a.parse_submission(raw, _prepared())
        self.assertTrue(sub.payload.startswith("diff --git"))
        self.assertNotIn("COMPLETE_TASK", sub.payload)

    def test_non_diff_rejected(self):
        with self.assertRaises(ValueError):
            self.a.parse_submission("I think the fix is in separable.py", _prepared())

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            self.a.parse_submission("", _prepared())


class TestPrompt(unittest.TestCase):
    def test_prompt_mentions_index_only_when_added(self):
        from toolpolicy import load_policy, resolve

        a = SweBenchAdapter()
        p = load_policy(FIXTURE_POLICY)
        base = resolve(p, "baseline", required_capabilities=[])
        plus = resolve(p, "baseline_plus_index_encouraged", required_capabilities=[])
        self.assertNotIn("index_query", a.agent_prompt({}, base).system_template)
        self.assertIn("index_query", a.agent_prompt({}, plus).system_template)


class TestRepairFailureMapping(unittest.TestCase):
    """E5-A3: repair outcomes map to the E4 buckets."""

    def test_resolved_is_ok(self):
        self.assertEqual(
            classify_failure("Submitted", has_valid_submission=True, resolved=True),
            FailureCategory.OK,
        )

    def test_valid_patch_unresolved_is_valid_but_wrong(self):
        self.assertEqual(
            classify_failure("Submitted", has_valid_submission=True, resolved=False),
            FailureCategory.VALID_BUT_WRONG,
        )

    def test_no_patch_limit_is_output_limit(self):
        self.assertEqual(
            classify_failure("LimitsExceeded"), FailureCategory.OUTPUT_LIMIT
        )

    def test_setup_is_infra(self):
        self.assertEqual(classify_failure("SetupError"), FailureCategory.INFRA)


if __name__ == "__main__":
    unittest.main()
