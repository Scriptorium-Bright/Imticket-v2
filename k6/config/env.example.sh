# Copy to env.local.sh and replace placeholders before sourcing.
export BASE_URL='http://<OCI_PUBLIC_IP>:8080'
export PROMETHEUS_URL='http://<OCI_PUBLIC_IP>:9090'
# Supply a preissued token pool; its size is independent of SMOKE_VUS.
export TEST_TOKENS_FILE='./k6/fixtures/tokens.local.json'
export SMOKE_VUS=1
export SMOKE_SEATS=1
# Automatic smoke: random accounts are saved/reused; JWTs refresh before each run.
export TEST_USER_COUNT=10
export TEST_ACCOUNT_WORKERS=4
# Remote Docker/MySQL cleanup through an existing SSH host alias.
# Leave unset for local Docker Compose.
# export K6_SSH_TARGET='imticket'
export K6_DB_CONTAINER='imticket-v2-mysql'
# Access a private Prometheus through the same SSH connection; tunnel closes at exit.
# export K6_PROMETHEUS_SSH_PORT=9090
# Choose the profile when preparing fixture data (1000 or 1500 seats).
# export WORKLOAD_PROFILE=musical-low
# Pilot: review the JSON with --plan before executing. Its pool size is explicit.
# export EXPERIMENT_CONFIG='./k6/config/pilot.local.json'
# TEST_USER_COUNT/SMOKE_VUS are smoke settings; Pilot uses auth.tokenPoolSize and arrival VU config.
# After preparation: source k6/fixtures/current.env.sh
# That file sets FIXTURE_MANIFEST and the server-generated PERFORMANCE_TIME_ID.
# For a single-token pool instead: unset TEST_TOKENS_FILE and set TEST_ACCESS_TOKEN.
# export TEST_ACCESS_TOKEN='<PREISSUED_ACCESS_TOKEN>'
# Existing-user token preparation only (separate command, outside the experiment):
# export TEST_USERNAME='<EXISTING_USERNAME>'
# export TEST_PASSWORD='<EXISTING_PASSWORD>'
# export TOKEN_OUTPUT_FILE='./k6/fixtures/tokens.local.json'
