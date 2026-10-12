"""Warm-up jitter is recorded without hiding incomplete/dropped work."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

K6_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(K6_ROOT / 'scripts'))
from common import APIS, write_json
from pilot_config import load_config
from pilot_observations import analyze, COUNTERS, REQUIRED


class WarmupDispatchTests(unittest.TestCase):
    def observation(self, dropped=0, unfinished=0):
        _, config = load_config(K6_ROOT / 'config/pilot.example.json', users=1)
        epoch = datetime(2026, 10, 12, tzinfo=timezone.utc).timestamp()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            points, counts = [], dict.fromkeys(COUNTERS, 0)
            def point(name, value, offset, tags=None):
                points.append({'type': 'Point', 'metric': name, 'data': {
                    'time': datetime.fromtimestamp(epoch + offset, timezone.utc).isoformat(),
                    'value': value, 'tags': {'scenario': 'warmup', **(tags or {})}}})
                if name in counts:
                    counts[name] += value
            point('scenario_start_epoch_ms', epoch * 1000, 0)
            for index in range(1, 61):
                start = index + .1
                for name in ('iteration_started', 'journey_started'):
                    point(name, 1, start)
                if index == 60 and unfinished:
                    continue
                for name in ('journey_finished', 'iterations', 'booking_completed', 'reservation_success', 'payment_applied'):
                    point(name, 1, start + .2)
                for name in ('http_attempted', 'http_completed', 'http_reqs'):
                    point(name, 6, start + .2)
                for api in APIS:
                    point('http_req_duration', 10, start + .2, {'api': api})
                for name in ('journey_duration', 'iteration_duration'):
                    point(name, 200, start + .2)
            if dropped:
                point('dropped_iterations', dropped, 60.1)
            point('vus', 1, 1)
            point('vus_max', 20, 1)
            point('workload_end_epoch_ms', (epoch + 60.5) * 1000, 60.5)
            metrics = {name: {'values': {'count': counts.get(name, 1)}} for name in REQUIRED}
            write_json(root / 'k6-summary.json', {'metrics': metrics})
            (root / 'k6-points.json').write_text(''.join(json.dumps(p) + '\n' for p in points))
            metadata = {'phase': 'warmup', 'k6_exit_code': 0, 'ended_at': datetime.fromtimestamp(epoch + 60.5, timezone.utc).isoformat()}
            return analyze(root, metadata, config), metadata

    def test_all_60_complete_with_100ms_lateness_passes_and_preserves_boundaries(self):
        o, m = self.observation()
        self.assertEqual(o['validation']['status'], 'passed')
        self.assertEqual(o['validation']['checks']['starts_after_arrival']['status'], 'observed')
        self.assertAlmostEqual(m['last_dispatch_after_planned_end_seconds'], .1, places=5)
        self.assertEqual(m['arrival_ended_at'], m['last_observed_arrival_dispatch_at'])
        self.assertNotEqual(m['arrival_ended_at'], m['drain_ended_at'])
        self.assertEqual(o['count_totals']['iterations'], 60)

    def test_jitter_policy_does_not_hide_dropped_or_unfinished_work(self):
        for arguments, check in [({'dropped': 1}, 'no_dropped_iterations'), ({'unfinished': 1}, 'no_unfinished_iterations')]:
            with self.subTest(arguments=arguments):
                o, _ = self.observation(**arguments)
                self.assertEqual(o['validation']['status'], 'failed')
                self.assertEqual(o['validation']['checks'][check]['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
