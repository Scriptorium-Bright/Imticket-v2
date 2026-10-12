"""Local stub Pilot integration only. Does not execute Smoke or contact Imticket."""
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from test_http_smoke import ContractHandler, K6_ROOT, read_json
from test_pilot import token
from pilot_prometheus import GROUPS


class PilotHandler(ContractHandler):
    def translate_auth(self):
        header = self.headers.get('Authorization', '')
        try:
            body = header.removeprefix('Bearer ').split('.')[1]
            member = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))['sub']
        except (IndexError, ValueError, KeyError):
            self.server.errors.append('Pilot JWT missing')
            member = 'invalid'
        self.headers.replace_header('Authorization', 'Bearer contract-user-' + member)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in {'/actuator/prometheus', '/api/v1/targets', '/api/v1/query_range'}:
            self.server.events.append((path, time.monotonic()))
            if path == '/actuator/prometheus':
                body = ('\n'.join(n + '{application="imticket-v2"} 0' for g in GROUPS.values() for n in g) + '\n').encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == '/api/v1/targets':
                now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                self.send_json({'status': 'success', 'data': {'activeTargets': [{'labels': {'job': 'imticket-v2'}, 'health': 'up',
                                'lastScrape': now, 'scrapeInterval': '5s', 'scrapeTimeout': '5s'}]}}, envelope=False)
            else:
                params = parse_qs(urlsplit(self.path).query)
                self.server.queries.append(params)
                start, end = float(params['start'][0]), float(params['end'][0])
                self.send_json({'status': 'success', 'data': {'resultType': 'matrix', 'result': [{'metric': {'application': 'imticket-v2'},
                                'values': [[start, '0'], [end, '1']]}]}}, envelope=False)
            return
        self.translate_auth()
        if path.startswith('/api/reservations/'):
            time.sleep(self.server.detail_delay)
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith('/api/auth/'):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.server.events.append((path, time.monotonic()))
            with self.server.mutex:
                identity = self.server.members.setdefault(data['username'], len(self.server.members) + 1)
            self.send_json({**token(identity, time.time() + 7200), 'role': 'MEMBER'})
            return
        self.translate_auth()
        if path == '/api/reservations/pre-reserve':
            raw = self.rfile.read(int(self.headers['Content-Length']))
            self.rfile = io.BytesIO(raw)
            with self.server.mutex:
                self.server.pre_reserve_calls += 1
                index = self.server.pre_reserve_calls
            if (self.server.failure_mode == 'mixed' and index in {2, 5}) or (self.server.failure_mode == 'system' and index == 1):
                self.server.events.append((path, time.monotonic()))
                business = self.server.failure_mode == 'mixed'
                self.send_json({'success': False, 'data': None, 'errorCode': 'SEAT_UNAVAILABLE' if business else 'STUB_ERROR',
                                'message': 'stub outcome'}, 409 if business else 503, envelope=False)
                return
        super().do_POST()


@unittest.skipUnless(shutil.which('k6'), 'k6 required')
class PilotHTTPTests(unittest.TestCase):
    def exercise(self, mode):
        server = ThreadingHTTPServer(('127.0.0.1', 0), PilotHandler)
        server.mode, server.failure_mode = 'success', mode
        server.errors, server.events, server.keys, server.queries = [], [], [], []
        server.reservations, server.fixture_seats, server.seats, server.members = {}, {}, {}, {}
        server.mutex, server.pre_reserve_calls = threading.Lock(), 0
        server.detail_delay = 1.3 if mode == 'mixed' else 3 if mode == 'interrupted' else 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        pointer = K6_ROOT / 'fixtures' / 'current.env.sh'
        previous_pointer = pointer.read_bytes() if pointer.exists() else None
        directories = []
        try:
            with tempfile.TemporaryDirectory() as temporary:
                config = read_json(K6_ROOT / 'config' / 'pilot.example.json')
                config['auth']['tokenPoolSize'] = 1
                if mode == 'system':
                    config['profile'] = 'musical-high'
                config['requestTimeout'] = '10s'
                scenario = {**config['measurement'], 'startRate': 2, 'stages': [{'duration': '2s', 'target': 2}, {'duration': '2s', 'target': 2}, {'duration': '1s', 'target': 0}],
                            'preAllocatedVUs': 4, 'maxVUs': 4, 'gracefulStop': '5s'}
                if mode != 'mixed':
                    scenario.update(startRate=1, stages=[{'duration': '2s', 'target': 1}], gracefulStop='0s' if mode == 'interrupted' else '5s')
                config['measurement'] = scenario
                # Keep the stub's final 1/s start away from the exact schedule boundary on busy CI hosts.
                config['warmup'] = {'enabled': mode == 'mixed', 'execution': {**scenario, 'startRate': 1, 'stages': [{'duration': '2.2s', 'target': 1}]}}
                config['validation']['minimumApiSamples'] = 1  # Stub coverage only, no statistical claim.
                config['prometheus'].update(contextBeforeSeconds=0, contextAfterSeconds=0, settleTimeoutSeconds=1)
                config_path = Path(temporary) / 'pilot.json'
                config_path.write_text(json.dumps(config))
                environment = {k: v for k, v in os.environ.items() if not k.startswith(('K6_', 'TEST_', 'SMOKE_', 'PROMETHEUS_', 'WORKLOAD_', 'FIXTURE_', 'PERFORMANCE_TIME_', 'EXPERIMENT_'))}
                url = f'http://127.0.0.1:{server.server_port}'
                environment.update(BASE_URL=url, PROMETHEUS_URL=url)
                result = subprocess.run([sys.executable, str(K6_ROOT / 'scripts' / 'run-experiment.py'), 'pilot', '--config', str(config_path),
                                         '--accounts', str(Path(temporary) / 'accounts.json'), '--keep-data'],
                                        env=environment, cwd=K6_ROOT.parent, capture_output=True, text=True, timeout=35)
                run_dirs = []
                for line in result.stdout.splitlines():
                    if line.startswith('Run directory: '):
                        run_dirs.append(Path(line.removeprefix('Run directory: ')))
                    elif line.startswith('Fixture manifest: '):
                        directories.append(Path(line.removeprefix('Fixture manifest: ')).parent)
                directories.extend(run_dirs)
                self.assertTrue(run_dirs, result.stdout + result.stderr)
                run = run_dirs[-1]
                metadata = read_json(run / 'run-metadata.json')
                summary = read_json(run / 'k6-summary.json')
                observations = read_json(run / 'pilot-observations.json')
                count = lambda n: summary['metrics'].get(n, {}).get('values', {}).get('count', 0)
                if mode == 'mixed':
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr + '\n' + json.dumps(metadata['pilot_validation'], indent=2))
                self.assertEqual(server.errors, [], result.stdout + result.stderr)
                self.assertEqual(metadata['auth']['token_count'], 1)
                self.assertEqual(metadata['seat_count'], 1500 if mode == 'system' else 1000)
                self.assertEqual(metadata['observed_seat_count'], metadata['seat_count'])
                self.assertEqual(metadata['effective_execution']['executor'], 'ramping-arrival-rate')
                self.assertEqual(metadata['pre_allocated_vus'], 4)
                self.assertTrue(metadata['arrival_started_at'])
                self.assertTrue(metadata['drain_ended_at'])
                if mode != 'interrupted':
                    self.assertEqual(count('iteration_started'), count('journey_started'))
                report = (run / 'report.md').read_text()
                logs = result.stdout + result.stderr + (run / 'k6.log').read_text()
                for forbidden in ('http://', 'Hikari가 병목', 'Tomcat이 병목', 'DB가 원인', 'CPU saturation'):
                    self.assertNotIn(forbidden, report)
                self.assertNotIn('eyJhbGci', (run / 'k6-points.json').read_text())
                self.assertTrue(all(q['status'] == 'collected' for q in read_json(run / 'prometheus' / 'collection-status.json')['queries']))
                self.assertEqual(len(server.queries), 37)
                if mode == 'mixed':
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(len(run_dirs), 2)
                    warm = read_json(run_dirs[0] / 'run-metadata.json')
                    self.assertNotEqual(warm['performance_time_id'], metadata['performance_time_id'])
                    self.assertEqual(metadata['warmup_run']['run_id'], warm['run_id'])
                    self.assertEqual(count('system_failures'), 0)
                    self.assertEqual(count('dropped_iterations'), 0)
                    self.assertEqual(count('journey_finished'), count('iterations'))
                    self.assertGreater(count('booking_completed'), 0)
                    self.assertGreater(count('business_failures'), 0)
                    self.assertNotIn('Journey failed:', logs)
                    self.assertEqual(summary['metrics']['http_req_failed']['values']['passes'], count('business_failures'))
                    self.assertGreater(metadata['drain_ended_at'], metadata['arrival_ended_at'])
                    self.assertEqual(metadata['pilot_validation']['status'], 'needs-review')
                    self.assertEqual(metadata['pilot_validation']['checks']['journey_accounting']['status'], 'passed')
                    self.assertEqual(metadata['pilot_validation']['checks']['seat_inventory']['status'], 'not-verified')
                    self.assertGreater(observations['gauge_observations']['vus']['samples'], 0)
                    self.assertGreater(observations['gauge_observations']['vus_max']['samples'], 0)
                elif mode == 'system':
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(count('system_failures'), 1)
                    self.assertEqual(observations['http_5xx_requests'], 1)
                    self.assertEqual(count('business_failures'), 0)
                    self.assertIn('Journey failed: pre-reserve, system-failure, http-5xx', logs)
                    self.assertEqual(metadata['pilot_validation']['status'], 'failed')
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertGreater(metadata['unfinished_iterations'], 0)
                    self.assertGreater(count('journey_started'), count('journey_finished'))
                    self.assertEqual(metadata['pilot_validation']['status'], 'failed')
                last_booking = max(t for path, t in server.events if path.startswith(('/api/seats/', '/api/reservations/', '/api/payments/')))
                first_query = min(t for path, t in server.events if path == '/api/v1/query_range')
                self.assertGreater(first_query, last_booking)
        finally:
            server.shutdown()
            server.server_close()
            for path in directories:
                shutil.rmtree(path, ignore_errors=True)
            if previous_pointer is not None:
                pointer.write_bytes(previous_pointer)
            else:
                pointer.unlink(missing_ok=True)

    def test_separate_fixtures_shared_member_business_outcome_and_drain(self):
        self.exercise('mixed')

    def test_http_5xx_is_system_failure_and_preserves_artifacts(self):
        self.exercise('system')

    def test_drain_expiry_retains_interrupted_accounting(self):
        self.exercise('interrupted')
