"""Opt-in real-server test: K6_SERVER_TESTS=1 with BASE_URL and Docker/SSH configured."""
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import K6_ROOT, read_json
from fixture_cleanup import clean_fixture, cleanup_sql, execute_sql, probe_database
from preparation import PreparationError, api_data, verify_initial_seats
from test_accounts import prepare_accounts

# Hyphenated command names are also used by the automatic orchestrator.
import importlib.util
spec = importlib.util.spec_from_file_location("auto_smoke", K6_ROOT / "scripts" / "run-smoke.py")
auto_smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto_smoke)


@unittest.skipUnless(os.environ.get("K6_SERVER_TESTS") == "1", "real-server test requires explicit opt-in")
class ServerEnvironmentTests(unittest.TestCase):
    def test_booking_reset_reuse_delete_isolation_and_transaction_rollback(self):
        base_url = os.environ["BASE_URL"]
        probe_database()
        _, tokens_path = prepare_accounts(base_url, 2)
        token = read_json(tokens_path)[0]["accessToken"]
        builder = auto_smoke.script_module("prepare-fixture")
        runner = auto_smoke.script_module("run-experiment")
        fixture_paths = []
        environment = {"TEST_ACCESS_TOKEN": "", "PERFORMANCE_TIME_ID": "",
                       "TEST_TOKENS_FILE": str(tokens_path), "SMOKE_VUS": "2", "SMOKE_SEATS": "3"}
        with patch.dict(os.environ, environment):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    for _ in range(2):
                        fixture_paths.append(builder.prepare("musical-low", str(K6_ROOT / "config" / "fixture-template.json"), 30))
                control, target = fixture_paths
                control_manifest, manifest = read_json(control), read_json(target)
                control_seats = api_data(base_url, "GET", f"/api/seats/{control_manifest['performance_time_id']}", token=token)
                args = SimpleNamespace(kind="smoke", profile="musical-low", fixture=str(target), run_number=91, phase="smoke")
                with contextlib.redirect_stdout(io.StringIO()):
                    result = runner.run(args)
                self.assertEqual(result, 0)
                live = api_data(base_url, "GET", f"/api/seats/{manifest['performance_time_id']}", token=token)
                self.assertEqual(sum(seat["status"] == "RESERVED" for seat in live), 6)
                run_id = read_json(target.parent / "measurement-claim.json")["run_id"]
                # Force a failure AFTER all DELETEs. The real MySQL connection
                # must roll back the entire transaction, preserving paid reservations.
                failing_sql = cleanup_sql(manifest, run_id).replace("COMMIT;", "INSERT INTO k6_guard VALUES (1);\nCOMMIT;")
                with self.assertRaisesRegex(ValueError, "database command failed"):
                    execute_sql(failing_sql, "K6_CLEANUP_OK")
                live_after = api_data(base_url, "GET", f"/api/seats/{manifest['performance_time_id']}", token=token)
                self.assertEqual(live_after, live)
                counts_sql = f"SELECT COUNT(*) FROM reservation r JOIN reserved_seat rs ON rs.reservation_id = r.id JOIN seat s ON s.id = rs.seat_id WHERE s.performance_time_id = {manifest['performance_time_id']};\nSELECT 'K6_QUERY_OK';\n"
                self.assertEqual(execute_sql(counts_sql, "K6_QUERY_OK").splitlines()[0], "6")
                clean_fixture(target, "reset")
                verify_initial_seats(base_url, manifest["performance_time_id"], token, manifest["conditions"])
                self.assertFalse((target.parent / "measurement-claim.json").exists())
                self.assertEqual(execute_sql(counts_sql, "K6_QUERY_OK").splitlines()[0], "0")
                args.run_number = 92
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.run(args), 0)
                clean_fixture(target, "delete")
                self.assertEqual(api_data(base_url, "GET", f"/api/seats/{manifest['performance_time_id']}", token=token), [])
                self.assertEqual(api_data(base_url, "GET", f"/api/seats/{control_manifest['performance_time_id']}", token=token), control_seats)
                self.assertEqual(read_json(target)["status"], "cleaned")
                # Simulate a failure after the real server has created the hall.
                # The saved partial manifest must still be enough to remove it.
                def fail_performance(base, method, api_path, *arguments, **keywords):
                    if api_path == "/api/catalog/performances":
                        raise PreparationError("Simulated performance preparation failure")
                    return api_data(base, method, api_path, *arguments, **keywords)

                with patch.object(builder, "api_data", fail_performance):
                    with contextlib.redirect_stdout(io.StringIO()):
                        with self.assertRaises(PreparationError) as caught:
                            builder.prepare("musical-low", str(K6_ROOT / "config" / "fixture-template.json"), 30)
                partial = caught.exception.fixture_manifest_path
                fixture_paths.append(partial)
                partial_hall = read_json(partial)["venue_hall_id"]
                clean_fixture(partial, "delete")
                output = execute_sql(f"SELECT COUNT(*) FROM venue_hall WHERE id = {partial_hall};\nSELECT 'K6_QUERY_OK';\n", "K6_QUERY_OK")
                self.assertEqual(output.splitlines()[0], "0")
            finally:
                for path in fixture_paths:
                    clean_fixture(path, "delete")


if __name__ == "__main__":
    unittest.main()
