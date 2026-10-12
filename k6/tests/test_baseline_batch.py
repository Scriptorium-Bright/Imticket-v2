"""Frozen first-batch comparison, reporting fields and independent integrity checks."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

K6_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(K6_ROOT / 'scripts'))
from baseline_batch import ARTIFACTS, inspect_run, reviewed_plan
from pilot_config import load_config
from pilot_observations import analyze
from seat_inventory import audit_snapshot


class BaselineBatchTests(unittest.TestCase):
    def test_nominal_end_is_not_confirmation_when_starts_are_late_or_slots_missing(self):
        _, config = load_config(K6_ROOT / 'config' / 'baseline/musical-low.single-attempt.json', kind='baseline')
        for late, expected_status in [(True, 'not-confirmed-late-dispatch'), (False, 'not-confirmed-dispatch-slot-gap')]:
            config['measurement'].update(startRate=1, stages=[{'duration': '4s', 'target': 1}])
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                points = [
                    {'type': 'Point', 'metric': 'scenario_start_epoch_ms', 'data': {'time': '2026-10-11T01:00:00Z', 'value': 1791680400000}},
                    {'type': 'Point', 'metric': 'iteration_started', 'data': {'time': '2026-10-11T01:00:04.02Z' if late else '2026-10-11T01:00:01Z', 'value': 1}},
                    {'type': 'Point', 'metric': 'workload_end_epoch_ms', 'data': {'time': '2026-10-11T01:00:04.1Z', 'value': 1791680404100}}]
                (directory / 'k6-summary.json').write_text('{}')
                (directory / 'k6-points.json').write_text('\n'.join(json.dumps(p) for p in points))
                metadata = {'phase': 'baseline', 'workloadModel': 'single-attempt', 'ended_at': '2026-10-11T01:00:05Z'}
                observation = analyze(directory, metadata, config)
                self.assertEqual(metadata['arrival_end_status'], expected_status)
                self.assertIsNone(metadata['arrival_ended_at'])
                self.assertEqual(observation['unobserved_configured_slots'], 3)
                self.assertEqual(observation['count_totals']['dropped_iterations'], 0)
                self.assertEqual(observation['validation']['checks']['arrival_boundary']['status'], 'failed')

    def test_frozen_pair_conditions_and_full_profile_peaks(self):
        plan = reviewed_plan(K6_ROOT / 'config' / 'baseline-first-batch.json')
        self.assertEqual(plan['measured_runs'], 4)
        self.assertEqual(plan['fixtures'], 8)
        for run in plan['runs']:
            self.assertEqual(run['scrape_opportunities_per_stage'], [12, 12, 12])
            self.assertEqual(run['configured_expected_starts'], 2400 if run['profile'] == 'musical-low' else 18000)
            self.assertEqual(run['effective_config']['auth']['tokenPoolSize'], 10)
        pair = plan['runs'][:2]
        self.assertNotEqual(pair[0]['effective_config']['measurement']['preAllocatedVUs'], pair[1]['effective_config']['measurement']['preAllocatedVUs'])

    def test_baseline_server_failures_are_observations_not_integrity_failures(self):
        _, config = load_config(K6_ROOT / 'config' / 'baseline/musical-low.single-attempt.json', kind='baseline')
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'k6-summary.json').write_text(json.dumps({'metrics': {'system_failures': {'values': {'count': 1}}}}))
            (directory / 'k6-points.json').write_text(json.dumps({'type': 'Point', 'metric': 'system_failures',
                'data': {'time': '2026-10-11T01:00:00Z', 'value': 1, 'tags': {'scenario': 'baseline', 'reason': 'timeout'}}}))
            observation = analyze(directory, {'phase': 'baseline', 'workloadModel': 'single-attempt', 'ended_at': '2026-10-11T01:00:01Z'}, config)
            check = observation['validation']['checks']['system_failure_observation']
            self.assertEqual(check['status'], 'observed')
            self.assertEqual(check['count'], 1)
            self.assertEqual(check['details'], {'timeout': 1})
            self.assertNotIn('no_system_failures', observation['validation']['checks'])
            self.assertEqual(observation['successful_booking_latency_ms']['count'], 0)

    def test_repeated_same_seat_reservation_mapping_is_distinct_from_oversell(self):
        snapshot = {'catalog_matches': 1, 'seats': [{'id': 1, 'status': 'RESERVED'}],
            'confirmed_assignments': [{'seat_id': 1, 'reservation_id': 7}, {'seat_id': 1, 'reservation_id': 7}]}
        result = audit_snapshot([{'id': 1}], snapshot)
        self.assertEqual(result['oversell_invariant'], 'passed')
        self.assertEqual(result['duplicate_oversold_seat_count'], 0)
        self.assertEqual(result['duplicate_reservation_mapping_count'], 1)
        self.assertEqual(result['status'], 'failed')

    def test_batch_stops_for_missing_artifacts_and_generator_drops(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for name in ARTIFACTS:
                path = directory / name
                path.parent.mkdir(exist_ok=True)
                path.write_text('{}')
            metadata = {'run_id': 'unit', 'profile': 'musical-low', 'workloadModel': 'single-attempt', 'run_number': 1,
                'performance_time_id': 2, 'warmup_run': {'performance_time_id': 1},
                'prometheus_collection_status': 'collected', 'k6_exit_code': 0}
            observations = {'validation': {'status': 'passed', 'checks': {'sample_policy': {'status': 'passed'}}}}
            for name, value in [('run-metadata.json', metadata), ('pilot-observations.json', observations),
                ('pilot-cleanup.json', {'errors': []}), ('seat-inventory.json', {'status': 'verified'})]:
                (directory / name).write_text(json.dumps(value))
            self.assertEqual(inspect_run(directory)['measurement_integrity'], 'passed')
            (directory / 'prometheus/hikari.json').unlink()
            self.assertEqual(inspect_run(directory)['measurement_integrity'], 'needs-review')
            observations['validation']['checks']['no_dropped_iterations'] = {'status': 'failed', 'details': 1}
            (directory / 'pilot-observations.json').write_text(json.dumps(observations))
            self.assertIn('no_dropped_iterations', inspect_run(directory)['failed_checks'])


if __name__ == '__main__':
    unittest.main()
