"""Actual k6 interpreter against localhost-only fixtures; no Imticket experiment."""
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
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from test_pilot_http import PilotHandler, K6_ROOT, read_json


class ControlledConflictHandler(PilotHandler):
    """Occupancy drives conflicts; no first-call failure injection."""
    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/api/reservations/pre-reserve':
            raw = self.rfile.read(int(self.headers['Content-Length']))
            self.rfile = io.BytesIO(raw)
            body = json.loads(raw)
            self.server.selected.append(body['seatIds'])
            self.server.reserve_keys.append(self.headers.get('Idempotency-Key'))
            if any(self.server.seats[i]['status'] != 'AVAILABLE' for i in body['seatIds']):
                self.server.events.append((path, time.monotonic()))
                self.send_json({'success': False, 'data': None, 'errorCode': 'SEAT_UNAVAILABLE',
                                'message': 'held seat'}, 409, envelope=False)
                return
            for identity in body['seatIds']:
                self.server.seats[identity]['status'] = 'LOCKED'
        elif path.startswith('/api/payments/') and path.endswith('/verify'):
            reservation = self.server.reservations[int(path.split('/')[3]) - 1000]
            for identity in reservation['seatIds']:
                self.server.seats[identity]['status'] = 'RESERVED'
        super().do_POST()


class ReselectionHandler(PilotHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith('/api/seats/'):
            time_id = int(path.rsplit('/', 1)[1])
            self.server.maps_read[time_id] = self.server.maps_read.get(time_id, 0) + 1
            # Preparation and exclusive claim each observe the untouched initial fixture.
            if self.server.variant == 'sold-out' and self.server.maps_read[time_id] >= 3:
                for identity in self.server.fixture_seats[time_id]:
                    self.server.seats[identity]['status'] = 'RESERVED'
        super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/api/reservations/pre-reserve':
            raw = self.rfile.read(int(self.headers['Content-Length']))
            self.rfile = io.BytesIO(raw)
            body = json.loads(raw)
            self.server.selected.append(body['seatIds'])
            if self.server.variant in {'conflict', 'system'} and len(self.server.selected) == 1:
                self.server.events.append((path, time.monotonic()))
                self.server.reserve_keys.append(self.headers.get('Idempotency-Key'))
                if self.server.variant == 'conflict':
                    for identity in body['seatIds']:
                        self.server.seats[identity]['status'] = 'LOCKED'
                self.send_json({'success': False, 'data': None, 'errorCode': 'SEAT_UNAVAILABLE' if self.server.variant == 'conflict' else 'STUB_ERROR',
                                'message': 'stub outcome'}, 409 if self.server.variant == 'conflict' else 503, envelope=False)
                return
            self.server.reserve_keys.append(self.headers.get('Idempotency-Key'))
        super().do_POST()


@unittest.skipUnless(shutil.which('k6'), 'k6 required')
class ReselectionHTTPTests(unittest.TestCase):
    def exercise(self, variant, kind='pilot', repeat=1, model='reselection'):
        server = ThreadingHTTPServer(('127.0.0.1', 0), ControlledConflictHandler if variant == 'controlled' else ReselectionHandler)
        server.mode, server.failure_mode, server.variant = 'success', 'none', variant
        server.errors, server.events, server.keys, server.queries = [], [], [], []
        server.selected, server.reserve_keys, server.maps_read = [], [], {}
        server.reservations, server.fixture_seats, server.seats, server.members = {}, {}, {}, {}
        server.mutex, server.pre_reserve_calls, server.detail_delay = threading.Lock(), 0, 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        pointer = K6_ROOT / 'fixtures' / 'current.env.sh'
        previous = pointer.read_bytes() if pointer.exists() else None
        directories = []
        try:
            with tempfile.TemporaryDirectory() as temporary:
                config = read_json(K6_ROOT / 'config' / 'reselection.pilot.example.json')
                config['workloadModel'] = model
                config['auth']['tokenPoolSize'] = 2
                config['inventory']['mode'] = 'api-only'
                # A 1/s executor's first start can fall at 1s; avoid ending these tiny stub phases at an arrival boundary.
                config['measurement'].update(startRate=1, stages=[{'duration': '2.2s', 'target': 1}], preAllocatedVUs=2, maxVUs=2, gracefulStop='2s')
                config['warmup']['enabled'] = kind == 'baseline'
                config['warmup']['execution'] = {**config['measurement'], 'stages': [{'duration': '1.2s', 'target': 1}]}
                config['validation']['minimumApiSamples'] = 1
                config['prometheus'].update(contextBeforeSeconds=0, contextAfterSeconds=0, settleTimeoutSeconds=1)
                config_path = Path(temporary) / 'config.json'
                config_path.write_text(json.dumps(config))
                environment = {k: v for k, v in os.environ.items() if not k.startswith(('K6_', 'TEST_', 'SMOKE_', 'PROMETHEUS_', 'WORKLOAD_', 'FIXTURE_', 'PERFORMANCE_TIME_', 'EXPERIMENT_'))}
                url = f'http://127.0.0.1:{server.server_port}'
                environment.update(BASE_URL=url, PROMETHEUS_URL=url)
                extra = ['--validate-seat-conflict'] if variant == 'controlled' else []
                result = subprocess.run([sys.executable, str(K6_ROOT / 'scripts' / 'run-experiment.py'), kind, '--config', str(config_path),
                                         '--workload-model', model, '--repeat', str(repeat),
                                         '--accounts', str(Path(temporary) / 'accounts.json'), '--keep-data', *extra],
                                        env=environment, cwd=K6_ROOT.parent, capture_output=True, text=True, timeout=45)
                runs = []
                for line in result.stdout.splitlines():
                    if line.startswith('Run directory: '):
                        runs.append(Path(line.removeprefix('Run directory: ')))
                    elif line.startswith('Fixture manifest: '):
                        directories.append(Path(line.removeprefix('Fixture manifest: ')).parent)
                directories.extend(runs)
                self.assertTrue(runs, result.stdout + result.stderr)
                self.assertEqual(server.errors, [], result.stdout + result.stderr)
                diagnostics = [read_json(r / 'run-metadata.json').get('pilot_validation') for r in runs]
                self.assertEqual(len(runs), repeat * (2 if kind == 'baseline' else 1), json.dumps(diagnostics) + result.stderr)
                for run in runs:
                    metadata = read_json(run / 'run-metadata.json')
                    self.assertRegex(run.name, r'^\d{8}T\d{6}\.\d{6}KST_')
                    self.assertEqual(metadata['time_zone'], 'Asia/Seoul')
                    for field in ('started_at', 'ended_at', 'workload_started_at', 'drain_ended_at', 'measurement_completed_at'):
                        self.assertTrue(metadata[field].endswith('+09:00'), (field, metadata[field]))
                    self.assertEqual(metadata['workloadModel'], model)
                    self.assertIn('_musical-low_' + model + '_', run.name)
                    self.assertEqual(read_json(run / 'k6-summary.json')['experiment']['workloadModel'], model)
                run = runs[-1]
                metadata, summary = read_json(run / 'run-metadata.json'), read_json(run / 'k6-summary.json')
                counts = lambda n: summary['metrics'].get(n, {}).get('values', {}).get('count', 0)
                self.assertEqual(counts('booking_journey_started' if model == 'reselection' else 'journey_started'), counts('iterations'), result.stdout + result.stderr)
                self.assertEqual(metadata['unfinished_iterations'], 0)
                self.assertTrue(metadata['drain_ended_at'])
                if model == 'single-attempt':
                    self.assertEqual(counts('http_reqs'), 6 * counts('booking_completed'))
                    self.assertNotIn('booking_journey_started', summary['metrics'])
                    self.assertGreater(counts('seats_confirmed'), 0)
                report = (run / 'report.md').read_text()
                for forbidden in ('http://', 'Hikari가 병목', 'Tomcat이 병목', 'DB가 원인', 'CPU saturation'):
                    self.assertNotIn(forbidden, report)
                if variant == 'system':
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(counts('booking_journey_system_failed'), 1)
                    self.assertEqual(metadata['pilot_validation']['status'], 'failed')
                    self.assertIn('Journey failed: pre-reserve, system-failure, http-5xx', result.stdout)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr + json.dumps(metadata['pilot_validation']))
                    self.assertEqual(counts('system_failures'), 0)
                    self.assertEqual(counts('business_failures'), 0)
                    self.assertEqual(metadata['pilot_validation']['checks']['reselection_accounting' if model == 'reselection' else 'journey_accounting']['status'], 'passed')
                    self.assertNotIn('Journey failed:', result.stdout)
                if variant == 'conflict':
                    self.assertGreater(counts('seat_conflicts'), 0)
                    self.assertGreater(counts('booking_journey_completed'), 0)
                    self.assertNotEqual(set(server.selected[0]), set(server.selected[1]))
                    self.assertEqual(len(server.selected[0]), len(server.selected[1]))
                    self.assertEqual(len(server.reserve_keys), len(set(server.reserve_keys)))
                    self.assertEqual(summary['metrics']['http_req_failed']['values']['passes'], counts('seat_conflicts'))
                elif variant == 'sold-out':
                    self.assertEqual(counts('booking_journey_sold_out'), counts('iterations'))
                    self.assertEqual(counts('pre_reserve_attempts'), 0)
                    self.assertEqual(counts('booking_completed'), 0)
                    self.assertNotIn('failed_journey_duration', summary['metrics'])
                elif variant == 'controlled':
                    self.assertIn('_reselection-validation_', run.name)
                    self.assertFalse(metadata['performance_measurement'])
                    audit = read_json(run / 'reselection-validation.json')
                    self.assertEqual(audit['status'], 'passed', json.dumps(audit))
                    self.assertTrue(all(audit['checks'].values()))
                    self.assertEqual(counts('seat_conflicts'), 1)
                    self.assertEqual(counts('reselections'), 1)
                    self.assertEqual(counts('pre_reserve_attempts'), 2)
                    self.assertEqual(counts('http_reqs'), 11)
                    self.assertEqual(server.selected[0], server.selected[1])
                    self.assertNotEqual(server.selected[1], server.selected[2])
                    self.assertIn('# Reselection Conflict Validation', report)
                if kind == 'journey-smoke':
                    self.assertEqual(metadata['arrival_end_status'], 'not-applicable (contract smoke)')
                    self.assertEqual(len(server.queries), 0)
                else:
                    self.assertEqual(len(server.queries), 37 * repeat)
                    self.assertIn(metadata['arrival_end_status'], {'schedule-completed', 'discrete-arrivals-exhausted-before-nominal-end'})
                    self.assertTrue(metadata['arrival_end_basis'])
                    self.assertLessEqual(metadata['arrival_ended_at'], metadata['drain_ended_at'])
                if kind == 'baseline':
                    self.assertEqual([read_json(r / 'run-metadata.json')['run_number'] for r in runs if read_json(r / 'run-metadata.json')['phase'] == 'baseline'], list(range(1, repeat + 1)))
                    times = [read_json(r / 'run-metadata.json')['performance_time_id'] for r in runs]
                    self.assertEqual(len(times), len(set(times)))
        finally:
            server.shutdown(); server.server_close()
            for directory in directories:
                shutil.rmtree(directory, ignore_errors=True)
            if previous is None:
                pointer.unlink(missing_ok=True)
            else:
                pointer.write_bytes(previous)

    def test_k6_reselection_conflict_to_booking(self): self.exercise('conflict')
    def test_k6_reselection_sold_out_has_no_system_failure(self): self.exercise('sold-out')
    def test_k6_reselection_system_failure_keeps_artifacts(self): self.exercise('system')
    def test_controlled_contract_entrypoint_against_localhost_only(self): self.exercise('conflict', 'journey-smoke')
    def test_controlled_real_interleaving_uses_one_holder_and_one_subject(self): self.exercise('controlled', 'journey-smoke')
    def test_baseline_repeat_three_uses_six_fresh_fixtures(self): self.exercise('success', 'baseline', 3)
    def test_single_attempt_baseline_preserves_one_attempt_and_six_api_calls(self): self.exercise('success', 'baseline', model='single-attempt')
