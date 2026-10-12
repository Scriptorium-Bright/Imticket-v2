"""Read-only run identity/configuration fingerprints; never records credential values."""
import hashlib
import json
import os
import shlex
import subprocess

from common import result_now
from fixture_cleanup import database_command, execute_sql


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def remote(command):
    database_command()  # Validate the existing SSH alias/container settings.
    target = os.environ.get('K6_SSH_TARGET')
    args = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', target, shlex.join(command)] if target else command
    return subprocess.run(args, capture_output=True, text=True, timeout=30, check=True).stdout


def capture():
    names = ['imticket-v2-app', os.environ.get('K6_DB_CONTAINER', 'imticket-v2-mysql'), 'imticket-v2-prometheus']
    containers = json.loads(remote(['docker', 'inspect', *names]))
    identities = {}
    runtime_conditions = {}
    directory = None
    for data in containers:
        name = data['Name'].lstrip('/')
        config = data['Config']
        directory = directory or (config.get('Labels') or {}).get('com.docker.compose.project.working_dir')
        identities[name] = {'container_id': data['Id'], 'image_id': data['Image'],
            'started_at': data['State']['StartedAt'], 'running': data['State']['Running'],
            'restart_count': data['RestartCount'],
            'config_sha256': digest(config), 'host_config_sha256': digest(data['HostConfig']),
            'mounts_sha256': digest(data.get('Mounts', []))}
        if name == 'imticket-v2-app':
            allowed = {'SPRING_PROFILES_ACTIVE', 'DB_POOL_MAX_SIZE', 'DB_POOL_MIN_IDLE', 'DB_CONNECTION_TIMEOUT_MS',
                       'TOMCAT_MAX_THREADS', 'TOMCAT_MIN_SPARE_THREADS', 'SERVER_TOMCAT_THREADS_MAX',
                       'SPRING_DATASOURCE_HIKARI_MAXIMUM_POOL_SIZE', 'SPRING_DATASOURCE_HIKARI_MINIMUM_IDLE'}
            env = dict(value.split('=', 1) for value in config.get('Env', []) if '=' in value)
            runtime_conditions['spring_environment'] = {key: value for key, value in env.items() if key in allowed}
            runtime_conditions['spring_configuration_sha256'] = digest(config)
            jvm = [*(config.get('Entrypoint') or []), *(config.get('Cmd') or [])]
            for variable in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JAVA_OPTS'):
                jvm.extend(shlex.split(env.get(variable, '')))
            try:
                jvm.extend(remote(['docker', 'exec', name, 'cat', '/proc/1/cmdline']).split('\x00'))
                runtime_conditions['jvm_command_observation'] = 'procfs'
            except subprocess.SubprocessError:
                runtime_conditions['jvm_command_observation'] = 'docker-config-only'
            runtime_conditions['explicit_jvm_flags'] = sorted(set(arg for arg in jvm if arg.startswith(('-X', '-XX:'))))
            runtime_conditions['running_image_revision_label'] = (config.get('Labels') or {}).get('org.opencontainers.image.revision')
    if not all(c['running'] for c in identities.values()):
        raise ValueError('Baseline requires the existing app/DB/Prometheus containers running')
    try:
        commit = remote(['git', '-C', directory, 'rev-parse', 'HEAD']).strip() if directory else None
    except subprocess.SubprocessError:
        commit = None
    variables = "'version','max_connections','innodb_buffer_pool_size','innodb_lock_wait_timeout','transaction_isolation','innodb_flush_log_at_trx_commit','sync_binlog'"
    sql = f"SHOW GLOBAL VARIABLES WHERE Variable_name IN ({variables});\nSELECT 'K6_BASELINE_ENV_OK';\n"
    rows = execute_sql(sql, 'K6_BASELINE_ENV_OK', timeout=30).splitlines()
    db_settings = dict(row.split('\t', 1) for row in rows if '\t' in row)
    candidates = [mount['Source'] for data in containers for mount in data.get('Mounts', [])]
    if directory:
        candidates.extend(directory + '/' + name for name in (
            '.env', 'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml',
            'src/main/resources/application.yml', 'src/main/resources/application.yaml',
            'src/main/resources/application.properties'))
    code = ('import hashlib,json,os,sys; paths=json.loads(sys.argv[1]); '
            'print(json.dumps({p:hashlib.sha256(open(p,"rb").read()).hexdigest() '
            'for p in paths if os.path.isfile(p)}))')
    config_files = json.loads(remote(['python3', '-c', code, json.dumps(sorted(set(candidates)))]))
    if os.environ.get('BASE_URL'):
        from collect_prometheus import fetch
        exposition = fetch(os.environ['BASE_URL'].rstrip('/') + '/actuator/prometheus', 10)
        configuration_metrics = {}
        for line in exposition.splitlines():
            if line.startswith(('tomcat_threads_config_max_threads', 'hikaricp_connections_max')):
                identity, value = line.rsplit(' ', 1)
                configuration_metrics[identity] = float(value)
        runtime_conditions['exposed_configuration_metrics'] = configuration_metrics
    stable = {'containers': identities, 'server_checkout_commit': commit, 'db_settings': db_settings,
        'runtime_conditions': runtime_conditions,
        'external_config_file_sha256': config_files}
    return {'captured_at': result_now(), **stable, 'comparison_sha256': digest(stable),
        'runtime_source_note': 'Checkout HEAD and running image ID are separate evidence; HEAD alone does not prove image contents.',
        'scope': 'Existing server identity/configuration, outside load. No host/DB-lock exporter or new time-series collector.'}
