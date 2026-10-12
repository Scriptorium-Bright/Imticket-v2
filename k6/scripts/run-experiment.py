"""Run a prepared experiment, preserve time boundaries, then collect and report."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from collect_prometheus import collect
from common import (K6_ROOT, REPO_ROOT, file_sha256, http_url, metric_values,
                    point_time_range, positive_integer, read_json, result_now, result_stamp, result_time, write_json)
from report import generate_report
from fixture_state import claim_fixture, prepared_fixture
from preparation import token_pool_from_env


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["smoke", "pilot", "journey-smoke", "baseline"])
    parser.add_argument("--profile")
    parser.add_argument("--fixture", default=os.environ.get("FIXTURE_MANIFEST"))
    parser.add_argument("--run-number", type=int, default=1)
    parser.add_argument("--repeat", type=int, default=1, help="Same selected Baseline model/profile repeated with fresh fixtures")
    parser.add_argument("--phase", choices=["smoke", "warmup"], default="smoke")
    parser.add_argument("--config", default=os.environ.get("EXPERIMENT_CONFIG"))
    parser.add_argument('--workload-model', choices=['single-attempt', 'reselection'], help='Assert the reviewed config model; does not override its behavior')
    parser.add_argument("--users", help="Explicit Pilot token pool size; overrides config")
    parser.add_argument("--accounts", type=Path, help="Existing generated account store for automatic preparation")
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument("--plan", action="store_true", help="Print Pilot settings/queries only; no server calls")
    parser.add_argument('--validate-seat-conflict', action='store_true', help='One controlled Reselection conflict; journey-smoke only, not a performance measurement')
    parser.add_argument('--harness-diagnostic', action='store_true', help='Reselection Target-VU progress trace; one diagnostic run, excluded from Baseline')
    args = parser.parse_args()
    if args.validate_seat_conflict and args.kind != 'journey-smoke':
        parser.error('--validate-seat-conflict is journey-smoke only; Baseline/Pilot settings are unchanged')
    if args.harness_diagnostic and (args.kind != 'baseline' or args.repeat != 1):
        parser.error('--harness-diagnostic requires one baseline invocation; no automatic repeats')
    if args.kind != 'smoke' and any(v == '--fixture' or v.startswith('--fixture=') for v in sys.argv[1:]):
        parser.error('Pilot always prepares fresh fixtures; --fixture is smoke-only')
    if args.kind != 'smoke' and any(v == '--phase' or v.startswith('--phase=') for v in sys.argv[1:]):
        parser.error('Pilot warm-up is controlled by config.warmup, not --phase')
    return args


def git_value(arguments):
    result = subprocess.run(["git", *arguments], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def run(args):
    if args.run_number < 1:
        raise ValueError("--run-number must be positive")
    base_url = http_url(os.environ.get("BASE_URL"), "BASE_URL")
    series_context = read_json(os.environ['K6_SERIES_CONTEXT']) if os.environ.get('K6_SERIES_CONTEXT') else None
    if series_context:
        from measurement_environment import capture_local, assert_environment
        local = capture_local(base_url)
        assert_environment(local, series_context['local_reference'])
    pilot = args.kind in {'pilot', 'baseline', 'journey-smoke'}
    config = args.pilot_settings if pilot else None
    validation = config.get('validationScenario') if pilot else None
    if pilot:
        from pilot_config import execution_for_phase
    execution = execution_for_phase(config, args.phase) if pilot else None
    fixture_path, fixture = prepared_fixture(args.fixture, base_url, args.profile)
    args.profile = args.profile or fixture["profile"]
    performance_time_id = fixture["performance_time_id"]
    vus = execution.get('preAllocatedVUs', execution.get('vus')) if pilot else positive_integer(os.environ.get("SMOKE_VUS", "1"), "SMOKE_VUS", 2)
    seats = None if pilot else positive_integer(os.environ.get("SMOKE_SEATS", "1"), "SMOKE_SEATS", 3)
    token_path = os.environ.get("TEST_TOKENS_FILE")
    pool = token_pool_from_env()
    if token_path:
        token_path = str(Path(token_path).expanduser().resolve(strict=True))
    if pilot:
        from pilot_config import token_window
        if not token_path or os.environ.get('TEST_ACCESS_TOKEN'):
            raise ValueError('Pilot requires TEST_TOKENS_FILE only')
        token_validity = token_window(pool, config, args.phase)
    profiles = read_json(K6_ROOT / "config" / "profiles.json")
    if args.profile != "smoke" and args.profile not in profiles:
        raise ValueError("Unknown --profile; see k6/config/profiles.json")
    profile = profiles.get(args.profile)
    if profile is None or profile["seatCount"] != fixture["conditions"]["seatCount"]:
        raise ValueError("Fixture seat count no longer matches its profile; prepare a new fixture")
    if validation:
        profile = {**profile, **validation['profileOverrides']}
    prometheus_url = os.environ.get("PROMETHEUS_URL")
    if prometheus_url:
        prometheus_url = http_url(prometheus_url, "PROMETHEUS_URL")
    step = config['prometheus']['stepSeconds'] if pilot else positive_integer(os.environ.get("PROMETHEUS_STEP_SECONDS", "5"), "PROMETHEUS_STEP_SECONDS")
    timeout = config['prometheus']['queryTimeoutSeconds'] if pilot else positive_integer(os.environ.get("PROMETHEUS_TIMEOUT_SECONDS", "10"), "PROMETHEUS_TIMEOUT_SECONDS")
    job = config['prometheus']['job'] if pilot else os.environ.get("PROMETHEUS_JOB", "imticket-v2")
    if not job or "\n" in job:
        raise ValueError("Invalid PROMETHEUS_JOB")
    k6_binary = shutil.which("k6")
    if not k6_binary:
        raise ValueError("k6 executable not found on PATH")
    version = subprocess.run([k6_binary, "version"], capture_output=True, text=True, check=True).stdout.strip()
    # Summary/report target the verified legacy schema, not the new k6 2.x schema.
    if not version.startswith("k6 v1."):
        raise ValueError("This harness supports k6 1.x (verified with 1.6.1)")
    timestamp = result_stamp()
    result_kind = 'reselection-validation' if validation else args.kind
    target_vu_run = pilot and config.get('workloadAxis') == 'target-vus'
    target_vus = config['measurement']['vus'] if target_vu_run else None
    diagnostic = bool(getattr(args, 'harness_diagnostic', False) and args.phase == 'baseline')
    run_id = (f"{timestamp}_{args.profile}_target-vu-{target_vus}_{config['workloadModel']}{'_warmup' if args.phase == 'warmup' else ''}_run-{args.run_number:02d}" if target_vu_run
              else f"{timestamp}_{args.profile}_{config['workloadModel']}_{result_kind}{'_warmup' if args.phase == 'warmup' else ''}_run-{args.run_number:02d}" if pilot
              else f"{timestamp}_{args.profile}_run-{args.run_number}")
    if diagnostic:
        run_id = run_id.replace(f'_run-{args.run_number:02d}', f'_harness-diagnostic_run-{args.run_number:02d}')
    if series_context:
        suffix = 'pf' if series_context['category'] == 'Preflight' else 'final'
        run_id = run_id.replace(f'_run-{args.run_number:02d}', f'_{suffix}_run-{args.run_number:02d}')
    result_root = K6_ROOT / "results"
    result_root.mkdir(exist_ok=True)
    run_dir = result_root / run_id
    run_dir.mkdir(mode=0o700)
    paths = [path for path in K6_ROOT.rglob("*") if path.is_file()
             and path.suffix in {".js", ".py", ".json"}
             and "results" not in path.parts and "fixtures" not in path.parts
             and ".local." not in path.name]
    metadata = {
        "schema_version": 1, "run_id": run_id, "profile": args.profile, "time_zone": "Asia/Seoul",
        "workloadModel": config['workloadModel'] if pilot else 'single-attempt',
        "workloadAxis": config.get('workloadAxis', 'iteration-arrival-rate') if pilot else 'smoke',
        "iteration_meaning": ('one user booking journey per target VU' if target_vu_run else
                              'one user booking journey' if pilot and config['workloadModel'] == 'reselection' else 'one booking attempt'),
        "run_number": args.run_number, "test_kind": args.kind, "phase": args.phase,
        "status": "preflight", "started_at": None, "ended_at": None,
        "measurement_started_at": None, "measurement_ended_at": None,
        "base_url": base_url, "performance_time_id": performance_time_id,
        "seat_count": profile.get("seatCount") if profile else None,
        "observed_seat_count": None, "arrival_rate": None,
        "arrival_rate_unit": "journey-starts/s", "arrival_pattern": None,
        "peak_journey_arrival_rate": profile.get("peakJourneyArrivalRate") if profile else None,
        "reference_attemptors": profile.get("referenceAttemptors") if profile else None,
        "hot_seat_ratio": profile.get("hotSeatRatio") if profile else None,
        "hot_traffic_ratio": profile.get("hotTrafficRatio") if profile else None,
        "seat_selection_ratio": profile.get("seatSelectionRatio") if profile else None,
        "profile_settings": profile, "smoke_seats_per_journey": seats,
        "execution": execution if pilot else {"executor": "per-vu-iterations", "vus": vus, "iterations": 1},
        "auth": {"mode": "preissued-token-pool", "token_count": len(pool),
                 "selection": "vu-id-to-unique-member" if target_vu_run and args.phase == 'baseline' else "iteration-round-robin",
                 "vu_member_mapping": "one-to-one" if target_vu_run and args.phase == 'baseline' else "none",
                 "declared_distinct_members": len({u["memberId"] for u in pool}) if all("memberId" in u for u in pool) else None,
                 "fixture_sha256": file_sha256(token_path) if token_path else None},
        "fixture": {"manifest": str(fixture_path), "manifest_sha256": file_sha256(fixture_path),
                    "fixture_id": fixture["fixture_id"], "conditions": fixture["conditions"],
                    "prepared_started_at": result_time(fixture["prepared_started_at"]),
                    "prepared_ended_at": result_time(fixture["prepared_ended_at"]),
                    "initial_state": fixture["initial_state"],
                    "initial_seats_sha256": fixture["initial_seats_sha256"]},
        "http_timeout": config['requestTimeout'] if pilot else os.environ.get("HTTP_TIMEOUT") or "k6 default",
        "prometheus": {"url": prometheus_url, "job": job, "step_seconds": step, "timeout_seconds": timeout},
        "git_commit": git_value(["rev-parse", "HEAD"]),
        "git_branch": git_value(["branch", "--show-current"]),
        "working_tree_dirty": bool(git_value(["status", "--porcelain"])),
        "source_sha256": {str(path.relative_to(K6_ROOT)): file_sha256(path) for path in sorted(paths)},
        "k6_version": version, "k6_exit_code": None,
    }
    if pilot:
        from pilot_config import seconds
        arrival = execution['executor'] == 'ramping-arrival-rate'
        arrival = execution['executor'] == 'ramping-arrival-rate'
        metadata.update(schema_version=3, configured_stages=execution.get('stages') if arrival else None,
                        configured_start_rate=execution.get('startRate') if arrival else None,
                        configured_time_unit=execution.get('timeUnit'),
                        configured_peak_iteration_arrival_rate=(max([execution['startRate'], *[s['target'] for s in execution['stages']]]) / seconds(execution['timeUnit'])) if arrival else None,
                        arrival_rate_unit='iterations-started/s' if arrival else 'not-applicable',
                        pre_allocated_vus=execution.get('preAllocatedVUs'), max_vus=execution.get('maxVUs', execution.get('vus')),
                        graceful_stop=execution['gracefulStop'], warmup=config['warmup'], warmup_run=getattr(args, 'warmup_run', None),
                        random_seed=config['randomSeed'], pilot_config_source=str(args.config_path),
                        pilot_config_source_sha256=file_sha256(args.config_path), token_pool_size=len(pool))
        if target_vu_run:
            metadata.update(configured_target_vus=target_vus, target_users=target_vus,
                            actual_target_vus=execution.get('vus') if args.phase == 'baseline' else None,
                            iteration_meaning='one user booking journey per target VU' if args.phase == 'baseline' else 'warm-up iteration')
            metadata['arrival_rate_unit'] = 'not-applicable; target-vus workload axis'
            metadata['peak_journey_arrival_rate'] = None
            metadata['reference_attemptors'] = None
            if args.phase == 'baseline':
                metadata.pop('arrival_rate', None)
                metadata.pop('arrival_pattern', None)
                metadata.pop('configured_stages', None)
                metadata.pop('configured_start_rate', None)
                metadata.pop('configured_time_unit', None)
                metadata.pop('configured_peak_iteration_arrival_rate', None)
            metadata['auth']['token_pool_size'] = len(pool)
            metadata['auth']['vu_member_mapping'] = 'one-to-one' if args.phase == 'baseline' else 'warm-up-only pool'
        else:
            metadata['auth']['token_pool_size'] = len(pool)
        metadata.update(reselection=config.get('reselection'), inventory_mode=config['inventory']['mode'])
        if validation:
            metadata.update(validation_scenario=validation, performance_measurement=False,
                            result_kind='reselection-validation')
        metadata['auth']['validity'] = token_validity
        metadata['prometheus']['settings'] = config['prometheus']
        write_json(run_dir / 'pilot-config.json', config)
    if series_context:
        metadata.update(measurement_category=series_context['category'], measurement_series_id=series_context['series_id'],
                        reproducibility_required=True, harness_git_sha=local['identity']['git']['sha'],
                        harness_dirty=local['identity']['git']['dirty'],
                        application_git_sha=series_context['server_reference'].get('server_checkout_commit'))
        write_json(run_dir / 'measurement-environment.json', {'local': local, 'server': series_context['server_reference']})
    if os.environ.get('K6_AUTH_OBSERVATION_FILE'):
        observation = read_json(os.environ['K6_AUTH_OBSERVATION_FILE'])
        metadata['auth_preparation'] = observation
        write_json(run_dir / 'auth-preparation.json', observation)
    if diagnostic:
        metadata.update(measurement_role='harness-diagnostic', performance_measurement=False,
                        harness_diagnostic={'enabled': True, 'identity_storage': 'raw point metadata; not indexed tags',
                            'added_output': 'phase markers and metadata; not performance-comparable',
                            'guard_or_duration_change': False})
    write_json(run_dir / "run-metadata.json", metadata)
    write_json(run_dir / "fixture-manifest.json", fixture)
    shutil.copyfile(fixture_path.parent / "initial-seats.json", run_dir / "fixture-initial-seats.json")
    print(f"Run directory: {run_dir}", flush=True)
    # Ignore personal k6 config/option overrides so the saved conditions match the run.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("K6_")}
    environment.pop('HARNESS_DIAGNOSTIC', None)
    if diagnostic:
        environment['HARNESS_DIAGNOSTIC'] = '1'
    # Credentials are not needed by the preissued-token test process.
    environment.pop("TEST_USERNAME", None)
    environment.pop("TEST_PASSWORD", None)
    environment.update({"BASE_URL": base_url, "PERFORMANCE_TIME_ID": str(performance_time_id),
                        "WORKLOAD_PROFILE": args.profile, "RUN_DIR": str(run_dir), "RUN_ID": run_id,
                        "RUN_PHASE": args.phase, "RUN_KIND": args.kind, "SMOKE_VUS": str(vus), "SMOKE_SEATS": str(seats),
                        "K6_NO_USAGE_REPORT": "true", "K6_NEW_MACHINE_READABLE_SUMMARY": "false"})
    if token_path:
        environment["TEST_TOKENS_FILE"] = token_path
    if pilot:
        environment.update(PILOT_CONFIG_FILE=str(run_dir / 'pilot-config.json'), HTTP_TIMEOUT=config['requestTimeout'])
        environment.pop('TEST_ACCESS_TOKEN', None)
    entrypoint = K6_ROOT / ('reselection-validation.js' if validation else 'pilot.js' if pilot else 'smoke.js')
    empty_config = run_dir / "k6-options.json"
    write_json(empty_config, {})
    prefix = [k6_binary, "--config", str(empty_config), "--no-color"]
    # Runs only JS init; no HTTP traffic. Provides a complete failure artifact on bad inputs.
    inspection = subprocess.run([*prefix, "inspect", "--include-system-env-vars=true", str(entrypoint)],
                                env=environment, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    (run_dir / "preflight.log").write_text(inspection.stderr, encoding="utf-8")
    if inspection.returncode:
        metadata.update(status="preflight-failed", k6_exit_code=inspection.returncode)
        write_json(run_dir / "run-metadata.json", metadata)
        generate_report(run_dir)
        print(f"Preflight failed; see {run_dir / 'preflight.log'}", file=sys.stderr)
        return inspection.returncode
    effective = json.loads(inspection.stdout)
    write_json(run_dir / "k6-effective-options.json", effective)
    metadata["effective_execution"] = effective.get("scenarios", {}).get(args.phase if pilot else 'smoke', metadata["execution"])
    try:
        metadata["fixture"]["claim"] = claim_fixture(fixture_path, fixture, base_url, pool[0]["accessToken"], run_id)
    except (ValueError, OSError) as error:
        metadata.update(status="fixture-preflight-failed", runner_error=str(error))
        write_json(run_dir / "run-metadata.json", metadata)
        generate_report(run_dir)
        print(f"Fixture initial state check failed; see {run_dir / 'run-metadata.json'}", file=sys.stderr)
        return 2
    command = [*prefix, "run", "--quiet", "--out", f"json={run_dir / 'k6-points.json'}", str(entrypoint)]
    process = None
    load_generator_sampler = None
    if target_vu_run and args.phase == 'baseline':
        from load_generator import LoadGeneratorSampler
        load_generator_sampler = LoadGeneratorSampler(run_dir)
    metadata.update(status="running", started_at=result_now())
    write_json(run_dir / "run-metadata.json", metadata)
    monotonic_start = time.monotonic()
    try:
        with (run_dir / "k6.log").open("w", encoding="utf-8") as log:
            if load_generator_sampler:
                load_generator_sampler.sample('before-workload')
            process = subprocess.Popen(command, env=environment, cwd=REPO_ROOT,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if load_generator_sampler:
                load_generator_sampler.start(process.pid)
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            exit_code = process.wait()
    except KeyboardInterrupt:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        exit_code = 130
    except OSError:
        exit_code = 2
        metadata["runner_error"] = "k6-process-io-error"
        if process is not None:
            process.terminate()
            process.wait()
    finally:
        if load_generator_sampler:
            load_generator_sampler.finish()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        metadata["ended_at"] = result_now()
        metadata["process_elapsed_seconds"] = time.monotonic() - monotonic_start
    metadata.update(k6_exit_code=exit_code, status=("completed" if pilot else "passed") if exit_code == 0 else "failed")
    # The k6 process has exited. Persist this boundary before post-processing so
    # fixture cleanup can distinguish a stopped run from an active measurement.
    metadata['status'] = 'postprocessing'
    write_json(run_dir / 'run-metadata.json', metadata)
    try:
        start, end = point_time_range(run_dir / "k6-points.json")
        metadata.update(measurement_started_at=start, measurement_ended_at=end)
    except (ValueError, OSError):
        metadata["point_time_range_status"] = "unreadable"
    summary_path = run_dir / "k6-summary.json"
    if summary_path.exists():
        summary = read_json(summary_path)
        metadata["observed_seat_count"] = metric_values(summary, "observed_seat_count").get("value")
        if profile is None:
            metadata["seat_count"] = metadata["observed_seat_count"]
        metadata["actual_journey_starts"] = metric_values(summary, "journey_started").get("count")
        metadata["actual_journey_finishes"] = metric_values(summary, "journey_finished").get("count")
    else:
        metadata["summary_status"] = "missing"
    if pilot and target_vu_run and args.phase == 'baseline':
        from seat_inventory import collect_inventory
        inventory = collect_inventory(run_dir, fixture, base_url, pool[0]['accessToken'], config['inventory']['mode'])
        metadata['seat_inventory'] = inventory
        from vu_baseline_observations import analyze as analyze_vu_baseline
        observations = analyze_vu_baseline(run_dir, metadata, config)
        write_json(run_dir / 'vu-baseline-observations.json', observations)
    elif pilot:
        from pilot_observations import analyze
        observations = analyze(run_dir, metadata, config)
        if validation:
            from reselection_validation import audit_validation
            validation_result = audit_validation(run_dir, observations)
            observations['validation']['checks']['controlled_reselection_path'] = validation_result
            if validation_result['status'] != 'passed':
                observations['validation']['status'] = 'failed'
            metadata['reselection_validation'] = {'status': validation_result['status'], 'artifact': 'reselection-validation.json'}
        if args.phase != 'warmup':
            from seat_inventory import collect_inventory
            inventory = collect_inventory(run_dir, fixture, base_url, pool[0]['accessToken'], config['inventory']['mode'])
            metadata['seat_inventory'] = inventory
            verified = inventory['status'] == 'verified'
            observations['validation']['checks']['seat_inventory'] = {
                'status': 'passed' if verified else 'failed' if inventory['status'] == 'failed' else 'not-verified',
                'inventory_status': inventory['status'], 'oversell_invariant': inventory['oversell_invariant']}
            if inventory['status'] == 'failed':
                observations['validation']['status'] = 'failed'
            elif not verified and observations['validation']['status'] == 'passed':
                observations['validation']['status'] = 'needs-review'
            write_json(run_dir / 'pilot-observations.json', observations)
    write_json(run_dir / "run-metadata.json", metadata)
    # Reporting/monitoring start only after the k6 process has exited.
    if pilot and args.phase in {'warmup', 'journey-smoke'}:
        output = run_dir / 'prometheus'
        output.mkdir(exist_ok=True)
        status = {'status': 'excluded-' + args.phase, 'queries': [], 'errors': []}
        write_json(output / 'collection-status.json', status)
    elif pilot:
        from pilot_prometheus import collect_pilot
        status = collect_pilot(run_dir, base_url, prometheus_url, config['prometheus'], metadata)
        metadata['prometheus_query_range'] = {key: status.get(key) for key in
                                             ('measurement_start_epoch', 'measurement_end_epoch', 'query_start_epoch', 'query_end_epoch', 'step_seconds')}
        check = {'status': 'passed' if status['status'] == 'collected' else 'not-verified', 'collection_status': status['status']}
        if target_vu_run and args.phase == 'baseline':
            observations['validation']['checks']['prometheus_collection'] = {
                'status': 'passed' if status['status'] == 'collected' else 'failed',
                'details': {'collection_status': status['status'], 'errors': status.get('errors', [])}}
            observations['validation']['status'] = ('failed' if any(v['status'] == 'failed'
                for v in observations['validation']['checks'].values()) else 'passed')
            metadata['vu_baseline_validation'] = observations['validation']
            write_json(run_dir / 'vu-baseline-observations.json', observations)
        else:
            observations['validation']['checks']['prometheus_collection'] = check
            if check['status'] != 'passed' and observations['validation']['status'] == 'passed':
                observations['validation']['status'] = 'needs-review'
            write_json(run_dir / 'pilot-observations.json', observations)
    else:
        status = collect(run_dir, base_url, prometheus_url, step, job, timeout)
    metadata["prometheus_collection_status"] = status["status"]
    metadata['measurement_completed_at'] = result_now()
    metadata['status'] = 'completed' if exit_code == 0 else 'failed'
    write_json(run_dir / "run-metadata.json", metadata)
    if diagnostic:
        from harness_observations import save_diagnostic
        save_diagnostic(run_dir)
    generate_report(run_dir)
    print(f"Report: {run_dir / 'report.md'}")
    print(f"Prometheus collection: {status['status']}")
    if pilot:
        validation_status = (metadata.get('vu_baseline_validation') or metadata.get('pilot_validation') or {}).get('status', 'not-run')
        print(f"Workload validation: {validation_status}")
    else:
        validation_status = 'not-run'
    return exit_code or (1 if pilot and validation_status == 'failed' else 0)


if __name__ == "__main__":
    try:
        arguments = cli()
        if arguments.kind in {'pilot', 'baseline', 'journey-smoke'}:
            if arguments.fixture:
                # The inherited convenience pointer is intentionally not used by Pilot.
                arguments.fixture = None
            from pilot_runner import execute_pilot
            raise SystemExit(execute_pilot(arguments))
        arguments.profile = arguments.profile or os.environ.get('WORKLOAD_PROFILE')
        raise SystemExit(run(arguments))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Cannot run experiment: {error}", file=sys.stderr)
        raise SystemExit(2)
