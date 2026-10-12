"""k6 over real local HTTP against a contract stub, NOT an Imticket server smoke result."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

K6_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(K6_ROOT / "scripts"))
from common import read_json


class ContractHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, data, status=200, envelope=True):
        body = json.dumps({"success": True, "data": data, "errorCode": None, "message": None} if envelope else data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def seat(self, identity, status=None):
        data = dict(self.server.seats[identity])
        if status:
            data["status"] = status
        return data

    def do_GET(self):
        path = urlsplit(self.path).path
        self.server.events.append((path, time.monotonic()))
        if path == "/actuator/prometheus":
            body = b'# TYPE actual_fixture_gauge gauge\nactual_fixture_gauge{application="imticket-v2"} 1\n'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/v1/query_range":
            query = parse_qs(urlsplit(self.path).query)
            self.server.queries.append(query)
            result = [{"metric": {"__name__": "actual_fixture_gauge", "job": "imticket-v2"},
                       "values": [[float(query["start"][0]), "1"]]}] if self.server.mode == "success" else []
            self.send_json({"status": "success", "data": {"resultType": "matrix", "result": result}}, envelope=False)
            return
        if not self.headers.get("Authorization", "").startswith("Bearer contract-user-"):
            self.server.errors.append("Authorization missing")
        if path.startswith("/api/seats/"):
            if self.server.mode == "timeout":
                time.sleep(0.2)
            identity = int(path.rsplit("/", 1)[1])
            self.send_json([self.seat(i) for i in self.server.fixture_seats[identity]])
        elif path.startswith("/api/reservations/"):
            identity = int(path.rsplit("/", 1)[1])
            item = self.server.reservations[identity]
            if item["owner"] != self.headers.get("Authorization"):
                self.server.errors.append("Journey switched its token before detail")
            self.send_json({**item, "status": "CONFIRMED", "expiredAt": None, "seats": [self.seat(i, "RESERVED") for i in item["seatIds"]]})
        else:
            self.server.errors.append(f"Unexpected path: {path}")
            self.send_json({}, 404)

    def do_POST(self):
        path = urlsplit(self.path).path
        self.server.events.append((path, time.monotonic()))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if path == "/api/auth/login":
            if body.get("password") != "contract-password":
                self.server.errors.append("Token preparation sent invalid login DTO")
            identity = int(body["username"].removeprefix("user-"))
            self.send_json({"memberId": identity, "accessToken": f"contract-user-{identity}", "role": "MEMBER"})
            return
        if not self.headers.get("Authorization", "").startswith("Bearer contract-user-"):
            self.server.errors.append("Authorization missing")
        if path == "/api/catalog/halls":
            self.server.hall = body
            self.send_json(5)
        elif path == "/api/catalog/performances":
            if self.server.mode == "catalog-failure":
                self.send_json({}, 503)
                return
            self.server.performance = body
            self.send_json(10)
        elif path == "/api/catalog/performances/10/times":
            if body["venueHallId"] != 5:
                self.server.errors.append("Fixture did not propagate venueHallId")
            identity = 77 + len(self.server.fixture_seats)
            hall = self.server.hall
            count = hall["rows"] * hall["seatsPerRow"]
            start = len(self.server.seats) + 1
            for offset in range(count):
                self.server.seats[start + offset] = {
                    "id": start + offset, "section": hall["section"],
                    "rowNumber": offset // hall["seatsPerRow"] + 1,
                    "seatNumber": offset % hall["seatsPerRow"] + 1, "grade": hall["grade"],
                    "price": self.server.performance["prices"][hall["grade"]], "status": "AVAILABLE",
                }
            self.server.fixture_seats[identity] = list(range(start, start + count))
            self.send_json(identity)
        elif path == "/api/seats/selection-summary":
            self.send_json({"performanceTimeId": body["performanceTimeId"], "seats": [self.seat(i) for i in body["seatIds"]],
                            "totalPrice": len(body["seatIds"]) * 10000})
        elif path == "/api/reservations/pre-reserve":
            key = self.headers.get("Idempotency-Key")
            if not key or not key.startswith("reserve-"):
                self.server.errors.append("pre-reserve key missing")
            self.server.keys.append(key)
            if self.server.mode in {"business", "system"}:
                status = 409 if self.server.mode == "business" else 503
                self.send_json({"success": False, "data": None, "errorCode": "SEAT_UNAVAILABLE", "message": "unavailable"}, status, envelope=False)
                return
            identity = 100 + body["seatIds"][0]
            item = {"id": identity, "reservationCode": f"reservation-{identity}", "totalPrice": len(body["seatIds"]) * 10000,
                    "status": "PENDING_PAYMENT", "expiredAt": "2026-10-12T12:00:00", "seatIds": body["seatIds"],
                    "owner": self.headers.get("Authorization")}
            self.server.reservations[identity] = item
            self.send_json(item)
        elif path == "/api/payments/prepare":
            key = self.headers.get("Idempotency-Key")
            if not key or not key.startswith("payment-"):
                self.server.errors.append("prepare key missing")
            self.server.keys.append(key)
            identity = body["reservationId"]
            if self.server.reservations[identity]["owner"] != self.headers.get("Authorization"):
                self.server.errors.append("Journey switched its token before prepare")
            amount = self.server.reservations[identity]["totalPrice"]
            self.send_json({"paymentOrderId": identity + 1000, "merchantOrderId": f"merchant-{identity}", "amount": amount,
                            "currency": "KRW", "provider": "FAKE", "providerPaymentId": f"fake-{identity}"})
        elif path.startswith("/api/payments/") and path.endswith("/verify"):
            order = int(path.split("/")[3])
            identity = order - 1000
            if self.server.reservations[identity]["owner"] != self.headers.get("Authorization"):
                self.server.errors.append("Journey switched its token before verify")
            if body != {"providerPaymentId": f"fake-{identity}"}:
                self.server.errors.append("verify did not use prepare providerPaymentId")
            self.send_json({"paymentOrderId": order, "paymentStatus": "APPLIED", "reservationId": identity,
                            "reservationStatus": "CONFIRMED", "providerTransactionId": f"fake-{identity}"})
        else:
            self.server.errors.append(f"Unexpected POST: {path}")
            self.send_json({}, 404)


@unittest.skipUnless(shutil.which("k6"), "k6 required")
class SmokeHTTPTests(unittest.TestCase):
    def exercise(self, mode, users=1, pool_size=None):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ContractHandler)
        server.mode, server.errors, server.events, server.keys, server.queries = mode, [], [], [], []
        server.reservations = {}
        server.fixture_seats, server.seats = {}, {}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        run_dir = None
        fixture_dir = None
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fixture = Path(temporary) / "tokens.json"
                pool_size = pool_size or users
                accounts = Path(temporary) / "accounts.json"
                accounts.write_text(json.dumps([{"username": f"user-{i + 1}", "password": "contract-password"} for i in range(pool_size)]))
                environment = {key: value for key, value in os.environ.items()
                               if not key.startswith(("TEST_", "K6_", "SMOKE_", "PROMETHEUS_", "FIXTURE_", "PERFORMANCE_TIME_", "WORKLOAD_"))}
                url = f"http://127.0.0.1:{server.server_port}"
                environment.update(BASE_URL=url, TEST_TOKENS_FILE=str(fixture),
                                   SMOKE_VUS=str(users), SMOKE_SEATS="3", HTTP_TIMEOUT="50ms" if mode == "timeout" else "2s",
                                   SMOKE_MAX_DURATION="5s", PROMETHEUS_URL=url)
                issuance = subprocess.run([sys.executable, str(K6_ROOT / "scripts" / "issue-token.py"),
                                           "--accounts", str(accounts), "--output", str(fixture)],
                                          env=environment, cwd=K6_ROOT.parent, capture_output=True, text=True, timeout=10)
                self.assertEqual(issuance.returncode, 0, issuance.stdout + issuance.stderr)
                self.assertEqual(len(read_json(fixture)), pool_size)
                self.assertEqual(stat.S_IMODE(fixture.stat().st_mode), 0o600)
                self.assertNotIn("contract-user-", issuance.stdout + issuance.stderr)
                self.assertNotIn("contract-password", issuance.stdout + issuance.stderr)
                preparation = subprocess.run([sys.executable, str(K6_ROOT / "scripts" / "prepare-fixture.py"),
                                              "--profile", "musical-low", "--exports-file", str(Path(temporary) / "fixture.env.sh")],
                                             env=environment, cwd=K6_ROOT.parent, capture_output=True, text=True, timeout=30)
                for line in preparation.stdout.splitlines():
                    if line.startswith("Fixture manifest: "):
                        manifest_path = Path(line.removeprefix("Fixture manifest: "))
                        fixture_dir = manifest_path.parent
                        environment["FIXTURE_MANIFEST"] = str(manifest_path)
                self.assertIsNotNone(fixture_dir, preparation.stdout + preparation.stderr)
                manifest = read_json(fixture_dir / "fixture-manifest.json")
                if mode == "catalog-failure":
                    self.assertNotEqual(preparation.returncode, 0)
                    self.assertEqual(manifest["status"], "failed")
                    self.assertEqual(manifest["venue_hall_id"], 5)
                    self.assertEqual(manifest["performance_time_id"], None)
                    self.assertEqual([path for path, _ in server.events].count("/api/catalog/performances"), 1)
                    self.assertFalse((Path(temporary) / "fixture.env.sh").exists())
                    return
                self.assertEqual(preparation.returncode, 0, preparation.stdout + preparation.stderr)
                self.assertEqual(manifest["verified_seat_count"], 1000)
                self.assertEqual(manifest["initial_state"], "all-AVAILABLE")
                if mode == "stale-fixture":
                    server.seats[1]["status"] = "LOCKED"
                process = subprocess.run([sys.executable, str(K6_ROOT / "scripts" / "run-experiment.py"), "smoke"],
                                         env=environment, cwd=K6_ROOT.parent, capture_output=True, text=True, timeout=30)
                for line in process.stdout.splitlines():
                    if line.startswith("Run directory: "):
                        run_dir = Path(line.removeprefix("Run directory: "))
                self.assertIsNotNone(run_dir, process.stdout + process.stderr)
                if mode == "stale-fixture":
                    self.assertNotEqual(process.returncode, 0)
                    metadata = read_json(run_dir / "run-metadata.json")
                    self.assertEqual(metadata["status"], "fixture-preflight-failed")
                    self.assertEqual(metadata["started_at"], None)
                    self.assertFalse((run_dir / "k6-summary.json").exists())
                    self.assertFalse((run_dir / "k6-points.json").exists())
                    self.assertTrue((run_dir / "report.md").exists())
                    self.assertFalse(any(path == "/api/reservations/pre-reserve" for path, _ in server.events))
                    return
                self.assertTrue((run_dir / "k6-summary.json").exists(), process.stdout + process.stderr)
                summary = read_json(run_dir / "k6-summary.json")
                metadata = read_json(run_dir / "run-metadata.json")
                values = lambda name: summary["metrics"][name]["values"]["count"]
                self.assertEqual(server.errors, [])
                self.assertEqual(values("journey_started"), users)
                self.assertEqual(values("journey_finished"), users)
                self.assertEqual(metadata["performance_time_id"], 77)
                self.assertEqual(metadata["auth"]["mode"], "preissued-token-pool")
                self.assertEqual(metadata["auth"]["token_count"], pool_size)
                self.assertEqual(metadata["auth"]["selection"], "iteration-round-robin")
                self.assertEqual(metadata["fixture"]["initial_state"], "all-AVAILABLE")
                self.assertTrue((run_dir / "fixture-manifest.json").exists())
                self.assertIsNotNone(metadata["measurement_started_at"])
                self.assertIsNotNone(metadata["measurement_ended_at"])
                self.assertEqual(metadata["arrival_rate"], None)
                self.assertEqual(metadata["prometheus_collection_status"], "collected" if mode == "success" else "empty")
                self.assertTrue((run_dir / "report.md").exists())
                self.assertTrue((run_dir / "k6-summary.txt").exists())
                failure_values = summary["metrics"]["http_req_failed"]["values"]
                text_summary = (run_dir / "k6-summary.txt").read_text()
                failure_line = next(line for line in text_summary.splitlines() if line.startswith("http_req_failed:"))
                self.assertIn(f"failed_requests={failure_values['passes']:.3f}", failure_line)
                self.assertIn(f"expected_requests={failure_values['fails']:.3f}", failure_line)
                self.assertNotIn("passes=", failure_line)
                self.assertNotIn("fails=", failure_line)
                self.assertNotIn("http_offered", summary["metrics"])
                self.assertNotIn("contract-user-", (run_dir / "k6-summary.json").read_text())
                self.assertNotIn("contract-user-", (run_dir / "k6-points.json").read_text())
                self.assertNotIn("contract-user-", (run_dir / "run-metadata.json").read_text())
                self.assertEqual(len(server.queries), 1)
                self.assertEqual(server.queries[0]["query"], ['{__name__="actual_fixture_gauge",job="imticket-v2"}'])
                paths = [path for path, _ in server.events]
                self.assertGreater(paths.index("/actuator/prometheus"), max(i for i, path in enumerate(paths) if path.startswith("/api/") and not path.startswith("/api/v1/")))
                again = subprocess.run([sys.executable, str(K6_ROOT / "scripts" / "run-experiment.py"), "smoke"],
                                       env=environment, cwd=K6_ROOT.parent, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(again.returncode, 0)
                self.assertIn("already claimed", again.stderr)
                if mode == "success":
                    self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                    self.assertEqual(values("booking_completed"), users)
                    self.assertEqual(values("reservation_success"), users)
                    self.assertEqual(values("business_failures"), 0)
                    self.assertEqual(values("system_failures"), 0)
                    self.assertEqual(values("http_attempted"), users * 6)
                    self.assertEqual(values("http_completed"), users * 6)
                    self.assertEqual(failure_values["passes"], 0)
                    self.assertEqual(failure_values["fails"], users * 6)
                    self.assertEqual(len(set(server.keys)), users * 2)
                    for api in ("seat-map", "selection-summary", "pre-reserve", "payment-prepare", "payment-verify", "reservation-detail"):
                        latency = summary["metrics"][f"http_req_duration{{api:{api}}}"]["values"]
                        self.assertEqual(latency["count"], users)
                        self.assertIn("p(99)", latency)
                elif mode == "business":
                    self.assertNotEqual(process.returncode, 0)
                    self.assertEqual(values("business_failures"), 1)
                    self.assertEqual(values("system_failures"), 0)
                    self.assertEqual(values("http_completed"), 3)
                    self.assertEqual(values("http_attempted"), 3)
                    self.assertEqual(failure_values["passes"], 1)
                    self.assertEqual(failure_values["fails"], 2)
                    self.assertEqual(values("api_business_failures{api:pre-reserve}"), 1)
                elif mode == "system":
                    self.assertNotEqual(process.returncode, 0)
                    self.assertEqual(values("business_failures"), 0)
                    self.assertEqual(values("system_failures"), 1)
                    self.assertEqual(values("http_completed"), 3)
                    self.assertEqual(values("http_attempted"), 3)
                    self.assertEqual(failure_values["passes"], 1)
                    self.assertEqual(failure_values["fails"], 2)
                else:
                    self.assertNotEqual(process.returncode, 0)
                    self.assertEqual(values("business_failures"), 0)
                    self.assertEqual(values("system_failures"), 1)
                    self.assertEqual(values("http_completed"), 0)
                    self.assertEqual(values("http_attempted"), 1)
                    self.assertEqual(failure_values["passes"], 1)
                    self.assertEqual(failure_values["fails"], 0)
                    report = (run_dir / "report.md").read_text()
                    self.assertIn("| http_req_duration{api:payment-verify} | 0 | N/A | N/A | N/A |", report)
        finally:
            server.shutdown()
            server.server_close()
            if run_dir:
                shutil.rmtree(run_dir)
            if fixture_dir:
                shutil.rmtree(fixture_dir)

    def test_two_users_complete_all_six_http_steps(self):
        self.exercise("success", users=2)

    def test_two_vus_share_one_member_token(self):
        self.exercise("success", users=2, pool_size=1)

    def test_partial_catalog_failure_is_saved_and_not_retried(self):
        self.exercise("catalog-failure")

    def test_changed_fixture_seat_state_stops_before_load(self):
        self.exercise("stale-fixture")

    def test_conflict_is_business_failure(self):
        self.exercise("business")

    def test_5xx_is_system_failure(self):
        self.exercise("system")

    def test_timeout_is_not_completed_http(self):
        self.exercise("timeout")


if __name__ == "__main__":
    unittest.main()
