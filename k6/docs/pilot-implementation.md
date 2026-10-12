# Pilot Test 구현 및 실행 설정

구현일: 2026-10-11. 이 문서는 구현과 현재 설정을 설명한다. 사용자가 실행한 첫 Pilot 결과는 별도 run directory에 보존돼 있다. 이번 수정에서는 실서버 Smoke/Pilot/Baseline을 실행하지 않았다.

## 추가·변경 파일

| 구분 | 파일 | 역할 |
|---|---|---|
| 추가 | `pilot.js` | ramping-arrival-rate, 기존 Journey/API/token/좌석 선택 재사용 |
| 추가 | `lib/arrival.js`, `lib/pilot-metrics.js` | 설정 검증, iteration 시작과 workload 경계 기록, Pilot threshold |
| 추가 | `config/pilot.example.json` | 변경 가능한 제안 설정. Token pool 크기는 미정 |
| 추가 | `scripts/pilot_runner.py`, `scripts/pilot.sh` | 계정/JWT 준비 → 독립 warm-up → 새 measured fixture → Pilot 1회 → 수집 → 정리 |
| 추가 | `scripts/pilot_config.py` | config/expiry 검증, duration/timeUnit 계산 |
| 추가 | `scripts/pilot_observations.py` | raw points에서 start/completion·시간대·latency·실패를 분리 집계 |
| 추가 | `scripts/pilot_prometheus.py` | 확인된 metric만 query_range로 종료 후 수집 |
| 추가 | `scripts/pilot_report.py` | 관측값과 수집/검증 상태 report |
| 추가 | `tests/arrival.test.js`, `tests/test_pilot.py`, `tests/test_pilot_http.py` | 순수 검증과 localhost Pilot stub 검증 |
| 추가 | `docs/pilot-query-plan.json`, 이 문서 | 제안 query 37개와 사용법 |
| 변경 | `lib/config.js`, `lib/summary.js` | Pilot config, phase/execution/VU/seed summary 지원 |
| 변경 | `lib/journey.js` | Business Failure 개별 error 로그 제거, metric/reason과 System Failure 로그 유지 |
| 변경 | `scripts/run-experiment.py`, `scripts/report.py` | 기존 smoke 경로 유지, Pilot 분기와 결과 저장 |
| 변경 | `README.md`, `config/env.example.sh` | 사용법과 설정 안내 |

애플리케이션·DB schema·인프라·exporter·Grafana·Prometheus 설정은 변경하지 않았다. Baseline 반복 실행이나 전체 profile 순회도 추가하지 않았다.

## Config 구조

[pilot.example.json](../config/pilot.example.json)을 복사해 수정한다. 숫자는 실행 코드에 기본값으로 숨기지 않고 JSON에 명시했다.

| 필드 | 설정 범위 |
|---|---|
| `profile` | 기존 `profiles.json`의 단일 profile. 좌석 수·hot/1/2/3석 비율 선택 |
| `fixtureTemplate` | 기존 공연장 배치/등급/가격 template |
| `randomSeed` | VU/run ID와 무관한 iteration별 좌석 선택 seed |
| `requestTimeout` | 각 예매 HTTP 요청 timeout |
| `auth.mode` | `automatic` 계정 재사용/JWT 갱신 또는 `preissued` 파일 사용 |
| `auth.tokenPoolSize` | 명시적 pool 크기. 기본 `null`, 실행 전에 지정 필수 |
| `auth.accountWorkers` | 사전 계정 준비 동시성. k6 VU와 무관 |
| `auth.tokenValidityMarginSeconds` | phase duration + gracefulStop 외 추가 JWT 유효 시간 여유 |
| `measurement` | executor, startRate, timeUnit, stages, preAllocatedVUs, maxVUs, gracefulStop |
| `warmup.enabled`, `warmup.execution` | 독립 warm-up 실행 여부와 arrival 설정 |
| `prometheus` | 기존 job/application, 5초 scrape 정보, query step/window/context/timeout |
| `validation` | stage start count 허용 차이, 선택적인 API 최소 sample 정책 |

`startRate`와 stage target은 `timeUnit`당 **새 iteration 시작 수**다. `timeUnit=2s`로 바꾸면 target 2는 1 iteration/s다. startRate/target 0과 gracefulStop `0s`도 허용하며, VU 수와 duration은 검증한다. profile peak를 stage에 자동 적용하지 않는다.

`EXPERIMENT_CONFIG` 또는 `--config`로 파일을 선택한다. `--profile`과 `--users`는 각각 config의 profile과 tokenPoolSize를 명시적으로 덮어쓴다. 기존 smoke의 `TEST_USER_COUNT=10`, `SMOKE_VUS`를 Pilot 기본값으로 가져오지 않는다. 적용된 config와 effective k6 options를 각 run에 저장한다.

## 제안 stage와 근거

`musical-low` 1000석을 첫 fixture 후보로 설정했다. 이미 smoke에서 검증한 좌석 규모이고, profile peak 20/s와 별개로 낮은 1–2 iteration/s에서 발생기와 기록을 검증한다.

| 구간 | Duration | Configured rate 변화 | 5초 scrape 관측 기회 | Schedule 적분값 |
|---|---|---|---:|---:|
| 시작 유지 | 30s | 1 → 1 iteration/s | 약 6 | 30 |
| 낮은 ramp | 30s | 1 → 2 iteration/s | 약 6 | 45 |
| 유지 | 60s | 2 → 2 iteration/s | 약 12 | 120 |
| ramp-down | 30s | 2 → 0 iteration/s | 약 6 | 30 |

유입 시간은 150초, schedule 적분값은 225다. 이는 expected start 수이며 exact 실행 횟수가 아니다. Attempt Scale 500을 소진하기 위해 duration을 계산하지 않았다. 실제 started/finished/iterations는 별도 counter에서 측정한다. scrape 정렬·누락·metric의 유한 값 여부에 따라 실제 관측점 수는 달라지므로 query 결과에 그 개수를 기록한다.

Warm-up 제안은 1 iteration/s × 60초, gracefulStop 30초다. 12회 정도의 scrape 기회를 주고 API/JVM/pool 초기 실행을 분리한다. 60초가 JIT 안정화를 보장한다는 의미는 아니다. warm-up 설정은 측정과 독립적으로 바꾸거나 명시적으로 비활성화할 수 있다.

측정과 warm-up 모두 `preAllocatedVUs=20`, `maxVUs=20`을 적용했다. Peak 2/s × Journey 점유 시간 5초 = 동시 10개의 작업 가정에 2배 여유를 둔 발생기 용량이다. Smoke latency로 산정하거나 서버 최대 용량을 추정한 값이 아니다. 모든 VU를 사전 할당하므로 실행 중 동적 VU 추가는 없다. drop이 발생하면 그대로 기록하고 서버 병목 원인을 자동 추정하거나 서버/VU 설정을 자동 변경하지 않는다.

첫 Pilot은 10/10 VU로 실행됐다. 이후 20/20 변경안을 설명하면서 config 파일을 수정하지 않은 누락이 원인이며, 런너가 20을 10으로 덮어쓴 것이 아니다. 현재 예제와 local config의 measurement/warmup을 모두 20/20으로 반영했다. 완료된 run의 config snapshot과 metadata는 실제 실행값인 10/10을 보존한다.

HTTP timeout 10초, gracefulStop 30초는 제안값이다. 여러 HTTP 요청이 지연되면 한 Journey가 drain을 넘길 수도 있다. 그 경우 미완료와 기록 누락을 보존하고 정상 검증으로 처리하지 않는다. 두 값도 변경 가능하다.

Arrival count 검증의 제안 허용 차이는 `max(2 iterations, expected × 5%)`다. 이는 finite schedule의 끝점/구간 경계 차이를 확인하는 발생기 검증 기준이며 latency SLO가 아니다. 설정값과 실제값은 항상 각각 저장한다. raw timestamp가 schedule 끝 경계에 찍힌 event도 원본 그대로 보존하고 nominal 구간 내부·외부 count를 별도로 표시한다. 종료 경계 후 10ms를 넘는 start event는 검증 실패로 기록한다.

## Token pool과 fixture

Token pool 크기는 **선택하지 않았다**. `auth.tokenPoolSize` 또는 `--users`에 원하는 계정 수를 지정해야 실제 실행을 시작한다. 미설정 상태에서는 plan만 볼 수 있고 계정/fixture를 생성하지 않는다.

`auth.tokenPoolSize=null`이면 실제 실행은 `token pool size is unset` 오류로 중단한다. 예를 들어 계정 10개를 실험 조건으로 선택했다면 `--users 10 --plan`으로 확인한 뒤, 실행 명령에도 `--users 10`을 전달한다. 10은 사용법 예시이며 자동 기본값이나 VU와의 1:1 매핑을 뜻하지 않는다. 반복 사용할 값은 local config에 저장할 수 있다. 사용자가 계정이나 토큰을 직접 생성할 필요는 없다.

기본 `automatic` mode는 기존 계정 준비 helper로 임의 계정을 생성·재사용하고 phase마다 JWT를 사전 갱신한다. 같은 계정 pool을 warm-up과 측정에서 재사용한다. VU와 Member를 1:1로 묶거나 iteration마다 회원을 만들지 않는다. iteration 번호로 pool을 순환 선택하며 Journey 안에서는 같은 JWT를 사용한다.

`preissued` mode는 `TEST_TOKENS_FILE`에 이미 발급된 pool을 지정한다. 명시한 pool 크기와 실제 길이가 일치해야 하며 JWT exp/sub, 반환된 memberId, 서로 다른 Member 수를 확인한다. 토큰 서명은 서버가 각 예매 요청에서 검증한다. 만료가 phase duration + drain + margin을 커버하지 못하면 실행 전에 중단하며 부하 중 로그인/갱신은 하지 않는다.

현재 회원 전체 잠금/단일 활성 예약 제한은 확인되지 않았으며 `(member_id, idempotency_key)` 제약은 iteration별 다른 키로 구분한다. 이 구조와 pool 크기는 [사전 검토](pilot-baseline-review.md)에 기록했다. Pool 재사용에 성능 영향이 없다고 단정하지 않는다.

Fixture 크기는 profile로 선택한다.

```bash
# 설정만 확인: 1000석
bash k6/scripts/pilot.sh --config k6/config/pilot.example.json --plan

# 설정만 확인: 1500석. hot/좌석 선택 비율도 musical-high profile을 따름
bash k6/scripts/pilot.sh --config k6/config/pilot.example.json --profile musical-high --plan
```

두 경우 모두 제안 Pilot stage는 그대로 유지된다. high profile의 peak를 자동 적용하지 않는다. 모든 phase에서 현재 catalog API로 새 hall/performance/time을 준비하고, 기존 검증으로 정확한 1000/1500 AVAILABLE seat를 확인한다. warm-up과 측정의 performanceTimeId가 같으면 중단한다. `--fixture`로 이전 회차를 전달할 수 없다.

Fixture 생성·인증·실행 전 좌석 검증은 k6 측정 밖에서 수행한다. 기존 DB 범위 지정 정리는 측정과 Prometheus 수집이 끝난 뒤에만 실행한다. `--keep-data`로 정리를 생략할 수 있다. 결과 파일과 테스트 계정은 보존한다. reset API를 추가하거나 측정 중 좌석을 복원하지 않는다.

## 시간과 세 가지 rate

| 항목 | 기록 기준 |
|---|---|
| Configured Iteration Arrival Rate | startRate/timeUnit과 각 stage의 선형 schedule. Expected count는 적분값 |
| Observed Iteration Start Rate | 별도 `iteration_started` raw counter. 전체 평균은 실제 phase start count / configured arrival duration |
| Completed Iteration Throughput | 기본 `iterations` raw counter의 완료 timestamp. 전체 평균은 workload 실제 구간(유입+drain)을 분모로 사용 |
| Booking Completed | 최종 예약 DTO 검증까지 성공한 `booking_completed`. 기본 iterations와 별도 |

시작·완료 count, strict nominal arrival window 내부 count, 경계 밖 start count를 모두 저장한다. 초별/구간별 rate는 서로 다른 event timestamp에서 계산한다. stage별 완료 수는 완료한 시각 기준이며 해당 stage에서 시작한 요청의 cohort가 아니다. 전체 평균과 k6 summary counter rate의 분모도 다를 수 있어 report에서 구분한다.

| Metadata | 의미 |
|---|---|
| `workload_started_at`, `arrival_started_at` | k6 `exec.scenario.startTime`에서 얻은 scenario 시작 |
| `arrival_planned_ended_at` | scenario 시작 + stage duration 합 |
| `arrival_ended_at`, `arrival_end_status` | 정상 종료에서 확인된 schedule 종료. 조기 중단 시 확정 시각을 만들지 않음 |
| `last_iteration_completed_at` | 마지막 기본 iterations 완료 event |
| `drain_ended_at`, `workload_ended_at` | HTTP 작업 없이 실행하는 teardown 경계 event |
| `measurement_started_at`, `measurement_ended_at` | 관측 데이터 구간, 시작부터 drain 종료까지 |
| `measurement_completed_at` | summary를 저장한 전체 k6 프로세스 종료. Prometheus 수집 제외 |
| `started_at`, `ended_at` | 기존 runner 프로세스 경계 |
| `prometheus_query_range` | 측정 데이터 범위와 조회용 context 범위를 각각 epoch로 기록 |

teardown 경계가 없으면 process 종료를 측정 끝의 upper bound로 기록하고 basis를 표시한다. 미완료 수는 `iteration_started - iterations`로 계산한 **관측된 미완료**이며 k6 native interruption counter라고 표시하지 않는다. 강제 중단 시 Journey finally/완료 point가 누락될 수 있어 이를 성공/실패 sample로 새로 만들어 넣지 않는다. k6 scenario 시작 시각의 근거는 [execution API](https://grafana.com/docs/k6/latest/javascript-api/k6-execution/)다.

## 검증과 결과

Pilot threshold는 System Failure 0, dropped_iterations 0, 호출된 API contract check를 확인한다. API별 latency submetric threshold는 summary에 값을 노출하기 위한 것이고 latency 성능 기준이 아니다. Global checks / Business Failure 0 / 모든 예매 성공을 요구하지 않는다. `SEAT_UNAVAILABLE`은 기존 Business Failure 분류를 유지한다.

Business Failure는 개별 console/error 로그를 출력하지 않는다. `business_failures`, API counter와 Journey 종료 outcome/reason은 raw points와 report에 계속 남긴다. System Failure는 개별 error 로그를 유지한다. HTTP 요청·응답 판정과 threshold는 이 로그 변경으로 바뀌지 않는다.

Report는 `System Failures`와 `HTTP Expected-Response Result`를 별도 구역에 둔다. `http_req_failed`의 분모는 HTTP sample 수이며 iteration 수가 아니다. 현재 `http.expectedStatuses(200)` 기준으로 non-200/transport 실패가 해당 지표에 포함된다. 따라서 `SEAT_UNAVAILABLE` 409는 Business Failure이면서 HTTP 실패 sample이다. 반대로 HTTP 200에서도 DTO/Journey 검증은 실패할 수 있다. raw Rate의 `passes`는 이 지표에서 실패 sample 수, `fails`는 기대 응답 sample 수이며 원본 JSON의 값·키는 유지한다.

종료 후 다음을 별도로 확인한다.

- 필요한 k6 metric과 VU raw sample 존재, raw/summary counter 일치.
- iteration 시작 → Journey 시작/끝 → 기본 iterations 완료와 business outcome count의 대조.
- 각 stage의 expected/observed start count와 drop, 미완료, System Failure.
- HTTP 전체/API별/Journey/iteration latency의 sample 수와 p50/p95/p99.
- HTTP 5xx, timeout/network/unexpected response 및 contract/client 실패 reason.
- Prometheus required raw series의 측정 구간 내 유한 값과 종료 후 scrape 확인.

`minimumApiSamples=null`은 percentile의 표본 충분성을 **미판정**으로 둔다. 이 상태의 전체 검증은 `needs-review`로 남기며 Baseline으로 자동 진행하지 않는다. 필요하면 명시한 최소 sample 수를 screening 기준으로 사용할 수 있지만 p99 정밀도의 보장은 아니다. 어떤 API에 sample이 없으면 0ms가 아니라 N/A/누락으로 기록한다.

```text
k6/results/{timestamp}_pilot_run-01/
  run-metadata.json
  pilot-config.json
  fixture-manifest.json
  fixture-initial-seats.json
  k6-effective-options.json
  k6-summary.json
  k6-summary.txt
  k6-points.json                  # raw NDJSON
  pilot-observations.json         # 초별/구간별 rate, latency sample, 실패, 검증
  k6.log / preflight.log
  report.md
  prometheus/
    query-plan.json
    collection-status.json
    actuator-prometheus.txt
    verified-metric-names.json
    http.json / tomcat.json / hikari.json / jvm.json
```

Warm-up은 `{timestamp}_pilot_warmup_run-01/`에 별도로 보존하고 측정 metadata에서 참조한다. warm-up summary는 합치지 않으며 Prometheus 수집은 measured Pilot 종료 후 수행한다. 실패/중단 run도 자료를 보존한다. report에는 Base URL, 토큰, 비밀번호를 출력하지 않고 원인·병목·saturation을 자동 서술하지 않는다.

## Prometheus query 목록

[pilot-query-plan.json](pilot-query-plan.json)에 예제 설정의 **37개 정확한 query**를 저장했다. 실행은 하지 않았다. Runtime query-plan도 각 run에 보존한다. 이름은 기존 [실서버 inventory](metric-inventory.json)에서 확인된 29개 sample 이름으로 제한한다.

| 그룹 | Raw query | 후처리 query |
|---|---|---|
| HTTP (8) | requests count/sum/bucket, 예매 URI filter | throughput, histogram p50/p95/p99, 5xx rate |
| Tomcat (3) | busy / config max / current threads | 없음 |
| Hikari (9) | active/idle/pending/max, acquisition count/sum/max, timeout total | acquisition 평균 (`rate(sum)/rate(count)`) |
| JVM (17) | process CPU/time, heap used/committed/max, allocation total, GC bucket/count/sum/max, live/peak/daemon/state/started threads | allocation rate, GC pause p95 |

모든 selector에 기존 job/application을 적용한다. HTTP는 `uri=~"/api/(seats|reservations|payments)(/.*)?"`, heap은 `area="heap"`로 구분한다. DB Lock Wait, MySQL Host, OCI Host, Hikari acquisition bucket, deadlocked-thread query는 없다.

제안 query 설정은 step 5초, rate window 30초, context 이전 30초/이후 5초다. 실험 중 Prometheus API 호출은 없다. k6 프로세스 종료 뒤 actuator를 한 번 읽고 query 범위 끝 이후의 fresh scrape를 최대 15초 기다린 뒤 query_range를 실행한다. Required raw query는 API 조회가 성공하고 expanded query range 안에 유한 series 값이 있으면 수집 완료로 본다. 측정 구간 안의 evaluation 시각이 0개, 1개, 2개 이상인지는 `temporal_coverage`에 별도로 기록하며, 짧은 workload를 이유로 수집 검증을 실패시키지 않는다. 데이터가 query range 안에 전혀 없거나 metric/target/API가 누락된 경우는 계속 collection gap으로 표시한다.

이는 짧은 workload와 5초 scrape cadence가 항상 겹친다고 주장하지 않는다. `in-window` coverage가 sparse하거나 context-only일 때는 그 사실을 report에 보존하고, 그 Run의 측정 순간에 해당 metric 값이 관측됐다고 과장하지 않는다. Query evaluation timestamp 수는 독립 scrape 수가 아니다. `query_range`의 raw 응답, before/after context, 실제 measurement/query epoch와 fresh-scrape 확인을 함께 보존한다. Workload duration, scrape interval, step, rate window는 이 validation 변경으로 바꾸지 않는다.

원본에는 context를 포함하되 report는 측정 구간 값을 표시한다. rate/quantile 함수는 전체 window가 측정 구간 안에 들어오는 값만 표에 표시한다. 초기 30초의 windowed 값은 후처리 표에서 제외하고 raw에 보존한다. 기존 Grafana의 1분 window는 변경하지 않았으므로 초기 구간을 비교할 때 window 차이를 함께 확인해야 한다. query evaluation point 수를 독립 scrape 수로 간주하지 않는다. 내부 resource는 앱 전체의 해당 시간대 관측이며 다른 요청·scheduler도 포함할 수 있다.

## 실행 명령

설정 확인만 할 때:

```bash
bash k6/scripts/pilot.sh --config k6/config/pilot.example.json --plan
```

실제 실행 전 local config를 만들고 tokenPoolSize 및 필요한 stage/VU/warm-up/sample 정책을 확인한다.

```bash
cp k6/config/pilot.example.json k6/config/pilot.local.json
# pilot.local.json의 auth.tokenPoolSize를 원하는 계정 수로 지정

bash k6/scripts/pilot.sh --config k6/config/pilot.local.json --plan
```

설정 확인 후 실제 Pilot 1회:

```bash
bash k6/scripts/pilot.sh --config k6/config/pilot.local.json --run-number 1
```

wrapper는 기존 env.local.sh를 읽는다. BASE_URL, 기존 Prometheus 접근/SSH/DB 정리 설정을 사용하고 로그인·token·회차 ID 전달은 자동 처리한다. Python 진입점은 `python3 k6/scripts/run-experiment.py pilot --config ...`이며 이 경우 환경을 먼저 source해야 한다. `--users N`으로 pool 크기를 덮어쓸 수도 있다. 이번 구현 작업에서는 위 실제 서버 실행 명령을 실행하지 않았다.

## 구현 검증

Node의 순수 helper/arrival 검증, Python의 config/expiry/query/report 검증, localhost HTTP stub의 Pilot 검증을 수행했다. Stub에서는 1000석 warm-up/측정 회차 분리, 1500석 선택, VU 4개와 Member 1개의 재사용, 정상 Business Failure, 5xx System Failure, 완료 대기와 drain 만료를 확인했다. 실제 서버 성능 결과로 사용하지 않으며 테스트 artifact와 임시 fixture는 정리했다.

```bash
node --test k6/tests/helpers.test.js k6/tests/arrival.test.js
PYTHONPATH=k6/scripts:k6/tests python3 -m unittest test_pilot test_report test_pilot_http -v
```

위 테스트는 Smoke 실행기나 실제 Imticket 서버를 호출하지 않는다.
