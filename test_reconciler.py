import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from fixtures import baseline, cases
from reconciler import html_report, main, reconcile


class EvidenceAcceptanceTests(unittest.TestCase):
    def test_orphan_result_is_unknown(self):
        bundle = baseline()
        bundle["call"]["transcript_with_tool_calls"].append(
            {"role": "tool_call_result", "tool_call_id": "unpaired", "content": "{}"})
        report = reconcile(bundle)
        self.assertEqual(report["verdict"], "UNKNOWN")
        self.assertIn("UNPAIRED_TOOL_RESULT", [x["code"] for x in report["findings"]])

    def test_duplicate_tool_id_is_not_safe_retry(self):
        bundle = baseline()
        bundle["call"]["transcript_with_tool_calls"].extend(
            copy.deepcopy(bundle["call"]["transcript_with_tool_calls"]))
        self.assertEqual(reconcile(bundle)["verdict"], "UNKNOWN")

    def test_report_omits_raw_transcript_and_arguments(self):
        bundle = baseline()
        bundle["call"]["transcript_with_tool_calls"].insert(0,
            {"role": "user", "content": "PRIVATE_TRANSCRIPT_TOKEN_123"})
        bundle["call"]["private_key"] = "PRIVATE_KEY_TOKEN_456"
        report = reconcile(bundle)
        encoded = json.dumps(report) + html_report(report)
        self.assertNotIn("PRIVATE_TRANSCRIPT", encoded)
        self.assertNotIn("PRIVATE_KEY", encoded)
        self.assertNotIn("contact_synthetic", encoded)
        self.assertNotIn("tool_1", encoded)

    def test_unsafe_report_identifier_redacted(self):
        bundle = baseline()
        bundle["contract"]["case_id"] = '<script>alert("unsafe")</script>'
        report = reconcile(bundle)
        self.assertEqual(report["case_id"], "redacted")
        self.assertNotIn("<script>", html_report(report))

    def test_cli_invalid_json_is_unknown_without_exception_data(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "bundle.json"
            source.write_text("PRIVATE_INVALID_JSON", encoding="utf-8")
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = main([str(source)])
            self.assertEqual(code, 2)
            report = json.loads(stream.getvalue())
            self.assertEqual(report["verdict"], "UNKNOWN")
            self.assertNotIn("PRIVATE_INVALID", stream.getvalue())

    def test_call_status_missing_prevents_pass(self):
        bundle = baseline()
        del bundle["call"]["call_status"]
        self.assertEqual(reconcile(bundle)["verdict"], "UNKNOWN")

    def test_incomplete_before_prevents_pass(self):
        bundle = baseline()
        bundle["before_snapshot"]["complete"] = False
        self.assertEqual(reconcile(bundle)["verdict"], "UNKNOWN")

    def test_empty_after_with_incomplete_scope_does_not_prove_absence(self):
        bundle = baseline()
        bundle["after_snapshot"].update(complete=False, appointments=[])
        report = reconcile(bundle)
        self.assertEqual(report["verdict"], "UNKNOWN")
        self.assertNotIn("CLAIM_WITHOUT_DURABLE_BOOKING", [x["code"] for x in report["findings"]])


def fixture_test(case):
    def test(self):
        report = reconcile(case["bundle"])
        self.assertEqual(report["verdict"], case["expected_verdict"], report)
        if case["expected_reason"]:
            self.assertIn(case["expected_reason"], [x["code"] for x in report["findings"]], report)
        if report["verdict"] == "PASS":
            self.assertFalse(any(x["severity"] in {"FAIL", "UNKNOWN"} for x in report["findings"]))
    return test


for scenario in cases():
    setattr(EvidenceAcceptanceTests, "test_fixture_" + scenario["name"], fixture_test(scenario))


if __name__ == "__main__":
    unittest.main()
