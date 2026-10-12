# Reselection 충돌 분기 validation

성능 측정이 아니라 기존 Reselection 코드의 한 경로를 확인하는 작은 실험이다. 애플리케이션/API/인프라와 두 모델의 Baseline 설정은 변경하지 않았다.

사용자가 실행한 `20261011T051214.683872Z_musical-low_reselection_pilot_run-01`은 Journey 시작/Booking 완료 각각 224, seat_conflicts/reselections/System Failure/drop 각각 0이었다. 해당 run에서는 재선택 분기를 실행하지 않아 별도 validation을 추가했다.

## 조건과 호출 순서

- 새 1000석 fixture, 인증된 Member 둘: subject와 conflict holder.
- subject는 1 VU × 1 iteration, 원하는 좌석 1석.
- validation에서만 Hot 범위를 첫 Seat 1개, Hot Traffic 100%, 1/2/3 비율 100/0/0으로 제한.
- think time/사용자 포기/warm-up은 이 validation에서 제외.
- 원본 config는 다시 쓰지 않고 실행용 snapshot에만 validationScenario를 기록.

```text
subject: 실제 seat-map → 첫 AVAILABLE 선택 → 실제 selection-summary
holder:  동일 좌석의 selection-summary → pre-reserve 성공
subject: 기존 pre-reserve → 실제 SEAT_UNAVAILABLE
         기존 Reselection engine에서 seat_conflicts +1, reselections +1
         실제 seat-map 재조회 → LOCKED 확인 → 다른 AVAILABLE 선택
         selection-summary → pre-reserve → prepare → verify → detail
         BOOKING_COMPLETED
```

같은 `reselectionJourney`/`runReselection`과 `bookingApi`를 사용한다. adapter의 선택적 API 인자는 validation의 호출 순서를 제어하기 위한 것이며 기존 Pilot/Baseline 호출은 원래 bookingApi를 생성한다. 서버 응답과 좌석 상태는 기존 API의 실제 결과다. VU 사이의 공유 inventory는 없다.

holder는 두 API만 호출하고 Journey로 집계하지 않는다. subject의 최초 3개 + 재선택 후 6개, holder의 2개를 합쳐 HTTP 요청은 11개다. subject의 pre_reserve_attempts는 2개다. holder의 미결제 선점도 새 fixture에 한정되며 snapshot 후 기존 scoped cleanup으로 함께 삭제한다.

## 실행

```bash
# 서버 호출 없이 제한된 조건 확인
bash k6/scripts/reselection.sh validation --config k6/config/reselection.local.json --plan

# 이 검증만 1건 실행
bash k6/scripts/reselection.sh validation --config k6/config/reselection.local.json --run-number 1
```

wrapper가 token pool 2를 명시한다. 계정/JWT 준비·새 fixture 생성·검증·결과 저장·DB snapshot·fixture 정리는 기존 도구가 수행한다. 본 측정 profile/stage/VU를 바꾸거나 두 모델의 Baseline을 자동 실행하지 않는다.

## 완료한 실서버 관측값

실행일: 2026-10-11 14:31:50 KST. [report](../results/20261011T143149.045306KST_musical-low_reselection_reselection-validation_run-01/report.md), [순서/metric audit](../results/20261011T143149.045306KST_musical-low_reselection_reselection-validation_run-01/reselection-validation.json).

| 관측 | 값 |
|---|---|
| performanceTimeId / initial seats | 16 / 1000 |
| 첫 선택 → holder 선점 → 충돌 | Seat 15001 |
| 재조회에서 Seat 15001 | LOCKED, AVAILABLE 전체 999 |
| 재선택·최종 예약 완료 | AVAILABLE Seat 15050 |
| seat_conflicts / reselections / pre_reserve_attempts | 1 / 1 / 2 |
| booking_completed / payment_applied / seats_confirmed | 1 / 1 / 1 |
| business_failures / system_failures / dropped_iterations | 0 / 0 / 0 |
| HTTP requests / expected HTTP / HTTP failed | 11 / 10 / 1 (예상한 409) |
| DB AVAILABLE / LOCKED / RESERVED | 998 / 1 / 1 |
| unique confirmed seats / oversold seats | 1 / 0 |
| validation / inventory / cleanup | passed / verified / errors 0 |

일곱 audit 항목이 모두 통과했다: counter 값, API 순서, 실제 SEAT_UNAVAILABLE, 충돌 후 재조회, 다른 AVAILABLE 좌석, holder/첫 선택 일치, 최종 예약 성공. actual DB의 read-only consistent snapshot도 저장했다. 실험용 좌석·예약·공연장·공연·회차를 정리했고 테스트 계정은 재사용하도록 남겼다.

## 결과 범위

결과 directory는 KST 접미사를 포함하며 metadata/report/trace 시각은 +09:00이다. `performance_measurement=false`로 표시한다. Prometheus 부하 관측은 제외하며 이 단일 sample의 latency/percentile은 Baseline 성능 기준으로 사용하지 않는다.

`validation_events`는 이 entrypoint에서만 사용하는 작은 raw trace Counter다. `k6-points.json`에서 trace와 실제 counter를 읽어 `reselection-validation.json`을 생성한다. report에 순서와 좌석 상태를 표시하고 원본 points를 보존한다. 별도 실험 중 holder의 HTTP 요청이 사용자 Journey 처리량으로 잘못 집계되지 않도록 역할과 요청 수를 명시한다.

다음 본 측정에서는 두 모델의 조건을 각각 확정하고 새 fixture로 수행한다. 충돌·재선택·SOLD_OUT·성공 E2E·retry 분포·서버 resource metric은 실제 workload 결과로 관측한다. 이 validation은 해당 성능 결과를 대신하지 않는다.

## 검증

기존 Node 단위 테스트, Python의 config/계산/report/inventory 테스트를 유지한다. validation plan은 원본 config 불변과 subject 1건을 검사한다. occupancy 기반 localhost 서버와 실제 k6를 연결한 테스트는 holder 성공 → 동일 좌석 충돌 → 다른 좌석 최종 성공을 검사한다. Single Attempt의 한 시도/여섯 API 회귀도 통과했다. 이후 위의 실서버 validation 1건이 통과했다.
