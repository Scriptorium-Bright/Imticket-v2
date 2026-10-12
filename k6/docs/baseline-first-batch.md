# Baseline 첫 batch

Pilot과 Controlled Conflict Validation 이후의 **성능 측정**이다. 앱 코드, 인프라, DB/애플리케이션 설정은 변경하지 않는다. 좌석 선택·재선택·API 계약·metric 의미는 검증된 구현을 유지한다.

## 고정한 effective config

전체 resolved 설정은 [baseline-first-batch-plan.json](baseline-first-batch-plan.json)에 저장한다. 실행 전 `--plan`은 HTTP/SSH/k6를 실행하지 않는다. 실행 중 config SHA-256이 바뀌면 다음 Run을 시작하지 않는다.

| Profile | Model | Seats | Peak iteration starts/s | preAllocated/max VU | Run |
|---|---|---:|---:|---:|---:|
| musical-low | single-attempt | 1000 | 20 | 64/64 | 1 |
| musical-low | reselection | 1000 | 20 | 160/160 | 1 |
| musical-high | single-attempt | 1500 | 150 | 256/256 | 1 |
| musical-high | reselection | 1500 | 150 | 512/512 | 1 |

두 모델의 공통 조건:

| 항목 | 값 |
|---|---|
| executor / timeUnit / startRate | ramping-arrival-rate / 1s / 0 |
| Stage 1 | 60s, target=profile peak |
| Stage 2 | 60s, target=profile peak 유지 |
| Stage 3 | 60s, target=0 |
| Arrival duration | 180s |
| gracefulStop | 90s, 실제 drain 종료는 별도로 관측 |
| Warm-up | 새 별도 fixture, 1/s·60s, 80/80 VU, gracefulStop=90s |
| JWT pool | 기존 테스트 Member 10개, iteration round-robin, VU 1:1 대응 없음 |
| HTTP timeout | 10s |
| Random seed | 20261011, iteration 번호에서 파생, 매 Run/모델 동일 정책 |
| Prometheus scrape / query step / rate window | 5s / 5s / 30s |
| Query context | measured 시작 전 30s / 종료 후 5s, 부하 종료 후 query_range |
| API sample screening | 전체 Run에서 각 API 최소 100 sample, p99 정밀도 보장 아님 |
| Inventory | 기존 API 상태 + MySQL REPEATABLE READ consistent read-only snapshot |

Musical Low/High는 같은 공연 유형에서 1,000/1,500석과 20/150 starts/s를 확인할 수 있어 첫 batch로 선택했다. Hot range/traffic와 1/2/3석 비율은 각 기존 profile을 그대로 사용한다. Profile 간 설정은 다르지만 **같은 profile의 두 모델**은 동일 조건을 사용한다. 측정 VU는 Journey 길이에 맞춰 별도로 준비하며 사용자 행동이나 arrival 설정을 변경하지 않는다.

Stage duration은 Attempt Scale에서 역산하지 않았다. 60초는 5초 scrape 기준 stage마다 12회 관측 기회와 30초 rate window를 포함한다. Schedule 적분은 Low 2,400 / High 18,000 starts다. 이는 exact iteration quota가 아니며 observed starts/drop/completion을 각각 기록한다.

### Pilot과 달라진 값

| 항목 | Pilot | 첫 Baseline |
|---|---|---|
| Arrival | start=1, peak=2, 30/30/60/30s | start=0, profile peak=20/150, 60/60/60s |
| Single measured VU | 실제 기존 Pilot artifact 10/10, 현재 local 20/20 | Low 64/64, High 256/256 |
| Reselection measured VU | 160/160 | Low 160/160 유지, High 512/512 |
| Single drain | 30s | 모델 간 동일한 90s 완료 허용 구간 |
| Warm-up VU / drain | Single 현재 local 20/20·30s, Reselection 80/80·90s | 둘 다 80/80·90s; arrival은 기존 1/s·60s 유지 |
| API sample policy | 미설정 | 전체 Run에서 API별 최소 100 |

Token pool 10, seed, timeout, metric query와 Reselection 정책/guard는 유지한다. Reselection think time/abandonment는 disabled이며 safety guard=60s는 기술적 종료로 분리한다.

실제 Pilot p99는 Single Attempt Journey 약 0.317s, Reselection 성공 Journey 약 0.286s이며 peak 2/s였다. 선택한 VU는 그 관측보다 여유를 둔 **첫 Baseline 초기값**이다. High에서 충분하다는 결론은 아직 없다. Low/High의 Single VU는 peak에서 약 3.2s/1.7s, Reselection VU는 약 8s/3.4s의 평균 동시 실행 시간을 수용할 수 있는 규모다. 극단적 요청 timeout/guard 시간 전체를 보장하는 산정은 아니다. maxVUs=preAllocatedVUs로 실행 중 동적 VU 생성을 피한다. 실제 active VU, dropped_iterations, iteration/Journey duration으로 generator 여유를 검토하고 값은 자동 조정하지 않는다.

Token pool은 기존 Pilot의 10개 정책을 유지한다. 현재 검토된 Member/예약 제약은 사용자 전체 booking lock/한 건 제한이 없으며 멱등 키는 각 iteration/attempt별로 달라진다. 서버 JWT 검증은 측정에 포함되고 계정 생성/로그인은 제외한다.

## 실행 순서와 시간

첫 batch는 Low Single → Low Reselection → High Single → High Reselection, 각 Run 1회, 순차 실행한다. Warm-up 4회와 measured 4회, fresh fixture 8개다. 고정 workload 시간은 총 960초(16분)이며 준비/DB audit/Prometheus/cleanup 시간이 추가된다. 각 k6 phase의 추가 drain 상한은 90초로 총 720초지만 실제 사용한 시간만 기록한다. 실행 도중 다음 조건을 바꾸지 않는다.

각 measured Run은 별도 warm-up → 새 measured fixture 검증 → k6 → API snapshot → DB audit → 종료 후 query_range/report → fixture cleanup으로 수행한다. 초기 정확한 seat count/all AVAILABLE, 서로 다른 performanceTimeId, fixture claim과 결과 snapshot을 보존한다. 각 회차 cleanup 완료 후 다음 Run을 시작한다.

기존 서버 commit, 실행 image/container ID, 앱/DB/Prometheus config 및 외부 설정 파일 hash, 주요 DB global variables를 **부하 외부에서 읽기 전용**으로 기록한다. 값이 batch 중 변경되면 다음 Run을 시작하지 않는다. HEAD만으로 image 내용을 증명하지 않으므로 둘을 따로 저장한다. Host/DB lock exporter나 새로운 runtime metric은 추가하지 않는다.

## 실행 명령

```bash
# 설정만 확인
bash k6/scripts/baseline-batch.sh --plan

# 첫 4회만 실행; 자동 로그인/fixture/audit/수집/cleanup
bash k6/scripts/baseline-batch.sh --execute
```

각 config로도 실행할 수 있다. 첫 batch wrapper와 중복해서 실행하지 않는다.

```bash
bash k6/scripts/baseline.sh --config k6/config/baseline/musical-low.single-attempt.json --run-number 1
bash k6/scripts/reselection.sh baseline --config k6/config/baseline/musical-low.reselection.json --run-number 1
bash k6/scripts/baseline.sh --config k6/config/baseline/musical-high.single-attempt.json --run-number 1
bash k6/scripts/reselection.sh baseline --config k6/config/baseline/musical-high.reselection.json --run-number 1
```

첫 batch를 검토한 후 동일 profile의 Run 2–3은 `--run-number 2 --repeat 2`로 지원한다. 기존 10개 profile × 2 model × 3 Run, 총 60회 구조를 유지한다. 다른 profile은 해당 peak/seat/profile에 맞는 config를 측정 전에 고정해야 하며 `--profile`만 바꿔 낮은 profile의 stages를 상속하지 않는다. 첫 batch에서 나머지 56회는 자동 실행하지 않는다.

## 관측과 측정 검증

Configured schedule / observed iteration_started / native iterations completed를 전체·stage·초별로 구분한다. Completed iteration에는 business/system/sold-out 종료가 포함되며 Booking Completed와 다르다. Single은 원래 occupied seat 선택과 SEAT_UNAVAILABLE terminal Business Failure를 유지하고, Reselection만 fresh AVAILABLE·fallback·retry를 수행한다.

전체/API latency의 count·p50·p95·p99·max를 저장한다. Single의 성공 booking_duration을 원본과 observations에 보존하고 Reselection 성공/sold-out/system duration은 별도 집단으로 표시한다. 좌석 소진 이후 후속 API sample이 0인 stage의 percentile은 N/A다. 최소 sample 정책은 **전체 Run** 기준이며 모든 stage의 결제 sample을 요구하지 않는다. Stage latency는 요청 완료 시각 기준이다.

Baseline의 system_failures/contract 실패는 결과로 관측한다. k6 metric/check 값은 유지하면서 Baseline에서만 zero-System-Failure threshold를 SLO로 강제하지 않는다. Pilot 검증 기준은 유지한다. dropped/unfinished/accounting 불일치, technical guard 종료, missing samples/artifacts, Prometheus 미검증, inventory/oversell/duplicate mapping 오류와 cleanup 실패는 측정 검토 대상으로 구분한다. System Failure가 없다는 사실만으로 saturation 유무를 판정하지 않는다.

`http_req_failed`의 HTTP 409 expected-response failure, Single Business Failure, Reselection 내부 Business Conflict, System Failure를 따로 표시한다. 단일 충돌마다 console error는 출력하지 않는다.

Run마다 원본 summary/points, stage observations, fixture initial/final snapshots, consistent DB snapshot/audit, report와 기존 37개 HTTP/Tomcat/Hikari/JVM query 결과를 저장한다. DB Lock Wait/MySQL Host/OCI Host 등은 현재 automatic collection unavailable이다. 성공 booking 수와 sold seat 수를 합치지 않는다. DB audit는 Seat별 CONFIRMED reservation이 최대 하나인지, 중복 Seat/Reservation mapping, fixture membership, capacity 및 RESERVED 상태를 따로 검증한다.

첫 batch report에서는 모델별 Run을 분리해 표시하며 원인/병목/튜닝 해석을 자동 작성하지 않는다. 결과 검토에서 generator/시간 경계/수집/재현 조건의 이상 여부를 먼저 판단한다.

## 첫 batch 실행 결과

2026-10-11 KST에 위의 4개 measured Run과 4개 warm-up을 실행했다. [전체 결과 및 모델별/API별/stage별 관측값](../results/20261011T145048.378588KST_baseline_first-batch/report.md)과 `batch-results.json`에 보존했다.

| Profile | Model | Started | Native dropped | System Failure | Technical guard | 측정 검증 |
|---|---|---:|---:|---:|---:|---|
| musical-low | single-attempt | 2399 | 0 | 0 | 해당 없음 | passed |
| musical-low | reselection | 2399 | 0 | 0 | 0 | passed |
| musical-high | single-attempt | 5986 | 12013 | 0 | 해당 없음 | failed |
| musical-high | reselection | 1987 | 8831 | 74 | 527 | failed |

자동 batch는 High Single의 측정 검증 실패로 중단했다. 이미 계획한 High Reselection 1회는 고정 512/512 VU config로 독립 실행해 첫 4개 결과를 확보했다. 중단 기록과 실패 판정을 그대로 보존했고 전체 60회로 확장하지 않았다. 네 번째 실행의 shell wrapper는 macOS Bash 3.2의 빈 배열 오류가 있어 서버 요청 전에 중단됐으며, 선택적 인자를 `set --`로 전달하도록 수정한 뒤 같은 config로 실행했다. 이 preflight 재시도는 추가 measured Run이 아니다.

4개 Run의 API sample 수와 원본 summary/points, 일관된 DB snapshot, 기존 37개 Prometheus query 수집이 저장됐고 8개 fixture 모두 정리됐다. 서버 image/config/DB fingerprints와 고정 config는 유지됐다. 모든 oversell/duplicate mapping 검증은 통과했다. Low Reselection은 unique confirmed seats=1000, High Reselection은 unique confirmed seats=1469 및 LOCKED=31로 관측됐다. Client seats_confirmed는 High Reselection에서 1439이며 DB confirmed assignments와 같은 값으로 취급하지 않는다.

High Single은 VU 256, High Reselection은 VU 512 상한에 도달했고 실제 유입이 configured arrival과 일치하지 않았다. 이 결과를 peak 150 starts/s가 제대로 전달된 Baseline으로 비교하지 않는다. High Reselection의 timeout 73/network error 1, guard 527은 별도 결과다. 서버·네트워크·load generator 중 무엇이 원인인지 자동 단정하지 않는다.

High Reselection에는 nominal 종료 이후 실제 시작 6건과 configured discrete slots 18000 대비 관측 start+native dropped=10818의 차이 7182개가 있었다. 이 차이를 dropped_iterations에 더하지 않는다. Arrival confirmed end는 N/A, planned end와 마지막 관측 이벤트·drain/process end는 원본 시각대로 보존한다. 다른 세 Run의 마지막 deadline slot 1개 차이는 허용된 scheduler 경계 조건으로 별도 표시한다. 후처리 회귀 테스트를 추가하고 derived metadata/observations/report만 수정했으며 raw summary/points가 byte 단위로 동일함을 SHA-256으로 확인했다. 기존 Smoke/Pilot/Controlled Validation 결과는 수정하지 않았다.

기존 guard는 loop/API 단계 **이전**의 checkpoint다. 진행 중인 API를 강제로 중단하는 장치가 아니므로 성공/SOLD_OUT/guard duration을 설정값 60초로 잘라 쓰지 않는다. High Reselection의 관측 성공 max=73539ms, guard max=107506ms, HTTP duration max=27909.410ms를 그대로 기록했다. 이 단계에서 guard/timeout/VU 또는 서버 설정을 자동 변경하지 않았다.

**현재 설정으로 나머지 56개 Run을 계속 실행하지 않는다.** High의 arrival generation 및 시간 경계/guard 종료가 측정 검토 대상이다. 성능 개선안은 제안하지 않는다.
