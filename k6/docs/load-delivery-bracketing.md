# Baseline load-delivery bracket

서버 최대 처리량을 구하는 단계가 아니다. 고정 조건에서 configured iteration arrival가 정상 전달되는 구간과 전달되지 않는 구간을 구분한다. Single Attempt와 Reselection은 따로 탐색한다.

## First Batch 경계의 조건 차이

| 기존 결과 | Seats | Hot range / traffic | 1/2/3석 | Single VU | Reselection VU |
|---|---:|---|---|---:|---:|
| musical-low, peak 20/s PASS | 1000 | 20% / 35% | 60/30/10 | 64 | 160 |
| musical-high, peak 150/s 전달 실패 | 1500 | 20% / 65% | 50/35/15 | 256 | 512 |

두 결과는 arrival rate 외에도 조건이 다르다. 따라서 기존 20/s PASS를 같은 조건의 lower endpoint로 사용하지 않는다. 새 탐색은 **musical-high** 조건과 기존 High의 모델별 VU를 고정한다. 150/s 실패 결과는 기존 reference로 읽고, 실행 시 서버 fingerprint가 같은지 확인한다. 20/s는 같은 High 조건에서 각 모델 1회 재확인한다. 이는 Smoke/Pilot 재실행이 아니라 같은 조건의 낮은 Baseline anchor 측정이다.

앱, 서버 설정, DB, OCI, Tomcat, Hikari, JVM, 좌석 선택/재선택 정책, token pool, think time, abandonment, HTTP timeout, warm-up, fixture, seed와 Prometheus 수집은 변경하지 않는다. 기존 First Batch 파일도 수정하지 않는다.

## VU 검토

`동시 VU 규모 ≈ arrival rate × Journey duration`으로 거친 검토를 한다. 실제 VU 요구량 산식이나 p50–p95 신뢰구간이 아니다. VU는 native iteration 전체가 끝날 때까지 점유되며 HTTP 외 JS 처리 시간도 포함한다. Little's Law 형태의 평균 근사에도 안정된 부하와 대표성 있는 평균이 필요하다.

| Model | maxVUs / actual max | Dropped | High Journey p50 / p95 | 150/s × p50~p95 | 85/s × p50~p95 |
|---|---|---:|---|---|---|
| Single Attempt | 256 / 256 | 12013 | 5.639s / 12.502s | 약 846~1875 VU | 약 479~1063 VU |
| Reselection | 512 / 512 | 8831 | 39.124s / 84.981s | 약 5869~12747 VU | 약 3326~7223 VU |

Native iteration 평균은 각각 6.116s, 42.062s다. 이를 85/s에 곱하면 약 520, 3575 VU다. 기존 ceiling에서 85/s를 전달하려면 평균 VU 점유 시간이 대략 Single 3.012s, Reselection 6.024s 이내여야 한다. 정상 stage 전환, 분산과 drain 등은 이 평균 근사에 포함되지 않는다.

High의 arrival가 미전달된 상태에서 나온 duration이고 여러 terminal outcome이 섞여 있다. 실패하거나 guard로 종료한 Journey의 길이를 무제한 retry의 완료 시간으로 취급하지 않는다. 낮은 rate에서 duration이 유지된다고 예측하지도 않는다.

**검토용 VU 후보만** 계산하면, 85/s에서 High native iteration 평균을 유지할 때 100개 단위 올림으로 Single 600, Reselection 3600이다. High Journey p95를 유지하는 보수적인 계산 후보는 각각 1100, 7300이다. 추가 여유는 확정하지 않았고 이 값은 보장치나 실행 권고가 아니다. 현재 256/512에서 추가 규모는 각각 +344/+3088 또는 +844/+6788이다. 어떤 값도 config에 적용하지 않는다.

MacBookAir10,1, CPU 8개, RAM 16GiB에서 2026-10-11 15:36:26 KST에 CPU idle 0.9%, 물리 메모리 약 15GiB 사용, swap 16263.88MiB가 관측됐다. 15:44:55 재확인에서도 idle 0.67%, 약 15GiB 사용, swap 16359.19MiB였고 실행 중인 k6는 없었다. 이 현재 상태에서는 수백~수천 VU 증설이나 새 측정을 진행할 여유를 확인하지 못했다. **실제 candidate 실행을 시작하지 않고 generator 검토 단계에서 중단한다.** 이 snapshot은 과거 High 실행 당시의 Mac CPU/RAM 기록이 아니며, 과거 실패 원인을 증명하지 않는다. 현재 상태만으로 다른 부하가 제거된 Mac의 VU 한계도 알 수 없다.

## 고정 config와 탐색

[`load-delivery-bracket.json`](../config/load-delivery-bracket.json)이 기존 High config를 참조한다. 후보마다 복사한 config에서 measured stage 1/2의 target만 바꾼다. profile JSON의 reference peak=150은 그대로 두며 실제 설정 peak는 `configured_peak_iteration_arrival_rate`와 stages에서 읽는다.

| 항목 | 고정값 |
|---|---|
| Seats / Hot range / Hot traffic / 1·2·3석 | 1500 / 20% / 65% / 50·35·15 |
| Single preAllocatedVUs / maxVUs | 256 / 256 |
| Reselection preAllocatedVUs / maxVUs | 512 / 512 |
| Arrival / startRate / timeUnit | ramping-arrival-rate / 0 / 1s |
| Stages | 60s: 0→peak, 60s: peak 유지, 60s: peak→0 |
| Graceful stop | 90s |
| Warm-up | 별도 fixture, 1/s·60s, 80/80 VU, drain allowance 90s |
| JWT pool / seed / HTTP timeout | 10 / 20261011 / 10s |
| Prometheus scrape / step / rate window | 5s / 5s / 30s, 기존 37개 query |
| Reselection think time / abandonment / guard | disabled / disabled / 기존 60s checkpoint |

Configured average는 `peak × 2/3`이며 exact iteration quota로 강제하지 않는다. peak 85/s라면 평균 56.667/s, schedule 적분 10200 starts다. 기존 마지막 deadline slot 1개 허용 규칙은 유지하고 그 이상 unobserved slot을 native dropped로 바꿔 쓰지 않는다.

모델별 계획:

1. High 조건에서 20/s anchor를 1회 실행한다. 실패하면 lower PASS를 만들지 않고 중단한다.
2. 통과하면 85/s를 1회 실행한다.
3. 유효한 85 PASS면 다음 후보 120/s, generation FAIL이면 산술상 다음 후보 55/s다. 다만 FAIL에 VU ceiling이 동반되면 **55/s도 실행하지 않고 즉시 중단**한다.
4. 각 모델의 highest PASS와 lowest observed generation FAIL 사이 중간값을 선택한다. 5/s 단위로 가장 가까운 값으로 반올림하고 정확한 절반은 위로 반올림한다.
5. 간격이 20 journey/s 이내면 탐색을 마친다. 20은 탐색 해상도이며 시스템 성능 기준이 아니다. 관측한 후보 사이의 모든 rate가 동일하게 동작한다는 보장은 없다.
6. 경계가 좁혀지고 generator 중단 사유가 없을 때만 endpoint의 총 3회 반복 확인 단계로 넘어간다. 탐색 실행기는 이 상태를 `narrowed-awaiting-endpoint-repeats`로 남긴다. 중단된 탐색이나 모든 중간 후보를 자동으로 3회 반복하지 않는다.

Single 탐색부터 순차 수행한다. 중단 조건이 나오면 다른 모델, 다음 후보, 반복 run도 시작하지 않는다. 따라서 첫 VU 중단이 이른 rate에서 나오면 두 모델의 bracket을 모두 완료하지 못할 수 있으며 미확인 endpoint는 null로 남긴다.

## 판정

`load-delivery-validation.json`은 새 판정이다. 기존 `pilot-observations.json`/report와 과거 판정을 덮어쓰지 않는다.

- `PASS`: arrival boundary, slot accounting, stage generation, dropped=0, unfinished=0, raw/summary/accounting, fixture isolation, Prometheus, DB audit/oversell 및 artifact 검증 통과.
- `VALID MEASUREMENT / SERVER FAILURE OBSERVED`: 위 조건은 통과하고 system failure가 관측됨. 전달 부하 측정 자체는 유효하다.
- `LOAD DELIVERY FAILED`: 필수 검증 실패. `failure_scope=arrival-generation`인 관측만 FAIL rate endpoint로 사용한다. 수집/DB audit/누락 artifact 등의 오류는 `failure_scope=measurement-integrity`로 구분해 탐색을 중단하며 rate endpoint로 사용하지 않는다.

SEAT_UNAVAILABLE은 기존 모델 의미를 유지한다. Single은 terminal Business Failure, Reselection은 내부 conflict/retry다. HTTP expected-response failure는 system failure rate로 해석하지 않는다. Safety guard는 별도 기술적 terminal outcome이며 정상 종료돼 accounting이 맞으면 이번 **load-delivery 판정**에서는 unfinished/drop으로 간주하지 않는다. 기존 Pilot/First Batch의 guard 검증 기준은 바꾸지 않는다. Guard duration과 metric도 그대로 기록한다.

기존 전체 Run API 최소 100 sample screening은 별도 관측으로 보존한다. 서버 오류/좌석 소진으로 payment 등의 sample이 적거나 0인 경우 해당 latency는 count와 N/A로 표시하고, 그것만으로 arrival 전달 실패로 바꾸지 않는다. 실제 발생한 전체 HTTP/Journey/iteration의 raw latency sample 수와 counter 수 불일치는 측정 무결성 실패로 중단한다.

Active VU가 ceiling에 도달한 사실만으로 FAIL로 만들지 않는다. 경고를 따로 기록하고, ceiling과 arrival generation 실패가 함께 나타나면 `generator_stop=true`로 즉시 자동 실행을 중단한다. 다음 VU 후보는 참고 계산만 저장하며 적용하지 않는다. 서버/system 오류 원인이나 saturation을 자동 판정하지 않는다.

## 실행과 결과

현재는 generator preflight에서 실제 실행을 보류했다. 아래 `--execute`는 generator 상태를 검토한 뒤 **기존 256/512 VU로 재개할 때** 쓰는 명령이다. 별도 값을 직접 채울 필요는 없다.

```bash
# 설정만 출력; SSH/HTTP/k6 실행 없음
bash k6/scripts/load-delivery-bracket.sh --plan

# Generator 검토 후 재개용. 후보만 순차 실행, VU 자동 증설 없음
bash k6/scripts/load-delivery-bracket.sh --execute

# 기존 Run을 수정하지 않고 새 delivery 판정만 stdout으로 확인
bash k6/scripts/load-delivery-bracket.sh --review-run <RUN_DIRECTORY>

# 부하/SSH/API 없이 판정·중단·config 보존 테스트
python3 -m unittest discover -s k6/tests -p 'test_load_delivery_bracket.py' -v
```

실제 실행은 기존 Baseline 실행기를 재사용한다. 후보당 새 warm-up와 measured fixture 각각 1개, 초기 all AVAILABLE 확인, 원본 points/summary, arrival/drain 시간 경계, API와 DB consistent snapshot, oversell audit, 종료 후 query_range 및 cleanup을 그대로 수행한다. 후보당 고정 workload는 240초(4분)이고 준비/audit/수집/cleanup과 실제 drain이 추가된다. 후보 개수는 관측 결과에 따라 정하며 미리 모든 rate를 나열하거나 실행하지 않는다.

Measured directory는 기존 KST `{timestamp}_musical-high_{model}_baseline_run-01/`이며 `pilot-config.json`, metadata stages와 새 delivery 검증 파일에서 candidate peak를 구분한다. 추가 `{timestamp}_load-delivery_bracket/`에 candidate config, 계획, 모델별 endpoint 상태, 전체 후보 관측/report를 저장한다. 기존 summary/points와 First Batch artifact는 읽기만 한다. 서버/config/source fingerprint가 달라지거나 fixture/audit/수집에 문제가 생기면 다음 run을 시작하지 않는다.

이번 preflight의 별도 results directory에는 Mac snapshot, 기존 네 Run의 읽기 전용 판정, VU 계산, resolved config와 실행 보류 사유를 저장한다. 새 measured run이 없으므로 같은 조건의 highest PASS는 아직 미확인이다. musical-low PASS 20/s와 musical-high FAIL 150/s는 서로 다른 profile의 관측으로 보존한다.
