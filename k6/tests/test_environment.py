"""HTTP account lifecycle tests; fixture SQL is also verified against the real MySQL server."""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import read_json
from collect_prometheus import collect
from preparation import PreparationError, api_data
from test_accounts import prepare_accounts


class AuthHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.server.mutex:
            self.server.events.append((self.path, body["username"]))
            account = self.server.accounts.get(body["username"])
            status = 200
            if self.path == "/api/auth/register":
                if account:
                    status = 409
                else:
                    account = {**body, "id": len(self.server.accounts) + 1}
                    self.server.accounts[body["username"]] = account
                    if self.server.fail_after_register:
                        self.server.fail_after_register = False
                        status = 503
            elif self.path == "/api/auth/login":
                if not account or account["password"] != body["password"]:
                    status = 401
            else:
                status = 404
            data = {"memberId": account["id"], "accessToken": f"contract-user-{account['id']}", "role": "MEMBER"} if status == 200 else None
        payload = json.dumps({"success": status == 200, "data": data, "errorCode": None, "message": None}).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class AccountEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), AuthHandler)
        self.server.mutex = threading.Lock()
        self.server.accounts, self.server.events = {}, []
        self.server.fail_after_register = False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.temporary = tempfile.TemporaryDirectory()
        self.pool = Path(self.temporary.name) / "accounts.local.json"
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temporary.cleanup()

    def test_grow_and_reuse_exact_account_count_refreshing_tokens(self):
        _, first_tokens = prepare_accounts(self.url, 2, pool_path=self.pool)
        original = read_json(self.pool)["accounts"]
        _, second_tokens = prepare_accounts(self.url, 5, workers=3, pool_path=self.pool)
        _, third_tokens = prepare_accounts(self.url, 2, pool_path=self.pool)
        accounts = read_json(self.pool)["accounts"]
        self.assertEqual(len(self.server.accounts), 5)
        self.assertEqual([len(read_json(p)) for p in (first_tokens, second_tokens, third_tokens)], [2, 5, 2])
        self.assertEqual([a["username"] for a in accounts[:2]], [a["username"] for a in original])
        self.assertEqual(sum(path == "/api/auth/register" for path, _ in self.server.events), 5)
        self.assertEqual(sum(path == "/api/auth/login" for path, _ in self.server.events), 4)
        self.assertEqual(len({a["password"] for a in accounts}), 5)
        self.assertTrue(all(len(a["username"]) <= 80 and 8 <= len(a["password"]) <= 72 for a in accounts))
        self.assertEqual(stat.S_IMODE(self.pool.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(third_tokens.stat().st_mode), 0o600)
        self.assertFalse(first_tokens == second_tokens)

    def test_unknown_register_outcome_recovers_saved_identity_by_login(self):
        self.server.fail_after_register = True
        with self.assertRaises(PreparationError):
            prepare_accounts(self.url, 1, pool_path=self.pool)
        saved = read_json(self.pool)["accounts"][0]
        self.assertEqual(saved["state"], "registering")
        self.assertEqual(len(self.server.accounts), 1)
        self.assertEqual(list(self.pool.parent.glob("tokens-*.json")), [])
        _, tokens = prepare_accounts(self.url, 1, pool_path=self.pool)
        self.assertEqual(read_json(tokens)[0]["memberId"], 1)
        self.assertEqual(self.server.events, [("/api/auth/register", saved["username"]),
                                              ("/api/auth/login", saved["username"])])

    def test_account_pool_is_bound_to_its_server(self):
        prepare_accounts(self.url, 1, pool_path=self.pool)
        with self.assertRaisesRegex(ValueError, "BASE_URL"):
            prepare_accounts("http://localhost:9999", 1, pool_path=self.pool)
        self.assertEqual(len(self.server.events), 1)

    def test_recreated_test_db_registers_same_saved_credentials(self):
        prepare_accounts(self.url, 1, pool_path=self.pool)
        username = read_json(self.pool)["accounts"][0]["username"]
        self.server.accounts.clear()
        prepare_accounts(self.url, 1, pool_path=self.pool)
        self.assertEqual(list(self.server.accounts), [username])
        self.assertEqual([path for path, _ in self.server.events],
                         ["/api/auth/register", "/api/auth/login", "/api/auth/register"])

    def test_transport_error_preserves_reason_url_and_login_index(self):
        from urllib.error import URLError

        reason = ConnectionResetError(54, "Connection reset by peer")
        with patch("preparation.build_opener") as build_opener:
            build_opener.return_value.open.side_effect = URLError(reason)
            with self.assertRaises(PreparationError) as caught:
                api_data(self.url, "POST", "/api/auth/login", {"username": "test", "password": "secret"},
                         request_context="login_index=2901/10000")

        message = str(caught.exception)
        self.assertIn("type=URLError", message)
        self.assertIn("reason_type=ConnectionResetError", message)
        self.assertIn("Connection reset by peer", message)
        self.assertIn("url=" + self.url + "/api/auth/login", message)
        self.assertIn("login_index=2901/10000", message)
        self.assertNotIn("secret", message)

    def test_account_login_passes_pool_position_as_diagnostic_context(self):
        prepare_accounts(self.url, 1, pool_path=self.pool)
        with patch("test_accounts.api_data", side_effect=PreparationError("transport failure")) as request:
            with self.assertRaises(PreparationError):
                prepare_accounts(self.url, 1, pool_path=self.pool)
        self.assertEqual(request.call_args.kwargs["request_context"], "login_index=1/1")


class MonitoringEnvironmentTests(unittest.TestCase):
    def test_unreachable_prometheus_stops_after_one_query_and_saves_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "run-metadata.json").write_text(json.dumps({
                "measurement_started_at": "2026-10-11T00:00:00+00:00",
                "measurement_ended_at": "2026-10-11T00:00:01+00:00"}))
            with patch("collect_prometheus.fetch", side_effect=["sample_a 1\nsample_b 2\nsample_c 3\n",
                                                                 URLError("connection refused")]) as fetch:
                result = collect(directory, "http://localhost:8080", "http://localhost:9090", 5, "imticket-v2", 1)
            self.assertEqual(fetch.call_count, 2)  # App discovery and one Prometheus query.
            self.assertEqual(result["status"], "partial")
            self.assertEqual(len(result["queries"]), 1)
            self.assertEqual(result["skipped_metric_count"], 2)
            self.assertEqual(read_json(directory / "prometheus" / "collection-status.json"), result)


if __name__ == "__main__":
    unittest.main()
