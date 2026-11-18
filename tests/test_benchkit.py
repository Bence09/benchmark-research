"""Offline tests for benchkit (E3).

No Docker, no API. Uses a synthetic prepared task and a fake index builder; the
BM25 serialize→CLI round-trip test runs only when rank_bm25 is installed.
"""

import os
import tempfile
import unittest
from pathlib import Path

from benchkit import BenchmarkAdapter, Coordinator
from benchkit.contract import PreparedTask
from benchkit.swe_explore_adapter import SweExploreAdapter

FIXTURE_POLICY = Path(__file__).resolve().parent.parent / "conditions" / "config.yaml"


def _prepared(tmp: Path) -> PreparedTask:
    repo = tmp / "repo"
    (repo / "xarray" / "core").mkdir(parents=True)
    (repo / "xarray" / "core" / "combine.py").write_text(
        "\n".join(f"line {i}" for i in range(1, 121))
    )
    (repo / "README.md").write_text("concat datasets attrs\n")
    counts = {"xarray/core/combine.py": 120, "README.md": 1}
    return PreparedTask(
        instance_id="pydata__xarray-4629",
        prepared_dir=tmp,
        repo_dir=repo,
        grading_path=tmp / "benchmark.grading.jsonl",
        task={
            "instance_id": "pydata__xarray-4629",
            "problem_statement": "concat loses attrs",
        },
        preparation={"line_counts": counts, "top_k": 5},
    )


class TestAdapterContract(unittest.TestCase):
    def test_adapter_conforms(self):
        self.assertIsInstance(SweExploreAdapter(), BenchmarkAdapter)

    def test_required_capabilities(self):
        self.assertEqual(
            SweExploreAdapter().required_capabilities(), ["grep", "read", "submit"]
        )


class TestParseSubmission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prepared = _prepared(Path(self.tmp.name))
        self.adapter = SweExploreAdapter()

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_regions(self):
        text = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nRELEVANT_FILES:\n- xarray/core/combine.py:10-40"
        sub = self.adapter.parse_submission(text, self.prepared)
        self.assertEqual(sub.kind, "regions")
        self.assertEqual(
            sub.payload, [{"path": "xarray/core/combine.py", "start": 10, "end": 40}]
        )

    def test_missing_sentinel_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.parse_submission("no marker here", self.prepared)

    def test_path_escape_rejected(self):
        text = "RELEVANT_FILES:\n- ../etc/passwd:1-1"
        with self.assertRaises(ValueError):
            self.adapter.parse_submission(text, self.prepared)

    def test_out_of_range_rejected(self):
        text = "RELEVANT_FILES:\n- xarray/core/combine.py:10-9999"
        with self.assertRaises(ValueError):
            self.adapter.parse_submission(text, self.prepared)

    def test_duplicate_rejected(self):
        text = "RELEVANT_FILES:\n- README.md:1-1\n- README.md:1-1"
        with self.assertRaises(ValueError):
            self.adapter.parse_submission(text, self.prepared)


class TestCoordinatorPlanning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prepared = _prepared(Path(self.tmp.name))
        self.coord = Coordinator(SweExploreAdapter())

    def tearDown(self):
        self.tmp.cleanup()

    def test_baseline_plan_has_no_index(self):
        plan = self.coord.plan(self.prepared, FIXTURE_POLICY, "baseline")
        self.assertIsNone(plan.index_provenance)
        self.assertFalse(any(m["dst"].startswith("/index") for m in plan.env.mounts))
        # tool description must NOT mention index_query in baseline
        self.assertNotIn("index_query", plan.prompt.system_template)

    def test_baseline_requires_no_builder(self):
        # baseline does not add index -> builder absence is fine
        self.coord.plan(self.prepared, FIXTURE_POLICY, "baseline", build_index=None)

    def test_index_condition_without_builder_raises(self):
        with self.assertRaises(RuntimeError):
            self.coord.plan(
                self.prepared, FIXTURE_POLICY, "baseline_plus_index", build_index=None
            )

    def test_index_condition_builds_and_mounts(self):
        calls = {}

        def fake_builder(repo_dir, serialized_out):
            Path(serialized_out).write_text('{"kind":"bm25","chunks":[]}')
            calls["repo"] = str(repo_dir)
            return {"kind": "bm25", "chunk_count": 0, "serialized_path": "index.json"}

        plan = self.coord.plan(
            self.prepared,
            FIXTURE_POLICY,
            "baseline_plus_index",
            build_index=fake_builder,
        )
        self.assertIsNotNone(plan.index_provenance)
        self.assertTrue(any(m["dst"] == "/index/index.json" for m in plan.env.mounts))
        self.assertTrue(
            any(m["dst"].endswith("index_query.py") for m in plan.env.mounts)
        )
        # prompt now describes the added tool (intervention is explicit)
        self.assertIn("index_query", plan.prompt.system_template)

    def test_lockdown_flags_preserved_with_index(self):
        def fake_builder(repo_dir, serialized_out):
            Path(serialized_out).write_text("{}")
            return {"kind": "bm25"}

        plan = self.coord.plan(
            self.prepared,
            FIXTURE_POLICY,
            "baseline_plus_index",
            build_index=fake_builder,
        )
        joined = " ".join(plan.env.run_args)
        for flag in (
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
        ):
            self.assertIn(flag, joined)
        # every index mount is read-only
        self.assertEqual(joined.count("dst=/index/index.json,readonly"), 1)


# --- Option B round trip: host serialize -> in-container CLI ---
try:
    import rank_bm25  # noqa: F401

    from benchkit.index_serialize import serialize_bm25
    from indextool.bm25 import BM25Payload

    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False


@unittest.skipUnless(_HAS_BM25, "rank_bm25 not installed")
class TestOptionBRoundTrip(unittest.TestCase):
    def test_serialized_index_scores_same_top_file_in_container_cli(self):
        import importlib.util

        cli_path = (
            Path(__file__).resolve().parent.parent
            / "benchkit"
            / "in_container"
            / "index_query.py"
        )
        spec = importlib.util.spec_from_file_location("index_query_cli", cli_path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "repo"
            (root / "xarray" / "core").mkdir(parents=True)
            (root / "xarray" / "core" / "combine.py").write_text(
                "def concat(objs):\n    # combine datasets and keep attrs\n    return merged\n"
            )
            (root / "unrelated.py").write_text("def add(a, b):\n    return a + b\n")

            payload = BM25Payload(chunk_size=20, chunk_overlap=5)
            info = payload.build(root)
            serialized = Path(d) / "index.json"
            prov = serialize_bm25(payload, info, serialized)
            self.assertEqual(prov["kind"], "bm25")

            os.environ["INDEX_FILE"] = str(serialized)
            os.environ["INDEX_REPO"] = str(root)
            os.environ["INDEX_MAX_TOP_K"] = "5"
            os.environ["INDEX_MAX_SNIPPET"] = "300"

            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["combine datasets keep attrs", "--top-k", "1"])
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("xarray/core/combine.py", out)


class TestNudgeStyle(unittest.TestCase):
    def setUp(self):
        from toolpolicy import load_policy

        self.policy = load_policy(FIXTURE_POLICY)
        self.adapter = SweExploreAdapter()

    def _prompt(self, condition):
        from toolpolicy import resolve

        eff = resolve(
            self.policy,
            condition,
            required_capabilities=self.adapter.required_capabilities(),
        )
        return eff, self.adapter.agent_prompt(
            {"instance_id": "x", "problem_statement": "y"}, eff
        )

    def test_neutral_does_not_encourage(self):
        eff, prompt = self._prompt("baseline_plus_index")
        idx = next(t for t in eff["tools"] if t["name"] == "index_query")
        self.assertEqual(idx["description_style"], "neutral")
        self.assertIn("index_query", prompt.system_template)
        # neutral presents the tool as optional
        self.assertIn("optional", prompt.system_template)
        self.assertNotIn("at least once", prompt.system_template)

    def test_encouraged_adds_nudge_text(self):
        eff, prompt = self._prompt("baseline_plus_index_encouraged")
        idx = next(t for t in eff["tools"] if t["name"] == "index_query")
        self.assertEqual(idx["description_style"], "encouraged")
        # encouraged directs usage rather than presenting it as optional
        self.assertIn("at least once", prompt.system_template)
        self.assertNotIn("It is optional", prompt.system_template)

    def test_nudge_is_only_difference(self):
        from toolpolicy import resolve

        n = {t["name"]: t for t in resolve(self.policy, "baseline_plus_index")["tools"]}
        e = {
            t["name"]: t
            for t in resolve(self.policy, "baseline_plus_index_encouraged")["tools"]
        }
        self.assertEqual(set(n), set(e))
        diffs = {k for k in n if n[k] != e[k]}
        self.assertEqual(diffs, {"index_query"})
        self.assertEqual(n["index_query"]["description_style"], "neutral")
        self.assertEqual(e["index_query"]["description_style"], "encouraged")

    def test_baseline_has_no_style(self):
        eff, prompt = self._prompt("baseline")
        names = {t["name"] for t in eff["tools"]}
        self.assertNotIn("index_query", names)
        self.assertNotIn("index_query", prompt.system_template)


if __name__ == "__main__":
    unittest.main()
