# Pilot / Baseline 사전 검토

검토일: 2026-10-11 KST. 이 문서는 현재 구현 확인과 다음 구현 범위를 기록한다. Pilot 또는 Baseline의 측정 결과가 아니다.

이후 구현과 제안 설정은 [Pilot 구현 문서](pilot-implementation.md)에 별도로 기록했다. 아래 내용은 구현 전 사전 검토 기록이다.

## 1. 현재 develop과 실행 대상

| 항목 | 확인 결과 |
|---|---|
| 로컬 브랜치 / HEAD | `develop` / `d9777b0` — 좌석 선택과 예약 정보 조회 추가 |
| 서버 checkout 브랜치 / HEAD | `develop` / `d9777b0` |
| 실행 앱 이미지 ID | `sha256:4847d09556597653ff855c3d17329b2ea8990e70a9829fcce970b836302d9a57` |
| 실행 앱 시작 시각 | `2026-10-10T18:16:00.283692444Z` |
| 실행 결제 provider | `fake` |
| k6 호환 범위 | 기존 1.x summary schema, 현재 검증 버전 1.6.1 |
| 현재 실행 진입점 | `smoke.js`, `run-experiment.py smoke`, 자동 준비용 `smoke.sh` |
| 현재 executor | `per-vu-iterations`, 1–2 VU, VU당 1 iteration |
| arrival-rate 구현 | 값이 비어 있는 JSON template과 좌석 선택 helper만 존재 |

로컬 작업 트리에 `.gitignore` 변경과 미추적 `k6/`가 있다. 서버 checkout에는 예약 패키지 19개 파일의 주석 추가가 있으며, diff에서 주석 외 변경 줄은 발견되지 않았다. checkout HEAD만으로 실행 이미지의 전체 소스를 증명할 수는 없다. 반복 측정에서는 앱 이미지 ID와 실제 실행 설정도 고정·기록해야 한다. 이번 검토에서 앱 소스, 서버 설정, 이미지 또는 인프라는 변경하지 않았다.

기존 구조는 유지할 수 있다. API 호출·계약 검증·Journey·token 선택·좌석 선택·metric·summary는 `lib/`에 분리돼 있고, 계정과 fixture 준비·초기 상태 검증·측정·Prometheus 수집·report는 Python 스크립트로 분리돼 있다.

사용자가 확인한 smoke는 JWT, API contract, 1000석 fixture, 결제와 metric tagging 검증을 완료했다. 1 iteration / 6 HTTP requests / booking_completed 1 / business_failures 0 / system_failures 0 / checks 7 pass / dropped_iterations 0이다. 이 latency를 성능 기준이나 VU 산정 기준으로 사용하지 않는다.

## 2. Smoke summary의 두 항목

### HTTP 요청 시도 수

`lib/api.js`의 `request()`는 `http.request()` 직전에 `metrics.httpAttempted.add(1, {api})`를 실행한다. 기존 `http_offered`도 같은 위치에서 증가했다. 의미는 **클라이언트가 시도한 HTTP 호출 수**이며, 서버에 도착한 요청 수나 iteration arrival rate가 아니다. timeout·network error·request exception도 시도에 포함될 수 있다.

앞선 변경에서 이미 `http_attempted`로 명칭을 바꿨다. 이번 검토에서는 추가 변경하지 않았다. 과거 raw summary / points는 보존하고, summary formatter와 report는 과거 `http_offered`를 읽을 때 새 이름으로 표시한다.

| 지표 | 현재 의미 |
|---|---|
| `journey_started` | 예매 iteration이 Journey 함수에 진입한 횟수 |
| `http_attempted` | HTTP 호출 시도 횟수 |
| `http_completed` | transport failure 없이 HTTP 응답을 받은 횟수. 4xx/5xx도 포함 |
| `http_reqs` | k6의 기본 HTTP 요청 수. custom attempted와 별도로 보존 |
| `http_contract_success` | 기대 상태와 응답 DTO 계약을 만족한 API 요청 수 |
| `booking_completed` | 예약 상세의 CONFIRMED / RESERVED 상태까지 확인한 횟수 |

성공한 Journey에는 6개 HTTP 요청이 있지만, 중간 실패 시 후속 요청을 보내지 않는다. iteration arrival rate에 항상 6을 곱해 HTTP RPS로 표시하면 안 된다.

### HTTP 실패율 표시

실제 성공 smoke의 raw `http_req_failed.values`는 `fails=6, rate=0, passes=0`이었다. k6 1.x Rate summary의 `passes`는 non-zero/true sample 수, `fails`는 zero/false sample 수다. 실패 여부를 sample로 받는 `http_req_failed`에서는 이름만 보면 방향을 반대로 읽기 쉽다.

앞선 변경에서 해당 지표와 submetric의 **text formatter만** 다음처럼 표시하도록 수정했다.

```text
http_req_failed: rate=0.000 failed_requests=0.000 expected_requests=6.000
```

raw JSON의 키·값, `checks`의 passes/fails, 실제 실패율은 바꾸지 않았다. `rate`는 0–1 비율이다. 현재 callback은 `http.expectedStatuses(200)`이므로 좌석 충돌 409도 이 지표에는 실패로 들어간다. 따라서 `http_req_failed`와 `system_failures`는 같은 지표가 아니다. HTTP 200도 DTO 검증에 실패할 수 있어 `expected_requests`가 예매 성공 수를 뜻하지 않는다.

근거: [k6 Rate](https://grafana.com/docs/k6/latest/javascript-api/k6-metrics/rate/), [기본 HTTP metrics](https://grafana.com/docs/k6/latest/using-k6/metrics/reference/). 이번 검토에서 Node 10개 테스트와 Python 13개 테스트가 통과했고, 명시적 opt-in이 필요한 실서버 테스트 1개는 실행하지 않았다. Python HTTP 테스트는 localhost stub만 사용했다.

## 3. Workload model과 profile

본 측정과 Pilot은 `ramping-arrival-rate`를 사용한다. 표기 단위는 **Iteration Arrival Rate (booking attempts started/s)**다. `Peak req/s`는 사용하지 않는다.

`config/profiles.json`이 profile의 유일한 실행 정의다. 요청한 10개 profile의 값과 일치한다.

| Profile | Seat Count | Attempt Scale | Profile Peak iterations/s | Hot Seat | Hot Traffic | 1/2/3석 |
|---|---:|---:|---:|---:|---:|---|
| musical-low | 1000 | 500 | 20 | 20% | 35% | 60/30/10 |
| musical-high | 1500 | 3000 | 150 | 20% | 65% | 50/35/15 |
| idol-low | 1000 | 800 | 40 | 30% | 45% | 55/30/15 |
| idol-high | 1500 | 8000 | 500 | 20% | 75% | 45/35/20 |
| rapper-low | 1000 | 400 | 20 | 20% | 35% | 65/25/10 |
| rapper-high | 1500 | 2500 | 120 | 20% | 60% | 55/30/15 |
| ballad-low | 1000 | 500 | 15 | 30% | 30% | 60/30/10 |
| ballad-high | 1500 | 2000 | 80 | 25% | 55% | 55/30/15 |
| overseas-low | 1000 | 700 | 30 | 25% | 40% | 60/30/10 |
| overseas-high | 1500 | 10000 | 700 | 15% | 80% | 45/35/20 |

기존 필드 `referenceAttemptors`는 Attempt Scale 참고값이고 exact iteration count가 아니다. `peakJourneyArrivalRate`는 profile에 기재한 peak다. Pilot의 실제 stage peak는 별도로 저장해야 한다. profile peak를 그대로 Pilot에 적용하거나 Attempt Scale에서 duration을 역산하지 않는다.

`selectWorkloadSeats()`를 재사용한다. ID 오름차순 앞쪽 `floor(seatCount × hotSeatRatio)`가 hot 영역이며, Journey마다 hot/cold와 1/2/3석을 확률로 선택한다. 한 Journey 안에서는 좌석 중복을 피하지만 사용자 사이의 재선택은 허용한다. 인접 좌석 선택을 보장하지 않으며 AVAILABLE 필터나 실패 재시도도 하지 않는다.

성공 예약은 좌석을 소진한다. stage가 진행되면서 Business Failure 비율과 후속 API sample 수가 바뀔 수 있으므로 이를 그대로 기록한다. 측정 도중 좌석을 reset하거나 남은 좌석으로 자동 대체하면 workload 의미가 달라진다.

## 4. Fixture와 인증

### 매 run의 초기 좌석 상태

현재 API 준비 순서는 다음과 같다.

```text
테스트 계정/JWT 준비
→ POST /api/catalog/halls
→ POST /api/catalog/performances
→ POST /api/catalog/performances/{id}/times
→ 반환된 performanceTimeId로 GET /api/seats/{id} 검증
```

`PerformanceService.createPerformanceTime()`은 회차 저장과 `SeatProvisioningService.createSeats()`를 같은 transaction에서 호출한다. 좌석 생성은 공연장 seat template과 공연별 등급 가격을 사용하며 초기 상태는 AVAILABLE이다.

`prepare-fixture.py`는 profile 좌석 수에 맞춰 hall/template을 만들고 좌석 수·좌표·등급·가격·AVAILABLE 상태를 검사한다. 현재 template은 50석/행, A 구역, R 등급, 10000원이며 1000석은 20행, 1500석은 30행이다. 준비 시작/종료, 반환 ID, 초기 좌석 snapshot과 hash를 manifest에 보존한다. runner는 실행 직전에 다시 검증하고 fixture claim으로 중복 사용을 막는다.

반복 측정 전략은 **warm-up과 각 measured run에 새 공연장·공연·회차를 생성**하는 것이다. 이전 예약은 새 회차의 좌석 상태에 영향을 주지 않는다. 준비는 k6 측정 경로 밖에서 수행한다. `current.env.sh`는 가장 최근 준비 결과를 가리키는 편의 파일이며, 실험의 기준 기록은 각 run에 복사한 manifest다. 자동 runner에서 ID 전달을 처리하므로 수동 source를 필수 절차로 만들 필요가 없다.

기존 `reset-fixture.py`도 사용할 수 있다. 이는 새 reset API가 아니라 기록된 fixture의 MySQL transaction 정리다. 기록 ID와 임의 이름, 테스트 회원, 좌석 참조 범위, FAKE 결제 여부 등을 검증한 뒤 관련 예약·결제·멱등 기록만 제거하고 좌석을 AVAILABLE로 복원한다. `--mode delete`는 해당 fixture의 catalog까지 제거한다. 자동 정리는 부하와 종료 후 관측 자료 수집이 끝난 다음 수행한다.

새 fixture 전략은 동일한 좌석 초기 조건을 재현한다. 전체 DB 크기, auto-increment ID, JVM/JIT 또는 DB cache 상태까지 동일하게 만든다는 뜻은 아니다. 테스트 계정은 유지·재사용하며 결과 파일도 삭제하지 않는다.

코드 근거: [회차 생성](../../src/main/java/org/example/ticket/performance/service/PerformanceService.java), [실제 좌석 생성](../../src/main/java/org/example/ticket/seat/service/SeatProvisioningService.java), [fixture 준비](../scripts/prepare-fixture.py), [실행 전 검증](../scripts/fixture_state.py), [범위 지정 정리](../scripts/fixture_cleanup.py).

### JWT pool과 Member 재사용

`test_accounts.py`는 임의 계정 이름/비밀번호를 기존 register API로 저장하고, 다음 실행에서 같은 계정을 재사용·JWT 갱신한다. 준비한 pool은 iteration 번호로 순환 선택하고 Journey 내에서는 같은 JWT를 사용한다. 로그인/회원가입/BCrypt/토큰 발급은 측정에서 제외하지만, 예매 요청의 서버 JWT 검증은 포함한다. VU와 Member를 1:1로 매핑하지 않는다.

현재 코드에서 확인한 제약은 다음과 같다.

| 대상 | 잠금 / 제약 |
|---|---|
| Member | repository에 `@Lock` 없음. entity에 `@Version` 또는 활성 예약 수 상태 없음. username unique |
| 예약 멱등성 | `(member_id, idempotency_key)` unique, 개별 claim ID 잠금 |
| Seat | 선택한 회차/좌석 ID에 비관적 잠금, entity의 version |
| Reservation | 개별 예약 ID 잠금. 회원별 활성 예약 수 제한 발견되지 않음 |
| PaymentOrder | `(member_id, idempotency_key)`, reservation ID, merchant order ID unique. 개별 주문 ID 잠금 |

Journey가 run/VU/iteration 조합의 서로 다른 예약·결제 멱등 키를 사용하므로 동일 Member의 여러 iteration을 재사용할 수 있는 구조다. 회원 전체를 명시적으로 직렬화하는 잠금이나 회원당 1회 예약 제한은 확인되지 않았다. DB 내부 인덱스/FK 검사/캐시 경합의 성능 영향이 없다는 증명은 아니므로 pool 크기와 구성은 고정된 실험 조건으로 남긴다.

기존 자동 smoke의 `TEST_USER_COUNT=10`은 smoke 준비 기본값이다. 이를 Pilot의 pool 크기로 자동 채택하지 않는다. Pilot은 사용자가 지정한 계정 수 또는 명시적으로 선택한 기존 pool의 실제 길이를 기록해야 한다. 회원은 iteration마다 만들지 않는다. 측정 전에 JWT 만료 시각과 예정 실행 시간을 확인하고, 측정 중 로그인 fallback이나 자동 갱신을 넣지 않는다. 코드 기본 JWT TTL은 2h이며 실제 토큰의 만료가 기준이다.

코드 근거: [MemberRepository](../../src/main/java/org/example/ticket/member/repository/MemberRepository.java), [예약 생성](../../src/main/java/org/example/ticket/reservation/service/ReservationCreationService.java), [예약 멱등 unique](../../src/main/java/org/example/ticket/reservation/domain/ReservationIdempotency.java), [결제 unique](../../src/main/java/org/example/ticket/payment/domain/PaymentOrder.java), [결제 준비](../../src/main/java/org/example/ticket/payment/service/PaymentPreparationService.java), [결제 완료](../../src/main/java/org/example/ticket/payment/service/PaymentCompletionService.java).

## 5. Pilot 실행 구조와 warm-up 경계

현재 `run-experiment.py`의 kind는 smoke만 허용한다. `--phase warmup`은 smoke의 tag/metadata를 바꿀 뿐 warm-up executor를 구현하지 않는다. `summary.experiment.kind`도 smoke로 고정돼 있다.

다음 구현은 기존 helper를 재사용한 Pilot 진입점과 runner의 Pilot 분기만 추가한다. Baseline 반복 실행과 전체 profile 순회는 Pilot 확인 뒤 구현한다.

한 Pilot의 제안 순서는 다음과 같다.

```text
명시된 실행 설정 / token pool 검증
→ 계정 재사용 및 JWT 사전 발급
→ warm-up 전용 fixture 준비·검증
→ warm-up 전용 k6 프로세스·결과 directory
→ 측정 전용 새 fixture 준비·검증
→ 측정 시각 및 arrival schedule 기록
→ ramping-arrival-rate Pilot 1회
→ 종료·drain 시각 기록 / k6 결과 저장
→ 종료 후 Prometheus 구간 수집
→ 관측값 report / Pilot 검증표 생성
→ 사용 fixture 정리
```

warm-up은 JVM class loading/JIT/pool 등 초기 영향 분리를 위한 단계이며 본 측정 summary에 합치지 않는다. warm-up 부하·duration은 별도 config로 받는다. warm-up을 생략하는 실행도 명시적으로 기록해야 하며, 미설정 값을 자동으로 결정하지 않는다. warm-up 후 새 fixture 준비가 발생한다는 경계도 metadata에 남긴다.

Pilot의 첫 fixture 후보는 이미 smoke로 검증한 `musical-low` 1000석이다. 해당 profile의 peak 20/s를 Pilot의 목표로 자동 적용하지 않는다. 낮은 유입률에서 scheduler·sample·metric 수집을 검증할 수 있도록 별도 stage를 정하고, 이유와 실제 값을 config/metadata에 남긴다.

현재 템플릿의 `startRate`, `stages(duration/target)`, `preAllocatedVUs`, `maxVUs`, `gracefulStop`은 미확정이다. `timeUnit=1s`를 유지하고 숫자 설정은 문서 보고 뒤 확정한다. startRate/target에는 0도 유효할 수 있어 smoke의 양의 정수 검증을 그대로 재사용하면 안 된다. stage target까지 선형으로 변화하며, 일정 rate 유지에는 이전 target과 같은 target의 별도 stage가 필요하다. executor가 유입을 조절하므로 iteration 끝의 sleep을 추가하지 않는다. [k6 executor 문서](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-arrival-rate/).

VU 수는 부하 발생기의 동시 실행 용량이고 pool 크기와 다르다. 충분한 VU가 없으면 설정된 iteration이 시작되지 않을 수 있다. Pilot에서 실제 Journey duration과 VU 사용·drop을 확인해 이후 실행 용량을 검토한다. 서버 설정을 튜닝하거나 VU를 몰래 늘리지 않는다. [Dropped iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/).

### Pilot 검증 기준

| 검증 항목 | 필요한 기록 / 판단 방식 |
|---|---|
| 의도한 arrival 실행 | effective executor/options와 입력 config 대조 |
| 실제 iteration start rate | Pilot 진입 직후 `iteration_started`를 기록하고 초/구간별 timestamp를 설정의 선형 arrival schedule과 비교. `journey_started`와도 대조 |
| dropped iterations | 시간대별/전체 count 저장. 0인지 확인. 발생 원인은 자동 단정하지 않음 |
| latency samples | API별 count, p50/p95/p99, 구간별 sample 수 보존 |
| Journey 기록 | `journey_started`, `journey_finished`, `journey_duration`, 중단 여부 대조 |
| 예약/결제/완료 | `reservation_success`, `payment_applied`, `booking_completed` 별도 count |
| 실패 분류 | Business와 System을 분리하고 API/reason/status/error_code 보존 |
| 시간 매칭 | scheduled arrival, Journey, drain, process, Prometheus 범위를 UTC로 기록 |
| 내부 metric | 실제 노출 이름, Prometheus series와 구간의 scrape 상태 확인 |
| 본 측정 stage duration | 후속 API sample 확보, 관측 구간 길이, percentile 변동, 좌석 소진 경과를 검토 후 결정 |

현재 `journey_started`는 Journey 진입 수이며 별도 iteration 진입 counter는 없다. Pilot에 추가할 `iteration_started`와 구분한다. k6 기본 `iterations`도 start rate 대용으로 사용하지 않는다. 정상 종료 시 Journey started/finished와 booking completed/business/system outcome 합계를 대조하고, 차이는 강제 중단이나 기록 누락 여부와 함께 보존한다.

예정 start 수는 arrival schedule의 적분으로 검증한다. 이는 Attempt Scale을 소진하려는 duration 계산이 아니다. 초 경계와 부분 구간의 반올림을 고려해야 하므로 매 초 target과 count가 정확히 같아야 한다고 판정하지 않는다.

표본이 있다는 것과 p99가 충분한 표본으로 계산됐다는 것은 다르다. API sample count와 원본 latency를 저장하고, 필요한 sample 정책을 명시적으로 결정한다. 정책이 아직 없으면 충분성은 `미판정`으로 표시한다. 보편적인 표본 수를 임의로 정하거나 작은 sample의 percentile만으로 Pilot 완료를 선언하지 않는다. 후속 API는 좌석 충돌로 sample이 줄 수 있으며, sample 0은 0ms가 아니라 N/A다.

현재 smoke threshold의 `checks rate==1`, `business_failures==0`, `booking_completed==VU 수`는 Pilot에 그대로 적용할 수 없다. 좌석 충돌은 기대 Business Failure이고 Journey 완료 check는 false가 될 수 있다. API contract/system 오류, drop, 기록 누락을 검증하는 Pilot 기준과 예매 성공률 관측을 분리해야 한다. latency SLO나 병목 판정 threshold를 새로 만들지 않는다.

## 6. 측정 시간과 결과 저장

현재 runner는 Python/k6 프로세스 시작/종료와 raw points의 첫 `journey_started` / 마지막 `journey_finished`를 저장한다. arrival schedule의 시작/끝과 gracefulStop drain은 분리하지 않는다. 강제 중단된 Journey는 finished/finally metric이 누락될 수 있으므로 마지막 정상 finish만으로 측정 종료를 결정하면 안 된다.

Pilot에서 다음 경계를 추가로 기록해야 한다.

| 시간 경계 | 용도 |
|---|---|
| 준비 시작/종료 | JWT / fixture가 측정 밖인지 확인 |
| warm-up 시작/종료 | 독립 run 참조, 본 측정과 분리 |
| scenario 시작 및 scheduled arrival 종료 | 설정 start rate / stages와 실제 starts 비교의 분모 |
| 첫 Journey 시작 / 마지막 Journey finish | 실제 업무 수행 구간 |
| drain 종료 / scenario 종료 / 강제 중단 | 미완료 Journey와 끝부분 처리량 식별 |
| 프로세스 시작/종료 / monotonic elapsed | 초기화·후처리 포함 구간과 경과 시간 확인 |
| Prometheus 조회 범위 / scrape 상태 | Grafana absolute time range와 대응 |

현재 counter의 summary `rate`는 `k6 testRunDuration`을 분모로 하는 평균이다. scheduled arrival rate와 동일한 분모가 아니다. Pilot report에는 분모를 명시하고, 시간대별 starts / Journey finishes / booking completed / HTTP attempted / HTTP completed를 별도로 저장해야 한다. 완료된 Journey와 성공한 예매 처리량도 각각 표시한다.

현행 directory는 `{timestamp}_{profile}_run-{n}`이며 아래 파일을 이미 보존한다. 다음 단계에서는 `run-01` 같은 두 자리 번호로 확장할 수 있다. 이전 결과 directory는 변경하지 않는다.

```text
k6/results/{timestamp}_{profile}_run-01/
  run-metadata.json
  fixture-manifest.json
  fixture-initial-seats.json
  k6-effective-options.json
  k6-options.json
  preflight.log
  k6.log
  k6-summary.json
  k6-summary.txt
  k6-points.json                 # timestamp/tag가 있는 NDJSON
  report.md
  prometheus/
    collection-status.json
    actuator-prometheus.txt
    verified-metric-names.json
    <verified-metric-name>.json  # 현재 collector 구조 유지
```

metadata에 profile/run 번호, fixture ID/seat count/초기 snapshot, profile 비율, token pool 크기·선택 방식·서로 다른 Member 수, Git/source hash, k6 버전, 실제 실행 옵션과 시간 경계가 있다. **configured arrival stages, Pilot의 실제 configured peak, warm-up 설정/독립 run 참조, preAllocatedVUs/maxVUs, 난수 seed, 중단 상태**는 다음 구현에서 보완한다.

현재 raw summary/points에 기본 `http_reqs`, `iteration_duration`을 보존하지만 report에는 나오지 않는다. `vus`, `vus_max`도 k6 기본 metric이지만 확인한 짧은 실서버 smoke summary에는 실제 sample이 없었다. Pilot에서는 VU 시계열 수집 여부까지 확인하고, 누락을 0으로 표시하지 않는다. 기존 report의 HTTP 전체/API별/Journey latency와 business/system counter는 유지하면서 다음을 추가한다.

- Traffic: configured stages, actual starts, Journey start/finish, HTTP attempted/completed/reqs, drop, VUs와 시간대별 throughput.
- Latency: HTTP 전체/API별, journey_duration, iteration_duration의 sample count와 p50/p95/p99.
- Business Result: reservation_success, payment_applied, booking_completed, business_failures.
- Failures: System 총수와 HTTP 5xx / timeout / network / unexpected response / contract 오류 / client 오류별 count.
- Tomcat / Hikari / JVM: 구간, series labels, 관측값, 미수집 여부.
- Artifacts: 원본과 config/metadata, warm-up 참조, Prometheus 자료 경로.

실패 reason은 이미 raw points에 있다. HTTP 5xx/timeout 등의 표는 종료 후 reason-tagged counter를 집계할 수 있다. Journey 실패와 API 실패 counter를 합해 같은 실패를 두 번 세지 않는다. HTTP 전체 5xx 수는 필요하면 기본 HTTP status points와 대조한다.

report에는 Base URL을 출력하지 않는 기존 동작을 유지한다. raw metadata의 대상 식별 정보와 비밀정보 없는 실행 조건은 보존한다. 원인·saturation·병목을 자동으로 서술하지 않는다.

## 7. 현재 서버의 Prometheus / Grafana 확인

읽기 전용 확인 시각: `2026-10-10T18:38:16.881086Z` (KST 2026-10-11 03:38:16). 앱 exposition에서 sample 이름 152개를 확인했다. 기존 Prometheus target은 `job=imticket-v2`, health `up`, scrape interval/timeout 각 5s다. 기존 SSH tunnel로 접근했으며 새 infrastructure/exporter는 추가하지 않았다.

확인 시점 직전 60초를 step 5s로 조회한 결과, 아래 그룹의 노출 이름은 모두 Prometheus series에도 존재했다. Gauge 단일 series는 13개 **query evaluation point**를 반환했다. 이는 13개의 독립된 scrape 또는 Pilot sample을 증명한다는 뜻은 아니다. 이 확인 구간은 부하 실험 구간이 아니다.

| 그룹 | 실제로 노출되고 수집된 이름 |
|---|---|
| HTTP throughput | `http_server_requests_seconds_count` |
| HTTP latency histogram | `http_server_requests_seconds_bucket`, `_sum`, `_count` |
| Tomcat | `tomcat_threads_busy_threads`, `tomcat_threads_config_max_threads`, `tomcat_threads_current_threads` |
| Hikari connections | `hikaricp_connections_active`, `_idle`, `_pending`, `_max` |
| Hikari acquisition | `hikaricp_connections_acquire_seconds_count`, `_sum`, `_max`; `hikaricp_connections_timeout_total` |
| CPU | `process_cpu_usage`, `process_cpu_time_ns_total`, `system_cpu_usage`, `system_cpu_count` |
| Heap | `jvm_memory_used_bytes`, `jvm_memory_committed_bytes`, `jvm_memory_max_bytes`의 `area=heap` series |
| Allocation | `jvm_gc_memory_allocated_bytes_total` |
| GC pause | `jvm_gc_pause_seconds_bucket`, `_count`, `_sum`, `_max` |
| Threads | `jvm_threads_live_threads`, `_peak_threads`, `_daemon_threads`, `_states_threads`, `_started_threads_total` |

전체 이름과 Prometheus series 존재 여부는 [metric-inventory.json](metric-inventory.json)에 기록했다. 원본 exposition/query 응답은 해당 inventory의 artifact directory에 보존했다. CPU 이름이 노출돼 있다는 사실로 OCI VM 전체의 host 관측이 확보됐다고 간주하지 않는다.

Hikari acquisition bucket은 현재 없다. 기존 sum/count에서 평균을 조회할 수 있고 max도 관측할 수 있지만 acquisition p95/p99를 histogram으로 계산할 수는 없다. 현재 Grafana의 deadlocked thread 패널이 쓰는 `jvm_threads_deadlocked_threads`도 exposition에 없다. 이 항목은 미수집으로 남기며 metric을 새로 만들지 않는다.

**DB Lock Wait / MySQL Host / OCI Host: 현재 자동 수집 불가.** 확인한 Prometheus에는 app target만 있고 해당 exporter sample도 발견되지 않았다.

현행 collector는 부하 종료 뒤 앱 exposition을 한 번 읽어 실제 이름을 확인하고, 해당 Journey 구간을 `query_range`로 조회한다. 실험 중 반복 Prometheus API 호출은 없다. 구체 이름을 가정한 metric이나 새 exporter 없이 이 구조를 재사용한다. [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/).

Pilot에서 다음 시간축 한계를 확인·보완해야 한다.

- `query_range`의 step은 query 해상도이며 scrape interval을 바꾸지 않는다. 5s보다 짧은 실험도 series가 반환될 수 있으므로 non-empty만으로 충분한 관측을 선언하지 않는다.
- 실험 종료 직후 최종 요청 counter가 아직 scrape되지 않았을 수 있다. 종료 후 scrape의 lastScrape를 확인하고, 측정 경계와 조회용 전후 context 구간을 따로 기록한다. 부하 중에는 API를 조회하지 않는다.
- 기존 Grafana의 rate/histogram query는 `[1m]` window를 사용한다. 측정 첫 1분에는 이전 warm-up/fixture 작업이 window에 들어갈 수 있다. absolute UTC 범위를 맞추는 것만으로 이 영향을 제거했다고 할 수 없다.
- raw app series에는 k6 run_id가 없다. HTTP URI/status labels로 예매 API를 구분할 수 있지만 Tomcat/Hikari/JVM은 해당 시간대 앱 전체 관측이며 다른 요청·scheduler 작업도 포함될 수 있다.
- app exposition을 수집하는 요청은 실험이 끝난 뒤에 발생한다. 로그인/fixture/actuator 요청을 예매 HTTP throughput에 섞지 않도록 query 조건과 window를 기록한다. 빈/NaN/누락 series는 0으로 바꾸지 않는다.

## 8. 실행 환경변수 목록

### 현재 구현이 사용하는 값

| 환경변수 | 역할 / 설정 방법 |
|---|---|
| `BASE_URL` | 기존 앱의 실제 origin. 인증/fixture/k6/metric discovery 공통 |
| `WORKLOAD_PROFILE` 또는 `--profile` | `profiles.json`의 단일 profile 선택 |
| `TEST_TOKENS_FILE` | 측정 전에 발급한 여러 JWT pool 파일. `TEST_ACCESS_TOKEN`과 동시 사용 금지 |
| `TEST_ACCESS_TOKEN` | 수동 smoke의 단일 JWT 대안. 여러 계정 Pilot은 pool 파일 사용 |
| `TEST_USER_COUNT` 또는 `--users` | 자동 계정 준비 수. smoke 기본값 10; Pilot 값은 별도 확정 |
| `TEST_ACCOUNT_WORKERS` 또는 `--workers` | 사전 계정 준비 동시 요청 수. 현재 기본 4. k6 VU와 무관 |
| `FIXTURE_MANIFEST` | 준비 도구/자동 runner가 전달하는 manifest 경로 |
| `PERFORMANCE_TIME_ID` | 준비 도구가 반환 ID에서 전달. 수동 입력하거나 run 사이 재사용하지 않음 |
| `HTTP_TIMEOUT` | 요청 timeout. 현재 미설정 시 k6 default, 실제 적용값 기록 |
| `PROMETHEUS_URL` | 기존 Prometheus 접근 주소. tunnel 사용 시 임시 주소로 자동 대체 |
| `PROMETHEUS_JOB` | 기존 scrape job. 현재 기본 `imticket-v2` |
| `PROMETHEUS_STEP_SECONDS` | 종료 후 query_range 해상도. 현재 기본 5 |
| `PROMETHEUS_TIMEOUT_SECONDS` | 종료 후 discovery/query timeout. 현재 기본 10 |
| `K6_SSH_TARGET` | 기존 원격 Docker/MySQL 정리와 임시 metrics tunnel의 SSH alias |
| `K6_DB_CONTAINER` | 기존 MySQL container 이름. 현재 기본 `imticket-v2-mysql` |
| `K6_PROMETHEUS_SSH_PORT` | 기존 서버 내부 Prometheus port. 현재 로컬 설정에 9090 지정 |
| `SMOKE_VUS`, `SMOKE_SEATS` | 기존 smoke 전용. Pilot arrival/VU 설정으로 재사용하지 않음 |
| `SMOKE_MAX_DURATION`, `SMOKE_GRACEFUL_STOP` | 기존 smoke 종료 상한. Pilot stage duration과 별도 |
| `RUN_DIR`, `RUN_ID`, `RUN_PHASE` | runner가 k6에 전달하는 내부 값. 사용자 수동 설정 불필요 |

현재 로컬 `env.local.sh`에는 서버/Prometheus/SSH와 smoke 계정 준비 설정이 있다. profile/fixture ID 등은 준비 과정에서 전달한다. 수동 로그인용 `TEST_USERNAME`, `TEST_PASSWORD`, `TOKEN_OUTPUT_FILE`은 기존 `issue-token.py`를 직접 쓸 경우에만 필요하고 자동 준비에는 필수가 아니다.

### 다음 구현에서 분리할 Pilot 설정 — 아직 사용 불가

`EXPERIMENT_CONFIG`로 별도 JSON을 지정하는 방식을 제안한다. 기존 `ramping-arrival-rate.template.json`의 필드를 재사용하고, warm-up 실행 설정을 따로 넣는다. 다음은 형식 제안이며 실행 가능한 설정이 아니다.

```json
{
  "profile": "musical-low",
  "randomSeed": null,
  "measurement": {
    "executor": "ramping-arrival-rate",
    "timeUnit": "1s",
    "startRate": null,
    "stages": [{"duration": null, "target": null}],
    "preAllocatedVUs": null,
    "maxVUs": null,
    "gracefulStop": null
  },
  "warmup": {"enabled": null, "execution": null},
  "sampleAssessment": {"minSamplesPerApi": null}
}
```

rate/duration/VU/warm-up/pool 크기와 표본 판정 정책은 미확정이다. pool 크기는 명시한 `TEST_USER_COUNT` 또는 선택한 JWT 파일의 실제 길이로 받고, stage/VU와 따로 기록한다. `maxVUs`를 생략하는 경우의 k6 기본 동작도 effective options에 명시한다. 미설정 config를 profile peak나 smoke default로 자동 채우지 않는다.

난수 seed와 iteration 번호를 이용해 좌석 선택 sequence를 재현할 수 있도록 한다. VU 배정이나 run ID가 바뀌었다고 동일 조건의 좌석 선택 sequence까지 달라지지 않게 설계하고 실제 seed를 보존한다.

## 9. 다음 작업의 범위와 Baseline 진입 조건

먼저 이 검토 결과를 보고한다. 그 다음 구현 범위는 Pilot config 검증, 기존 Journey/helper를 쓰는 arrival-rate 진입점, 독립 warm-up/fixture 경계, 시간대별 관측 자료와 필요한 metadata/report 보완이다. 이번 검토에서는 실행 코드를 추가하거나 실제 Pilot을 실행하지 않았다.

Pilot 결과에서 scheduler/actual arrivals/drop, API별 sample과 percentile, Journey/business/system 집계, Prometheus 시간 매칭과 내부 metric을 확인한다. sample 정책 또는 실행 설정이 미확정이면 그 상태를 명시한다. 부족한 항목을 숨긴 채 Baseline으로 자동 진행하지 않는다.

Pilot이 정상 종료하고 본 측정 stage duration이 결정되면, **선택한 단일 profile**을 같은 조건으로 run-01 / run-02 / run-03 반복할 수 있게 확장한다. 매 run의 JWT 준비·fixture 검증·독립 warm-up·측정·종료 후 수집·report·정리를 수행하고 raw 데이터를 보존한다. 전체 profile 일괄 실행, 앱 로직 변경, 성능 개선, 예상 병목에 따른 설정 튜닝, 신규 infra/exporter, 단일 metric에 근거한 원인 자동 단정은 이 단계에 포함하지 않는다.
