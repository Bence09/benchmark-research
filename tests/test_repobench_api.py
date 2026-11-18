import argparse
import importlib.util as _ilu
import json
import os
import tempfile
import unittest
from pathlib import Path

_spec = _ilu.spec_from_file_location(
    "repobench_api", Path(__file__).parents[1] / "oracle" / "repobench_api.py"
)
runner = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(runner)


@unittest.skipUnless(
    os.environ.get("REPOBENCH_SOURCE"), "Set REPOBENCH_SOURCE to your official checkout"
)
class OfficialFunctions(unittest.TestCase):
    def setUp(self):
        self.source = Path(os.environ["REPOBENCH_SOURCE"])

    def test_official_prompt_with_header_prefix(self):
        import re

        build = runner.functions(
            self.source / "data/utils.py", ["construct_prompt"], {"re": re}
        )["construct_prompt"]
        data = {
            "repo_name": "org/repo",
            "file_path": "a.py",
            "import_statement": "from b import B",
            "cropped_code": '"""License header"""\n',
            "context": [{"path": "b.py", "snippet": "class B: pass"}],
        }
        prompt = build(data, tokenizer=None, max_token_nums=None)
        self.assertIn("from b import B", prompt)
        self.assertIn("class B: pass", prompt)
        self.assertIn('"""License header"""', prompt)

    def test_official_em_ignores_whitespace(self):
        metric = runner.functions(
            self.source / "evaluation/metrics.py", ["exact_match_score"]
        )
        self.assertEqual(metric["exact_match_score"](["  x = 1"], ["x   = 1"]), 1)
        self.assertEqual(metric["exact_match_score"](["x=1"], ["x = 1"]), 0)

    def test_official_extraction_skips_comments(self):
        extract = runner.functions(
            self.source / "run.py", ["get_first_line_not_comment"]
        )["get_first_line_not_comment"]
        self.assertEqual(
            extract("\n# comment\n    return result\nnext()"), "    return result"
        )

    def test_scoring_saved_fenced_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepared = root / "prepared"
            out = root / "run"
            prepared.mkdir()
            out.mkdir()
            runner.dump(
                prepared / "reference.grading.json", {"next_line": "return result"}
            )
            prep = {
                "reference_sha256": runner.sha(prepared / "reference.grading.json"),
                "extraction_source_sha256": runner.sha(self.source / "run.py"),
                "metrics_source_sha256": runner.sha(
                    self.source / "evaluation/metrics.py"
                ),
                "source_dir": str(self.source),
                "source_row_index": 0,
                "level": "2k",
                "split": "cross_file_first",
            }
            runner.dump(
                out / "manifest.json",
                {"prepared_dir": str(prepared), "preparation": prep},
            )
            runner.dump(out / "metrics.json", {"finish_reason": "stop"})
            (out / "completion.raw.txt").write_text("```python\nreturn result\n```")
            runner.score(argparse.Namespace(run_dir=str(out)))
            result = json.loads((out / "scores.json").read_text())
            self.assertEqual(result["exact_match"], 1)
            self.assertEqual(result["edit_similarity_0_to_100"], 100)
            self.assertTrue(result["fences_removed"])
            self.assertIsNone(result["codebleu"])
            runner.dump(out / "metrics.json", {"finish_reason": "length"})
            with self.assertRaises(ValueError):
                runner.score(argparse.Namespace(run_dir=str(out)))


if __name__ == "__main__":
    unittest.main()
