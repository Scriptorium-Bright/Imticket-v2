"""Local experiment identity and resource evidence; never changes services/configuration."""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from urllib.parse import urlsplit

from common import REPO_ROOT, result_now


def command(args, timeout=10):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise ValueError(f'Environment observation failed: {args[0]} (exit {result.returncode})')
    return result.stdout.strip()


def git_identity():
    sha = command(['git', '-C', str(REPO_ROOT), 'rev-parse', 'HEAD'])
    status = command(['git', '-C', str(REPO_ROOT), 'status', '--porcelain'])
    return {'sha': sha, 'dirty': bool(status), 'branch': command(['git', '-C', str(REPO_ROOT), 'branch', '--show-current'])}


def docker_state():
    processes = command(['ps', '-axo', 'pid=,comm='])
    runtime, helpers = [], []
    for line in processes.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, executable = parts
        if 'com.docker.vmnetd' in executable or 'com.docker.socket' in executable:
            helpers.append({'pid': int(pid), 'executable': executable})
        elif any(name in executable for name in ('Docker.app/', 'com.docker.backend', 'dockerd', 'vpnkit')):
            runtime.append({'pid': int(pid), 'executable': executable})
    colima = []
    if shutil.which('colima'):
        raw = command(['colima', 'list', '--json'])
        colima = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return {'desktop_running': bool(runtime), 'desktop_runtime_processes': runtime,
            'privileged_helpers_only_are_not_desktop_runtime': helpers,
            'colima_profiles': [{key: p.get(key) for key in ('name', 'status', 'runtime', 'cpus', 'memory')} for p in colima],
            'local_container_runtime_running': bool(runtime) or any(p.get('status') == 'Running' for p in colima)}


def resource_snapshot():
    vm = command(['vm_stat'])
    total = int(command(['sysctl', '-n', 'hw.memsize']))
    swap = command(['sysctl', 'vm.swapusage'])
    pressure = command(['memory_pressure', '-Q'])
    page = re.search(r'page size of (\d+) bytes', vm)
    free = re.search(r'^Pages free:\s+(\d+)', vm, re.MULTILINE)
    used = re.search(r'used\s*=\s*([\d.]+)([MG])', swap)
    percent = re.search(r'System-wide memory free percentage:\s*(\d+)%', pressure)
    return {'total_physical_memory_bytes': total,
            'free_memory_bytes': int(free[1]) * int(page[1]) if free and page else None,
            'system_wide_memory_free_percent': int(percent[1]) if percent else None,
            'swap_used_bytes': float(used[1]) * 1024 ** (2 if used[2] == 'M' else 3) if used else None,
            'vm_stat_raw': vm, 'swap_usage_raw': swap, 'memory_pressure_raw': pressure,
            'note': 'vm_stat free pages and memory_pressure free percentage have different OS definitions; neither is inferred from the other.'}


def local_servers():
    result = subprocess.run(['lsof', '-nP', '-iTCP', '-sTCP:LISTEN', '-Fpcn'], capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise ValueError('Cannot observe local listening processes')
    servers, current = [], {}
    for line in result.stdout.splitlines():
        if line.startswith('p'):
            current = {'pid': int(line[1:]), 'listeners': []}
            servers.append(current)
        elif line.startswith('c'):
            current['name'] = line[1:]
        elif line.startswith('n'):
            current['listeners'].append(line[1:])
    unnecessary = []
    for server in servers:
        if server.get('name', '').lower() in {'java', 'mysqld', 'postgres', 'redis-server'}:
            unnecessary.append(server)
        elif server.get('name', '').lower() in {'node', 'python', 'python3'}:
            cwd = subprocess.run(['lsof', '-a', '-p', str(server['pid']), '-d', 'cwd', '-Fn'], capture_output=True, text=True).stdout
            if '/studyCollection/' in cwd:
                unnecessary.append(server)
    return {'listeners': servers, 'unnecessary_development_servers': unnecessary}


def capture_local(base_url):
    host = urlsplit(base_url).hostname
    route = command(['route', '-n', 'get', host])
    network = {name: match[1] if (match := re.search(r'^\s*' + name + r':\s*(\S+)', route, re.MULTILINE)) else None
               for name in ('gateway', 'interface')}
    network['destination_host'] = host
    git = git_identity()
    docker = docker_state()
    servers = local_servers()
    stable = {'host_name': platform.node(), 'os': platform.platform(), 'architecture': platform.machine(),
              'k6_version': command(['k6', 'version']), 'k6_binary': shutil.which('k6'),
              'git': git, 'network_path': network, 'total_physical_memory_bytes': int(command(['sysctl', '-n', 'hw.memsize']))}
    return {'captured_at': result_now(), 'identity': stable, 'docker': docker, 'local_servers': servers,
            'resources': resource_snapshot(),
            'identity_sha256': hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()}


def assert_environment(snapshot, expected=None):
    issues = []
    if snapshot['identity']['git']['dirty']:
        issues.append('dirty-working-tree')
    if snapshot['docker']['local_container_runtime_running']:
        issues.append('local-docker-runtime-on')
    if snapshot['local_servers']['unnecessary_development_servers']:
        issues.append('local-development-server-on')
    if expected and snapshot['identity_sha256'] != expected['identity_sha256']:
        issues.append('load-generator-environment-mismatch')
    if issues:
        raise ValueError(', '.join(issues))
