"""Persistent random HTTP test accounts; authentication runs before measurement."""
import fcntl
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from common import K6_ROOT, http_url, positive_integer, read_json, utc_now, result_now, write_json
from preparation import PreparationError, api_data, error_details


def private_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    temporary.replace(path)


def token_response(data):
    if (not isinstance(data, dict) or not isinstance(data.get("accessToken"), str)
            or not data["accessToken"] or "<" in data["accessToken"]
            or any(c.isspace() for c in data["accessToken"])):
        raise ValueError("Authentication did not return a real accessToken")
    return {"memberId": positive_integer(data.get("memberId"), "auth memberId"),
            "accessToken": data["accessToken"]}


def prepare_accounts(base_url, count, workers=4, pool_path=None, timeout=30):
    base_url = http_url(base_url, "BASE_URL")
    count = positive_integer(count, "test account count")
    workers = positive_integer(workers, "account workers", 32)
    timeout = positive_integer(timeout, "account timeout")
    namespace = hashlib.sha256(base_url.encode()).hexdigest()[:16]
    pool_path = Path(pool_path or K6_ROOT / "fixtures" / f"accounts-{namespace}.local.json").resolve()
    pool_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with pool_path.with_suffix(pool_path.suffix + ".lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Test account pool is in use by another preparation") from None
        if pool_path.exists():
            pool = read_json(pool_path)
            if (not isinstance(pool, dict) or pool.get("schema_version") != 1
                    or pool.get("base_url") != base_url or not isinstance(pool.get("accounts"), list)):
                raise ValueError("Test account pool does not match BASE_URL/schema")
            os.chmod(pool_path, 0o600)
            for account in pool["accounts"]:
                if (not isinstance(account, dict) or not isinstance(account.get("username"), str)
                        or not account["username"].startswith("k6-user-")
                        or not isinstance(account.get("password"), str)
                        or not 8 <= len(account["password"]) <= 72
                        or account.get("state") not in {"new", "registering", "ready"}):
                    raise ValueError("Invalid generated account record")
        else:
            pool = {"schema_version": 1, "base_url": base_url, "created_at": utc_now(), "accounts": []}
        created = max(0, count - len(pool["accounts"]))
        for _ in range(created):
            pool["accounts"].append({"username": "k6-user-" + uuid.uuid4().hex,
                                     "password": secrets.token_urlsafe(30), "state": "new"})
        selected = pool["accounts"][:count]
        new_usernames = {account["username"] for account in selected if account["state"] == "new"}
        for account in selected:
            if account["state"] == "new":
                account["state"] = "registering"
        # Persist credentials BEFORE register, including uncertain/partial HTTP outcomes.
        private_json(pool_path, pool)
        mutex = threading.Lock()
        completed = 0
        started = time.monotonic()
        observation = {'started_at': result_now(), 'target_authentication_count': count,
                       'completed_authentication_count': 0, 'failure_count': 0, 'workers': workers,
                       'failures': [], 'automatic_retry': False, 'phase': os.environ.get('K6_PREPARATION_PHASE')}
        observation_path = os.environ.get('K6_AUTH_OBSERVATION_FILE')

        def save_observation(status):
            observation.update(status=status, ended_at=result_now(), elapsed_seconds=time.monotonic() - started,
                               completed_authentication_count=completed)
            if observation_path:
                write_json(observation_path, observation)

        save_observation('authenticating')

        def authenticate(indexed_account):
            index, account = indexed_account
            payload = {key: account[key] for key in ("username", "password")}
            nonlocal completed
            if account["username"] in new_usernames:
                data = api_data(base_url, "POST", "/api/auth/register", payload, timeout=timeout,
                                request_context=f"account_index={index}/{count}")
            else:
                try:
                    data = api_data(base_url, "POST", "/api/auth/login", payload, timeout=timeout,
                                    request_context=f"login_index={index}/{count}")
                except PreparationError as error:
                    if error.http_status != 401 or os.environ.get('K6_STRICT_SERIES') == '1':
                        raise
                    # The previous register may have failed before creating a member,
                    # or a dedicated test DB may have been recreated. Same saved identity.
                    data = api_data(base_url, "POST", "/api/auth/register", payload, timeout=timeout,
                                    request_context=f"account_index={index}/{count}; login returned 401")
            token = token_response(data)
            with mutex:
                account.update(state="ready", member_id=token["memberId"], authenticated_at=utc_now())
                completed += 1
                if completed % 100 == 0:
                    private_json(pool_path, pool)
                    print(f"Test accounts authenticated: {completed}/{count}", flush=True)
            return token

        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(authenticate, (index, account)): index for index, account in enumerate(selected, start=1)}
                tokens, failure = [None] * count, None
                for future in as_completed(futures):
                    if future.cancelled():
                        continue
                    try:
                        tokens[futures[future] - 1] = future.result()
                    except Exception as error:
                        detail = getattr(error, 'details', None) or error_details(error, 'POST', 'authentication')
                        observation['failures'].append({**detail, 'authentication_index': futures[future]})
                        observation['failure_count'] += 1
                        if failure is None:
                            failure = error
                            for pending in futures:
                                pending.cancel()
                if failure is not None:
                    raise failure
            if len({entry["memberId"] for entry in tokens}) != count:
                raise ValueError("Authentication returned duplicate member IDs")
            token_path = pool_path.parent / f"tokens-{uuid.uuid4().hex}.local.json"
            private_json(token_path, tokens)
            save_observation('completed')
        except Exception:
            save_observation('failed')
            raise
        finally:
            # Executor has joined before this save; partial progress is recoverable.
            private_json(pool_path, pool)
    print(f"Test accounts ready: {count}; pool additions: {created}; tokens refreshed", flush=True)
    return pool_path, token_path
