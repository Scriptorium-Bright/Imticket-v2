"""Optional temporary SSH tunnel for a private Prometheus port."""
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from urllib.error import URLError
from urllib.request import urlopen

from common import positive_integer
from fixture_cleanup import database_command


@contextmanager
def prometheus_tunnel():
    remote_port = os.environ.get("K6_PROMETHEUS_SSH_PORT")
    if not remote_port:
        yield
        return
    remote_port = positive_integer(remote_port, "Prometheus SSH port", 65535)
    target = os.environ.get("K6_SSH_TARGET")
    if not target:
        raise ValueError("K6_PROMETHEUS_SSH_PORT requires K6_SSH_TARGET")
    database_command()  # Reuse validation of the SSH target before invoking ssh.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        local_port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{local_port}"
    previous = os.environ.get("PROMETHEUS_URL")
    process = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                "-o", "ExitOnForwardFailure=yes", "-N", "-L",
                                f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", target],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise ValueError("Prometheus SSH tunnel exited before it was ready")
            try:
                with urlopen(url + "/-/ready", timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise ValueError("Prometheus did not become reachable through SSH")
            time.sleep(0.1)
        os.environ["PROMETHEUS_URL"] = url
        print("Prometheus ready through temporary SSH tunnel", flush=True)
        yield
    finally:
        if previous is None:
            os.environ.pop("PROMETHEUS_URL", None)
        else:
            os.environ["PROMETHEUS_URL"] = previous
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
