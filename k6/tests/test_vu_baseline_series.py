"""Series acceptance distinguishes observed failures from truncated or invalid measurements."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from vu_baseline_series import ARTIFACTS, inspect_run


class SeriesAcceptanceTests(unittest.TestCase):
    def fixture(self, directory):
        for name in ARTIFACTS:
            path = directory / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('{}', encoding='utf-8')
        metadata = {'run_id': 'unit', 'profile': 'rapper-low', 'workloadModel': 'reselection', 'run_number': 2,
                    'phase': 'baseline', 'workloadAxis': 'target-vus', 'configured_target_vus': 2,
                    'status': 'completed', 'k6_exit_code': 0, 'unfinished_iterations': 0, 'seat_count': 1,
                    'performance_time_id': 2, 'warmup_run': {'performance_time_id': 1},
                    'prometheus_collection_status': 'collected', 'fixture': {'claim': {'verified_seat_count': 1}},
                    'auth': {'token_count': 2, 'declared_distinct_members': 2, 'selection': 'vu-id-to-unique-member'}}
        observations = {'counts': dict(iteration_started=2, journey_started=2, journey_finished=2,
                                      iterations=2, dropped_iterations=0, booking_journey_guard_stopped=0,
                                      system_failures=2, seats_confirmed=0),
                        'validation': {'status': 'passed'}}
        values = {'run-metadata.json': metadata, 'vu-baseline-observations.json': observations,
                  'pilot-config.json': {'condition': 'fixed'},
                  'fixture-initial-seats.json': [{'id': 1, 'status': 'AVAILABLE'}],
                  'seat-inventory.json': {'status': 'verified', 'db': {'oversell_invariant': 'passed',
                      'duplicate_oversold_seat_count': 0, 'duplicate_reservation_mapping_count': 0,
                      'unique_sold_seat_count': 0}},
                  'pilot-cleanup.json': {'fixture_count': 2, 'errors': []},
                  'prometheus/collection-status.json': {'status': 'collected', 'errors': []},
                  'load-generator-observations.json': {'status': 'collected',
                      'k6_process_cpu_percent_max': 100, 'k6_process_rss_bytes_max': 1000}}
        for name, value in values.items():
            (directory / name).write_text(json.dumps(value), encoding='utf-8')
        return observations

    def test_all_system_failures_remain_valid_but_guard_termination_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            observations = self.fixture(directory)
            self.assertTrue(inspect_run(directory)['valid'])
            # Even an older formatter's "passed" accounting cannot accept a guard outcome.
            observations['counts']['booking_journey_guard_stopped'] = 1
            (directory / 'vu-baseline-observations.json').write_text(json.dumps(observations))
            self.assertIn('no-technical-guard-stop', inspect_run(directory)['issues'])

    def test_fixed_config_and_local_runtime_errors_block_further_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.fixture(directory)
            self.assertTrue(inspect_run(directory, {'condition': 'fixed'})['valid'])
            self.assertIn('effective-config-unchanged', inspect_run(directory, {'condition': 'changed'})['issues'])
            (directory / 'k6.log').write_text('Request Failed: request timeout\n')
            self.assertTrue(inspect_run(directory)['valid'])
            (directory / 'k6.log').write_text('panic: runtime allocation failed\n')
            result = inspect_run(directory)
            self.assertIn('no-fatal-client-runtime-error', result['issues'])
            self.assertEqual(result['fatal_client_log_line_numbers'], [1])

    def test_harness_diagnostic_cannot_be_selected_as_valid_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.fixture(directory)
            path = directory / 'run-metadata.json'
            metadata = json.loads(path.read_text())
            metadata.update(measurement_role='harness-diagnostic', performance_measurement=False)
            path.write_text(json.dumps(metadata))
            self.assertIn('performance-measurement', inspect_run(directory)['issues'])


if __name__ == '__main__':
    unittest.main()
