import argparse
import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("bench", Path(__file__).parents[1] / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class Measurements(unittest.TestCase):
    def test_usage_counts_format_failures_once(self):
        trajectory = {"messages": [
            {"role": "assistant", "extra": {"response": {"usage": {"prompt_tokens": 12, "completion_tokens": 4}}, "actions": [{"command": "ls"}]}},
            {"role": "user", "extra": {"interrupt_type": "FormatError", "response": {"usage": {"prompt_tokens": 20, "completion_tokens": 6}}}},
            {"role": "user", "content": "tool observation"},
        ]}
        m = bench.metrics(trajectory)
        self.assertEqual(m["input_tokens_recorded"], 32)
        self.assertEqual(m["output_tokens_recorded"], 10)
        self.assertEqual(m["max_input_tokens_recorded"], 20)
        self.assertEqual(m["recorded_api_responses"], 2)
        self.assertEqual(m["format_errors"], 1)
        self.assertEqual(m["tool_actions"], 1)
        self.assertTrue(m["usage_complete"])
        self.assertIsNone(m["monetary_cost"])

    def test_missing_usage_not_reported_as_complete_or_zero(self):
        self.assertIsNone(bench.metrics({})["output_tokens_recorded"])
        m = bench.metrics({"messages": [{"extra": {"response": {}}}]})
        self.assertFalse(m["usage_complete"])
        self.assertEqual(m["recorded_api_responses"], 1)
        self.assertIsNone(m["input_tokens_recorded"])

    def test_selection_preserves_order_and_revision(self):
        class Dataset(list):
            _fingerprint = "frozen-test-fingerprint"
        dataset = Dataset([{"instance_id": "b"}, {"instance_id": "a"}])
        mod = types.ModuleType("datasets")
        mod.load_dataset = unittest.mock.Mock(return_value=dataset)
        args = argparse.Namespace(instances="a,b", dataset_revision="revision-sha")
        with patch.dict(sys.modules, {"datasets": mod}):
            rows, fp = bench.select_tasks(args)
            self.assertEqual([r["instance_id"] for r in rows], ["a", "b"])
            self.assertEqual(fp, dataset._fingerprint)
            self.assertEqual(mod.load_dataset.call_args.kwargs["revision"], "revision-sha")
            args.instances = "missing"
            with self.assertRaises(SystemExit):
                bench.select_tasks(args)

    def test_export_includes_process_failures_without_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "example__repo-1"
            task.mkdir()
            bench.dump(task / "task.json", {"instance_id": task.name})
            bench.dump(task / "process.json", {"status": "wall_timeout", "wall_seconds": 100})
            bench.summarize(argparse.Namespace(run_dir=tmp))
            with (root / "summary.csv").open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["status"], "wall_timeout")
            self.assertEqual(rows[0]["instance_id"], task.name)

    def test_evaluator_uses_new_id_and_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bench.dump(root / "predictions.json", [])
            args = argparse.Namespace(run_dir=tmp, evaluator_python=sys.executable)
            with patch.object(bench.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as mock:
                bench.evaluate(args)
                first = mock.call_args.args[0]
                bench.evaluate(args)
                second = mock.call_args.args[0]
            self.assertNotEqual(first[-1], second[-1])
            self.assertEqual(mock.call_args.kwargs["cwd"], root)
            self.assertEqual(first[first.index("--max_workers") + 1], "1")


if __name__ == "__main__":
    unittest.main()
