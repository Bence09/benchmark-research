"""Offline tests for metrics (E4). No Docker, no API."""

import json
import tempfile
import unittest
from pathlib import Path

from metrics import (
    read_metrics, classify_command, Channel,
    classify_failure, FailureCategory,
    join_runs, to_prometheus, to_csv,
)

REAL_TRAJ = (Path(__file__).resolve().parent.parent /
             "runs" / "20261103T150903Z-explore-e27b3900" / "trajectory.traj.json")


class TestClassify(unittest.TestCase):
    def test_search_leaders(self):
        self.assertEqual(classify_command('grep -n "x" f.py'), Channel.SEARCH)
        self.assertEqual(classify_command("rg foo"), Channel.SEARCH)
        self.assertEqual(classify_command("find . -name '*.py'"), Channel.SEARCH)

    def test_read_leaders(self):
        self.assertEqual(classify_command("sed -n '1,20p' f.py"), Channel.READ)
        self.assertEqual(classify_command("nl -ba f.py"), Channel.READ)
        self.assertEqual(classify_command("cat f.py"), Channel.READ)

    def test_index_leader(self):
        self.assertEqual(classify_command('index_query "q" --top-k 5'), Channel.INDEX)
        self.assertEqual(classify_command('/opt/index_query.py "q"'), Channel.INDEX)

    def test_first_program_in_pipeline_wins(self):
        self.assertEqual(classify_command('grep -n foo f.py | head -30'), Channel.SEARCH)
        self.assertEqual(classify_command("nl -ba f.py | sed -n '490,535p'"), Channel.READ)

    def test_unknown_leader_unattributed(self):
        self.assertEqual(classify_command('python -c "print(open(1))"'), Channel.UNATTRIBUTED)
        self.assertEqual(classify_command("awk '{print}' f.py"), Channel.UNATTRIBUTED)

    def test_env_prefix_skipped(self):
        self.assertEqual(classify_command("FOO=1 grep x f"), Channel.SEARCH)


class TestReadMetricsSynthetic(unittest.TestCase):
    def _traj(self, *pairs):
        """pairs: (command, observation_text). Build messages in mini's shape."""
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "issue"}]
        for cmd, obs in pairs:
            msgs.append({"role": "assistant", "content": None,
                         "extra": {"actions": [{"command": cmd, "tool_call_id": "t"}]}})
            msgs.append({"role": "tool", "content": obs})
        return {"messages": msgs}

    def test_attributes_per_channel(self):
        traj = self._traj(
            ('grep -n x f.py', "a\nb\nc"),            # search: 3 lines
            ("sed -n '1,5p' f.py", "l1\nl2\nl3\nl4\nl5"),  # read: 5 lines
            ('index_query "q"', "[1] f.py:1-10\nsnippet"),  # index: 2 lines
        )
        m = read_metrics(traj)
        self.assertEqual(m["channels"]["search"]["lines"], 3)
        self.assertEqual(m["channels"]["read"]["lines"], 5)
        self.assertEqual(m["channels"]["index"]["lines"], 2)
        self.assertEqual(m["lines_read_total"], 10)

    def test_unattributed_not_counted_as_read(self):
        traj = self._traj(('python -c "print(1)"', "lots\nof\noutput"))
        m = read_metrics(traj)
        self.assertEqual(m["unattributed_calls"], 1)
        self.assertEqual(m["lines_read_total"], 0)  # not folded into a read channel
        self.assertEqual(m["channels"]["unattributed"]["lines"], 3)

    def test_empty_trajectory(self):
        m = read_metrics({"messages": []})
        self.assertEqual(m["lines_read_total"], 0)


@unittest.skipUnless(REAL_TRAJ.exists(), "real trajectory fixture absent")
class TestReadMetricsReal(unittest.TestCase):
    def test_real_explore_trajectory_attribution(self):
        traj = json.loads(REAL_TRAJ.read_text())
        m = read_metrics(traj)
        ch = m["channels"]
        # From inspection: grep x3-ish (search), nl|sed x2 (read), cat submit (read).
        self.assertGreater(ch["search"]["calls"], 0)
        self.assertGreater(ch["read"]["calls"], 0)
        self.assertGreater(m["lines_read_total"], 0)
        # index tool was not present in this baseline run
        self.assertEqual(ch["index"]["calls"], 0)


class TestFailures(unittest.TestCase):
    def test_infra(self):
        self.assertEqual(classify_failure("SetupError"), FailureCategory.INFRA)

    def test_output_limit(self):
        self.assertEqual(classify_failure("LimitsExceeded"), FailureCategory.OUTPUT_LIMIT)

    def test_policy_setup(self):
        self.assertEqual(classify_failure("Submitted", policy_error=True),
                         FailureCategory.POLICY_SETUP)

    def test_valid_but_wrong(self):
        self.assertEqual(classify_failure("Submitted", has_valid_submission=True, resolved=False),
                         FailureCategory.VALID_BUT_WRONG)

    def test_ok(self):
        self.assertEqual(classify_failure("Submitted", has_valid_submission=True, resolved=True),
                         FailureCategory.OK)


class TestJoinAndEmit(unittest.TestCase):
    def _run_dir(self, root: Path, condition, exit_status, lines_obs):
        d = root / condition
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({
            "benchmark": "SWE-Explore", "instance_id": "pydata__xarray-4629",
            "condition": condition,
            "index_provenance": ({"build_seconds": 7.8, "index_bytes": 5000}
                                 if condition == "baseline_plus_index" else None),
        }))
        (d / "metrics.json").write_text(json.dumps({
            "exit_status": exit_status, "valid_regions_saved": True,
            "input_tokens_recorded": 1000, "output_tokens_recorded": 50,
            "recorded_api_responses": 4, "tool_actions": 6, "wall_seconds": 12.3,
        }))
        (d / "scores.json").write_text(json.dumps({"scores": {"f1_score": 0.42}}))
        (d / "trajectory.traj.json").write_text(json.dumps({"messages": [
            {"role": "assistant", "extra": {"actions": [{"command": "grep -n x f.py"}]}},
            {"role": "tool", "content": "\n".join(["x"] * lines_obs)},
        ]}))
        return d

    def test_join_two_conditions(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            a = self._run_dir(root, "baseline", "Submitted", 10)
            b = self._run_dir(root, "baseline_plus_index", "Submitted", 4)
            rows = join_runs([a, b])
            self.assertEqual(len(rows), 2)
            by = {r["condition"]: r for r in rows}
            # index condition reports build cost separately; baseline has none
            self.assertIsNone(by["baseline"]["index_build_seconds"])
            self.assertEqual(by["baseline_plus_index"]["index_build_seconds"], 7.8)
            # outcome kept distinct from cost
            self.assertEqual(by["baseline"]["native_score"], 0.42)

    def test_prometheus_omits_nulls(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            a = self._run_dir(root, "baseline", "Submitted", 10)
            rows = join_runs([a])
            text = to_prometheus(rows)
            self.assertIn('bench_lines_read_total{', text)
            self.assertIn('channel="search"', text)
            # baseline has no index build -> metric absent, not zero
            self.assertNotIn("bench_index_build_seconds", text)

    def test_csv_has_channel_columns(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            a = self._run_dir(root, "baseline", "Submitted", 10)
            csv_text = to_csv(join_runs([a]))
            self.assertIn("channel_search_lines", csv_text)
            self.assertIn("native_score", csv_text)


if __name__ == "__main__":
    unittest.main()
