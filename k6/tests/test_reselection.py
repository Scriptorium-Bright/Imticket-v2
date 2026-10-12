"""Model configuration, raw accounting and inventory audit; no server calls."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

K6_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(K6_ROOT / 'scripts'))
from pilot_config import discrete_arrivals, load_config, pending_settings, stage_schedule
from pilot_observations import analyze, COUNTERS, RESELECTION_COUNTERS, REQUIRED
from seat_inventory import audit_snapshot, snapshot_sql
from report import generate_report
from pilot_runner import execute_pilot


class ModelTests(unittest.TestCase):
    def test_controlled_validation_plan_is_one_journey_and_leaves_source_config_unchanged(self):
        path = K6_ROOT / 'config' / 'reselection.pilot.example.json'
        original = path.read_bytes()
        args = SimpleNamespace(kind='journey-smoke', config=path, users=2, profile=None, plan=True,
                               run_number=1, repeat=1, workload_model='reselection', validate_seat_conflict=True)
        with patch('builtins.print') as output, patch('pilot_runner.prepare_accounts', side_effect=AssertionError('network')):
            self.assertEqual(execute_pilot(args), 0)
        plan = json.loads(output.call_args.args[0])
        self.assertEqual(plan['experiment_kind'], 'reselection-validation')
        self.assertFalse(plan['config']['warmup']['enabled'])
        self.assertEqual(plan['config']['smoke']['vus'], 1)
        self.assertEqual(plan['config']['smoke']['iterations'], 1)
        self.assertEqual(plan['config']['validationScenario']['profileOverrides']['seatSelectionRatio'], [1, 0, 0])
        self.assertEqual(path.read_bytes(), original)
        args.users = 1
        with self.assertRaisesRegex(ValueError, 'two prepared Members'):
            execute_pilot(args)

    def test_arrival_boundary_does_not_move_when_drain_crosses_nominal_end(self):
        _, config = load_config(K6_ROOT / 'config' / 'reselection.pilot.example.json', users=1, allow_pending=True)
        config['measurement'].update(startRate=1, stages=[{'duration': '1.2s', 'target': 1}], preAllocatedVUs=2, maxVUs=2)
        totals = dict.fromkeys((*COUNTERS, *RESELECTION_COUNTERS, 'seats_confirmed'), 0)
        totals.update(iteration_started=1, journey_started=1, journey_finished=1, iterations=1, http_attempted=6,
                      http_completed=6, http_reqs=6, booking_completed=1, reservation_success=1, payment_applied=1,
                      booking_journey_started=1, booking_journey_completed=1, seat_selection_attempts=1,
                      pre_reserve_attempts=1, seats_confirmed=2)
        summary = {'metrics': {n: {'values': {'count': v}} for n, v in totals.items()}}
        points = []
        def emit(n, value, offset, **tags):
            points.append({'type': 'Point', 'metric': n, 'data': {'value': value,
                           'time': datetime.fromtimestamp(1000 + offset, timezone.utc).isoformat(),
                           'tags': {'scenario': 'pilot', **tags}}})
        for n, v in totals.items(): emit(n, v, 1.01 if n in {'iteration_started', 'journey_started', 'booking_journey_started'} else 1.49)
        emit('scenario_start_epoch_ms', 1000000, 1.01); emit('workload_end_epoch_ms', 1001500, 1.5)
        for n in ('vus', 'vus_max', 'journey_duration', 'iteration_duration'):
            emit(n, 2 if n.startswith('vus') else 480, 1.49)
            summary['metrics'][n] = {'values': {'value': 2, 'count': 1}}
        emit('successful_booking_duration', 480, 1.49)
        emit('retries_per_journey', 0, 1.49); emit('retries_per_successful_booking', 0, 1.49)
        for api in ('seat-map', 'selection-summary', 'pre-reserve', 'payment-prepare', 'payment-verify', 'reservation-detail'):
            emit('http_req_duration', 10, 1.49, api=api)
            summary['metrics'][f'http_attempted{{api:{api}}}'] = {'values': {'count': 1}}
        summary['metrics']['http_req_duration'] = {'values': {'count': 6}}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'k6-summary.json').write_text(json.dumps(summary))
            (directory / 'k6-points.json').write_text('\n'.join(json.dumps(p) for p in points))
            metadata = {'phase': 'pilot', 'workloadModel': 'reselection', 'k6_exit_code': 0,
                        'ended_at': datetime.fromtimestamp(1001.5, timezone.utc).isoformat()}
            observation = analyze(directory, metadata, config)
            self.assertEqual(observation['validation']['checks']['arrival_boundary']['status'], 'passed')
            self.assertLess(metadata['arrival_ended_at'], metadata['arrival_planned_ended_at'])
            self.assertLess(metadata['arrival_planned_ended_at'], metadata['drain_ended_at'])
            self.assertEqual(metadata['arrival_end_status'], 'discrete-arrivals-exhausted-before-nominal-end')
            self.assertIn('last observed start/drop', metadata['arrival_end_basis'])

    def test_discrete_schedule_slots_are_separate_from_nominal_duration_and_real_starts(self):
        settings = {'timeUnit': '1s', 'startRate': 1, 'stages': [{'duration': '1.2s', 'target': 1}]}
        self.assertEqual(discrete_arrivals(stage_schedule(settings)), {'configured_slots': 1, 'last_slot_offset_seconds': 1})
        settings.update(startRate=2, stages=[{'duration': '3s', 'target': 0}])
        self.assertEqual(discrete_arrivals(stage_schedule(settings)), {'configured_slots': 3, 'last_slot_offset_seconds': 3})
        settings.update(startRate=0, stages=[{'duration': '3s', 'target': 2}])
        self.assertEqual(discrete_arrivals(stage_schedule(settings)), {'configured_slots': 3, 'last_slot_offset_seconds': 3})

    def test_pending_reselection_vus_are_not_inherited_and_smoke_can_be_reviewed_separately(self):
        path = K6_ROOT / 'config' / 'reselection.pilot.example.json'
        _, config = load_config(path, users=10, allow_pending=True)
        self.assertIsNone(config['measurement']['preAllocatedVUs'])
        self.assertIn('measurement.maxVUs', pending_settings(config))
        with self.assertRaisesRegex(ValueError, r'measurement.preAllocatedVUs'):
            load_config(path, users=10)
        _, smoke = load_config(path, users=10, kind='journey-smoke')
        self.assertEqual(smoke['smoke']['vus'], 1)

    def test_baseline_has_no_assumed_duration_or_attempt_scale_exhaustion(self):
        for model in ('single-attempt', 'reselection'):
            path = K6_ROOT / 'config' / f'baseline.{model}.example.json'
            _, config = load_config(path, users=10, allow_pending=True, kind='baseline')
            self.assertIsNone(config['measurement']['stages'])
            self.assertIsNone(config['measurement']['startRate'])
            self.assertEqual(config['workloadModel'], model)
            with self.assertRaises(ValueError):
                load_config(path, users=10, kind='baseline')

    def test_read_only_query_is_exactly_fixture_scoped(self):
        sql = snapshot_sql({'fixture_id': 'random-fixture', 'venue_hall_id': 1, 'performance_id': 2, 'performance_time_id': 3})
        self.assertIn('WITH CONSISTENT SNAPSHOT, READ ONLY', sql)
        self.assertIn("r.reservation_status='CONFIRMED'", sql)
        self.assertIn('s.performance_time_id=3', sql)
        for word in ('UPDATE ', 'DELETE ', 'INSERT ', 'FOR UPDATE', 'ALTER ', 'CREATE '):
            self.assertNotIn(word, sql)
        with self.assertRaises(ValueError):
            snapshot_sql({'fixture_id': "x'; DROP TABLE seat;--"})

    def test_unique_count_within_capacity_is_insufficient_to_prove_no_oversell(self):
        initial = [{'id': 1}, {'id': 2}, {'id': 3}]
        snapshot = {'catalog_matches': 1, 'seats': [{'id': 1, 'status': 'RESERVED'}, {'id': 2, 'status': 'AVAILABLE'}, {'id': 3, 'status': 'LOCKED'}],
                    'confirmed_assignments': [{'seat_id': 1, 'reservation_id': 11}, {'seat_id': 1, 'reservation_id': 12}]}
        audit = audit_snapshot(initial, snapshot)
        self.assertEqual(audit['unique_sold_seat_count'], 1)
        self.assertEqual(audit['confirmed_seat_assignments'], 2)
        self.assertEqual(audit['oversell_invariant'], 'failed')
        self.assertEqual(audit['duplicate_oversold_seat_count'], 1)
        snapshot['confirmed_assignments'].pop()
        self.assertEqual(audit_snapshot(initial, snapshot)['status'], 'verified')

    def test_empty_sales_missing_fixture_and_non_reserved_confirmed_seats(self):
        initial = [{'id': 1}, {'id': 2}]
        snapshot = {'catalog_matches': 1, 'seats': [{'id': 1, 'status': 'AVAILABLE'}, {'id': 2, 'status': 'AVAILABLE'}], 'confirmed_assignments': []}
        audit = audit_snapshot(initial, snapshot)
        self.assertEqual(audit['oversell_invariant'], 'passed')
        self.assertEqual(audit['seat_sell_through_rate'], 0)
        snapshot['confirmed_assignments'] = [{'seat_id': 1, 'reservation_id': 11}]
        self.assertEqual(audit_snapshot(initial, snapshot)['status'], 'failed')
        snapshot['seats'].pop()
        self.assertEqual(audit_snapshot(initial, snapshot)['inventory_identity_invariant'], 'failed')

    def test_raw_reselection_endings_latency_and_report_are_separate(self):
        _, config = load_config(K6_ROOT / 'config' / 'reselection.pilot.example.json', users=1, kind='journey-smoke')
        config['smoke']['vus'] = 2
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            counts = dict.fromkeys((*COUNTERS, *RESELECTION_COUNTERS, 'seats_confirmed'), 0)
            counts.update(iteration_started=2, journey_started=2, journey_finished=2, iterations=2,
                          booking_journey_started=2, booking_journey_completed=1, booking_journey_sold_out=1,
                          booking_completed=1, reservation_success=1, payment_applied=1, seats_confirmed=3,
                          seat_selection_attempts=3, pre_reserve_attempts=2, seat_conflicts=1, reselections=1,
                          http_attempted=10, http_completed=10, http_reqs=10)
            summary = {'metrics': {n: {'values': {'count': count}} for n, count in counts.items()}}
            for n in REQUIRED:
                summary['metrics'].setdefault(n, {'values': {'count': 2}})
            points = []
            def point(name, value, offset=1, tags=None):
                points.append({'type': 'Point', 'metric': name, 'data': {'value': value,
                              'time': datetime.fromtimestamp(1000 + offset, timezone.utc).isoformat(),
                              'tags': {'scenario': 'journey-smoke', **(tags or {})}}})
            for n, count in counts.items(): point(n, count)
            point('scenario_start_epoch_ms', 1000000)
            point('workload_end_epoch_ms', 1002000, 2)
            for n in ('journey_duration', 'iteration_duration'):
                point(n, 100); point(n, 30)
            for api, count in [('seat-map', 3), ('selection-summary', 2), ('pre-reserve', 2), ('payment-prepare', 1), ('payment-verify', 1), ('reservation-detail', 1)]:
                summary['metrics'][f'http_attempted{{api:{api}}}'] = {'values': {'count': count}}
                for _ in range(count): point('http_req_duration', 10, tags={'api': api})
            point('successful_booking_duration', 120)
            point('sold_out_journey_duration', 30)
            point('retries_per_journey', 1); point('retries_per_journey', 0)
            point('retries_per_successful_booking', 1)
            (directory / 'k6-points.json').write_text('\n'.join(json.dumps(p) for p in points))
            (directory / 'k6-summary.json').write_text(json.dumps(summary))
            metadata = {'test_kind': 'journey-smoke', 'phase': 'journey-smoke', 'workloadModel': 'reselection',
                        'ended_at': datetime.fromtimestamp(1002, timezone.utc).isoformat(), 'run_id': 'unit-reselection',
                        'run_number': 1, 'profile': 'musical-low', 'seat_count': 1000, 'performance_time_id': 1,
                        'execution': config['smoke'], 'auth': {'token_count': 1},
                        'fixture': {'initial_state': 'all-AVAILABLE', 'prepared_started_at': None, 'prepared_ended_at': None}}
            result = analyze(directory, metadata, config)
            self.assertEqual(result['validation']['status'], 'passed', result['validation'])
            self.assertEqual(result['outcome_latency_ms']['successful_booking_duration']['avg'], 120)
            self.assertEqual(result['outcome_latency_ms']['sold_out_journey_duration']['avg'], 30)
            self.assertEqual(result['outcome_latency_ms']['failed_journey_duration']['count'], 0)
            self.assertIsNone(result['configured_expected_starts'])
            self.assertEqual(result['http_throughput_per_second'], 5)
            (directory / 'run-metadata.json').write_text(json.dumps(metadata))
            generate_report(directory)
            report = (directory / 'report.md').read_text()
            self.assertIn('Business Conflict and Reselection', report)
            self.assertIn('| average_seats_per_completed_booking | 3.000 |', report)
            self.assertIn('| Oversell invariant | not-verified |', report)
            self.assertIn('Main user experience metric: successful_booking_duration', report)
