# Target VU 기반 Baseline

## 실험 질문과 입력 조건

이 workload는 예매가 열렸을 때 한 사용자 집단이 동시에 참여하는 상황을 모델링한다. 독립변수는 `Target Users / Target VUs`이며, iteration arrival rate가 아니다. 측정 대상 VU마다 iteration 한 번만 실행하고, 그 iteration은 한 사용자의 Single Attempt 또는 Reselection Journey 하나를 수행한다. Journey가 끝난 VU는 새 예매를 시작하지 않는다.

Profile의 `targetUsers`가 VU 수를 결정한다. 기존 arrival-rate 결과를 보존하기 위해 남겨둔 `referenceAttemptors`는 `targetUsers`와 같은 값이다. `peakJourneyArrivalRate`는 기존 arrival-rate 모델에서만 사용하는 값으로, Target VU 실행에는 사용하지 않는다. 모든 profile은 실제 산업 통계가 아닌 synthetic workload다.

## Executor와 사용자 인증

본 측정은 k6 `per-vu-iterations`, `vus=profile.targetUsers`, `iterations=1`을 사용한다. 설정한 VU는 같은 scenario 시작 시점에 실행되도록 배치되지만, 정확히 같은 순간에 모두 시작한다고 가정하지 않는다. `iteration_started`와 `journey_started`의 raw point timestamp에서 첫 시작, 마지막 시작, start spread, 초 단위 시작 분포를 계산한다.

측정 전에 자동으로 서로 다른 Member/JWT를 준비한다. 측정 token pool 크기는 Target VU와 같아야 한다. `vu.idInTest`로 각 VU에 서로 다른 토큰을 배정하고, preflight에서 JWT subject와 Member ID가 중복되지 않는지 확인한다. 회원 생성과 로그인은 측정에 포함하지 않는다.

## Run 경계

Warm-up은 기존 설정인 60초 동안 1 iteration-start/s를 유지한다. 별도 fixture와 60명짜리 별도 계정 pool을 사용한다. 본 측정은 새 fixture를 생성하고 profile에 지정된 Target VU만큼 서로 다른 Member를 사용한다. Fixture 생성, 인증, Prometheus 조회, inventory snapshot, cleanup은 k6 측정 구간 밖에서 수행한다.

측정 scenario의 `maxDuration`과 `gracefulStop`은 config에 명시한다. Timeout과 미완료 Journey는 결과에 남기며, VU를 자동으로 추가하지 않는다.

## 설정과 실행 명령

최초 validation 설정은 `config/vu-baseline/rapper-low.single-attempt.json`과 `config/vu-baseline/rapper-low.reselection.json`이다. 다른 profile 값은 `config/profiles.json`에서 읽는다. Config validator는 executor VU 수와 token pool 크기가 해당 profile과 같은지 확인한다.

```bash
bash k6/scripts/baseline.sh --profile rapper-low \
  --config k6/config/vu-baseline/rapper-low.single-attempt.json \
  --workload-model single-attempt --run-number 1

bash k6/scripts/baseline.sh --profile rapper-low \
  --config k6/config/vu-baseline/rapper-low.reselection.json \
  --workload-model reselection --run-number 1
```

각 명령은 별도 warm-up과 새 measured fixture에서 Run 한 회를 수행한다. 두 모델은 순서대로 실행하며, 같은 VU가 Journey를 반복하지 않는다.

## 결과 보존과 해석

Target VU 결과는 `results/{timestamp}_{profile}_target-vu-{count}_{workloadModel}_run-{n}/`에 저장한다. k6 summary, timestamp가 포함된 raw points, metadata, VU 관측값, Mac resource snapshot, Prometheus query-range 결과, 최종 inventory snapshot, oversell audit와 report를 보존한다. 기존 ramping-arrival 결과는 원래 directory와 의미를 유지한다.

Report에는 Target VU, 실제 시작 수, start spread, 실제 Journey throughput, HTTP RPS, latency sample 수와 percentile, Business/System 결과, VU 사용량, 좌석 상태를 기록한다. 전석 판매 시간은 최종 DB consistent snapshot에서 모든 초기 Seat ID가 중복 없이 CONFIRMED 상태임을 확인한 경우에만 표시한다. 시각은 client가 완료로 관측한 좌석 누적 수가 초기 좌석 수에 도달한 booking 완료 event를 기준으로 하며, DB commit 시각이 아니다. 최초 `NO_SELECTABLE_SEAT`/`SOLD_OUT` event는 별도로 기록한다.

System failure는 관측 결과로 보존한다. Journey 시작 누락, dropped/미완료 iteration, Prometheus 수집 누락, inventory invariant 실패는 측정 검증 문제로 표시한다. 원인이나 성능 개선안을 자동으로 작성하지 않는다.

### 짧은 workload의 Prometheus 수집과 coverage

Prometheus collection validity와 측정 구간의 시간 coverage를 별도로 기록한다. k6 종료 후 기존 방식대로 expanded `query_range`와 fresh scrape를 확인한다. Required query의 API 응답/series와 query 범위 내 유한 값이 정상적으로 수집되면 collection은 통과한다. 측정 구간의 distinct evaluation timestamp가 0개(context-only), 1개, 2개 이상인지는 query별 `temporal_coverage`와 `collection-status.json` 요약으로 구분한다. 짧은 workload가 5초 scrape grid와 겹치지 않거나 한 번만 겹쳤다는 이유만으로 Run을 무효 처리하지 않는다. 실제 query range에 값이 전혀 없거나 target/API/metric 수집이 실패한 경우는 계속 collection gap이다.

Sparse/context-only coverage는 workload 순간의 내부 metric을 충분히 시간 분해해 관측했다는 뜻이 아니다. 원시 query 응답과 앞뒤 context, measurement/query 시간 경계를 함께 보존하며, evaluation point 수를 독립 scrape 수로 해석하지 않는다. 이 처리는 workload·scrape·query 설정을 바꾸지 않고 validation 기준만 collection 성공과 temporal coverage로 분리한다.

## Run 1 수용 기준과 실행 보호 상한

수용한 `idol-high / single-attempt` 대체 Run 1은 `20261011T213908.074809KST_idol-high_target-vu-8000_single-attempt_run-01`이다. 이 Run만 `maxDuration=20m`을 사용했고, 다른 Single Attempt Run 1은 `10m`을 사용했다. `gracefulStop=90s`는 동일하다. 관측된 Journey 구간은 약 22.6초이므로 이 실행 상한이 측정값을 잘라내지 않았다. 8,000명이 모두 정확히 한 번 시작·종료했으며, booking 0건과 system failure 8,000건은 그대로 보존한다. 이전 미완료 idol-high Single Run은 최종 집계에서 제외하되 raw artifact를 보존한다. 이전 10분 상한이나 당시 Mac 상태 중 하나를 단독 원인으로 판정하지 않는다.

최초 `overseas-high / reselection` Run 1인 `20261011T185943.843812KST_overseas-high_target-vu-10000_reselection_run-01`은 622 Journey가 technical guard로 종료돼 최종 Baseline에서 제외한다. Accounting, Prometheus 수집, DB invariant 검증 결과는 보존한다. Client가 완료로 관측한 좌석은 383석, 최종 DB의 unique confirmed Seat ID는 428개로 45석 차이가 났으며 원인은 판정하지 않는다.

Overseas-high Reselection 대체 Run과 이후 반복에서는 `config/vu-baseline/overseas-high.reselection.json`의 Journey technical guard를 기존 executor `maxDuration`과 같은 `10m`으로 설정한다. 사용자 retry 제한이나 abandonment를 추가하지 않고, 기존 60초 checkpoint 종료를 뒤로 옮긴다. Executor 상한과 `gracefulStop=90s`는 그대로 유지한다. Guard stop이나 미완료 iteration이 있으면 여전히 수용하지 않는다. 다른 Reselection profile은 기존 60초 guard를 유지한다. 이 profile별 기술적 실행 상한 차이는 최종 문서에도 명시한다.

Target VU 검증에 `no_safety_guard_termination`을 추가했다. Terminal accounting이 맞더라도 guard가 Journey를 잘라냈다면 유효한 최종 Run으로 인정하지 않는다. 서버 timeout과 system failure는 관측된 terminal outcome으로 유지하며, 그 수치만으로 측정을 무효화하지 않는다. 기존 결과 파일은 다시 계산하거나 덮어쓰지 않는다.

첫 extended-guard 대체 Run인 `20261011T220051.108233KST_overseas-high_target-vu-10000_reselection_run-01`에서는 10,000 Journey가 모두 시작·종료했고 guard, dropped, unfinished는 0이었다. Booking 0건, system failure 10,000건이 기록됐으며 DB snapshot과 oversell invariant는 통과했다. 측정 후 Prometheus 수집이 처음에는 timeout으로 실패했지만, 같은 측정 구간·query·timeout으로 다시 조회해 수집했다. 두 수집 시도 모두 보존했다. 종료 직후 seat-map API snapshot은 timeout으로 누락됐고 cleanup 후 복구할 수 없어, 이 시도는 artifact 누락으로 제외한다. 동일 extended-guard config를 새 fixture에서 다시 실행한다.

후속 `20261011T222051.652618KST_overseas-high_target-vu-10000_reselection_run-01`에서는 10,000 VU가 모두 시작했지만 Journey 종료는 7,746개, 완료 iteration은 6,659개였다. Guard 종료 2,201개, 미종료 Journey 2,254개, 미완료 iteration 3,341개로 최종 Run에서 제외했다. Prometheus, API/DB snapshot, inventory/oversell audit, cleanup은 통과했다. Client 완료 좌석은 41석, 최종 DB 확정 좌석은 276석으로 235석 차이가 관측됐으며 원인은 판정하지 않는다. k6 CPU 최대 358.7%, RSS 최대 약 2.90GiB, Mac swap 최대 약 12.28GiB가 기록됐다. 서버 identity/설정은 유지됐다. 이 값으로 서버 또는 Mac을 단독 원인으로 판정하지 않는다.

이후에는 guard를 더 늘리지 않는다. Run 1은 19/20 유효 상태로 유지하며 Run 2·3을 보류한다. 마지막으로 실행된 API/재선택/종료 기록 구간을 확인하는 별도의 [harness 진단](reselection-harness-diagnostic.md)을 사용한다. 진단 marker가 추가된 결과는 Baseline에 포함하지 않는다.

이 시점의 유효한 Run 1은 19/20개였다. 위 accounting/guard 검증 실패로 해당 profile에서 자동 진행을 중단했고 Run 2·3은 시작하지 않았다. 이후 guard나 executor 상한을 다시 자동으로 변경하지 않았다. 자세한 시도별 기록과 수용 목록은 `results/20261011T223605.705751KST_baseline-run1-followup/`에 보존했다.

## 추적을 끈 대체 Run 1 수용 — 2026-10-11 23:46 KST

`20261011T234526.992418KST_overseas-high_target-vu-10000_reselection_run-01`을 20번째 유효 Run 1로 수용했다. **현재 Run 1은 20/20 유효이며 Run 2·3은 시작하지 않았다.**

추적 옵션 없이 기존 `overseas-high.reselection.json`으로 한 회 실행했다. Effective config는 이전 미완료 시도 및 추적을 켠 진단 Run과 동일하다. Summary와 raw point에 `harness_phase`가 없음을 확인했다. 서버 identity/설정 fingerprint는 실행 전후 및 직전 진단 실행과 같다. 애플리케이션·인프라·서버 설정은 변경하지 않았다.

Target VU, `iteration_started`, `journey_started`, `journey_finished`, `iterations`는 모두 10,000이다. 미완료, dropped iteration, technical guard 종료는 모두 0이다. 별도 warm-up과 새 fixture, Prometheus 수집, API/DB inventory audit, oversell invariant, 두 fixture cleanup 검증을 통과했다. 예약 완료 44건, system failure 9,956건(timeout 9,468 / network-error 488)은 그대로 보존한다. Client 완료 좌석과 DB unique confirmed seats는 각각 83석이며 중복 Seat ID는 0개다.

이번 실행에서는 이전 미완료 현상이 재현되지 않았다. 이전 실패 원인이나 문제가 해결됐다고 판정하지 않는다. 이전 제외 Run과 진단 artifact는 수정하거나 삭제하지 않는다. 최신 관측값과 수용 목록은 [재실행 검토 보고서](../results/20261011T233802.607461KST_overseas-high-reselection-untraced-run1-review/report.md)와 해당 directory의 `follow-up-status.json`에 보존했다.

## 고정 설정으로 반복 측정

Run 2·3은 각 profile/model에서 수용한 Run 1의 effective config를 그대로 복사해 사용한다. 위 두 실행 상한 예외도 유지한다. Target VU, 좌석 선호와 좌석 수 분포, retry, 비활성 think time/abandonment, HTTP timeout, warm-up, fixture, Prometheus 조건은 반복 중 바꾸지 않는다.

`scripts/vu_baseline_series.py --prepare`는 각 profile/model의 가장 최근 유효한 Target VU Run 1을 선택한다. 20개 조합을 모두 확보해야 Run 2·3을 준비한다. 새 KST series directory에 effective config 복사본, source hash, 수용/제외 artifact 목록과 관측값 요약을 저장한다. 이전 Run directory는 보존한다.

```bash
python3 k6/scripts/vu_baseline_series.py --prepare
# 출력된 series directory를 지정한다.
bash -c 'source k6/config/env.local.sh; python3 k6/scripts/vu_baseline_series.py --execute k6/results/{출력된-series-directory}'
```

Run 2의 low 전체 → high 전체를 순서대로 실행한 뒤, Run 3도 같은 순서로 실행한다. 각 profile의 Single Attempt와 Reselection은 별도 fixture와 기존 별도 warm-up을 사용한다. Driver는 측정 구간 밖에서 고정 effective config와 서버 identity를 확인한다. Accounting, artifact, collection, inventory, guard, cleanup 또는 client runtime 검증에 문제가 있으면 후속 Run을 시작하지 않는다. 관측된 HTTP timeout, Business Conflict, system failure 수치만으로 중단하지 않는다. 각 Run의 원본 k6 log와 raw point는 해당 directory에 남기고, orchestration log도 보존한다.
