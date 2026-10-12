"""Low-frequency local macOS load-generator observations using built-in commands."""
import platform
import os
import re
import subprocess
import threading
import time

from common import result_now, write_json


class LoadGeneratorSampler:
    def __init__(self, run_dir, interval_seconds=5):
        self.run_dir = run_dir
        self.interval = interval_seconds
        self.pid = None
        self.samples = []
        self.stop_event = threading.Event()
        self.thread = None
        self.system = platform.system()
        self.total_memory_bytes = self._sysctl('hw.memsize') if self.system == 'Darwin' else None

    @staticmethod
    def _command(arguments, timeout=2):
        try:
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    def _sysctl(self, name):
        raw = self._command(['sysctl', '-n', name])
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    def sample(self, phase):
        entry = {'at': result_now(), 'phase': phase, 'platform': self.system,
                 'total_memory_bytes': self.total_memory_bytes}
        if self.system == 'Darwin':
            entry['vm_stat_raw'] = self._command(['vm_stat'])
            entry['swap_usage_raw'] = self._command(['sysctl', 'vm.swapusage'])
            entry['memory_pressure_raw'] = self._command(['memory_pressure', '-Q'])
            if os.environ.get('K6_STRICT_SERIES') == '1':
                from measurement_environment import docker_state
                try:
                    entry['docker'] = docker_state()
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    entry['docker_observation_error'] = type(error).__name__
        if self.pid is not None:
            raw = self._command(['ps', '-o', '%cpu=,rss=,state=', '-p', str(self.pid)])
            entry['k6_process_raw'] = raw
            if raw:
                fields = raw.split()
                try:
                    entry['k6_process_cpu_percent'] = float(fields[0])
                    entry['k6_process_rss_bytes'] = int(fields[1]) * 1024
                    entry['k6_process_state'] = fields[2] if len(fields) > 2 else None
                except (ValueError, IndexError):
                    pass
        self.samples.append(entry)

    def start(self, pid):
        self.pid = pid
        self.sample('workload-start')
        self.thread = threading.Thread(target=self._run, name='k6-loadgen-sampler', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.wait(self.interval):
            self.sample('during-workload')

    def finish(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=self.interval + 2)
        self.sample('workload-end')
        cpu = [sample['k6_process_cpu_percent'] for sample in self.samples if 'k6_process_cpu_percent' in sample]
        rss = [sample['k6_process_rss_bytes'] for sample in self.samples if 'k6_process_rss_bytes' in sample]
        page_size, free_pages, swap_used, free_percent = None, [], [], []
        for sample in self.samples:
            vm = sample.get('vm_stat_raw') or ''
            match = re.search(r'page size of ([\d,]+) bytes', vm)
            if match:
                page_size = int(match.group(1).replace(',', ''))
            page = re.search(r'^Pages free:\s+([\d.]+)', vm, re.MULTILINE)
            if page:
                free_pages.append(int(float(page.group(1).replace('.', ''))))
            swap = sample.get('swap_usage_raw') or ''
            match = re.search(r'used\s*=\s*([\d.]+)([MG])', swap)
            if match:
                swap_used.append(float(match.group(1)) * (1024 ** (2 if match.group(2) == 'M' else 3)))
            match = re.search(r'System-wide memory free percentage:\s*(\d+)%', sample.get('memory_pressure_raw') or '')
            if match:
                free_percent.append(int(match[1]))
        status = 'collected' if self.system == 'Darwin' and self.samples else 'platform-limited'
        write_json(self.run_dir / 'load-generator-observations.json', {
            'status': status, 'sampling_interval_seconds': self.interval,
            'source': 'macOS vm_stat, sysctl, and ps; local load-generator observation only',
            'total_memory_bytes': self.total_memory_bytes,
            'k6_process_cpu_percent_max': max(cpu) if cpu else None,
            'k6_process_rss_bytes_max': max(rss) if rss else None,
            'host_memory_summary': {
                'vm_stat_page_size_bytes': page_size,
                'minimum_free_pages': min(free_pages) if free_pages else None,
                'minimum_free_bytes': min(free_pages) * page_size if free_pages and page_size else None,
                'maximum_swap_used_bytes': max(swap_used) if swap_used else None,
                'minimum_system_wide_memory_free_percent': min(free_percent) if free_percent else None,
                'raw_snapshots_retained': True,
            },
            'samples': self.samples,
            'docker_off_during_observation': (all('docker' in sample and not sample['docker']['local_container_runtime_running']
                                                for sample in self.samples) if os.environ.get('K6_STRICT_SERIES') == '1' else None),
        })
