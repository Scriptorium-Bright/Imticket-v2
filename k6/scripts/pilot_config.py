"""Explicit Pilot settings; validation and plan generation make no server calls."""
import base64
import copy
import json
import math
import re
import time
from pathlib import Path

from common import K6_ROOT, read_json


def integer(value, name, minimum=0, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def seconds(value, name="duration", allow_zero=False):
    if not isinstance(value, str) or not re.fullmatch(r"(?:\d+(?:\.\d+)?(?:ms|s|m|h))+", value):
        raise ValueError(f"{name} must be a k6 duration (ms/s/m/h)")
    units = {"ms": .001, "s": 1, "m": 60, "h": 3600}
    total = sum(float(n) * units[u] for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value))
    if not math.isfinite(total) or total < 0 or (total == 0 and not allow_zero):
        raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'}")
    return total


def execution(value, name, allow_pending=False):
    required = {"executor", "timeUnit", "startRate", "stages", "preAllocatedVUs", "maxVUs", "gracefulStop"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"{name} must contain exactly: {', '.join(sorted(required))}")
    if value["executor"] != "ramping-arrival-rate":
        raise ValueError(f"{name} requires ramping-arrival-rate")
    if allow_pending and any(value[k] is None for k in ('startRate', 'stages', 'preAllocatedVUs', 'maxVUs')):
        # Validate supplied fields too; null is a review decision, never a default inherited from another model.
        for key in ('startRate', 'preAllocatedVUs', 'maxVUs'):
            if value[key] is not None:
                integer(value[key], name + '.' + key, 0 if key == 'startRate' else 1)
        seconds(value['timeUnit'], name + '.timeUnit')
        seconds(value['gracefulStop'], name + '.gracefulStop', allow_zero=True)
        if value['stages'] is not None:
            probe = {**value, 'startRate': value['startRate'] or 0, 'preAllocatedVUs': 1, 'maxVUs': 1}
            execution(probe, name)
        return value
    seconds(value["timeUnit"], name + ".timeUnit")
    seconds(value["gracefulStop"], name + ".gracefulStop", allow_zero=True)
    integer(value["startRate"], name + ".startRate")
    integer(value["preAllocatedVUs"], name + ".preAllocatedVUs", 1)
    integer(value["maxVUs"], name + ".maxVUs", value["preAllocatedVUs"])
    if not isinstance(value["stages"], list) or not value["stages"]:
        raise ValueError(f"{name}.stages must be nonempty")
    for stage in value["stages"]:
        if not isinstance(stage, dict) or set(stage) != {"duration", "target"}:
            raise ValueError(f"{name} stages require duration and target only")
        seconds(stage["duration"], name + ".stage.duration")
        integer(stage["target"], name + ".stage.target")
    if max([value["startRate"], *[s["target"] for s in value["stages"]]]) == 0:
        raise ValueError(f"{name} must schedule some arrivals")
    return value


def target_vu_execution(value, name, target_users):
    required = {"executor", "vus", "iterations", "maxDuration", "gracefulStop"}
    if not isinstance(value, dict) or set(value) != required or value["executor"] != "per-vu-iterations":
        raise ValueError(f"{name} must explicitly use per-vu-iterations")
    if value["vus"] is None:
        value["vus"] = target_users
    integer(value["vus"], name + ".vus", 1)
    if value["vus"] != target_users:
        raise ValueError(f"{name}.vus must equal profile targetUsers ({target_users})")
    integer(value["iterations"], name + ".iterations", 1)
    if value["iterations"] != 1:
        raise ValueError(f"{name}.iterations must be 1 for one user journey per VU")
    seconds(value["maxDuration"], name + ".maxDuration")
    seconds(value["gracefulStop"], name + ".gracefulStop", allow_zero=True)
    return value


def validate_target_vu_token_members(pool, target_users):
    if len(pool) != target_users:
        raise ValueError('Target VU token pool size must equal profile targetUsers')
    members = [entry.get('memberId') for entry in pool]
    if any(type(member) is not int or member < 1 for member in members) or len(set(members)) != target_users:
        raise ValueError('Target VU token pool must contain one unique Member per VU')


def execution_for_phase(config, phase):
    return config['warmup']['execution'] if phase == 'warmup' else config['smoke'] if phase == 'journey-smoke' else config['measurement']


def pending_settings(config, kind='pilot'):
    pending = ['auth.tokenPoolSize'] if config['auth']['tokenPoolSize'] is None else []
    if kind != 'journey-smoke':
        sections = [] if config.get('workloadAxis') == 'target-vus' else [('measurement', config['measurement'])]
        if config['warmup']['enabled']:
            sections.append(('warmup.execution', config['warmup']['execution']))
        for name, value in sections:
            pending.extend(name + '.' + k for k in ('startRate', 'stages', 'preAllocatedVUs', 'maxVUs') if value[k] is None)
    return pending


def validate_reselection(policy):
    if not isinstance(policy, dict) or set(policy) != {'selectionPolicy', 'thinkTime', 'abandonment', 'safetyGuard'}:
        raise ValueError('reselection must contain selectionPolicy, thinkTime, abandonment, safetyGuard')
    if policy['selectionPolicy'] != 'preferred-region-then-other-then-all':
        raise ValueError('Unsupported reselection selectionPolicy')
    guard = policy['safetyGuard']
    if not isinstance(guard, dict) or set(guard) != {'maxJourneyDuration'}:
        raise ValueError('safetyGuard requires maxJourneyDuration; it is not a user retry limit')
    seconds(guard['maxJourneyDuration'], 'safetyGuard.maxJourneyDuration')
    think = policy['thinkTime']
    if not isinstance(think, dict) or set(think) != {'enabled', 'distribution', 'duration', 'min', 'max'} or type(think['enabled']) is not bool:
        raise ValueError('thinkTime fields must match reselection.pilot.example.json')
    if think['enabled']:
        if think['distribution'] == 'constant':
            seconds(think['duration'], 'thinkTime.duration', allow_zero=True)
        elif think['distribution'] == 'uniform':
            if seconds(think['min'], 'thinkTime.min', allow_zero=True) > seconds(think['max'], 'thinkTime.max', allow_zero=True):
                raise ValueError('thinkTime.min must be <= max')
        else:
            raise ValueError('thinkTime distribution must be constant or uniform')
    abandon = policy['abandonment']
    if not isinstance(abandon, dict) or set(abandon) != {'enabled', 'maxReselections', 'maxDuration'} or type(abandon['enabled']) is not bool:
        raise ValueError('abandonment fields must match reselection.pilot.example.json')
    if abandon['maxReselections'] is not None:
        integer(abandon['maxReselections'], 'abandonment.maxReselections')
    if abandon['maxDuration'] is not None:
        seconds(abandon['maxDuration'], 'abandonment.maxDuration')
    if abandon['enabled'] and abandon['maxDuration'] is None and abandon['maxReselections'] is None:
        raise ValueError('Enabled user abandonment needs an explicit limit')


def smoke_execution(value):
    if not isinstance(value, dict) or set(value) != {'executor', 'vus', 'iterations', 'maxDuration', 'gracefulStop'}:
        raise ValueError('smoke fields must match reselection.pilot.example.json')
    if value['executor'] != 'per-vu-iterations':
        raise ValueError('journey-smoke uses per-vu-iterations')
    integer(value['vus'], 'smoke.vus', 1, 2)
    integer(value['iterations'], 'smoke.iterations', 1, 2)
    seconds(value['maxDuration'], 'smoke.maxDuration')
    seconds(value['gracefulStop'], 'smoke.gracefulStop', allow_zero=True)


def stage_schedule(settings):
    unit = seconds(settings["timeUnit"])
    previous = settings["startRate"] / unit
    offset = 0.0
    result = []
    for index, stage in enumerate(settings["stages"]):
        duration = seconds(stage["duration"])
        target = stage["target"] / unit
        result.append({"stage": index + 1, "start_offset_seconds": offset,
                       "end_offset_seconds": offset + duration, "duration_seconds": duration,
                       "configured_start_rate_per_second": previous,
                       "configured_end_rate_per_second": target,
                       "configured_expected_starts": (previous + target) * duration / 2})
        previous, offset = target, offset + duration
    return result


def configured_starts(schedule, start, end):
    """Integrate a linear schedule over an arbitrary time bin, including zero-rate drain."""
    result = 0.0
    for stage in schedule:
        a = max(start, stage["start_offset_seconds"])
        b = min(end, stage["end_offset_seconds"])
        if b <= a:
            continue
        slope = (stage["configured_end_rate_per_second"] - stage["configured_start_rate_per_second"]) / stage["duration_seconds"]
        ra = stage["configured_start_rate_per_second"] + slope * (a - stage["start_offset_seconds"])
        rb = stage["configured_start_rate_per_second"] + slope * (b - stage["start_offset_seconds"])
        result += (ra + rb) * (b - a) / 2
    return result


def discrete_arrivals(schedule):
    """k6 1.x cal() dispatches at integer integrated areas 1..floor(total).

    The last possible slot can precede the nominal duration. These are configured
    slots, NOT observed starts or a guarantee that a deadline-boundary slot runs.
    """
    total = sum(s['configured_expected_starts'] for s in schedule)
    slots = math.floor(total + 1e-9)
    if slots == 0:
        return {'configured_slots': 0, 'last_slot_offset_seconds': None}
    before = 0.0
    for stage in schedule:
        area = slots - before
        if area <= stage['configured_expected_starts'] + 1e-9:
            rate = stage['configured_start_rate_per_second']
            slope = (stage['configured_end_rate_per_second'] - rate) / stage['duration_seconds']
            root = math.sqrt(max(0, rate * rate + 2 * slope * area))
            offset = 2 * area / (rate + root)
            return {'configured_slots': slots, 'last_slot_offset_seconds': stage['start_offset_seconds'] + offset}
        before += stage['configured_expected_starts']
    raise ValueError('Cannot determine configured discrete arrival slots')


def load_config(path, profile=None, users=None, allow_pending=False, kind='pilot'):
    if not path:
        raise ValueError("Set EXPERIMENT_CONFIG or --config; review k6/config/pilot.example.json first")
    path = Path(path).expanduser().resolve(strict=True)
    config = copy.deepcopy(read_json(path))
    required = {"schemaVersion", "profile", "fixtureTemplate", "randomSeed", "requestTimeout",
                "auth", "measurement", "warmup", "prometheus", "validation"}
    optional = {'workloadModel', 'reselection', 'smoke', 'inventory', 'workloadAxis'}
    if (not isinstance(config, dict) or not required <= set(config) or set(config) - required - optional
            or type(config["schemaVersion"]) is not int or config["schemaVersion"] != 1):
        raise ValueError("Pilot config must use the fields and schemaVersion of pilot.example.json")
    config.setdefault('workloadModel', 'single-attempt')
    config.setdefault('inventory', {'mode': 'api-only'})  # legacy files remain readable; no fabricated DB proof
    config.setdefault('workloadAxis', 'iteration-arrival-rate')
    if config['workloadModel'] not in {'single-attempt', 'reselection'}:
        raise ValueError('workloadModel must be single-attempt or reselection')
    if config['workloadAxis'] not in {'iteration-arrival-rate', 'target-vus'}:
        raise ValueError('workloadAxis must be iteration-arrival-rate or target-vus')
    if config['inventory'] not in ({'mode': 'db'}, {'mode': 'api-only'}):
        raise ValueError('inventory.mode must be db or api-only (oversell unverified)')
    if config['workloadModel'] == 'reselection':
        validate_reselection(config.get('reselection'))
    if 'smoke' in config:
        smoke_execution(config['smoke'])
    if kind == 'journey-smoke' and (config['workloadModel'] != 'reselection' or 'smoke' not in config):
        raise ValueError('journey-smoke requires a reselection config with smoke execution')
    if profile:
        config["profile"] = profile
    profiles = read_json(K6_ROOT / "config" / "profiles.json")
    if not isinstance(config["profile"], str) or config["profile"] not in profiles or profiles[config["profile"]]["seatCount"] not in {1000, 1500}:
        raise ValueError("Select a configured 1000/1500-seat profile")
    profile_settings = profiles[config['profile']]
    integer(config["randomSeed"], "randomSeed", 0, 2**32 - 1)
    seconds(config["requestTimeout"], "requestTimeout")
    if not isinstance(config["fixtureTemplate"], str) or not config["fixtureTemplate"]:
        raise ValueError('fixtureTemplate must be a path to an existing template')
    template = Path(config["fixtureTemplate"]).expanduser()
    config["fixtureTemplate"] = str((template if template.is_absolute() else K6_ROOT / template).resolve(strict=True))
    auth = config["auth"]
    if not isinstance(auth, dict) or set(auth) != {"mode", "tokenPoolSize", "accountWorkers", "tokenValidityMarginSeconds"}:
        raise ValueError("auth fields must match pilot.example.json")
    if not isinstance(auth["mode"], str) or auth["mode"] not in {"automatic", "preissued"}:
        raise ValueError("auth.mode must be automatic or preissued")
    if users is not None:
        if not re.fullmatch(r"[1-9]\d*", str(users)):
            raise ValueError("--users must be a positive integer")
        auth["tokenPoolSize"] = int(users)
    if config['workloadAxis'] == 'target-vus':
        target_users = integer(profile_settings.get('targetUsers'), 'profile.targetUsers', 1)
        if auth['tokenPoolSize'] is None:
            auth['tokenPoolSize'] = target_users
        if auth['tokenPoolSize'] != target_users:
            raise ValueError('VU Baseline tokenPoolSize must equal profile targetUsers')
    if auth["tokenPoolSize"] is None and not allow_pending:
        raise ValueError(
            "Pilot token pool size is unset (auth.tokenPoolSize=null). "
            "Set auth.tokenPoolSize in the config or pass --users <count>. "
            "In automatic mode, accounts and JWTs are prepared before measurement."
        )
    if auth["tokenPoolSize"] is not None:
        integer(auth["tokenPoolSize"], "auth.tokenPoolSize (set config or --users)", 1)
    integer(auth["accountWorkers"], "auth.accountWorkers", 1, 32)
    integer(auth["tokenValidityMarginSeconds"], "auth.tokenValidityMarginSeconds")
    if config['workloadAxis'] == 'target-vus':
        target_vu_execution(config['measurement'], 'measurement', profile_settings['targetUsers'])
    else:
        execution(config["measurement"], "measurement", allow_pending=True)
    warmup = config["warmup"]
    if not isinstance(warmup, dict) or set(warmup) not in ({"enabled", "execution"}, {"enabled", "execution", "tokenPoolSize"}) or type(warmup["enabled"]) is not bool:
        raise ValueError("warmup requires boolean enabled and execution")
    if 'tokenPoolSize' in warmup:
        integer(warmup['tokenPoolSize'], 'warmup.tokenPoolSize', 1)
    if warmup["enabled"]:
        execution(warmup["execution"], "warmup.execution", allow_pending=True)
    elif warmup["execution"] is not None:
        execution(warmup["execution"], "warmup.execution", allow_pending=True)
    if not allow_pending and pending_settings(config, kind):
        raise ValueError('Review and set explicit settings before preparation: ' + ', '.join(pending_settings(config, kind)))
    if not allow_pending and kind != 'journey-smoke':
        if config['workloadAxis'] == 'target-vus':
            target_vu_execution(config['measurement'], 'measurement', profile_settings['targetUsers'])
        else:
            execution(config['measurement'], 'measurement')
        if warmup['enabled']:
            execution(warmup['execution'], 'warmup.execution')
    prom = config["prometheus"]
    if not isinstance(prom, dict) or set(prom) != {"job", "application", "scrapeIntervalSeconds", "stepSeconds", "rateWindow",
                                                "contextBeforeSeconds", "contextAfterSeconds", "settleTimeoutSeconds", "queryTimeoutSeconds"}:
        raise ValueError("prometheus fields must match pilot.example.json")
    for key in ("job", "application"):
        if not isinstance(prom[key], str) or not prom[key] or any(c in prom[key] for c in '\n\r'):
            raise ValueError(f"prometheus.{key} must be a nonempty label")
    for key in ("scrapeIntervalSeconds", "stepSeconds", "settleTimeoutSeconds", "queryTimeoutSeconds"):
        integer(prom[key], "prometheus." + key, 1)
    for key in ("contextBeforeSeconds", "contextAfterSeconds"):
        integer(prom[key], "prometheus." + key)
    if seconds(prom["rateWindow"], "prometheus.rateWindow") < 2 * prom["scrapeIntervalSeconds"]:
        raise ValueError("rateWindow must cover at least two configured scrape intervals")
    validation = config["validation"]
    if config['workloadAxis'] == 'target-vus':
        if not isinstance(validation, dict) or set(validation) != {'journeyCountExact', 'warmupArrivalCountTolerance', 'minimumApiSamples'} or validation['journeyCountExact'] is not True:
            raise ValueError('Target VU validation requires exact Journey accounting, warm-up arrival tolerance, and minimumApiSamples')
        tolerance = validation['warmupArrivalCountTolerance']
        if not isinstance(tolerance, dict) or set(tolerance) != {'absoluteIterations', 'relative'}:
            raise ValueError('warmupArrivalCountTolerance requires absoluteIterations and relative')
        integer(tolerance['absoluteIterations'], 'warmupArrivalCountTolerance.absoluteIterations')
        if type(tolerance['relative']) not in {int, float} or not 0 <= tolerance['relative'] < 1:
            raise ValueError('warmupArrivalCountTolerance.relative must be in [0, 1)')
    else:
        if not isinstance(validation, dict) or set(validation) != {"arrivalCountTolerance", "minimumApiSamples"}:
            raise ValueError("validation fields must match pilot.example.json")
        tolerance = validation["arrivalCountTolerance"]
        if not isinstance(tolerance, dict) or set(tolerance) != {"absoluteIterations", "relative"}:
            raise ValueError("arrivalCountTolerance requires absoluteIterations and relative")
        integer(tolerance["absoluteIterations"], "absoluteIterations")
        if type(tolerance["relative"]) not in {int, float} or not 0 <= tolerance["relative"] < 1:
            raise ValueError("arrivalCountTolerance.relative must be in [0, 1)")
    if validation["minimumApiSamples"] is not None:
        integer(validation["minimumApiSamples"], "minimumApiSamples", 1)
    return path, config


def token_window(pool, config, phase):
    """Read expiry/sub only; the server still verifies signatures on booking requests."""
    expected_pool_size = (config['warmup'].get('tokenPoolSize')
                          if phase == 'warmup' and config['warmup'].get('tokenPoolSize') is not None
                          else config["auth"]["tokenPoolSize"])
    if len(pool) != expected_pool_size:
        raise ValueError("JWT pool size differs from the explicitly selected tokenPoolSize")
    if config.get('workloadAxis') == 'target-vus' and phase == 'baseline':
        validate_target_vu_token_members(pool, config['measurement']['vus'])
    settings = execution_for_phase(config, phase)
    required = (seconds(settings['maxDuration']) if settings['executor'] == 'per-vu-iterations'
                else sum(seconds(s["duration"]) for s in settings["stages"])) + seconds(settings["gracefulStop"], allow_zero=True)
    required += config["auth"]["tokenValidityMarginSeconds"]
    expiries, members = [], []
    for item in pool:
        try:
            segments = item["accessToken"].split('.')
            if len(segments) != 3:
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(segments[1] + '=' * (-len(segments[1]) % 4)))
            exp = payload["exp"]
            member = int(payload["sub"])
            if type(exp) not in {int, float} or not math.isfinite(exp) or member < 1 or item.get("memberId") != member:
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ValueError("Pilot requires JWT exp/sub and matching memberId from preissued authentication") from None
        expiries.append(exp)
        members.append(member)
    if len(set(members)) != len(members):
        raise ValueError("Pilot token pool requires distinct prepared Members; reuse happens across iterations")
    remaining = min(expiries) - time.time()
    if remaining < required:
        raise ValueError("JWT expires before the configured phase/drain plus validity margin; refresh before measurement")
    return {"minimum_expiry_epoch": min(expiries), "minimum_remaining_seconds_at_preflight": remaining,
            "required_validity_seconds": required, "distinct_members": len(set(members))}
