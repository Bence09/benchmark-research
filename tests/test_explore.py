import importlib.util
import importlib.util as _ilu
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

_spec = _ilu.spec_from_file_location(
    "explore", Path(__file__).parents[1] / "oracle" / "explore.py"
)
_explore = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_explore)
parse_regions, unpack_snapshot, select_record, METRICS = (
    _explore.parse_regions,
    _explore.unpack_snapshot,
    _explore.select_record,
    _explore.METRICS,
)


class AdapterTests(unittest.TestCase):
    def test_order_and_inclusive_ranges(self):
        result = parse_regions(
            "RELEVANT_FILES:\n- b.py:3-4\n- a.py:1-2", {"a.py": 5, "b.py": 9}, 5
        )
        self.assertEqual(result, [("b.py", 3, 4), ("a.py", 1, 2)])

    def test_invalid_predictions_rejected(self):
        for text in [
            "RELEVANT_FILES:\n- ../a.py:1-2",
            "RELEVANT_FILES:\n- a.py:1-99",
            "RELEVANT_FILES:\n- a.py:3-2",
            "RELEVANT_FILES:\n- /a.py:1-2",
            "RELEVANT_FILES:\n- a.py:1-2\n- a.py:1-2",
            "no submission",
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_regions(text, {"a.py": 10}, 5)

    def test_top_k_is_region_count(self):
        with self.assertRaises(ValueError):
            parse_regions("RELEVANT_FILES:\n- a.py:1-2\n- a.py:3-4", {"a.py": 10}, 1)

    def test_archive_strips_root_and_rejects_traversal(self):
        def archive(name):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w:gz") as tar:
                info = tarfile.TarInfo(name)
                info.size = 4
                tar.addfile(info, io.BytesIO(b"text"))
            return data.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unpack_snapshot(archive("snapshot/pkg/a.py"), root / "good")
            self.assertEqual((root / "good/pkg/a.py").read_text(), "text")
            with self.assertRaises(ValueError):
                unpack_snapshot(archive("snapshot/../escape"), root / "bad")

    def test_record_selection_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bench.jsonl"
            row = {
                "instance_id": "repo-1",
                "ground_truth": {"read_core_regions": [["a.py", 1, 2]]},
            }
            path.write_text(json.dumps(row) + "\n")
            self.assertEqual(select_record(path, "repo-1"), row)
            with self.assertRaises(ValueError):
                select_record(path, "missing")

    @unittest.skipUnless(
        os.environ.get("SWE_EXPLORE_SOURCE"),
        "Set SWE_EXPLORE_SOURCE for real scorer test",
    )
    def test_real_upstream_scorer(self):
        source = Path(os.environ["SWE_EXPLORE_SOURCE"]) / "eval.py"
        spec = importlib.util.spec_from_file_location("real_explore_eval", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        row = {
            "instance_id": "toy",
            "ground_truth": {
                "read_core_files": ["a.py"],
                "read_core_regions": [["a.py", 1, 4]],
                "read_optional_files_map": {},
                "read_optional_regions_map": {},
                "main_files": ["a.py"],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bench.jsonl"
            path.write_text(json.dumps(row) + "\n")
            evaluator = module.ExploreEvaluator(path, {"toy": {"a.py": 5}})
            scores = evaluator.evaluate(
                lambda issue, iid: [("a.py", 1, 2)], ["toy"], METRICS
            )["toy"]
        self.assertEqual(scores["precision"], 1)
        self.assertEqual(scores["recall"], 0.5)
        self.assertEqual(scores["hit_file_rate"], 1)
        self.assertEqual(set(scores), set(METRICS))


if __name__ == "__main__":
    unittest.main()
