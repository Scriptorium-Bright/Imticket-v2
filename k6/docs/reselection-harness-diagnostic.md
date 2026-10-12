# Reselection 미완료 Journey 진단

최종 Baseline은 Run 1의 19/20 유효 Run 상태로 유지한다. `overseas-high / reselection`의 guard 종료·미완료 Run은 제외하고 원본을 보존한다. Run 2·3은 시작하지 않는다.

이번 확인은 미완료 Journey가 마지막으로 어느 코드 구간을 실행했는지에 한정한다. 서버 원인 분석이나 성능 개선은 포함하지 않는다. guard를 더 늘리거나 HTTP timeout·좌석 선택·재선택·warm-up·서버 설정을 바꾸지 않는다.

## 과거 결과에서 확인된 사실

`20261011T222051.652618KST_overseas-high_target-vu-10000_reselection_run-01`의 기록:

| 구간 | 기록 수 |
|---|---:|
| journey_started | 10,000 |
| 종료 유형 counter 합계 | 8,424 |
| retries_per_journey sample | 8,398 |
| journey_finished | 7,746 |
| journey_duration sample | 6,718 |
| native iterations | 6,659 |

종료 유형 counter가 기록된 678건은 뒤의 `journey_finished`가 없다. 따라서 미완료 Journey 2,254건을 모두 “종료 유형을 결정하지 못했다”로 읽을 수 없다. 결과 반환 뒤 counter·Trend·console·check·함수 반환 과정도 독립적으로 구분해야 한다.

`reselection_stale_refreshes=0`이다. 기록된 retry sample 8,398개의 최대 retry는 1이며, 이 값은 sample을 남기지 못한 사용자에게 적용할 수 없다. 과거 raw points에는 VU/Journey ID가 없어 사용자별 마지막 위치를 복원할 수 없다.

guard에 남은 `api=pre-reserve`도 선점 HTTP를 기다렸다는 뜻은 아니다. loop-entry checkpoint에서는 이전 API 이름이 남고, before-api checkpoint에서는 아직 호출하지 않은 다음 API 이름이 남는다.

## 진단 모드

`--harness-diagnostic`를 명시한 Target-VU Reselection 1회에만 켜진다. 일반 Baseline에서는 꺼져 있다. warm-up에서도 꺼져 있다.

다음을 기존 JSON raw output에 기록한다.

- VU ID, VU 내부 iteration ID, phase 순번.
- API 호출 전후, HTTP 반환, response 분류 전후.
- 좌석 선택 전후, 충돌 후 retry 대기, checkpoint 위치.
- 요청 좌석 수, retry/attempt 수.
- 종료 유형 결정과 이후 counter·duration·retry·log·journey_finished·check·return 구간.

식별자는 [k6 VU metric metadata](https://grafana.com/docs/k6/latest/javascript-api/k6-execution/)로 저장한다. VU ID를 indexed tag로 넣지 않는다. 좌석 내용·응답 body·JWT·계정 비밀번호를 기록하지 않는다. `harness_phase` marker는 현재 위치를 기록하는 용도다.

추적은 출력 작업을 추가한다. 이 실행의 latency나 throughput은 기존 Baseline과 비교하지 않는다. directory에 `_harness-diagnostic_`를 넣고 metadata에 `performance_measurement=false`, `measurement_role=harness-diagnostic`를 저장한다. 반복 측정 selector도 진단 결과를 유효 Baseline으로 받아들이지 않는다.

## 실행 조건

직전 실패 Run과 같은 effective config를 사용한다.

- Target VUs: 10,000, `per-vu-iterations`, `iterations=1`.
- safety guard: 10m.
- executor maxDuration: 10m, gracefulStop: 90s.
- HTTP timeout: 10s.
- warm-up·fixture·token pool·Prometheus·inventory·cleanup: 기존 방식.

진단 실행은 자동 반복하지 않는다.

```bash
bash -c 'source k6/config/env.local.sh; python3 k6/scripts/run-experiment.py baseline --config k6/config/vu-baseline/overseas-high.reselection.json --harness-diagnostic --run-number 1'
```

서버 요청 없이 설정만 확인:

```bash
python3 k6/scripts/run-experiment.py baseline --config k6/config/vu-baseline/overseas-high.reselection.json --harness-diagnostic --plan
```

과거 Run의 원본을 변경하지 않고 오프라인 검토 artifact를 생성:

```bash
python3 k6/scripts/harness_observations.py k6/results/20261011T222051.652618KST_overseas-high_target-vu-10000_reselection_run-01
```

## artifact와 해석 범위

진단 Run은 기존 fixture·raw points·summary·Prometheus·inventory artifact에 다음을 추가한다.

- `harness-observations.json`: 기록 구간별 수, 미완료 사용자 마지막 phase 집계, API 흐름.
- `harness-users.jsonl`: VU별 마지막 위치와 종료/완료 기록.
- `report.md`: 한국어로 관측 사실과 확인 한계를 기록.

마지막 phase는 마지막으로 기록된 실행 위치다. 스레드 stack이나 실제 대기 원인을 확정하는 자료가 아니다. phase 사이의 시간에는 동작 자체와 marker 기록·스케줄링이 포함된다. native HTTP duration과 JavaScript 반환 시각도 별도로 확인한다.

k6 1.6.1 공개 소스에서 custom metric 추가는 [sample channel로 전송](https://github.com/grafana/k6/blob/v1.6.1/internal/js/modules/k6/metrics/metrics.go)하며, [PushIfNotDone](https://github.com/grafana/k6/blob/v1.6.1/metrics/sample.go)은 context가 이미 끝났으면 기록하지 않는다. 이 동작만으로 과거 Run의 실제 대기 원인을 확정하지 않는다. 설치된 binary는 `commit/devel`로 표시되므로 공개 tag 소스와 정확히 동일한 build인지까지 확인된 것은 아니다.

## 로컬 검증

외부 Imticket 서버에 요청하지 않고 다음을 확인했다.

- observer를 켜도 API 순서·원하는 좌석 수·conflict → booking outcome이 동일하다.
- 실제 k6와 localhost 가짜 API에서 충돌 후 seat-map 재조회·성공을 추적한다.
- HTTP 중 executor가 종료되면 마지막 phase가 `http.dispatch`로 남고 terminal outcome을 만들어내지 않는다.
- 종료 유형 결정 뒤 완료 기록이 없는 경우를 재선택 무한 반복으로 분류하지 않는다.
- 진단 Run은 완료/accounting 검증이 통과해도 Baseline selector에서 제외한다.

기존 `journey-smoke` 통합 테스트 1건은 token pool 선택에서 실패했다. `per-vu-iterations`라는 executor 이름만으로 Target-VU 인증 분기를 선택하는 기존 경로이며, 이번 Target-VU Baseline 진단과 다른 조건이다. 범위를 넓히지 않고 기록만 남겼다. 기존 Baseline와 다른 Reselection 통합 경로 6건은 통과했다.

## 10,000 VU 진단 결과

`20261011T231047.355098KST_overseas-high_target-vu-10000_reselection_harness-diagnostic_run-01`은 직전 실패 Run과 동일한 effective config에서 실행했다. 이번에는 VU 10,000개가 각각 한 Journey를 시작·종료했고 native iteration도 10,000개 완료했다. guard·unfinished·dropped는 0이다.

BOOKING_COMPLETED 959명, SOLD_OUT 347명, SYSTEM_FAILURE 8,694명으로 끝났다. conflict와 reselection은 각각 3,311건, stale refresh는 0건이다. retry 최대값은 8이다. Prometheus·inventory·oversell 검증과 fixture cleanup이 끝났고 서버 fingerprint는 유지됐다.

HTTP 호출 전 초기 metric 기록 구간에서 최대 50.165초, 종료 후 check 기록 구간에서 최대 23.137초가 관측됐다. 이 시간에는 marker 자체와 스케줄링이 포함된다. native stack은 Mac 기본 sample 도구로 k6 프로세스만 1초 관측했으며, Go 주소를 함수 이름으로 복원한 사본도 보존했다. metric 추가·JSON writer·JavaScript VM·메모리 관리 함수가 관측됐지만, 사용자별 goroutine의 실제 대기 stack을 확보한 것은 아니다.

이번에는 미완료 상태를 재현하지 못했다. 추적이 추가된 실행의 결과로 과거 미완료 원인을 확정하지 않는다. 진단 Run은 최종 Baseline에서 제외하고 유효 Run 1은 19/20으로 유지한다. Run 2·3은 시작하지 않는다.

종합 기록: `results/20261011T225717.801522KST_reselection_harness-review/report.md`.
