"""Offline tests for the tool-policy schema loader (E1-S1).

Covers a valid parse and each D6 rejection path. No mini/Docker/API.
"""

import unittest
from pathlib import Path

from toolpolicy.schema import PolicyError, load_policy

FIXTURE = Path(__file__).resolve().parent.parent / "conditions" / "config.yaml"


def _policy(**conditions):
    return {"version": 1, "conditions": conditions}


class TestSchemaValid(unittest.TestCase):
    def test_fixture_file_parses(self):
        data = load_policy(FIXTURE)
        self.assertIn("baseline", data["conditions"])
        self.assertIn("baseline_plus_index", data["conditions"])

    def test_dict_source_accepted(self):
        data = load_policy(_policy(baseline={"tools": {"grep": {"directive": "allow"}}}))
        self.assertEqual(data["conditions"]["baseline"]["tools"]["grep"]["directive"], "allow")


class TestSchemaRejections(unittest.TestCase):
    def test_wrong_version(self):
        with self.assertRaises(PolicyError):
            load_policy({"version": 2, "conditions": {"a": {}}})

    def test_empty_conditions(self):
        with self.assertRaises(PolicyError):
            load_policy({"version": 1, "conditions": {}})

    def test_unknown_directive(self):
        with self.assertRaises(PolicyError):
            load_policy(_policy(a={"tools": {"grep": {"directive": "banish"}}}))

    def test_missing_directive(self):
        with self.assertRaises(PolicyError):
            load_policy(_policy(a={"tools": {"grep": {}}}))

    def test_add_without_payload_kind(self):
        with self.assertRaises(PolicyError):
            load_policy(_policy(a={"tools": {"idx": {"directive": "add", "payload": {}}}}))

    def test_add_without_payload_at_all(self):
        with self.assertRaises(PolicyError):
            load_policy(_policy(a={"tools": {"idx": {"directive": "add"}}}))

    def test_extends_unknown_parent(self):
        with self.assertRaises(PolicyError):
            load_policy(_policy(a={"extends": "ghost", "tools": {}}))

    def test_extends_self(self):
        with self.assertRaises(PolicyError):
            load_policy(_policy(a={"extends": "a", "tools": {}}))

    def test_tools_not_mapping(self):
        with self.assertRaises(PolicyError):
            load_policy(_policy(a={"tools": ["grep"]}))

    def test_limits_not_mapping(self):
        with self.assertRaises(PolicyError):
            load_policy(_policy(a={"tools": {"grep": {"directive": "allow", "limits": 5}}}))


if __name__ == "__main__":
    unittest.main()
