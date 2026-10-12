# Target VU 재현성 검증과 새 Final Baseline

기존 60회는 Exploratory Baseline, 기존 8,000/10,000 VU는 Stress Observation으로 보존한다. 원본 결과를 변경하지 않는다. 새 측정은 별도 series와 `pf` / `final` run directory에 저장한다.

## 실행 조건

develop에 harness를 commit/push하고 clean Git 상태에서 실행한다. Docker Desktop과 Colima 등 로컬 container runtime, 불필요한 로컬 개발 서버가 OFF인지 실제 process/listener 상태로 확인한다. Docker privileged helper만 남아 있는 경우는 Docker Desktop 실행으로 간주하지 않는다.

```bash
source k6/config/env.local.sh
python3 -u k6/scripts/reproducible_baseline.py \
  --exploratory-index k6/results/20261012T102210.909736KST_final-baseline-run2-continuation/series-results.json
```

이 명령은 기존의 수용한 Run 1 effective config 20개를 검증하고 복사한다. Target VU, seed, timeout, guard, maxDuration, gracefulStop, warm-up 및 좌석 정책은 그대로 유지한다. application checkout SHA와 실제 running container/image를 별도로 기록한다. Harness commit을 서버에 배포하지 않는다.

1 VU = 1 User = 1 Journey = 1 Iteration이며 `per-vu-iterations`, `iterations=1`을 유지한다. 400~3000 VU Reselection guard는 60s, 5000/6000은 5m다. HTTP timeout은 10s다.

## 고정된 준비 순서

기존 runner 순서를 유지한다. 별도 warm-up 계정/JWT와 fixture → warm-up 및 검증 → 측정용 unique Member/JWT 준비 → fresh measured fixture와 all AVAILABLE 검증 → workload → 사후 Prometheus query_range / DB consistent inventory audit → fixture cleanup → report / validation이다. JWT 발급과 fixture 준비는 measured latency/RPS에 포함하지 않는다. 새로운 cooldown이나 sleep을 넣지 않는다.

JWT preparation은 target/completed/failure count와 elapsed time을 기록한다. 전송 예외는 class/repr/str 및 underlying reason/class/repr, authentication index, API, timestamp를 기록한다. Strict series에서는 로그인 실패를 회원가입으로 재시도하지 않는다. 실패 후 이미 실행 중인 준비 요청은 종료를 기다리고 추가 retry는 하지 않는다.

## 15회 Preflight

rapper-low Single, idol-low Reselection, musical-high Single, idol-high Reselection, overseas-high Reselection을 각각 정확히 3회 측정한다. 각 반복에서 다섯 조합을 순서대로 실행한다.

각 Run validity 및 환경 일치 여부를 우선 확인한다. 정상적인 timeout/network/system failure, business result, latency, RPS, sell-through 편차는 실패 기준이 아니다. Client / DB confirmed-seat 값도 각각 보존한다.

Journey와 최초 seat-map 요청의 시작 시각, spread, elapsed-second별 dispatch count를 기록한다. `first_request_started`는 `http.request` 직전 client intent marker이며 실제 socket 전송이나 서버 도착 시각을 뜻하지 않는다. Native blocked/connecting/sending/waiting/receiving/duration 분포와 status=0/error code/error message를 함께 보존한다.

구체적인 burst 입력 이상 판정은 같은 조합의 세 반복 중 두 spread가 1s 미만이고 한 spread가 6s 이상인 경우다. 사용자 지적 사례를 검출하는 실험 입력 비교 규칙이며 성능 SLA가 아니다. business/server 결과만으로 FAIL하지 않는다. 애매한 편차는 PASS다.

## PASS 이후 60회

15회 유효성 통과 후 명확한 dispatch anomaly가 없으면 추가 승인 없이 새 Final Run 1 전체 20회 → Run 2 전체 20회 → Run 3 전체 20회를 진행한다. Preflight는 Final 반복에 재사용하지 않는다. 최대 측정 횟수는 75회이며 retry/resume은 지원하지 않는다.

미완료/dropped/guard/accounting/Prometheus/inventory/oversell/cleanup 실패, JWT preparation 실패 또는 환경 변경이 있으면 artifact를 보존하고 즉시 이후 실행을 중단한다. 서버나 workload 설정을 변경하지 않는다.

## 저장과 비교

Series directory에는 고정 config와 SHA, Mac/OCI 환경, preparation sequence, JWT 상세 기록, 개별 Run validation, 기존 결과 분류, Preflight decision, comparison CSV 및 `report.md`를 보존한다. 개별 Run은 기존 결과 구조를 유지한다.

Mac CPU/RSS/free-memory/swap 및 Docker 상태를 workload 중에 기록한다. 메모리 관측값만으로 서버 원인을 판정하지 않는다. Prometheus evaluation timestamp, measurement window 내부 sample 수, query 상태 및 실패 분류를 기록한다. 짧은 workload에서도 기존 scrape-context 수집을 유지하며 최소 workload 길이를 합격 조건으로 추가하지 않는다.

각 조합의 세 Run 원값과 min/median/max를 보여준다. 평균 하나로 덮어쓰지 않는다. 완료 후 Exploratory와 New Final의 주요 세 조합을 별도 행으로 비교한다. 병목 분석이나 개선안은 생성하지 않는다.
