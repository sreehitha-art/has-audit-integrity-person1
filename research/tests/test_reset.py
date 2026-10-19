import unittest
from research.reset import reset_hms_research, reset_anchor_research
from research.tampering import UnsafeTargetError

ENV = {"RESEARCH_MODE": "1"}

class ResetTests(unittest.TestCase):
    def test_never_touches_dev_databases(self):
        calls = []
        ex = lambda db, sql: calls.append((db, sql))
        for db in ("hms_dev", "anchor_dev", "hms_template", "postgres"):
            with self.assertRaises(UnsafeTargetError): reset_hms_research(ex, db, ENV)
            with self.assertRaises(UnsafeTargetError): reset_anchor_research(ex, db, ENV)
        self.assertEqual(calls, [])                      # nothing executed

    def test_requires_research_mode(self):
        calls = []
        with self.assertRaises(UnsafeTargetError): reset_hms_research(lambda *a: calls.append(a), "hms_research", {})
        with self.assertRaises(UnsafeTargetError): reset_anchor_research(lambda *a: calls.append(a), env={})
        self.assertEqual(calls, [])

    def test_allowed_targets_issue_expected_sql(self):
        calls = []; ex = lambda db, sql: calls.append((db, sql))
        reset_hms_research(ex, env=ENV); reset_anchor_research(ex, env=ENV)
        self.assertIn(("postgres", 'CREATE DATABASE "hms_research" TEMPLATE "hms_template"'), calls)
        self.assertIn(("anchor_research", "TRUNCATE TABLE retained_checkpoints"), calls)
        self.assertTrue(all("hms_dev" not in s and "anchor_dev" not in s for _, s in calls))
if __name__ == "__main__":
    unittest.main()
