# Baseline workload models — 구현과 실행 전 검토

develop `d9777b0`의 실제 API/DTO/좌석·예약 코드를 확인하고 기존 Pilot 실행기를 확장했다. 애플리케이션 코드, 서버 API, 인프라, exporter, 서버 설정은 변경하지 않았다. 기존 Single Attempt와 사용자가 실행한 Reselection Smoke/Pilot 결과를 보존한다. 별도의 [실서버 충돌 validation 1건](reselection-validation.md)도 완료했다. Baseline 본 측정은 아직 실행하지 않았다.

## 두 모델

| 항목 | single-attempt | reselection |
|---|---|---|
| 1 iteration | 한 번의 예매 시도 | 한 사용자의 예매 Journey |
| 좌석 선택 대상 | 기존 Hot/Cold 영역의 전체 좌석, 점유 상태 필터 없음 | 매번 서버 seat-map을 다시 조회한 AVAILABLE 좌석 |
| pre-reserve SEAT_UNAVAILABLE | Business Failure로 종료 | conflict 기록 후 재조회·재선택 |
| 원하는 N석 | 시도 시작에 1/2/3석 결정 | Journey 시작에 한 번 결정, 모든 재선택에서 유지 |
| 본 실행기 | ramping-arrival-rate | ramping-arrival-rate |

Single Attempt는 기존 `bookingJourney`/`selectWorkloadSeats`를 계속 호출한다. 기존 여섯 API, 멱등 키, occupied 좌석 선택, Business/System 종료와 기존 metric을 유지했다. `journey.js`에는 `seats_confirmed`의 0 초기화와 성공 시 N석 집계만 추가했다. 정상 Business Failure의 개별 error log를 생략하는 기존 Pilot 동작도 유지한다.

Reselection은 공통 `bookingApi`, 응답 contract, 인증 pool을 재사용하며 행동 모듈만 분리했다. VU 사이에 좌석 상태를 공유하지 않는다. token pool SharedArray는 인증 목록이며 좌석 inventory가 아니다.

## Reselection pseudocode

```text
Journey 시작; desired N을 profile의 1/2/3 비율로 한 번 결정
반복:
  기술적 guard / 선택적으로 활성화한 사용자 포기 조건 확인
  재선택이면 선택적으로 think time 적용; reselections 증가
  서버 seat-map 재조회
  전체 AVAILABLE이 N 미만이면 SOLD_OUT 종료
  Hot Traffic 비율로 원하는 Hot/Cold 영역 추첨
  원하는 영역 → 다른 영역 → 전체 AVAILABLE 순서로 N석 선택
  직전 충돌 seat set과 같으면 다른 조합 선택
  N석은 있지만 직전 충돌 set만 가능하면 재조회; SOLD_OUT으로 바꾸지 않음
  selection-summary
  새로운 attempt 멱등 키로 pre-reserve
  정확한 SEAT_UNAVAILABLE이면 conflict 기록 → 다음 반복
  그 외 시스템/contract 오류이면 SYSTEM_FAILURE 종료
  payment prepare → verify → reservation detail
  BOOKING_COMPLETED 종료
```

선점/결제 준비 키는 run/iteration/attempt에 따라 달라진다. 재선택 payload에 직전 attempt 키를 재사용하지 않는다. 통신 오류·5xx·알 수 없는 409는 자동 재시도하지 않는다. 기존 verify의 정상 Business 응답인 EXPIRED/REFUND_PENDING은 `BUSINESS_TERMINATED`로 별도 기록한다.

## SOLD_OUT, N석, Hot Seat

`SeatSelectionService`/`ReservationCreationService`는 동일 회차의 중복 없는 Seat ID를 검사하며 같은 행·연속 좌석 조건은 없다. 따라서 현재 사용자의 N개 AVAILABLE 좌석을 전체 seat-map에서 선택할 수 없으면 SOLD_OUT이다. 3석 Journey에서 AVAILABLE 2석이면 종료하되 1/2석 사용자는 예매할 수 있다. LOCKED는 AVAILABLE로 간주하지 않는다. SOLD_OUT은 조회 시점의 사용자별 no-selectable-seat이며 영구적 매진 선언이 아니다.

Hot 영역은 **전체 Seat ID 오름차순 앞쪽 floor(initial count × hotSeatRatio)**다. 판매 후 남은 좌석 기준으로 이동하지 않는다. 각 재선택도 hotTrafficRatio로 원하는 영역을 추첨한다. 사용자가 선택한 정책대로 부족하면 다른 영역, 이어 전체 AVAILABLE로 보완한다. N을 임의로 줄이지 않는다.

`seat_choices`에 requested_region/region/seats tag, `seat_selection_fallbacks`에 requested/selected region tag를 저장한다. report에는 요청한 영역 → 실제 영역의 count를 표시한다. 소진 후 실제 Hot 선택 비율이 원래 비율과 같다고 주장하지 않는다. 직전 충돌 set 회피로 영역을 바꾸는 경우도 fallback이다. 1/2/3 비율은 Journey의 원하는 N 분포이며 HTTP attempt별 분포와 같다고 가정하지 않는다.

## Config와 제안 Pilot 조건

기존 schemaVersion 1을 유지하고 알려진 선택 필드만 확장했다. 과거 config에 workloadModel이 없으면 single-attempt로 읽으며 원본 파일은 다시 쓰지 않는다.

| Config | 용도 |
|---|---|
| pilot.example.json / 기존 local | 기존 Single Attempt Pilot, 기존 20/20 값 유지 |
| reselection.pilot.example.json | Reselection 계약 Smoke와 완만한 Pilot 제안 |
| baseline.single-attempt.example.json | Baseline A, stage/VU 결정 대기 |
| baseline.reselection.example.json | Baseline B, Reselection Pilot 검토 뒤 stage/VU 결정 |

아래는 선택 항목 발췌이며 실행에는 예제 전체 JSON을 사용한다.

```json
{
  "workloadModel": "reselection",
  "profile": "musical-low",
  "auth": {"mode": "automatic", "tokenPoolSize": null},
  "measurement": {
    "executor": "ramping-arrival-rate", "timeUnit": "1s", "startRate": 1,
    "stages": [
      {"duration": "30s", "target": 1}, {"duration": "30s", "target": 2},
      {"duration": "60s", "target": 2}, {"duration": "30s", "target": 0}
    ],
    "preAllocatedVUs": null, "maxVUs": null, "gracefulStop": "90s"
  },
  "reselection": {
    "selectionPolicy": "preferred-region-then-other-then-all",
    "thinkTime": {"enabled": false, "distribution": null, "duration": null, "min": null, "max": null},
    "abandonment": {"enabled": false, "maxReselections": null, "maxDuration": null},
    "safetyGuard": {"maxJourneyDuration": "60s"}
  },
  "inventory": {"mode": "db"}
}
```

token pool 크기는 config 또는 `--users`로 명시한다. Reselection measurement와 warm-up의 preAllocatedVUs/maxVUs는 각각 null이며 Single Attempt의 20/20을 자동 복사하지 않는다. 미설정 값은 `--plan`에서 pending_settings로 보이며 실제 실행은 계정/fixture 준비 **전에** 오류로 중단한다. Baseline 예제의 startRate/stages도 null이다. Attempt Scale이나 profile peak로 duration을 역산하지 않는다.

제안 Pilot은 1/s 30초 유지 → 2/s까지 30초 ramp → 2/s 60초 유지 → 0/s까지 30초 ramp다. 기존 Pilot과 유입 조건을 비교할 수 있고 5초 scrape 기준 stage마다 6/6/12/6회 관측 기회가 있다. 총 150초, schedule 적분은 225 starts이며 exact iteration 소진 목표가 아니다. peak는 HTTP RPS가 아니라 새 Journey starts/s다. 최소 API sample 정책은 별도로 선택하며 이 조건이 p99 정밀도를 보장하지 않는다.

예제 warm-up은 1/s 60초를 제안하되 enabled/execution 전체를 설정할 수 있다. 별도 회차·k6 프로세스로 수행하며 본 결과와 합치지 않는다. Reselection Smoke는 독립 per-vu-iterations, 1 VU × 1 iteration이며 maxDuration/gracefulStop도 설정 가능하다. Smoke latency는 성능 기준이 아니다. 1초 미만 계약 Smoke에서 native VU gauge가 없으면 N/A다. Pilot/Baseline에서는 VU sample 검증을 필수로 유지한다.

계약 Smoke는 여섯 API를 실제로 지나 최종 예약 조회를 완료한 Booking이 최소 한 건 있어야 검증 완료다. 이는 Pilot/Baseline의 모든 iteration 성공 조건이 아니다. SOLD_OUT만 발생한 계약 Smoke는 시스템 오류로 재분류하지 않지만 전체 예매 contract를 검증한 것으로 표시하지 않는다.

60초 safety guard는 무한 재선택 방지를 위한 **변경 가능한 기술적 보호 제안**이다. 사용자 max retry/포기 조건이 아니며 retry 횟수 상한은 없다. API 전과 루프/think time 경계에서 검사하고 진행 중 HTTP는 timeout까지 걸릴 수 있다. 엄격한 Journey SLA 60초가 아니다. 예제 timeout 10초에서 guard + 진행 중 요청 시간을 수용하도록 drain 90초를 제안했다. guard 종료는 booking_journey_guard_stopped로 기록하고 검증 실패로 표시한다. SOLD_OUT이나 서버 System Failure로 바꾸지 않는다. 사용자 포기와 think time은 기본 비활성화이며 도입 시 분포/상한을 실험자가 명시한다.

## VU, 계정, fixture 전제

각 VU는 한 번에 하나의 iteration을 실행한다. `peak starts/s × 예상 Journey seconds + 변동 여유`로 초기 VU를 검토하되 retry/think time을 포함하고 Reselection Pilot의 실제 duration, active/max VU, dropped_iterations로 확인한다. Single Attempt p95만으로 Reselection VU를 확정하지 않는다. 부족한 VU와 서버 오류/지연은 별도로 기록한다. [k6 arrival-rate VU allocation](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/arrival-rate-vu-allocation/).

현재 작업 환경의 `reselection.local.json`은 실행 준비를 위해 measurement 160/160 VU, warm-up 80/80 VU, token pool 10으로 설정했다. measurement peak 2/s에서 기술적 guard 60초와 진행 중 요청 timeout 10초를 감안하면 약 140 VU이며, 여유 20을 더해 160을 준비한다. warm-up은 1/s이므로 약 70 VU에 여유 10을 더해 80이다. 이는 실측 최적 VU 수가 아니라 Pilot에서 generator의 VU 부족을 피하기 위한 초기 설정이다. maxVUs도 같은 값으로 두어 실행 중 추가 VU 생성 없이 시작한다. 준비된 VU 중 필요한 만큼만 사용하며 configured arrival rate는 기존 1~2/s 그대로다. pool 10은 기존 실서버 Single Attempt Pilot과 같은 인증 조건을 사용한 선택이며 VU 수와 별개다. 예제 config의 미확정 값과 Baseline config는 그대로 유지한다.

계정/JWT는 기존 자동 도구로 측정 전에 생성·재사용·갱신한다. iteration별 회원가입/로그인은 없다. pool은 iteration round-robin이며 VU/Member 1:1 대응이 없다. 검토한 예매 경로에서 사용자 관련 unique는 Member+idempotency key이고 write lock은 해당 claim/Seat/Reservation 대상이다. Member 전체 lock이나 Member당 한 예약 제한은 발견하지 않았다. 이를 서버 전체에 대한 무경합 보장으로 확대하지 않는다. pool 크기는 별도 실험 조건이다.

profile 또는 `--profile`로 fixture를 선택한다. musical-low=1000석, musical-high=1500석이며 profile 변경이 Pilot stage를 profile peak로 자동 변경하지 않는다. 기존 API가 공연장 seat template으로 회차 생성 시 actual Seat를 만드는 구조를 사용한다. 매번 새 all-AVAILABLE 좌석 수·ID·배치·가격을 사전 claim에서 재검증한다.

## Metrics와 latency

공통/native metric은 유지한다. Configured Iteration Arrival Rate는 schedule 적분, Observed Iteration Start Rate는 raw iteration_started, Completed Iteration Throughput은 raw native iterations 완료 event로 계산한다. 세 값을 같다고 가정하지 않는다. 초별/stage별 count/rate를 보존한다. 전체 completed 분모는 actual workload+drain, 전체 observed starts/s 분모는 configured arrival duration이다. 엄격한 arrival 구간 안/밖 start count도 기록한다. stage별 latency는 완료 timestamp 기준이며 start cohort가 아니다.

| Reselection custom metric | 의미 |
|---|---|
| booking_journey_started | 시작 Journey |
| booking_journey_completed | BOOKING_COMPLETED; 모든 종료 수가 아님 |
| booking_journey_sold_out / booking_journey_system_failed | 현재 N석 불가 / 시스템 오류 종료 |
| booking_journey_abandoned / booking_journey_guard_stopped | 사용자 포기 / 기술적 guard 종료 |
| booking_journey_business_terminated | 기존 결제 만료/환불 대기 Business 종료 |
| seat_selection_attempts | fresh map 이후 선택 판단, SOLD_OUT 판단 포함 |
| pre_reserve_attempts / seat_conflicts | 실제 선점 시도 / 정확한 SEAT_UNAVAILABLE event |
| reselections | 최초 판단 이후 다시 seat-map 조회를 시도한 cycle |
| reselection_stale_refreshes | 직전 충돌 set만 보여 추가 조회한 수 |
| seat_selection_fallbacks | 요청 영역과 실제 영역이 달라진 수 |
| retries_per_journey / retries_per_successful_booking | 모든 Journey / 성공 Journey의 reselection cycle Trend; 0도 sample |
| successful_booking_duration | Journey 시작 → 최종 예약 조회 완료 |
| sold_out_journey_duration | Journey 시작 → N석 선택 불가 확인 |
| failed_journey_duration | Journey 시작 → System Failure 종료 |
| abandoned_journey_duration / guard_stopped_journey_duration / business_terminated_journey_duration | 각 별도 종료 E2E |
| seats_confirmed | client가 detail까지 완료한 Booking 안의 N석 합, 두 모델 공통 |

Outcome latency/retry Trend는 count/avg/p50/p95/p99/max를 저장하고 따로 표시한다. sample이 없으면 count=0, 나머지는 N/A다. 메인 사용자 경험은 successful_booking_duration이다. 기존 혼합 journey_duration과 성공 booking_duration도 raw/summary에 보존하되 Reselection report에서 혼합 Journey를 성공 E2E로 표시하지 않는다.

reservation_success/payment_applied/booking_completed/journey_started/finished는 유지한다. Reselection conflict는 business_failures를 증가시키지 않는다. native iterations는 SOLD_OUT 등 모든 종료를 포함한다. 시작=종료=terminal outcome 합과 duration sample 수를 검증한다. 모든 iteration 성공은 합격 조건이 아니다.

HTTP Failed / Business Conflict / System Failure를 구분한다. http_req_failed는 기존 http.expectedStatuses(200) 그대로다. 409 conflict는 HTTP failed sample이지만 Reselection Journey 실패가 아니다. raw Rate의 passes=failed HTTP, fails=expected HTTP를 유지하고 text/report에서 failed_requests/expected_requests로 표시한다. HTTP 200 DTO 오류는 HTTP failed rate=0이어도 System Failure가 될 수 있다. 정상 conflict는 console error를 출력하지 않는다. System Failure와 guard 종료만 body/token/URL 없이 최소 로그를 남긴다.

## Seat inventory와 oversell

k6 종료 후 cleanup 전에 기존 seat-map API와 DB snapshot을 저장한다. 부하 도중 inventory/Prometheus 조회는 없다. 신규 API/schema는 없다. DB 연결은 기존 fixture_cleanup의 Docker/SSH 경로를 재사용한다. db mode이면 keep-data도 사전 DB 연결 확인을 생략하지 않는다.

DB snapshot은 **해당 연결의 REPEATABLE READ read-only consistent transaction**에서 생성한다. 글로벌/서버 설정을 바꾸지 않고 FOR UPDATE도 사용하지 않는다. random fixture name과 공연장/공연/회차 ID를 대조하고 해당 Seat 및 CONFIRMED Reservation의 reserved_seat 연결만 읽는다. [MySQL transaction 문법](https://dev.mysql.com/doc/refman/8.0/en/commit.html).

ReservedSeat의 unique는 (reservation_id, seat_id)여서 그 제약 하나로 서로 다른 최종 예약 사이의 Seat 중복을 증명할 수 없다. 다음을 함께 확인한다.

1. 각 Seat ID가 두 개 이상의 서로 다른 CONFIRMED Reservation에 포함되지 않음.
2. unique confirmed Seat IDs ≤ initial Seat IDs.
3. 최종 Seat ID 집합이 초기와 같고 confirmed Seat가 초기 fixture 밖에 없고 RESERVED 상태임.

unique count가 capacity보다 작아도 같은 Seat에 두 예약이 있으면 oversell 실패다. duplicate Seat별 Reservation ID 목록을 보존한다. DB 실패/미접근을 0건 oversell로 표시하지 않는다. inventory.mode=api-only는 상태 관찰만 가능하며 oversell=not-verified, validation=needs-review다. 새 예제는 db mode다. SQL 생성·집계 단위 테스트와 작은 실서버 validation에서 실제 DB 연결/read-only snapshot 실행을 확인했다. 해당 validation의 중복 판매 0건을 높은 부하의 결과로 확대하지 않는다.

initial/final AVAILABLE/LOCKED/RESERVED, confirmed assignments, unique sold Seat IDs, confirmed Booking 수, duplicate/oversold Seat 수, sell-through를 저장한다. `seat_sell_through_rate = unique confirmed seats / initial seats`이며 중복이 없으면 confirmed assignments/initial과 같다. client seats_confirmed 및 average_seats_per_completed_booking은 별도다. 최종 client 응답 전에 오류가 나도 DB에는 CONFIRMED가 남을 수 있어 두 값을 억지로 일치시키지 않는다. API/DB snapshot은 서로 다른 시각이며 scheduler/background 변화도 snapshot 시점의 값으로 남긴다.

## 시간, 결과, Prometheus

새 configured directory는 `results/{timestamp}_{profile}_{workloadModel}_{kind}[_warmup]_run-{NN}/`다. 기존 directory를 rename/수정하지 않는다. metadata/summary에도 workloadModel을 남긴다. Baseline `--repeat 3`은 각 회차별 warm-up/measured fixture를 따로 만들고 수집 후 그 회차의 기존 scoped cleanup을 수행한다. 실패하면 다음 회차로 진행하지 않는다. 다른 model/profile을 자동 실행하지 않는다.

새 results의 `{timestamp}`는 한국 표준시(KST, UTC+09:00)이며 `20261011T213000.123456KST`처럼 시간대를 명시한다. run-metadata의 process/arrival/drain/fixture 준비 시각, observations의 stage/초별 구간, inventory/collection의 기록 시각은 `+09:00`으로 저장한다. metadata의 time_zone은 Asia/Seoul이다. report도 KST로 표시하며 과거 UTC metadata를 읽어도 같은 instant를 변환해 표시한다. k6-points의 원본 시각, 복사한 fixture manifest, Prometheus 응답·epoch는 원본을 보존한다. epoch에 9시간을 더하지 않으며 query_range/latency/throughput 계산 구간은 동일하다. 기존 results를 다시 쓰거나 rename하지 않는다.

```text
run-metadata.json
pilot-config.json                       # 공통 실행기의 기존 snapshot 파일명 유지
fixture-manifest.json / fixture-initial-seats.json
k6-effective-options.json / k6-options.json / preflight.log / k6.log
k6-summary.json / k6-summary.txt
k6-points.json                          # timestamp/tag를 포함하는 raw NDJSON
pilot-observations.json                 # 초별/stage별 configured/observed/completed 및 latency
fixture-final-seats.json / seat-inventory.json
seat-inventory-query.sql / seat-inventory-db-snapshot.json  # DB 가능할 때
prometheus/query-plan.json / collection-status.json
prometheus/http.json / tomcat.json / hikari.json / jvm.json
report.md
```

metadata는 workload 시작, arrival 시작/계획 종료/확인 종료, 마지막 iteration/Journey 완료, teardown/drain 종료, k6 process 종료, 수집 완료 measurement_completed_at을 구분한다. Prometheus core 구간은 arrival 시작~actual teardown/drain이다. configured 최대 drain을 무조건 duration에 더하지 않는다. 중단이면 종료 확인 상태/unfinished를 남긴다.

k6 1.x는 schedule 적분이 정수 1..floor(total)에 도달하는 시각에 discrete arrival slot을 만든다. 마지막 slot이 nominal stage 종료 전이고 더 이상 시작할 slot이 없으면 실제 workload도 먼저 끝날 수 있다. normal 종료와 모든 slot의 start/drop 집계를 확인한 경우 arrival_ended_at은 마지막 관측 dispatch 시각을 사용하고 그 basis/status를 표시한다. 이는 native scheduler shutdown의 정확한 timestamp가 아닌 관측 가능한 경계 proxy다. configured_last_arrival_slot_at과 nominal arrival_planned_ended_at도 따로 남긴다. 마지막 slot이 deadline에 걸려 실행되지 않은 경우와 중단은 완료로 꾸미지 않는다. [검증한 k6 1.6.1 실행기](https://github.com/grafana/k6/blob/v1.6.1/lib/executor/ramping_arrival_rate.go).

Prometheus는 기존 metric-inventory.json의 실제 검증 이름과 37개 query 계획을 유지한다. 5초 step/rate window 30초/before-after context는 config로 바꿀 수 있다. 전체 query는 `--plan`, 기존 pilot-query-plan.json, 각 run의 query-plan.json에 보존한다.

| 그룹 | Metric/query |
|---|---|
| HTTP | http_server_requests_seconds count/sum/bucket; booking API throughput, histogram p50/p95/p99, 5xx rate |
| Tomcat | busy / config max / current threads |
| Hikari | active / idle / pending / max; acquire seconds count/sum/max, timeout total, acquisition mean |
| JVM | process CPU usage/time; heap used/committed/max; allocation counter/rate; GC pause bucket/count/sum/max/p95; live/peak/daemon/state/started threads |

query_range는 부하 종료 뒤에만 실행한다. 기존 heap/booking API filter를 유지한다. DB Lock Wait / MySQL Host / OCI Host / Hikari acquisition histogram / deadlocked-thread는 현재 자동 수집 불가다. query 평가 point와 실제 scrape sample을 같은 뜻으로 취급하지 않는다. 종료 후 inventory GET이 after-context에 나타날 수 있으나 core workload에 포함하지 않는다.

report는 shared HTTP/API/resource와 model별 Business/UX 표를 구분하고 조건·관측값·미검증 상태만 기록한다. baseURL을 표시하지 않으며 병목/원인/CPU saturation을 자동 판정하지 않는다.

## 실행 명령 — 구조·설정 검토 뒤 사용

현재 작업 환경의 local config에는 위의 VU/token pool 선택을 반영했다. guard/drain과 최소 sample 정책은 config에 기록한 상태를 유지한다. wrapper가 env.local.sh를 읽고 자동 계정/JWT/fixture를 준비하므로 수동 로그인/current.env source는 필요 없다. 다른 환경에서 local config가 없으면 예제를 복사한 뒤 해당 환경의 VU/token pool을 정한다.

```bash
# 서버 호출 없이 설정 검토
bash k6/scripts/reselection.sh smoke --config k6/config/reselection.local.json --plan
bash k6/scripts/reselection.sh pilot --config k6/config/reselection.local.json --plan

# 구조·설정 검토 후 계약 Smoke만 실행
bash k6/scripts/reselection.sh smoke --config k6/config/reselection.local.json --run-number 1

# Smoke 결과 확인 후 Pilot만 실행 (현재 local config의 VU는 설정 완료)
bash k6/scripts/reselection.sh pilot --config k6/config/reselection.local.json --run-number 1
```

Smoke는 독립 smoke execution을 사용하므로 미확정 Pilot VU 때문에 막히지 않는다. 예제에서 새 config를 만드는 경우 실제 Pilot 전에 measurement/warmup.execution의 preAllocatedVUs/maxVUs를 각각 설정해야 한다. 현재 local config에는 이미 반영했다. wrapper는 config.workloadModel=reselection을 확인하며 몰래 모델을 바꾸지 않는다.

필요 환경변수는 기존 BASE_URL, 원격 DB의 K6_SSH_TARGET/컨테이너 K6_DB_CONTAINER, PROMETHEUS_URL 또는 기존 tunnel용 K6_PROMETHEUS_SSH_PORT다. 다른 환경 파일은 K6_ENV_FILE로 선택한다. preissued auth에서는 TEST_TOKENS_FILE이 필요하다. 자동 모드는 기존 account store를 재사용하고 --accounts로 store를 선택할 수 있다. JWT 만료는 stage+drain+여유를 사전 검증한다. 시각 동기화와 실제 metric 수집은 Pilot에서 확인한다.

다음 명령은 실제 Pilot 검토 뒤 Baseline stage/startRate/VU/warm-up을 명시한 후 사용한다. profile peak를 쓰려면 실험자가 직접 stages를 작성한다.

```bash
cp k6/config/baseline.single-attempt.example.json k6/config/baseline.single-attempt.local.json
cp k6/config/baseline.reselection.example.json k6/config/baseline.reselection.local.json

bash k6/scripts/baseline.sh --config k6/config/baseline.single-attempt.local.json --users 10 --repeat 3 --plan
bash k6/scripts/baseline.sh --config k6/config/baseline.reselection.local.json --users 10 --repeat 3 --plan

# 조건 확정 뒤 한 model/profile만 3회
bash k6/scripts/baseline.sh --config k6/config/baseline.single-attempt.local.json --users 10 --repeat 3
# 별도 실험으로 Reselection 3회
bash k6/scripts/baseline.sh --config k6/config/baseline.reselection.local.json --users 10 --repeat 3
```

## 변경 파일과 검증

새 JS는 lib/reselection-seats.js(AVAILABLE/fallback), reselection.js(순수 engine), reselection-journey.js(k6 adapter), reselection-metrics.js다. pilot.js/config.js/summary.js가 model을 선택·기록한다. metrics.js/journey.js는 공통 seats_confirmed만 추가했다. api.js/contracts.js/seats.js와 HTTP contract는 변경하지 않았다.

Python pilot_config.py/pilot_runner.py/run-experiment.py는 model/phase/config/반복을 확장했다. pilot_observations.py/pilot_report.py/report.py는 raw accounting/종료별 latency/model report를 확장했다. seat_inventory.py, reselection.sh/baseline.sh, 세 config 예제를 추가했다. package.json, 새 unit/localhost 테스트, 기존 Single Pilot stub 회귀 테스트, README를 갱신했다. Prometheus 수집기/query는 수정하지 않았다.

후속 KST 저장은 common.py의 offset 변환과 result prefix, report/observations/collection/claim/cleanup 기록에 적용했다. query 표현식과 epoch 구간은 유지했다. test_result_timezone.py에서 날짜 경계·중복 변환·혼합 offset·Prometheus epoch 보존을 검증한다. 작은 충돌 validation은 reselection-validation.js, scripts/reselection_validation.py, runner의 명시적 validation flag와 별도 report/trace를 추가했다. validation에서만 adapter의 선택적 API 주입 인자로 실제 API 호출 순서를 제어하며 기존 Pilot/Baseline 호출은 그대로다.

구현 후 로컬 검증은 Node 단위 테스트 24개, Python 단위 테스트 20개, localhost 서버와 실제 k6를 연결한 회귀 테스트 9개로 총 53개가 통과했다. 네 config의 `--plan`도 서버 호출 없이 확인했다. 셸 문법·Python compile·diff 공백 검사도 통과했다. 실제 DB 연결/SQL 실행과 실서버 Reselection 실험은 이 검증에 포함하지 않는다.

```bash
node --test k6/tests/*.test.js
python3 -m unittest discover -s k6/tests -p test_pilot.py -v
python3 -m unittest discover -s k6/tests -p test_reselection.py -v
python3 -m unittest discover -s k6/tests -p test_report.py -v
python3 -m unittest discover -s k6/tests -p test_result_timezone.py -v
python3 -m unittest discover -s k6/tests -p test_reselection_http.py -v
python3 -m unittest discover -s k6/tests -p test_pilot_http.py -v
```

HTTP 테스트는 localhost에만 연결하고 테스트 fixture/results를 정리한 뒤 current fixture pointer를 복원한다. 실서버 테스트 전체 discover를 실행할 필요는 없다. unit은 선택 분포/N 유지/fallback/stale set/guard/사용자 포기/think time/응답 분류/oversell을 검사한다. 실제 k6 연결 테스트는 conflict→success, SOLD_OUT, 5xx, 독립 계약 entrypoint, Baseline 3회/6개 fresh fixture를 확인한다. 이를 실서버 Reselection Smoke/Pilot 완료나 성능 측정 결과로 취급하지 않는다.
