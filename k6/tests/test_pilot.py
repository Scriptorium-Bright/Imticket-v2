"""Pilot plan, schedule and token expiry tests; no smoke or real-server calls."""
import base64
import copy
import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

K6_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(K6_ROOT / 'scripts'))
from pilot_config import configured_starts, load_config, stage_schedule, token_window
from pilot_prometheus import GROUPS, collect_pilot, query_plan, reclassify_saved_collection
from pilot_runner import execute_pilot


def token(member, expiry):
    body = base64.urlsafe_b64encode(json.dumps({'sub': str(member), 'exp': expiry}).encode()).decode().rstrip('=')
    return {'memberId': member, 'accessToken': 'eyJhbGciOiJIUzI1NiJ9.' + body + '.stub-signature'}


class PilotConfigurationTests(unittest.TestCase):
    def settings(self):
        return load_config(K6_ROOT / 'config' / 'pilot.example.json', users=2)[1]

    def test_plan_has_no_server_preparation_and_leaves_token_size_pending(self):
        args = SimpleNamespace(config=K6_ROOT / 'config' / 'pilot.example.json', profile=None, users=None, plan=True, run_number=1)
        with patch('pilot_runner.prepare_accounts', side_effect=AssertionError('network')), patch('pilot_runner.probe_database', side_effect=AssertionError('database')), patch('builtins.print') as output:
            self.assertEqual(execute_pilot(args), 0)
        data = json.loads(output.call_args.args[0])
        self.assertEqual(data['pending_settings'], ['auth.tokenPoolSize'])
        self.assertEqual(data['configured_expected_starts'], 225)
        self.assertEqual(data['scrape_opportunities_per_stage'], [6, 6, 12, 6])
        with self.assertRaisesRegex(ValueError, r"token pool size is unset.*--users <count>"):
            load_config(args.config)

    def test_nonsecond_timeunit_and_drain_are_integrated_separately(self):
        execution = copy.deepcopy(self.settings()['measurement'])
        execution.update(startRate=0, timeUnit='2s', stages=[{'duration': '10s', 'target': 4}, {'duration': '10s', 'target': 0}])
        stages = stage_schedule(execution)
        self.assertEqual(configured_starts(stages, 0, 20), 20)
        self.assertEqual(configured_starts(stages, 20, 30), 0)
        self.assertEqual(configured_starts(stages, 5, 15), 15)

    def test_profile_selects_1500_fixture_without_increasing_pilot_rate(self):
        _, config = load_config(K6_ROOT / 'config' / 'pilot.example.json', profile='musical-high', users=2)
        self.assertEqual(config['profile'], 'musical-high')
        self.assertEqual(max(s['target'] for s in config['measurement']['stages']), 2)

    def test_token_window_checks_count_identity_distinctness_and_expiry(self):
        config = self.settings()
        pool = [token(1, time.time() + 3600), token(2, time.time() + 3600)]
        self.assertEqual(token_window(pool, config, 'pilot')['distinct_members'], 2)
        for invalid in [pool[:1], [pool[0], pool[0]], [token(1, time.time() + 10), pool[1]], [{**pool[0], 'memberId': 99}, pool[1]]]:
            with self.assertRaises(ValueError):
                token_window(invalid, config, 'pilot')

    def test_queries_use_inventory_and_exclude_unavailable_metrics(self):
        config = self.settings()
        plan = query_plan(config['prometheus'])
        names = {name for entries in plan.values() for q in entries for name in q['metric_names']}
        self.assertEqual(names, {name for group in GROUPS.values() for name in group})
        text = json.dumps(plan)
        for forbidden in ('mysql_', 'node_', 'oci_', 'hikaricp_connections_acquire_seconds_bucket', 'jvm_threads_deadlocked_threads'):
            self.assertNotIn(forbidden, text)
        self.assertIn('/api/(seats|reservations|payments)', text)
        self.assertEqual(sum(len(v) for v in plan.values()), 37)

    def test_bad_vu_stage_or_disabled_warmup_values_stop_before_execution(self):
        for mutate in [lambda x: x['measurement'].update(maxVUs=0),
                       lambda x: x['measurement'].update(stages=[]),
                       lambda x: x['measurement'].update(startRate=True),
                       lambda x: x['warmup'].update(enabled='false')]:
            config = self.settings()
            mutate(config)
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'config.json'
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_pilot_cli_rejects_reused_fixture_and_smoke_phase_flags(self):
        for option, value in [('--fixture', '/nonexistent/old-fixture.json'), ('--phase', 'warmup')]:
            result = subprocess.run([sys.executable, str(K6_ROOT / 'scripts' / 'run-experiment.py'), 'pilot', '--plan',
                                     '--config', str(K6_ROOT / 'config' / 'pilot.example.json'), option, value], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('error:', result.stderr)
            self.assertNotIn('Run directory:', result.stdout)

    def test_context_only_prometheus_data_is_collected_with_sparse_coverage_reported(self):
        config = self.settings()['prometheus']
        start, end = time.time() - 300, time.time() - 150
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
        metadata = {'measurement_started_at': iso(start), 'measurement_ended_at': iso(end)}
        exposition = '\n'.join(name + ' 0' for group in GROUPS.values() for name in group)
        def response(url, _):
            if url.endswith('/actuator/prometheus'):
                return exposition
            if url.endswith('/api/v1/targets'):
                return json.dumps({'data': {'activeTargets': [{'labels': {'job': 'imticket-v2'}, 'health': 'up',
                                   'lastScrape': iso(end + 10), 'scrapeInterval': '5s'}]}})
            return json.dumps({'status': 'success', 'data': {'resultType': 'matrix', 'result': [{'metric': {}, 'values': [[start - 10, '1']]}]}})
        with tempfile.TemporaryDirectory() as temporary, patch('pilot_prometheus.fetch', side_effect=response):
            status = collect_pilot(temporary, 'http://stub.invalid', 'http://prometheus.invalid', config, metadata)
            self.assertEqual(status['status'], 'collected')
            self.assertTrue(all(q['finite_in_window_points'] == 0 for q in status['queries']))
            self.assertTrue(all(q['status'] == 'collected' for q in status['queries'] if q['required']))
            self.assertTrue(all(q['temporal_coverage'] == 'context-only' for q in status['queries'] if q['required']))
            self.assertEqual(status['temporal_coverage']['status'], 'context-only')
            self.assertFalse(status['temporal_coverage']['collection_validity_depends_on_in_window_density'])

    def test_single_in_window_prometheus_evaluation_does_not_invalidate_short_run(self):
        config = self.settings()['prometheus']
        start, end = time.time() - 20, time.time() - 14
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
        metadata = {'measurement_started_at': iso(start), 'measurement_ended_at': iso(end)}
        exposition = '\n'.join(name + ' 0' for group in GROUPS.values() for name in group)
        def response(url, _):
            if url.endswith('/actuator/prometheus'):
                return exposition
            if url.endswith('/api/v1/targets'):
                return json.dumps({'data': {'activeTargets': [{'labels': {'job': 'imticket-v2'}, 'health': 'up',
                                   'lastScrape': iso(end + 10), 'scrapeInterval': '5s'}]}})
            return json.dumps({'status': 'success', 'data': {'resultType': 'matrix', 'result': [{'metric': {},
                               'values': [[start - 5, '1'], [start + 1, '2'], [end + 5, '3']]}]}})
        with tempfile.TemporaryDirectory() as temporary, patch('pilot_prometheus.fetch', side_effect=response):
            status = collect_pilot(temporary, 'http://stub.invalid', 'http://prometheus.invalid', config, metadata)
        required = [q for q in status['queries'] if q['required']]
        self.assertEqual(status['status'], 'collected')
        self.assertTrue(all(q['status'] == 'collected' for q in required))
        self.assertTrue(all(q['distinct_finite_in_window_evaluation_times'] == 1 for q in required))
        self.assertTrue(all(q['temporal_coverage'] == 'single-sample-in-window' for q in required))
        self.assertEqual(status['temporal_coverage']['status'], 'sparse-in-window')

    def test_empty_required_prometheus_series_remains_a_collection_gap(self):
        config = self.settings()['prometheus']
        start, end = time.time() - 20, time.time() - 14
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
        metadata = {'measurement_started_at': iso(start), 'measurement_ended_at': iso(end)}
        exposition = '\n'.join(name + ' 0' for group in GROUPS.values() for name in group)
        def response(url, _):
            if url.endswith('/actuator/prometheus'):
                return exposition
            if url.endswith('/api/v1/targets'):
                return json.dumps({'data': {'activeTargets': [{'labels': {'job': 'imticket-v2'}, 'health': 'up',
                                   'lastScrape': iso(end + 10), 'scrapeInterval': '5s'}]}})
            return json.dumps({'status': 'success', 'data': {'resultType': 'matrix', 'result': []}})
        with tempfile.TemporaryDirectory() as temporary, patch('pilot_prometheus.fetch', side_effect=response):
            status = collect_pilot(temporary, 'http://stub.invalid', 'http://prometheus.invalid', config, metadata)
        self.assertEqual(status['status'], 'partial')
        self.assertTrue(all(q['status'] == 'empty' for q in status['queries'] if q['required']))

    def test_saved_range_responses_can_be_reclassified_without_network_access(self):
        config = self.settings()['prometheus']
        plan = query_plan(config)
        start, end = time.time() - 20, time.time() - 17
        query_start, query_end = start - config['contextBeforeSeconds'], end + config['contextAfterSeconds']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'prometheus'
            root.mkdir()
            queries = []
            for group, specs in plan.items():
                entries = []
                for spec in specs:
                    entry = {**spec, 'file': group + '.json', 'status': 'insufficient-measurement-points',
                             'parameters': {'query': spec['expression'], 'start': query_start,
                                            'end': query_end, 'step': config['stepSeconds']},
                             'response': {'status': 'success', 'data': {'resultType': 'matrix',
                                'result': [{'metric': {}, 'values': [[start + 1, '1']]}]}}}
                    entries.append(entry)
                    queries.append({key: value for key, value in entry.items() if key != 'response'})
                (root / (group + '.json')).write_text(json.dumps({'queries': entries}))
            status = {'status': 'partial', 'errors': [], 'scrape': {'status': 'fresh'}, 'queries': queries,
                      'measurement_start_epoch': start, 'measurement_end_epoch': end,
                      'query_start_epoch': query_start, 'query_end_epoch': query_end}
            (root / 'collection-status.json').write_text(json.dumps(status))
            revised = reclassify_saved_collection(temporary)
            self.assertEqual(revised['status'], 'collected')
            self.assertEqual(revised['previous_collection_status'], 'partial')
            self.assertEqual(revised['temporal_coverage']['status'], 'sparse-in-window')
            self.assertTrue(all(q['temporal_coverage'] == 'single-sample-in-window'
                                for q in revised['queries'] if q['required']))
            self.assertTrue(all('response' not in q for q in revised['queries']))
