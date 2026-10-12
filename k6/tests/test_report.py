"""Report compatibility when the HTTP attempt counter is renamed."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from report import generate_report


class ReportMetricTests(unittest.TestCase):
    def render(self, metrics):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "run-metadata.json").write_text(json.dumps({
                "run_id": "test-run", "run_number": 1, "test_kind": "smoke", "phase": "smoke",
                "profile": "musical-low", "performance_time_id": 1, "base_url": "http://fixture.invalid"}))
            summary = directory / "k6-summary.json"
            summary.write_text(json.dumps({"metrics": metrics}))
            original = summary.read_bytes()
            generate_report(directory)
            text = (directory / "report.md").read_text()
            self.assertEqual(summary.read_bytes(), original)
            self.assertNotIn("http_offered", text)
            self.assertNotIn("fixture.invalid", text)
            self.assertIn("| API | Attempted |", text)
            return text

    def test_old_summary_retains_counts_and_api_breakdown_under_new_label(self):
        text = self.render({"http_offered": {"values": {"count": 12, "rate": 6}},
                            "http_offered{api:seat-map}": {"values": {"count": 2, "rate": 1}}})
        self.assertIn("| http_attempted | 12 | 6 |", text)
        self.assertIn("| seat-map | 2 |", text)

    def test_current_zero_count_takes_precedence_over_legacy_count(self):
        text = self.render({"http_attempted": {"values": {"count": 0, "rate": 0}},
                            "http_offered": {"values": {"count": 12, "rate": 6}},
                            "http_attempted{api:seat-map}": {"values": {"count": 0, "rate": 0}},
                            "http_offered{api:seat-map}": {"values": {"count": 2, "rate": 1}}})
        self.assertIn("| http_attempted | 0 | 0 |", text)
        self.assertIn("| seat-map | 0 |", text)


class PilotReportFailureMeaningTests(unittest.TestCase):
    def render(self, failed, expected, system_failures, business_failures, reasons):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            documents = {
                'run-metadata.json': {
                    'test_kind': 'pilot', 'phase': 'pilot', 'run_id': 'report-test', 'run_number': 1,
                    'profile': 'musical-low', 'seat_count': 1000, 'performance_time_id': 1,
                    'execution': {'executor': 'ramping-arrival-rate', 'preAllocatedVUs': 20, 'maxVUs': 20},
                    'fixture': {'initial_state': 'all-AVAILABLE', 'prepared_started_at': None, 'prepared_ended_at': None},
                    'auth': {'token_count': 10}, 'base_url': 'http://fixture.invalid'},
                'k6-summary.json': {'metrics': {
                    'http_req_failed': {'values': {'rate': failed / (failed + expected), 'passes': failed, 'fails': expected}},
                    'system_failures': {'values': {'count': system_failures}},
                    'business_failures': {'values': {'count': business_failures}}}},
                'pilot-observations.json': {'system_failure_reasons': reasons, 'http_5xx_requests': 0},
            }
            for name, data in documents.items():
                (directory / name).write_text(json.dumps(data))
            originals = {name: (directory / name).read_bytes() for name in documents}
            generate_report(directory)
            text = (directory / 'report.md').read_text()
            self.assertEqual({name: (directory / name).read_bytes() for name in documents}, originals)
            self.assertNotIn('fixture.invalid', text)
            return text

    def test_seat_conflicts_have_http_failures_without_system_failures(self):
        text = self.render(46, 1160, 0, 46, {})
        system = text.split('## System Failures', 1)[1].split('## HTTP Expected-Response Result', 1)[0]
        self.assertIn('| system_failures (Journey) | 0 |', system)
        self.assertNotIn('http_req_failed', system)
        self.assertIn('| business_failures | 46 |', text)
        self.assertIn('| failed_requests (k6 HTTP rule) | 46 |', text)
        self.assertIn('| expected_requests (HTTP 200; not Booking Completed) | 1160 |', text)
        self.assertIn('| http_req_failed rate (%) | 3.814 |', text)
        self.assertIn('`http.expectedStatuses(200)`', text)
        self.assertIn('HTTP requests, not iterations', text)
        self.assertIn('HTTP 409 is a Business Failure', text)

    def test_invalid_http_200_can_be_system_failure_with_zero_http_failure_rate(self):
        text = self.render(0, 6, 1, 0, {'invalid-json': 1})
        self.assertIn('| system_failures (Journey) | 1 |', text)
        self.assertIn('| invalid-json | 1 |', text)
        self.assertIn('| failed_requests (k6 HTTP rule) | 0 |', text)
        self.assertRegex(text, r'\| http_req_failed rate \(%\) \| 0(?:\.0+)? \|')
        self.assertIn('HTTP 200 response can still fail DTO/Journey validation', text)


if __name__ == "__main__":
    unittest.main()
