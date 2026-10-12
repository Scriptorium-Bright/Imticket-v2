"""Offline accounting: a decided terminal outcome and completed Journey are separate."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from harness_observations import analyze_points


class HarnessObservationTests(unittest.TestCase):
    def point(self, metric, value, state, sequence, outcome=None):
        meta = {'harness_vu': '1', 'harness_iteration': '0', 'harness_state': state,
                'harness_sequence': str(sequence), 'harness_state_started_ms': '1000'}
        if outcome:
            meta['harness_outcome'] = outcome
        return {'type': 'Point', 'metric': metric, 'data': {'time': '2026-10-11T23:00:00+09:00',
                'value': value, 'tags': {}, 'metadata': meta}}

    def test_terminal_decision_without_finished_is_a_finalization_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'points.json'
            points = [self.point('journey_started', 1, 'journey.initial-metrics', 1),
                      self.point('harness_phase', 1000, 'engine.result', 2, 'SYSTEM_FAILURE'),
                      self.point('booking_journey_system_failed', 1, 'finalize.terminal-counter', 3, 'SYSTEM_FAILURE'),
                      self.point('harness_phase', 1000, 'finalize.terminal-duration', 4, 'SYSTEM_FAILURE')]
            path.write_text('\n'.join(json.dumps(p) for p in points))
            data = analyze_points(path)
            self.assertEqual(data['per_vu']['unfinished_with_terminal_decision'], 1)
            self.assertEqual(data['per_vu']['unfinished_journey_last_phase'], {'finalize.terminal-duration': 1})
            self.assertEqual(data['terminal_recording_chain']['terminal_counter_minus_journey_finished'], 1)

    def test_old_raw_without_identity_cannot_invent_per_vu_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'points.json'
            point = self.point('journey_started', 1, 'http.dispatch', 1)
            point['data'].pop('metadata')
            path.write_text(json.dumps(point))
            data = analyze_points(path)
            self.assertFalse(data['raw_points_have_vu_identity'])
            self.assertEqual(data['users'], [])
