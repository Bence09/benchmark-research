"""Offline tests for the tool-policy resolver (E1-S2).

Covers extends additivity (D2), derived enforcement (D5), required-capability
validation (R4/D6), the untouched-default when no condition is selected (R11),
contradiction rejection, and declared/resolved hashing (R9).
"""

import unittest
from pathlib import Path

from toolpolicy.resolver import ResolutionError, resolve
from toolpolicy.schema import load_policy
from toolpolicy.summary import render_summary

FIXTURE = Path(__file__).resolve().parent.parent / "conditions" / "config.yaml"


def _by_name(effective):
    return {t["name"]: t for t in effective["tools"]}


class TestResolveFixture(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(FIXTURE)

    def test_baseline_tools(self):
        eff = resolve(self.policy, "baseline")
        tools = _by_name(eff)
        self.assertEqual(set(tools), {"grep", "read", "submit", "network"})
        self.assertNotIn("index_query", tools)

    def test_extends_is_additive(self):
        base = _by_name(resolve(self.policy, "baseline"))
        plus = _by_name(resolve(self.policy, "baseline_plus_index"))
        # The only difference is the declared addition (R2/D2).
        self.assertEqual(set(plus) - set(base), {"index_query"})
        self.assertEqual(set(base) - set(plus), set())
        # Inherited tools are unchanged.
        for name in base:
            self.assertEqual(base[name]["directive"], plus[name]["directive"])

    def test_enforcement_derivation(self):
        tools = _by_name(resolve(self.policy, "baseline_plus_index"))
        self.assertEqual(
            tools["network"]["enforcement"], "enforced"
        )  # D5 hard-enforceable
        self.assertEqual(tools["network"]["status"], "denied")
        self.assertEqual(tools["grep"]["enforcement"], "n/a")  # allow
        self.assertEqual(tools["index_query"]["enforcement"], "best-effort")  # add
        self.assertEqual(tools["index_query"]["status"], "added")

    def test_payload_is_surfaced_but_opaque(self):
        idx = _by_name(resolve(self.policy, "baseline_plus_index"))["index_query"]
        self.assertEqual(idx["payload_kind"], "bm25")  # surfaced
        self.assertIsNone(idx["index_ref_sha256"])  # null until prepare (E2)
        self.assertEqual(idx["result_shape"], "pointers_plus_snippet")

    def test_hashes_present_and_differ(self):
        base = resolve(self.policy, "baseline")
        plus = resolve(self.policy, "baseline_plus_index")
        for eff in (base, plus):
            self.assertRegex(eff["declared_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(eff["resolved_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(base["resolved_sha256"], plus["resolved_sha256"])


class TestRequiredCapabilities(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(FIXTURE)

    def test_required_present_ok(self):
        resolve(
            self.policy, "baseline", required_capabilities=["grep", "read", "submit"]
        )

    def test_missing_required_rejected_before_run(self):
        with self.assertRaises(ResolutionError):
            resolve(self.policy, "baseline", required_capabilities=["index_query"])

    def test_added_tool_satisfies_required(self):
        resolve(
            self.policy, "baseline_plus_index", required_capabilities=["index_query"]
        )


class TestEdgeCases(unittest.TestCase):
    def test_no_condition_is_untouched_default(self):
        eff = resolve(
            {"version": 1, "conditions": {}} if False else load_policy(FIXTURE), None
        )
        self.assertIsNone(eff["condition"])
        self.assertTrue(eff["default_environment"])
        self.assertEqual(eff["tools"], [])
        self.assertIsNone(eff["resolved_sha256"])

    def test_unknown_condition_rejected(self):
        with self.assertRaises(ResolutionError):
            resolve(load_policy(FIXTURE), "ghost")

    def test_add_then_remove_is_contradiction(self):
        policy = {
            "version": 1,
            "conditions": {
                "a": {
                    "tools": {"idx": {"directive": "add", "payload": {"kind": "bm25"}}}
                },
                "b": {"extends": "a", "tools": {"idx": {"directive": "remove"}}},
            },
        }
        with self.assertRaises(ResolutionError):
            resolve(policy, "b")


class TestSummary(unittest.TestCase):
    def test_summary_renders_and_flags_soft(self):
        eff = resolve(load_policy(FIXTURE), "baseline_plus_index")
        text = render_summary(eff)
        self.assertIn("baseline_plus_index", text)
        self.assertIn("index_query", text)
        # best-effort note should mention the softly-restricted capability
        self.assertIn("best-effort", text)

    def test_summary_default(self):
        text = render_summary(resolve(load_policy(FIXTURE), None))
        self.assertIn("default environment", text)


if __name__ == "__main__":
    unittest.main()
