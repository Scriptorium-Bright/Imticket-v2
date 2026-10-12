"""Result timezone conversion must preserve measurement instants and raw data."""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

K6_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(K6_ROOT / 'scripts'))
from common import point_time_range, result_time
from pilot_config import load_config
from pilot_prometheus import GROUPS, collect_pilot
from report import generate_report


class ResultTimezoneTests(unittest.TestCase):
    def test_midnight_conversion_preserves_epoch_and_is_not_applied_twice(self):
        utc = '2026-10-11T16:00:00.123456Z'
        kst = '2026-10-12T01:00:00.123456+09:00'
        self.assertEqual(result_time(utc), kst)
        self.assertEqual(result_time(kst), kst)
        epoch = datetime.fromisoformat(utc.replace('Z', '+00:00')).timestamp()
        self.assertEqual(datetime.fromisoformat(result_time(epoch)).timestamp(), epoch)
        with self.assertRaises(ValueError):
            result_time('2026-10-12T01:00:00')

    def test_mixed_offset_raw_points_keep_chronological_window_and_original_bytes(self):
        points = [('journey_started', '2026-10-11T23:59:59Z'),
                  ('journey_started', '2026-10-12T08:59:58+09:00'),
                  ('journey_finished', '2026-10-11T23:59:59.9Z'),
                  ('journey_finished', '2026-10-12T09:00:00.1+09:00')]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'points.json'
            path.write_text('\n'.join(json.dumps({'type': 'Point', 'metric': name,
                            'data': {'time': timestamp, 'value': 1}}) for name, timestamp in points))
            original = path.read_bytes()
            self.assertEqual(point_time_range(path), ('2026-10-12T08:59:58.000000+09:00',
                                                     '2026-10-12T09:00:00.100000+09:00'))
            self.assertEqual(path.read_bytes(), original)

    def test_prometheus_queries_have_identical_epochs_for_utc_and_kst_metadata(self):
        config = load_config(K6_ROOT / 'config' / 'pilot.example.json', users=1)[1]['prometheus']
        start, end = '2026-10-11T15:30:00+00:00', '2026-10-11T15:32:30+00:00'
        a, b = datetime.fromisoformat(start).timestamp(), datetime.fromisoformat(end).timestamp()
        exposition = '\n'.join(name + ' 0' for group in GROUPS.values() for name in group)
        queries = []

        def fetch(url, _):
            if url.endswith('/actuator/prometheus'):
                return exposition
            if url.endswith('/api/v1/targets'):
                return json.dumps({'data': {'activeTargets': [{'labels': {'job': 'imticket-v2'},
                    'health': 'up', 'lastScrape': '2026-10-11T15:32:40Z', 'scrapeInterval': '5s'}]}})
            params = parse_qs(urlsplit(url).query)
            queries.append((float(params['start'][0]), float(params['end'][0])))
            return json.dumps({'status': 'success', 'data': {'resultType': 'matrix',
                'result': [{'metric': {}, 'values': [[a, '0'], [b, '0']]}]}})

        for convert in (lambda value: value, result_time):
            metadata = {'measurement_started_at': convert(start), 'measurement_ended_at': convert(end)}
            with tempfile.TemporaryDirectory() as temporary, patch('pilot_prometheus.fetch', side_effect=fetch):
                status = collect_pilot(temporary, 'http://stub.invalid', 'http://metrics.invalid', config, metadata)
                self.assertEqual(status['status'], 'collected')
                self.assertEqual((status['measurement_start_epoch'], status['measurement_end_epoch']), (a, b))
                self.assertTrue(status['start'].endswith('+09:00'))
                self.assertTrue(status['collection_finished_at'].endswith('+09:00'))
        self.assertEqual(len(queries), 74)
        self.assertEqual(set(queries), {(a - config['contextBeforeSeconds'], b + config['contextAfterSeconds'])})

    def test_both_reports_render_legacy_utc_times_in_kst_without_rewriting_metadata(self):
        for kind in ('smoke', 'pilot'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                metadata = {'run_id': 'old-utc-run', 'run_number': 1, 'test_kind': kind, 'phase': kind,
                    'profile': 'musical-low', 'performance_time_id': 1, 'seat_count': 1000,
                    'execution': {}, 'auth': {'token_count': 1},
                    'started_at': '2026-10-11T16:00:00Z', 'ended_at': '2026-10-11T16:00:01Z',
                    'fixture': {'initial_state': 'all-AVAILABLE',
                        'prepared_started_at': '2026-10-11T15:59:00Z',
                        'prepared_ended_at': '2026-10-11T15:59:01Z'}}
                path = directory / 'run-metadata.json'
                path.write_text(json.dumps(metadata))
                original = path.read_bytes()
                generate_report(directory)
                report = (directory / 'report.md').read_text()
                self.assertIn('Asia/Seoul (KST, UTC+09:00)', report)
                self.assertIn('2026-10-12T01:00:00.000000+09:00', report)
                self.assertIn('2026-10-12T00:59:00.000000+09:00', report)
                self.assertNotIn('2026-10-11T16:00:00Z', report)
                self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
