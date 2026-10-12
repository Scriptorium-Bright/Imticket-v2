import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from common import write_json, read_json
from measurement_environment import docker_state, assert_environment
from pilot_prometheus import collection_error
from preparation import api_data, PreparationError
from reproducibility_report import anomaly_evidence
from reproducible_baseline import execute, PREFLIGHT, PROFILES, MODELS
from test_accounts import prepare_accounts
from vu_baseline_observations import analyze, NATIVE_TIMINGS


class ReproducibilityTests(unittest.TestCase):
    def test_business_and_rps_variance_cannot_fail_preflight_but_dispatch_can(self):
        results = [{'profile': 'musical-high', 'workloadModel': 'single-attempt', 'valid': True, 'directory': str(i)} for i in range(3)]
        policy = {'fastSpreadSeconds': 1, 'outlierSpreadSeconds': 6}
        values = [{'Journey start spread (s)': x, 'First request spread (s)': x,
                   'HTTP RPS': rps, 'booking_completed': bookings} for x, rps, bookings in [(0.1, 128, 1002), (0.2, 459, 4), (0.3, 473, 6)]]
        with patch('reproducibility_report.run_values', side_effect=values):
            self.assertEqual(anomaly_evidence(results, policy), [])
        values[2]['Journey start spread (s)'] = 6.77
        with patch('reproducibility_report.run_values', side_effect=values):
            evidence = anomaly_evidence(results, policy)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['field'], 'Journey start spread (s)')

    def test_one_invalid_preflight_stops_without_retry_or_final_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root / 'series-plan.json', {'preflight_combinations': list(PREFLIGHT)})
            write_json(root / 'series-results.json', {'status': 'prepared', 'results': []})
            with patch('reproducible_baseline.save'), patch('reproducible_baseline.execute_one', return_value=False) as run:
                self.assertEqual(execute(root), 2)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[3], 'Preflight')

    def test_pass_executes_exactly_15_preflight_and_60_independent_final_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root / 'series-plan.json', {'preflight_combinations': list(PREFLIGHT), 'profile_order': list(PROFILES),
                       'models': list(MODELS), 'anomaly_policy': {}})
            write_json(root / 'series-results.json', {'status': 'prepared', 'results': []})
            with patch('reproducible_baseline.save'), patch('reproducible_baseline.anomaly_evidence', return_value=[]), patch('reproducible_baseline.execute_one', return_value=True) as run, patch('builtins.print'):
                self.assertEqual(execute(root), 0)
            categories = [call.args[3] for call in run.call_args_list]
            self.assertEqual(categories, ['Preflight'] * 15 + ['New Final Baseline'] * 60)
            final = [call.args[4:] for call in run.call_args_list[15:]]
            self.assertEqual(len(set(final)), 60)

    def test_docker_privileged_helper_does_not_mean_desktop_is_on(self):
        with patch('measurement_environment.command', return_value='319 /Library/PrivilegedHelperTools/com.docker.vmnetd'), patch('measurement_environment.shutil.which', return_value=None):
            self.assertFalse(docker_state()['local_container_runtime_running'])
        with patch('measurement_environment.command', return_value='300 /Applications/Docker.app/Contents/MacOS/com.docker.backend'), patch('measurement_environment.shutil.which', return_value=None):
            self.assertTrue(docker_state()['local_container_runtime_running'])

    def test_structured_jwt_failure_contains_socket_evidence_and_never_retries(self):
        with patch('preparation.build_opener') as opener:
            opener.return_value.open.side_effect = URLError(ConnectionRefusedError(61, 'Connection refused'))
            with self.assertRaises(PreparationError) as context:
                api_data('http://localhost:8080', 'POST', '/api/auth/login', {'password': 'must-not-log'}, request_context='login_index=1/2')
        detail = context.exception.details
        self.assertEqual(detail['exception_class'], 'URLError')
        self.assertEqual(detail['reason_class'], 'ConnectionRefusedError')
        self.assertEqual(detail['errno'], 61)
        self.assertIn('exception_repr', detail)
        self.assertIn('timestamp', detail)
        self.assertNotIn('must-not-log', json.dumps(detail))
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / 'auth.json'
            with patch.dict(os.environ, {'K6_AUTH_OBSERVATION_FILE': str(artifact)}), patch('test_accounts.api_data', side_effect=context.exception) as request, patch('builtins.print'):
                with self.assertRaises(PreparationError):
                    prepare_accounts('http://localhost:8080', 1, workers=1, pool_path=Path(tmp) / 'accounts.json')
            result = read_json(artifact)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(result['failure_count'], 1)
            self.assertEqual(result['completed_authentication_count'], 0)
            self.assertEqual(result['failures'][0]['authentication_index'], 1)
            self.assertEqual(result['status'], 'failed')

    def test_native_timing_and_first_dispatch_are_accounted_separately_from_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(root / 'k6-summary.json', {'metrics': {'dropped_iterations': {'values': {'count': 0}}}})
            write_json(root / 'seat-inventory.json', {'status': 'verified', 'db': {'initial_seat_count': 1, 'unique_sold_seat_count': 0, 'oversell_invariant': 'passed'}})
            points = []
            def point(name, value, second, tags=None):
                points.append({'type': 'Point', 'metric': name, 'data': {'time': f'2026-10-12T00:00:{second:02d}Z', 'value': value, 'tags': {'scenario': 'baseline', **(tags or {})}}})
            for second in (1, 2):
                for name in ('iteration_started', 'journey_started', 'first_request_started'):
                    point(name, 1, second)
                for name in ('journey_finished', 'iterations', 'system_failures'):
                    point(name, 1, second + 10)
                point('journey_duration', 10000, second + 10, {'outcome': 'system-failure'})
                point('http_reqs', 1, second + 10, {'status': '0', 'error_code': '1050' if second == 1 else '1212', 'error': 'request timeout' if second == 1 else 'read: connection reset by peer'})
                for name in NATIVE_TIMINGS:
                    point(name, 10000 if name == 'http_req_duration' else 1, second + 10)
            point('vus_max', 2, 1)
            (root / 'k6-points.json').write_text(''.join(json.dumps(p) + '\n' for p in points))
            result = analyze(root, {'phase': 'baseline', 'configured_target_vus': 2, 'reproducibility_required': True}, {'workloadModel': 'single-attempt', 'measurement': {'vus': 2}})
            self.assertEqual(result['validation']['status'], 'passed')
            self.assertEqual(result['first_request_dispatch']['spread_seconds'], 1)
            self.assertEqual(result['transport']['counts']['status_0'], 2)
            self.assertEqual(result['transport']['counts']['timeout'], 1)
            self.assertEqual(result['transport']['counts']['connection_reset'], 1)
            self.assertEqual(result['native_http_timings_ms']['http_req_connecting']['count'], 2)
            self.assertEqual(result['latency_ms']['failed_journey_duration']['count'], 2)

    def test_prometheus_timeout_and_parse_failure_are_distinct(self):
        self.assertEqual(collection_error(URLError(TimeoutError('timed out')), 'query-range:x')['classification'], 'query-timeout')
        self.assertEqual(collection_error(json.JSONDecodeError('bad', '', 0), 'parse-query-response')['classification'], 'parse-failure')


if __name__ == '__main__':
    unittest.main()
