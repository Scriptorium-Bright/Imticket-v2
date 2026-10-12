"""Actual k6 interpreter, localhost stub only; never contacts Imticket."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from test_reselection_http import ReselectionHandler
from test_pilot import token
from test_pilot_http import K6_ROOT
from harness_observations import analyze_points


class SlowMapHandler(ReselectionHandler):
    def do_GET(self):
        if self.path.startswith('/api/seats/'):
            time.sleep(2)
        super().do_GET()


@unittest.skipUnless(shutil.which('k6'), 'k6 required')
class HarnessHTTPTests(unittest.TestCase):
    def exercise(self, enabled, interrupted=False):
        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowMapHandler if interrupted else ReselectionHandler)
        server.mode, server.failure_mode, server.variant = 'success', 'none', 'success' if interrupted else 'conflict'
        server.errors, server.events, server.keys, server.queries = [], [], [], []
        server.selected, server.reserve_keys, server.maps_read = [], [], {}
        server.reservations, server.members = {}, {}
        server.fixture_seats = {9: list(range(1, 11))}
        server.seats = {i: {'id': i, 'status': 'AVAILABLE', 'price': 10000, 'section': 'A',
                            'rowNumber': 1, 'seatNumber': i, 'grade': 'VIP'} for i in range(1, 11)}
        server.mutex, server.pre_reserve_calls, server.detail_delay = threading.Lock(), 0, 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                config = {'runId': 'local-diagnostic-test', 'performanceTimeId': 9,
                          'baseUrl': f'http://127.0.0.1:{server.server_port}', 'requestTimeout': '2s',
                          'profile': {'seatCount': 10, 'referenceAttemptors': 1, 'peakJourneyArrivalRate': 1,
                                      'hotSeatRatio': .2, 'hotTrafficRatio': 1, 'seatSelectionRatio': [1, 0, 0]},
                          'reselection': {'thinkTime': {'enabled': False}, 'abandonment': {'enabled': False},
                                          'safetyGuard': {'maxJourneyDuration': '30s'}}}
                script = directory / 'test.js'
                script.write_text(f"""import {{reselectionJourney}} from '{K6_ROOT}/lib/reselection-journey.js';
import {{harnessTrace}} from '{K6_ROOT}/lib/harness-trace.js';
import {{pilotMetrics}} from '{K6_ROOT}/lib/pilot-metrics.js';
const config={json.dumps(config)};
export const options={{scenarios:{{baseline:{{executor:'per-vu-iterations',vus:1,iterations:1,maxDuration:'{'1s' if interrupted else '5s'}',gracefulStop:'0s'}}}}}};
export default function(){{
  if(harnessTrace)harnessTrace('iteration.start');
  pilotMetrics.iterationStarted.add(1);
  reselectionJourney(config, {json.dumps(token(1, time.time()+7200)['accessToken'])}, () => 0);
  if(harnessTrace)harnessTrace('iteration.return');
}}
""")
                empty = directory / 'options.json'
                empty.write_text('{}')
                environment = {k: v for k, v in os.environ.items() if not k.startswith(('K6_', 'HARNESS_', 'TEST_'))}
                if enabled:
                    environment['HARNESS_DIAGNOSTIC'] = '1'
                output = directory / 'points.json'
                result = subprocess.run(['k6', '--config', str(empty), '--address', '', 'run', '--quiet', '--no-usage-report',
                    '--out', f'json={output}', str(script)], env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                data = analyze_points(output)
                self.assertEqual(data['counts']['journey_started'], 1, result.stdout + result.stderr)
                if not interrupted:
                    self.assertEqual(data['counts']['journey_finished'], 1, result.stdout + result.stderr)
                points = [json.loads(line) for line in output.read_text().splitlines()]
                self.assertEqual(server.errors, [])
                for point in points:
                    if point['type'] == 'Point':
                        self.assertNotIn('harness_vu', point['data'].get('tags', {}))
                        self.assertNotIn('eyJ', json.dumps(point))
                return data, points
        finally:
            server.shutdown(); server.server_close()

    def test_trace_keeps_conflict_to_booking_semantics_and_is_off_by_default(self):
        plain, plain_points = self.exercise(False)
        traced, traced_points = self.exercise(True)
        self.assertEqual(plain['counts'], traced['counts'])
        self.assertEqual(traced['counts']['journey_finished'], 1)
        self.assertEqual(traced['counts']['seat_conflicts'], 1)
        self.assertEqual(traced['counts']['reselections'], 1)
        self.assertFalse(any(p['metric'] == 'harness_phase' for p in plain_points))
        self.assertEqual(traced['users'][0]['phase']['state'], 'iteration.return')
        markers = [p['data']['metadata'] for p in traced_points if p.get('metric') == 'harness_phase' and p['type'] == 'Point']
        conflict = next(int(m['harness_sequence']) for m in markers if m['harness_state'] == 'engine.conflict-retry-pending')
        self.assertTrue(any(int(m['harness_sequence']) > conflict and m['harness_state'] == 'http.dispatch'
                            and m['harness_api'] == 'seat-map' for m in markers))

    def test_executor_interruption_identifies_pending_http_without_inventing_terminal_outcome(self):
        data, _ = self.exercise(True, interrupted=True)
        self.assertEqual(data['per_vu']['unfinished_journey_last_phase'], {'http.dispatch': 1})
        self.assertEqual(data['per_vu']['unfinished_with_terminal_decision'], 0)
        self.assertEqual(data['users'][0]['phase']['api'], 'seat-map')
        self.assertEqual(data['counts']['iterations'], 0)


if __name__ == '__main__':
    unittest.main()
