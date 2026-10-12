"""Bracketing policy tests without generating HTTP load, fixtures, SSH, or Prometheus queries."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

K6_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(K6_ROOT / 'scripts'))
import load_delivery_bracket as bracket
from baseline_batch import ARTIFACTS
from common import file_sha256, read_json, write_json
from pilot_config import load_config


def fake_run(directory, config, dropped=0, active=2, system=0, guard=0):
    directory.mkdir(parents=True)
    for name in (*ARTIFACTS, 'server-environment.json'):
        target = directory / name
        target.parent.mkdir(exist_ok=True)
        write_json(target, {})
    checks = {name: {'status': 'passed'} for name in bracket.DELIVERY_CHECKS | bracket.INTEGRITY_CHECKS}
    if config['workloadModel'] == 'reselection':
        checks.update({name: {'status': 'passed'} for name in ('reselection_accounting', 'retry_samples')})
        checks.update({'latency_accounting_' + name: {'status': 'passed'} for name in bracket.OUTCOME_TRENDS})
    checks['raw_summary_counts'] = {'status': 'passed'}
    checks['sample_policy'] = {'status': 'passed'}
    checks['no_safety_guard_termination'] = {'status': 'failed' if guard else 'passed'}
    checks['system_failure_observation'] = {'status': 'observed', 'count': system}
    if dropped:
        checks['no_dropped_iterations'] = {'status': 'failed'}
        checks['arrival_stage_2'] = {'status': 'failed'}
    counts = dict.fromkeys(('iteration_started', 'journey_started', 'journey_finished', 'iterations',
                           'http_attempted', 'http_completed', 'http_reqs', 'booking_completed',
                           'seats_confirmed', 'business_failures', 'system_failures',
                           'dropped_iterations', 'booking_journey_guard_stopped'), 0)
    counts.update(iteration_started=10, iterations=10, journey_started=10, journey_finished=10,
                  http_attempted=10, http_completed=10, http_reqs=10,
                  booking_completed=1, seats_confirmed=1, system_failures=system,
                  dropped_iterations=dropped, booking_journey_guard_stopped=guard)
    distribution = {'count': 10, 'avg': 1000, 'p50': 800, 'p95': 2000, 'p99': 2400, 'max': 2500}
    observation = {'configured_execution': config['measurement'], 'configured_expected_starts': 120 * config['measurement']['stages'][0]['target'],
                   'configured_arrival_duration_seconds': 180, 'validation': {'status': 'failed' if guard or dropped else 'passed', 'checks': checks},
                   'count_totals': counts, 'gauge_observations': {'vus': {'max': active}},
                   'observed_iteration_start_rate_per_second': 10 / 180, 'completed_iteration_throughput_per_second': 10 / 181,
                   'http_throughput_per_second': 1, 'unobserved_configured_slots': 0,
                   'latency_ms': {name: distribution for name in ('journey_duration', 'http_req_duration', 'iteration_duration')},
                   'api_latency_ms': {}, 'successful_booking_latency_ms': distribution, 'outcome_latency_ms': {},
                   'retry_distributions': {}, 'business_failure_reasons': {}, 'system_failure_reasons': {},
                   'http_5xx_requests': 0, 'seat_selection_regions': {}, 'stage_observations': []}
    write_json(directory / 'pilot-observations.json', observation)
    write_json(directory / 'pilot-config.json', config)
    write_json(directory / 'run-metadata.json', {'phase': 'baseline', 'profile': 'musical-high', 'workloadModel': config['workloadModel'],
               'run_number': 1, 'iteration_meaning': 'test population', 'performance_time_id': 2, 'seat_count': 2,
               'warmup_run': {'performance_time_id': 1}, 'k6_exit_code': 99 if guard or dropped else 0,
               'prometheus_collection_status': 'collected'})
    write_json(directory / 'fixture-manifest.json', {'verified_seat_count': 2})
    write_json(directory / 'fixture-initial-seats.json', [{'id': i, 'status': 'AVAILABLE'} for i in (1, 2)])
    write_json(directory / 'seat-inventory.json', {'status': 'verified', 'oversell_invariant': 'passed', 'db': {}})
    write_json(directory / 'pilot-cleanup.json', {'errors': []})
    write_json(directory / 'prometheus/collection-status.json', {'status': 'collected', 'queries_executed_after_k6_exit': True})
    write_json(directory / 'server-environment.json', {'unchanged': True})


class LoadDeliveryBracketTests(unittest.TestCase):
    def setUp(self):
        _, self.config = load_config(K6_ROOT / 'config/baseline/musical-high.single-attempt.json', kind='baseline')

    def test_only_peak_changes_and_previous_config_is_not_mutated(self):
        original = copy.deepcopy(self.config)
        candidate = bracket.candidate_config(self.config, 85)
        self.assertEqual(self.config, original)
        self.assertEqual(bracket.condition(candidate), bracket.condition(original))
        self.assertEqual(candidate['measurement']['stages'], [{'duration': '60s', 'target': 85},
                         {'duration': '60s', 'target': 85}, {'duration': '60s', 'target': 0}])
        self.assertEqual(candidate['measurement']['maxVUs'], 256)
        self.config['measurement']['startRate'] = 1
        with self.assertRaises(ValueError):
            bracket.candidate_config(self.config, 85)

    def test_halfway_rounds_up_and_resolution_is_not_a_capacity_claim(self):
        self.assertEqual(bracket.midpoint(20, 150), 85)
        self.assertEqual(bracket.midpoint(20, 85), 55)
        self.assertEqual(bracket.midpoint(85, 150), 120)
        self.assertEqual(bracket.next_candidate(None, 150), 20)
        self.assertIsNone(bracket.next_candidate(65, 85))

    def test_low_profile_pass_cannot_be_high_profile_anchor(self):
        # Historical result files are ignored/private and reflect older source hashes.
        # This policy test uses the current configs and explicit synthetic references.
        declaration_path = K6_ROOT / 'config/load-delivery-bracket.json'
        declaration = read_json(declaration_path)
        references = {declaration_path: declaration}
        for model in declaration['models']:
            _, config = load_config(K6_ROOT / model['config'], kind='baseline')
            directory = K6_ROOT / model['failingReference']
            references[directory / 'pilot-config.json'] = config
            references[directory / 'run-metadata.json'] = {'source_sha256': {}, 'execution': config['measurement']}
            references[directory / 'pilot-observations.json'] = {
                'count_totals': {'dropped_iterations': 1},
                'latency_ms': {name: {'avg': 1000, 'p50': 800, 'p95': 2000}
                               for name in ('journey_duration', 'iteration_duration')},
                'gauge_observations': {'vus': {'max': 256}}, 'unobserved_configured_slots': 0}
            references[directory / 'server-environment.json'] = {'unchanged': True, 'before': {'comparison_sha256': 'fixed-server'}}
            references[K6_ROOT / model['passingContext'] / 'run-metadata.json'] = {'profile': 'musical-low'}
        def read_reference(path):
            return references[Path(path)] if Path(path) in references else read_json(path)
        with patch.object(bracket, 'read_json', side_effect=read_reference), patch.object(bracket, 'fixed_sources', return_value={}):
            preview = bracket.review_plan(declaration_path)
        for model in preview['models']:
            self.assertIsNone(model['highest_valid_delivered_peak'])
            self.assertEqual(model['passing_context_profile'], 'musical-low')
            self.assertFalse(model['passing_context_is_same_condition_anchor'])
            self.assertEqual(model['fixture_seat_count'], 1500)

    def test_server_failure_does_not_invalidate_delivered_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_run(Path(tmp) / 'run', self.config, system=2)
            result = bracket.inspect_run(Path(tmp) / 'run')
            self.assertEqual(result['classification'], bracket.SERVER_FAILURE)
            self.assertTrue(result['valid_load_delivery'])
            self.assertFalse(result['stop_automatic_execution'])

    def test_early_server_failures_with_no_payment_samples_do_not_redefine_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'run'
            fake_run(directory, self.config, system=2)
            observation = read_json(directory / 'pilot-observations.json')
            observation['validation']['checks']['sample_policy'] = {'status': 'insufficient'}
            observation['validation']['checks']['latency_samples_present'] = {'status': 'failed'}
            write_json(directory / 'pilot-observations.json', observation)
            result = bracket.inspect_run(directory)
            self.assertEqual(result['classification'], bracket.SERVER_FAILURE)
            self.assertTrue(result['warnings'])

    def test_missing_actual_latency_points_are_an_integrity_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'run'
            fake_run(directory, self.config)
            observation = read_json(directory / 'pilot-observations.json')
            observation['latency_ms']['http_req_duration']['count'] = 9
            write_json(directory / 'pilot-observations.json', observation)
            result = bracket.inspect_run(directory)
            self.assertIn('latency_sample_accounting_http_req_duration', result['integrity_failures'])

    def test_guard_is_a_technical_outcome_not_an_unfinished_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.config['workloadModel'] = 'reselection'
            fake_run(Path(tmp) / 'run', self.config, guard=1)
            result = bracket.inspect_run(Path(tmp) / 'run')
            self.assertEqual(result['classification'], bracket.PASS)
            self.assertEqual(result['legacy_validation_status'], 'failed')
            self.assertTrue(result['warnings'])

    def test_vu_ceiling_alone_warns_but_ceiling_with_drops_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'run'
            fake_run(directory, self.config, active=256)
            result = bracket.inspect_run(directory)
            self.assertEqual(result['classification'], bracket.PASS)
            self.assertTrue(result['ceiling_reached'])
            self.assertFalse(result['generator_stop'])
            observations = read_json(directory / 'pilot-observations.json')
            observations['count_totals']['dropped_iterations'] = 1
            write_json(directory / 'pilot-observations.json', observations)
            result = bracket.inspect_run(directory)
            self.assertEqual(result['classification'], bracket.FAIL)
            self.assertTrue(result['generator_stop'])
            self.assertTrue(result['usable_as_failed_rate_endpoint'])

    def test_missing_collection_is_not_a_failed_rate_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'run'
            fake_run(directory, self.config)
            (directory / 'prometheus/hikari.json').unlink()
            result = bracket.inspect_run(directory)
            self.assertEqual(result['failure_scope'], 'measurement-integrity')
            self.assertFalse(result['usable_as_failed_rate_endpoint'])
            self.assertTrue(result['stop_automatic_execution'])

    def test_reused_or_non_available_fixture_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'run'
            fake_run(directory, self.config)
            write_json(directory / 'fixture-initial-seats.json', [{'id': 1, 'status': 'LOCKED'}, {'id': 2, 'status': 'AVAILABLE'}])
            result = bracket.inspect_run(directory)
            self.assertIn('fixture_initial_state', result['integrity_failures'])

    def test_missing_required_validation_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'run'
            fake_run(directory, self.config)
            observations = read_json(directory / 'pilot-observations.json')
            del observations['validation']['checks']['raw_summary_counts']
            write_json(directory / 'pilot-observations.json', observations)
            self.assertFalse(bracket.inspect_run(directory)['valid_load_delivery'])

    def test_first_generator_stop_prevents_next_candidate_other_model_and_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'results').mkdir()
            declaration = root / 'plan.json'
            write_json(declaration, {'review': 'test'})
            entries = []
            for model in ('single-attempt', 'reselection'):
                config = copy.deepcopy(self.config)
                config['workloadModel'] = model
                source = root / (model + '.json')
                write_json(source, config)
                entries.append({'workloadModel': model, 'fixed_config': config,
                                'config_source': str(source), 'config_sha256': file_sha256(source)})
            preview = {'models': entries, 'reference_server_fingerprint': 'same', 'plan_source': str(declaration),
                       'plan_sha256': file_sha256(declaration), 'fixed_source_sha256': {},
                       'declaration': {'lowerAnchorPeak': 20, 'resolution': 20, 'roundingIncrement': 5}}
            calls = []

            def run(command, **kwargs):
                config = read_json(command[command.index('--config') + 1])
                peak = config['measurement']['stages'][0]['target']
                calls.append((config['workloadModel'], peak))
                fake_run(root / 'results' / ('measured-' + str(peak)), config,
                         dropped=10 if peak == 85 else 0, active=256 if peak == 85 else 4)
                return type('Completed', (), {'returncode': 99 if peak == 85 else 0})()

            with patch.object(bracket, 'K6_ROOT', root), patch.object(bracket, 'capture', return_value={'comparison_sha256': 'same'}), \
                    patch.object(bracket, 'fixed_sources', return_value={}), patch.object(bracket.subprocess, 'run', side_effect=run):
                self.assertEqual(bracket.execute(preview), 1)
            self.assertEqual(calls, [('single-attempt', 20), ('single-attempt', 85)])
            results = list((root / 'results').glob('*_load-delivery_bracket/bracket-results.json'))
            state = read_json(results[0])
            self.assertEqual(state['status'], 'stopped-for-review')
            self.assertEqual(state['brackets']['single-attempt'], {'highest_valid_delivered_peak': 20, 'first_invalid_delivered_peak': 85})
            self.assertIsNone(state['brackets']['reselection']['highest_valid_delivered_peak'])

    def test_changed_server_fingerprint_prevents_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'results').mkdir()
            declaration = root / 'plan.json'
            write_json(declaration, {})
            preview = {'models': [], 'reference_server_fingerprint': 'same', 'plan_source': str(declaration),
                       'plan_sha256': file_sha256(declaration), 'declaration': {}}
            with patch.object(bracket, 'K6_ROOT', root), patch.object(bracket, 'capture', return_value={'comparison_sha256': 'different'}):
                self.assertEqual(bracket.execute(preview), 1)

    def test_orchestration_error_is_saved_as_stopped_not_narrowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'results').mkdir()
            preview = {'models': [{'workloadModel': 'single-attempt'}], 'reference_server_fingerprint': 'same',
                       'plan_source': '/missing-declaration', 'plan_sha256': 'missing', 'declaration': {}}
            with patch.object(bracket, 'K6_ROOT', root), patch.object(bracket, 'capture', return_value={'comparison_sha256': 'same'}), \
                    patch.object(bracket, 'file_sha256', side_effect=OSError('unreadable')):
                self.assertEqual(bracket.execute(preview), 1)
            state = read_json(next((root / 'results').glob('*_load-delivery_bracket/bracket-results.json')))
            self.assertEqual(state['status'], 'stopped-for-review')
            self.assertEqual(state['runs'], [])
            self.assertIn('OSError', state['stop_reasons'][0])


if __name__ == '__main__':
    unittest.main()
