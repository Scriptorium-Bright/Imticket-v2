"""Automatic accounts/tokens, fresh fixtures, smoke, and scoped server cleanup."""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from common import K6_ROOT, http_url, positive_integer, read_json
from fixture_cleanup import clean_fixture, probe_database
from metrics_tunnel import prometheus_tunnel
from test_accounts import prepare_accounts


def script_module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), K6_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("WORKLOAD_PROFILE", "musical-low"))
    parser.add_argument("--users", default=os.environ.get("TEST_USER_COUNT", "10"))
    parser.add_argument("--workers", default=os.environ.get("TEST_ACCOUNT_WORKERS", "4"))
    parser.add_argument("--accounts", type=Path)
    parser.add_argument("--repeat", default="1")
    parser.add_argument("--run-number", default="1")
    parser.add_argument("--keep-data", action="store_true", help="Keep server fixture data; skip DB cleanup")
    args = parser.parse_args()
    base_url = http_url(os.environ.get("BASE_URL"), "BASE_URL")
    count = positive_integer(args.users, "test users")
    workers = positive_integer(args.workers, "account workers", 32)
    repeats = positive_integer(args.repeat, "repeat")
    first_run = positive_integer(args.run_number, "run number")
    profiles = read_json(K6_ROOT / "config" / "profiles.json")
    if args.profile not in profiles:
        raise ValueError("Unknown profile")
    positive_integer(os.environ.get("SMOKE_VUS", "1"), "SMOKE_VUS", 2)
    positive_integer(os.environ.get("SMOKE_SEATS", "1"), "SMOKE_SEATS", 3)
    if not shutil.which("k6"):
        raise ValueError("k6 executable not found")
    version = subprocess.run(["k6", "version"], capture_output=True, text=True, check=True).stdout
    if not version.startswith("k6 v1."):
        raise ValueError("This smoke harness requires k6 1.x")
    with prometheus_tunnel():
        return execute_runs(args, base_url, count, workers, first_run, repeats, profiles)


def execute_runs(args, base_url, count, workers, first_run, repeats, profiles):
    if not args.keep_data:
        # Detect missing DB/SSH access before creating accounts/catalog data.
        probe_database()
    fixture_builder = script_module("prepare-fixture")
    runner = script_module("run-experiment")
    template = K6_ROOT / "config" / "fixture-template.json"
    fixture_builder.fixture_conditions(profiles[args.profile], read_json(template))
    for number in range(first_run, first_run + repeats):
        print(f"Automatic smoke: {args.profile}; run {number}; test members {count}", flush=True)
        pool, tokens = prepare_accounts(base_url, count, workers, args.accounts)
        os.environ.pop("TEST_ACCESS_TOKEN", None)
        os.environ.pop("PERFORMANCE_TIME_ID", None)
        os.environ.update(TEST_TOKENS_FILE=str(tokens), TEST_ACCOUNTS_FILE=str(pool), WORKLOAD_PROFILE=args.profile)
        # Use the returned manifest directly, never a potentially stale current pointer.
        try:
            fixture = fixture_builder.prepare(args.profile, str(template), 30)
        except (ValueError, OSError) as error:
            partial = getattr(error, "fixture_manifest_path", None)
            if partial and not args.keep_data:
                clean_fixture(partial, "delete")
            raise
        arguments = SimpleNamespace(kind="smoke", profile=args.profile, fixture=str(fixture),
                                    run_number=number, phase="smoke")
        try:
            result = runner.run(arguments)
        finally:
            if not args.keep_data:
                clean_fixture(fixture, "delete")
        if result:
            return result
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Automatic smoke failed: {error}", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        raise SystemExit(130)
