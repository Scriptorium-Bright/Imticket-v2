"""Target VU acceptance distinguishes technical truncation from observed server failures."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from vu_baseline_observations import analyze


class TargetVUAcceptanceTests(unittest.TestCase):
    def analyze_outcome(self, outcome):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'k6-summary.json').write_text(json.dumps({
                'metrics': {'dropped_iterations': {'values': {'count': 0}}}}))
            (directory / 'seat-inventory.json').write_text(json.dumps({
                'status': 'verified', 'db': {'initial_seat_count': 2,
                    'unique_sold_seat_count': 1, 'oversell_invariant': 'passed'}}))
            points = []
            def point(name, value, second=1):
                points.append({'type': 'Point', 'metric': name, 'data': {
                    'time': f'2026-10-11T00:00:0{second}Z', 'value': value,
                    'tags': {'scenario': 'baseline'}}})
            for name in ('iteration_started', 'journey_started', 'booking_journey_started'):
                point(name, 2)
            for name in ('journey_finished', 'iterations'):
                point(name, 2, 2)
            point('booking_journey_completed', 1, 2)
            point(outcome, 1, 2)
            if outcome == 'booking_journey_system_failed':
                point('system_failures', 1, 2)
            point('vus', 2)
            point('vus_max', 2)
            (directory / 'k6-points.json').write_text(''.join(json.dumps(p) + '\n' for p in points))
            return analyze(directory, {'phase': 'baseline', 'configured_target_vus': 2},
                           {'workloadModel': 'reselection', 'measurement': {'vus': 2}})

    def test_guard_stop_fails_even_when_all_journeys_are_accounted_for(self):
        result = self.analyze_outcome('booking_journey_guard_stopped')
        self.assertEqual(result['validation']['checks']['reselection_terminal_accounting']['status'], 'passed')
        self.assertEqual(result['validation']['checks']['no_safety_guard_termination']['status'], 'failed')
        self.assertEqual(result['validation']['status'], 'failed')

    def test_system_failure_remains_a_valid_observation(self):
        result = self.analyze_outcome('booking_journey_system_failed')
        self.assertEqual(result['validation']['checks']['no_safety_guard_termination']['status'], 'passed')
        self.assertEqual(result['validation']['checks']['system_failures_are_observations']['count'], 1)
        self.assertEqual(result['validation']['status'], 'passed')
