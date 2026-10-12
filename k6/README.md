# Imticket-v2 Baseline k6

현재 Target VU 기반 측정은 [재현성 Preflight → 새 Final Baseline 실행 문서](docs/reproducible-baseline.md)를 따른다. 기존 arrival-rate 실험과 Exploratory 결과는 별도로 보존한다.

[두 workload model의 구현·metric·좌석 검증·실행 전 설정](docs/baseline-workload-models.md)을 추가했다. Single Attempt는 기존 한 번의 시도를 유지하고, Reselection은 서버 AVAILABLE 조회와 재선택으로 별도 구현했다. 실서버 실험은 구조·설정 검토 뒤 진행한다.

현재 구현은 **Single Attempt / Reselection workload와 자동 준비·결과 보존 도구**다. 기존 Single Attempt와 사용자가 실행한 Reselection Smoke/Pilot 결과를 보존하며, 별도의 작은 충돌 validation도 완료했다. develop의 Controller / DTO / SecurityConfig / 통합 테스트를 확인해 작성했으며 앱 비즈니스 로직, 서버 API, monitoring component는 변경하지 않는다.

재선택 분기만 확인하는 검증은 `bash k6/scripts/reselection.sh validation --config k6/config/reselection.local.json --run-number 1`로 실행한다. Member 둘/subject Journey 1건으로 실제 서버에서 충돌 → 재조회 → 다른 AVAILABLE 선택 → 최종 성공을 확인한다. 별도 결과에 저장하며 Baseline 조건을 변경하지 않는다. [완료한 validation의 조건과 관측값](docs/reselection-validation.md)을 참고한다.

[Pilot 구현·설정·실행 명령](docs/pilot-implementation.md)을 추가했다. 첫 실서버 Pilot 결과는 run directory에 보존했다. Baseline 본 측정의 고정 config와 Low/High 대표 4회 계획은 [첫 batch 문서](docs/baseline-first-batch.md)에 기록한다. [사전 검토](docs/pilot-baseline-review.md)와 [실서버 metric 목록](docs/metric-inventory.json)은 구현 전 확인 기록이다. Smoke latency는 성능 기준으로 사용하지 않는다.

```bash
# 설정만 확인; 서버 호출 없음
bash k6/scripts/baseline-batch.sh --plan

# musical-low/high × Single Attempt/Reselection × Run 1만 순차 실행
bash k6/scripts/baseline-batch.sh --execute
```

첫 batch는 기존 env.local.sh, 자동 계정/JWT와 매 회차 새 fixture를 사용한다. 실행 중 설정을 자동 변경하지 않고 전체 60회로 자동 확장하지 않는다. dropped/unfinished/accounting/수집/inventory/cleanup 문제는 측정 검토 대상으로 기록한다. Baseline System Failure는 관측 결과이며 zero-error SLO로 강제하지 않는다.

2026-10-11의 [첫 Baseline batch 결과](results/20261011T145048.378588KST_baseline_first-batch/report.md)는 Low 두 모델의 측정 검증이 통과했고 High 두 모델은 arrival-generation 검증에 실패했다. 네 Run의 관측값과 실패 artifact를 모두 보존했고 나머지 56회는 실행하지 않았다. 변경한 고정 config, VU/arrival 근거와 시간 경계 재검증은 [첫 batch 문서](docs/baseline-first-batch.md)에 기록한다.

```bash
# 서버 호출 없이 설정과 37개 query만 확인
bash k6/scripts/pilot.sh --config k6/config/pilot.example.json --plan
```

Pilot의 token pool 크기는 config의 `auth.tokenPoolSize` 또는 `--users`로 명시한다. 예제의 `null`을 자동 smoke 기본값으로 채우지 않는다. 별도 warm-up과 새 측정 fixture를 사용하며, Pilot 완료 후 Baseline으로 자동 진행하지 않는다.

현재 Pilot 예제/local config는 measurement와 warm-up 모두 20/20 VU를 사전 할당한다. 완료된 첫 Pilot은 변경안이 파일에 반영되기 전의 10/10으로 실행됐으며 run snapshot은 그 실제 값을 보존한다.

`token pool size is unset` 오류가 나면 사용하려는 계정 수를 지정한다. 예를 들어 **10개 계정을 선택한 경우** 아래와 같이 설정 확인 후 실행한다. 10은 명령 예시이며 자동 기본값이 아니다. `automatic` mode에서는 계정 생성·재사용과 JWT 발급을 측정 전에 자동으로 처리한다.

```bash
bash k6/scripts/pilot.sh --config k6/config/pilot.local.json --users 10 --plan
bash k6/scripts/pilot.sh --config k6/config/pilot.local.json --users 10 --run-number 1
```

같은 크기를 계속 사용하려면 local config의 `auth.tokenPoolSize`에 지정해 `--users`를 생략한다.

## 자동 준비와 반복 실행

`env.local.sh`에 BASE_URL과 원격 서버의 SSH alias인 K6_SSH_TARGET을 설정하면 아래 한 명령으로 실행한다. 현재 로컬 설정은 `imticket` SSH alias와 `imticket-v2-mysql` 컨테이너를 사용한다. 로컬 Docker Compose에서는 K6_SSH_TARGET을 생략한다.

```bash
bash k6/scripts/smoke.sh --profile musical-low

# 계정 pool 1000개 준비, 매번 새 1000석 fixture로 smoke 3회
bash k6/scripts/smoke.sh --profile musical-low --users 1000 --repeat 3
```

`smoke.sh`가 env.local.sh를 읽으므로 수동 `source`, 로그인, 토큰 복사, 회차 ID 전달은 필요 없다. 순서는 DB 연결 확인 → 임의 계정 생성/재사용 및 JWT 갱신 → 공연장/공연/회차/좌석 생성 → smoke → 결과/Prometheus 수집 → 해당 fixture의 서버 데이터 삭제다. 실패한 smoke도 실행이 종료된 뒤 정리하고, 실패 코드를 유지하며 반복을 중단한다. 측정이 시작되기 전에 수행하는 회원가입/로그인과 fixture 생성은 k6 예매 지표에 포함하지 않는다.

Prometheus가 서버 내부에서만 접근 가능하면 K6_PROMETHEUS_SSH_PORT=9090을 설정한다. 자동 실행이 임시 SSH tunnel을 열어 수집하고 종료 시 닫는다. 현재 로컬 설정에는 이 값도 적용했다. 직접 수집하는 Prometheus endpoint가 응답하지 않으면 첫 연결 실패 후 수집을 종료해 각 metric마다 timeout을 반복하지 않는다.

기존 smoke 자동 준비의 기본 계정 pool은 10개이며 `--users` 또는 TEST_USER_COUNT로 바꾼다. `--workers`/TEST_ACCOUNT_WORKERS는 사전 계정 준비의 동시 요청 수이고 기본 4, 최대 32다. pool 크기를 늘리면 부족한 계정만 추가한다. 계정 정보는 서버 주소별 `accounts-{hash}.local.json`에 권한 0600으로 보관하고, 매 실행 전에 같은 계정으로 새 토큰을 발급한다. 임의 아이디/비밀번호는 회원가입 요청 전에 저장하므로 응답을 받지 못한 경우에도 다음 준비에서 같은 계정의 로그인으로 복구한다. 계정은 fixture 삭제 후에도 유지해 반복 실행마다 회원 수가 계속 늘지 않도록 한다.

계정 수와 VU 수는 별도다. `--users 1000`은 회원 pool 준비 수이며, smoke는 여전히 1–2 VU로 예매 흐름을 확인한다. Pilot은 별도 `pilot.sh`와 arrival config를 사용한다. Baseline은 model별 config로 실행하며 stage/VU는 각 모델의 Pilot 검토 뒤 확정한다.

서버 데이터를 검사할 때는 `--keep-data`로 자동 삭제를 생략한다. 저장된 fixture를 직접 초기화할 수도 있다.

```bash
source k6/config/env.local.sh
source k6/fixtures/current.env.sh

# 대상 SQL 확인
python3 k6/scripts/reset-fixture.py --dry-run

# 해당 회차의 예약/결제/멱등 기록 제거 + 좌석 AVAILABLE 복원
python3 k6/scripts/reset-fixture.py

# 해당 fixture의 공연장/공연/회차/좌석까지 제거
python3 k6/scripts/reset-fixture.py --mode delete
```

초기화는 기록된 ID와 임의 fixture 이름을 모두 대조하고, 해당 회차의 기록된 테스트 회원 예약만 대상으로 MySQL 트랜잭션을 실행한다. 예약이 다른 회차의 좌석도 참조하거나, 외부 회원 예약/FAKE 이외 결제/공유된 공연장·공연이 있으면 중단한다. 외래 키를 해제하거나 전체 DB를 비우지 않는다. 실행 중인 fixture는 정리하지 않는다. reset 후에는 완료한 claim을 보관하고 재사용을 허용하며, delete 후에는 manifest를 cleaned로 표시한다. 결과 디렉터리와 초기 좌석 snapshot은 보존한다.

계정만 미리 준비하려면 `python3 k6/scripts/prepare-accounts.py --users 1000`을 실행하고 `source k6/fixtures/accounts.env.sh`로 불러온다. 아래 수동 토큰/fixture 명령은 기존 계정이나 토큰을 직접 지정하려는 경우에 사용할 수 있다.

## 디렉터리

```text
k6/
  smoke.js
  pilot.js
  lib/
    config.js             # 환경변수 검증, SharedArray token pool
    tokens.js             # iteration 단위 pool 순환 선택, VU와 무관
    api.js                # 실제 HTTP 경로, 인증·멱등 키, API tag
    contracts.js          # DTO 검증, Business/System Failure 분류
    journey.js            # 좌석 조회부터 예약 상세까지
    metrics.js            # API·Journey 지표
    seats.js              # smoke 좌석 분리, 향후 workload 확률 선택
    summary.js            # JSON / TXT summary
    arrival.js / pilot-metrics.js # Pilot arrival 검증·시작/종료 기록
  config/
    env.example.sh
    profiles.json         # 10개 workload profile의 유일한 정의
    fixture-template.json # 배치·등급·가격·공연 시작 시각 규칙
    ramping-arrival-rate.template.json  # 미확정 실행 설정; 아직 실행하지 않음
    pilot.example.json    # 낮은 Pilot 제안값, token pool 크기는 명시 필요
  fixtures/tokens.example.json
  scripts/
    run-experiment.py
    pilot.sh / pilot_runner.py # 단일 Pilot과 별도 warm-up, 새 fixture
    pilot_config.py / pilot_observations.py # 설정과 초별 관측 집계
    pilot_prometheus.py / pilot_report.py # inventory 기반 종료 후 수집·report
    smoke.sh / run-smoke.py # 계정·토큰·fixture·실행·정리 자동화
    prepare-accounts.py / test_accounts.py # 임의 계정 pool 생성·재사용·JWT 갱신
    reset-fixture.py / fixture_cleanup.py # 기록된 fixture의 MySQL 초기화/삭제
    issue-token.py        # 선택 사항: 측정 전에 기존 계정 로그인
    prepare-fixture.py    # 공연장 → 공연 → 회차 생성·초기 좌석 검증
    fixture_state.py      # 측정 전 재검증, fixture 단일 사용 claim
    preparation.py        # 측정 경로와 분리된 기존 API 호출
    collect_prometheus.py # 부하 종료 후 별도 수집 / 재수집
    report.py             # 저장된 결과로 report 재생성
    common.py
  tests/
  results/                # Git 제외
```

## 현재 코드에서 확인한 API 계약

모든 성공 응답은 `{"success":true,"data":...,"errorCode":null,"message":null}`이며 HTTP 200이다. 실패 본문은 `success:false`, `data:null`, `errorCode`, `message`를 사용한다. 5xx/인증 실패 등은 JSON envelope가 없을 수도 있다.

| API tag | HTTP 경로 | 요청 | 사용·검증하는 data |
|---|---|---|---|
| 인증, 측정 제외 | `POST /api/auth/login` | `{username,password}` | `accessToken`, `memberId`, `role` |
| seat-map | `GET /api/seats/{performanceTimeId}` | 없음 | 배열의 `id,section,rowNumber,seatNumber,grade,price,status` |
| selection-summary | `POST /api/seats/selection-summary` | `{performanceTimeId,seatIds}` | `performanceTimeId,seats,totalPrice` |
| pre-reserve | `POST /api/reservations/pre-reserve` | `{performanceTimeId,seatIds}` + `Idempotency-Key` | `id,reservationCode,totalPrice,status=PENDING_PAYMENT,expiredAt` |
| payment-prepare | `POST /api/payments/prepare` | `{reservationId}` + 별도 `Idempotency-Key` | `paymentOrderId,merchantOrderId,amount,currency,provider,providerPaymentId` |
| payment-verify | `POST /api/payments/{paymentOrderId}/verify` | `{providerPaymentId}` | `paymentStatus=APPLIED,reservationStatus=CONFIRMED`, 주문·예약 ID |
| reservation-detail | `GET /api/reservations/{reservationId}` | 없음 | 본인 예약의 `id,reservationCode,totalPrice,status=CONFIRMED,expiredAt=null,seats` |

SecurityConfig 때문에 **6개 예매 API 모두 Bearer 인증이 필요**하다. 선택 확인은 좌석을 선점하거나 AVAILABLE만 허용하는 API가 아니다. 통합 테스트는 서비스 계층에서 선점 충돌, 멱등 재호출, 결제 후 `APPLIED/CONFIRMED/RESERVED`를 확인한다. 테스트 fixture의 `passwordHash="not-used-in-service-test"`는 HTTP 로그인용 계정 데이터가 아니다.

현재 자동 서버 결제 Journey는 기존 **FAKE gateway**에 맞춘다. prepare 응답이 `PORTONE`이면 `unsupported-payment-provider`로 종료한다. PortOne은 외부 결제 PAID 승인이 있어야 verify가 성공하며, 이번 코드가 승인 정보를 생성하지 않는다. prepare까지 도달한 뒤 실패하면 기존 hold 만료 처리에 맡긴다.

catalog에는 예매 시작용 GET 조회가 없다. 별도 fixture 준비 명령이 기존 생성 API의 반환 ID를 이어서 사용하고, 생성된 `PERFORMANCE_TIME_ID`를 이후 측정에 전달한다. 예매 k6 스크립트에는 fixture 생성 요청을 넣지 않는다.

## 1. 사전 준비

필수 실행 도구는 **k6 1.x**와 Python 3.10 이상이다. 현재 구현은 로컬 k6 1.6.1의 기존 `handleSummary` JSON schema 기준으로 검증한다. 외부 JS 라이브러리나 Python package 설치는 필요 없다.

실행 환경은 모두 환경변수로 받는다. 저장소 루트에서 실행한다.

```bash
export BASE_URL='http://<OCI_PUBLIC_IP>:8080'
export TEST_TOKENS_FILE='./k6/fixtures/tokens.local.json'

# 서버 metric도 수집할 때만 지정
export PROMETHEUS_URL='http://<OCI_PUBLIC_IP>:9090'
```

`<...>`는 실제 값으로 바꾼다. 현재 Docker Compose의 공개 포트 예시이며 도메인/포트가 다르면 실제 접근 URL을 사용한다. BASE_URL에 `/api`를 붙이지 않는다. `PROMETHEUS_URL`을 생략하면 k6 결과와 report는 저장하고 서버 수집 상태를 `not-configured`로 기록한다.

반복해서 사용할 경우:

```bash
cp k6/config/env.example.sh k6/config/env.local.sh
# env.local.sh의 placeholder를 실제 값으로 수정
source k6/config/env.local.sh
```

### 토큰 / 계정 fixture

첫 Baseline은 이미 로그인된 사용자가 대상이다. k6 init/iteration과 측정 runner에서 register/login을 호출하지 않으며 로그인·BCrypt·토큰 발급 비용은 제외한다. 자동 준비 스크립트는 측정 전에 계정과 토큰을 준비한다. 각 예매 HTTP 요청의 서버 JWT 검증은 포함한다. 서버 JWT TTL 기본값은 2h이며 측정 도중 자동 갱신이나 로그인 fallback은 없다.

`TEST_TOKENS_FILE`의 **전체 token pool**을 사용한다. iteration 번호에 따라 `pool[iterationInTest % pool.length]`를 선택하고 Journey 종료까지 같은 JWT를 유지한다. pool을 VU에 할당하거나 VU/iteration마다 회원을 만들지 않는다. pool보다 많은 동시 VU를 허용하며 pool을 재사용할 수 있다. pool 크기는 입력 파일의 길이이고 코드가 임의로 늘리거나 줄이지 않는다. 별도 pool 권장 크기는 정하지 않았다. `TEST_ACCESS_TOKEN`은 한 개짜리 pool 입력으로도 사용할 수 있다.

비밀번호만 있다면 기존 계정으로 **별도의 사전 명령**을 실행할 수 있다.

```bash
export TEST_USERNAME='<EXISTING_USERNAME>'
export TEST_PASSWORD='<EXISTING_PASSWORD>'
export TOKEN_OUTPUT_FILE='./k6/fixtures/tokens.local.json'
python3 k6/scripts/issue-token.py

unset TEST_ACCESS_TOKEN
export TEST_TOKENS_FILE='./k6/fixtures/tokens.local.json'
```

여러 기존 계정은 `[{"username":"...","password":"..."}, ...]` 형태의 `k6/fixtures/accounts.local.json`을 준비하고 다음 명령으로 발급한다. 입력한 계정 수만 사용하며 register나 계정 자동 생성은 수행하지 않는다.

```bash
python3 k6/scripts/issue-token.py \
  --accounts k6/fixtures/accounts.local.json \
  --output k6/fixtures/tokens.local.json
```

이 도구는 `data.accessToken`과 `data.memberId`를 파일 권한 0600으로 저장하고 토큰을 stdout에 출력하지 않는다. 파일이 이미 있으면 덮어쓰지 않는다. TEST_USERNAME/TEST_PASSWORD는 사전 발급 명령에서만 사용하며 runner는 k6 자식 프로세스에 전달하지 않는다. 이 로그인 비용과 fixture 생성 비용은 workload metric에 포함하지 않는다.

사전 발급 pool 파일은 `tokens.example.json` 형태다. 아래 두 항목은 형식 예시이며 필요한 pool 크기를 뜻하지 않는다. smoke VU 수와 실제 Member 수를 1:1로 대응시키지 않는다.

```json
[
  {"memberId": 11, "accessToken": "<TOKEN_OF_MEMBER_11>"},
  {"memberId": 12, "accessToken": "<TOKEN_OF_MEMBER_12>"}
]
```

`memberId`는 토큰 발급 응답의 실제 값이다. runner가 fixture 경로를 절대 경로로 바꿔 k6에 전달한다. TEST_ACCESS_TOKEN과 TEST_TOKENS_FILE은 동시에 지정하지 않는다. `k6/fixtures/`의 실제 fixture와 `*.local.*` 설정은 Git 제외다. 토큰·비밀번호는 실험 metadata/summary/points에 저장하지 않는다.

계정이 부족하면 자동 준비 스크립트가 기존 register API로 측정 전에 준비한다. AuthService가 실제 BCrypt hash를 저장하고 JWT를 발급한다. username 중복은 409/USERNAME_DUPLICATED이고 비밀번호는 8–72자다. 회원을 DB에 직접 삽입하지 않는다. pool 크기, 순환 선택 방식, 입력에 memberId가 있으면 선언된 서로 다른 Member 수를 run metadata/report에 기록한다. JWT를 새로 발급하면 token fixture hash도 달라진다.

### 동일 Member 동시 예약의 코드 근거

- `MemberRepository`에는 `@Lock` 조회가 없고 `Member`에는 `@Version`이나 예약 관련 변경 상태가 없다. `members`의 unique는 username이다.
- `ReservationCreationService`는 Member를 `findById`로 읽는다. 비관적 잠금은 idempotency claim ID와 선택 seat ID에 걸리며 Member ID에 걸리지 않는다.
- `ReservationIdempotency`와 `PaymentOrder`의 회원 관련 unique는 각각 `(member_id, idempotency_key)`다. 매 Journey의 다른 키로 충돌을 피한다.
- `PaymentOrder`의 `reservation_id` unique는 같은 예약의 중복 결제 주문 방지다. 회원 전체 예약을 제한하지 않는다.
- 결제 준비/완료의 잠금은 개별 reservation/payment order/seat에 걸린다. JWT 필터는 stateless이며 회원별 활성 예약 수 제한도 없다.

이 근거로 현재 유효한 다른 멱등 키를 쓰는 **제한된 token pool 재사용을 허용**한다. 이는 DB 내부 인덱스/캐시 경합의 성능 영향이 0이라는 증명은 아니다. pool 크기를 고정된 실험 조건으로 남겨 비교한다. 회원별 명시적 직렬화나 한 회원당 단일 활성 예약 제약은 현재 코드에서 발견하지 못했다.

## 2. Fixture 준비 / 새 초기 좌석 상태

```bash
python3 k6/scripts/prepare-fixture.py --profile musical-low
source k6/fixtures/current.env.sh
```

순서는 인증된 기존 API `POST /api/catalog/halls` → `POST /api/catalog/performances` → `POST /api/catalog/performances/{반환된 performanceId}/times`다. hall 반환 ID는 `venueHallId`로 전달한다. 회차 생성 서비스가 실제 좌석을 AVAILABLE로 생성한다. 준비 완료 시 좌석 map의 개수/좌표/가격/등급과 모든 좌석의 AVAILABLE 상태를 확인한다.

profile의 seatCount에 맞춰 `fixture-template.json`의 기본 배치를 적용한다. 기본 50석/행으로 1000석은 20행, 1500석은 30행이며 section A, 등급 R, 가격 10000원이다. 이 값들은 테스트용 fixture 값이고 해당 JSON 한 곳에서 변경한다. 기존 hall API는 단일 section/등급의 직사각형만 지원하므로 seatCount가 seatsPerRow로 나누어지지 않으면 임의로 좌석을 추가하지 않고 오류를 반환한다. 기본 공연 시작 시각은 준비 시점 UTC에 1일을 더한 LocalDateTime으로 생성·기록한다. 예매 부하 stage 시간과는 관계없다.

```text
k6/fixtures/{UTC timestamp}_{profile}_{unique suffix}/
  fixture-manifest.json # API payload/반환 ID, 준비 시작·종료, 좌석 조건
  initial-seats.json    # ID 포함 실제 초기 좌석 snapshot과 fingerprint
  fixture.env.sh       # FIXTURE_MANIFEST / PERFORMANCE_TIME_ID / WORKLOAD_PROFILE
  measurement-claim.json # 이 fixture를 사용한 run, 측정 전 좌석 재검증
```

`current.env.sh`는 가장 최근 성공한 준비 결과의 편의 포인터다. 각 fixture의 원본 `fixture.env.sh`도 유지한다. `--exports-file`로 편의 포인터 위치를 지정할 수 있다. runner는 `FIXTURE_MANIFEST` 또는 `--fixture <manifest>`를 읽어 회차 ID를 얻는다. 수동 PERFORMANCE_TIME_ID가 남아 있다면 manifest와 동일해야 한다. BASE_URL/profile/snapshot fingerprint 불일치도 실행 전에 막는다.

runner는 fixture를 하나의 run에 독점 claim한 뒤 측정 직전 준비 상태를 다시 HTTP로 확인한다. 이미 claim한 fixture는 reset 스크립트로 초기화하지 않는 한 재측정하지 않는다. 자동 실행은 **매 run 전에 같은 profile/template으로 새 fixture를 준비**한다. warm-up과 본 측정도 각각 별도 fixture가 필요하다. 로컬 claim 파일은 이 runner 사이의 재사용을 방지하며 외부 사용자의 동시 좌석 변경까지 막는 서버 락은 아니다.

기존 API에는 좌석 reset/delete가 없다. 자동 실행은 새 회차를 만들고 실행 후 MySQL 정리 스크립트로 해당 fixture를 삭제한다. `--keep-data`로 정리를 생략하면 과거 fixture/예약 데이터는 DB에 남는다. reset은 예약/결제/멱등 기록을 제거하고 해당 회차의 좌석을 AVAILABLE로 되돌린다. 이런 정리는 DB의 auto-increment 값이나 전체 캐시 상태까지 snapshot 시점으로 복원하지는 않는다. 서버 API/비즈니스 코드는 변경하지 않는다.

catalog POST는 멱등 키 API가 아니므로 준비 실패/timeout을 자동 재시도하지 않는다. 성공한 반환 ID와 마지막 in-flight 요청을 partial manifest에 보존한다. 자동 실행은 부분 생성 실패도 고유한 fixture 이름과 기록된 ID로 확인해 삭제한다. 조회 결과가 여러 건이거나 기록된 ID와 다르면 정리를 중단한다. 생성/재검증 요청은 Python 사전 단계라 k6 metric에 섞이지 않는다. 서버 측 지표에는 준비 시점의 부하가 남을 수 있으므로 준비 시간과 측정 시간을 따로 보존하고 대기 간격을 임의로 추가하지 않는다.

## 3. Smoke 실행

fixture를 준비하고 source한 뒤 실행한다. 기본 1 VU, 1석, VU별 전체 Journey 1회다. 실제 Member는 iteration마다 pool에서 선택한다. 본 측정 duration이나 arrival rate는 적용하지 않는다.

```bash
python3 k6/scripts/run-experiment.py smoke --run-number 1
```

2 VU, Journey마다 3석을 검증하려면 (token pool은 동일하게 재사용):

```bash
unset TEST_ACCESS_TOKEN
export TEST_TOKENS_FILE='./k6/fixtures/tokens.local.json'
export SMOKE_VUS=2
export SMOKE_SEATS=3
python3 k6/scripts/prepare-fixture.py --profile musical-low
source k6/fixtures/current.env.sh
python3 k6/scripts/run-experiment.py smoke --run-number 1
```

선택 가능 값은 SMOKE_VUS=1/2, SMOKE_SEATS=1/2/3이다. smoke의 seat slot만 VU index로 분리한다. 이는 토큰/Member 배정과 무관하며 2 VU가 같은 Member JWT를 써도 좌석은 겹치지 않는다. 해당 좌석이 부족하거나 AVAILABLE이 아니면 `smoke-fixture-unavailable`로 실패한다. 다른 좌석으로 조용히 대체하지 않는다.

반복 실행은 fixture 준비 명령부터 다시 수행한다. 확정 RESERVED 좌석은 hold 만료로 해제되지 않는다.

종료 코드 0은 전체 Journey가 VU 수만큼 완료되고 Business/System Failure가 0이며 check가 모두 통과했음을 뜻한다. SEAT_UNAVAILABLE은 Business Failure로 정확히 기록하지만 smoke 목적상 전체 Journey 미완료이므로 종료 코드는 실패다. 임의의 latency SLO는 지정하지 않는다.

선택 환경변수:

| 변수 | 의미 |
|---|---|
| HTTP_TIMEOUT | 개별 요청 제한; 미지정 시 k6 기본 60s |
| SMOKE_MAX_DURATION | 1–2회 실행 완료를 기다리는 상한; 미지정 시 per-vu-iterations 기본 10m |
| SMOKE_GRACEFUL_STOP | k6 graceful stop; 미지정 시 k6 기본 30s |
| WORKLOAD_PROFILE 또는 `--profile` | fixture/profile 일치 확인. smoke 좌석/arrival 전략에는 적용하지 않음 |
| FIXTURE_MANIFEST 또는 `--fixture` | 성공적으로 준비한 fixture manifest. PERFORMANCE_TIME_ID는 이 파일에서 전달 |
| PROMETHEUS_STEP_SECONDS | 기본 5초, 현재 prometheus.yml scrape interval에 맞춤 |
| PROMETHEUS_JOB | 기본 `imticket-v2`, 현재 scrape job 이름 |
| PROMETHEUS_TIMEOUT_SECONDS | 후처리 HTTP timeout; 기본 10초 |

smoke의 실행 상한은 성능 측정 stage 시간이 아니다. 기존 개인 k6 옵션 파일과 K6_* 환경변수는 runner가 배제하고, 저장한 스크립트 설정으로 실행한다. 본 측정 시간을 위 기본 상한에서 가져오지 않는다.

## 지표 정의

6개 API에 `api`, 정규화된 `name`, `scenario`, `run_id`, `profile`, `phase` tag를 붙인다. 실패에는 `reason`, 서버 `code` 및 k6 `error_code`를 기록한다. raw URL/예약 ID/좌석 ID/사용자 ID를 metric tag로 넣지 않는다. 전체 Journey는 `booking-journey` group으로 측정한다.

| 지표 | 정의 |
|---|---|
| journey_started | 실제로 시작한 Journey 수 / starts/s |
| journey_finished | 성공 또는 실패로 종료된 Journey 수 / finishes/s |
| http_attempted | 클라이언트가 HTTP 호출을 시도한 횟수 / Attempted HTTP RPS. 전송 실패·timeout도 시도에 포함 |
| http_completed | transport 오류 없이 HTTP 응답(status > 0)을 받은 요청 수 / Completed HTTP RPS. 4xx/5xx 포함, timeout/network 제외 |
| http_contract_success | HTTP 200과 응답 DTO/상태 검증이 모두 성공한 수 |
| http_req_duration | k6 HTTP p50(`med`)/p95/p99. 정상·오류 모두 포함; API별 submetric도 저장 |
| journey_duration | 좌석 조회 시작부터 성공/실패 종료까지의 전체 시간 |
| booking_duration | 예약 상세 CONFIRMED 확인까지 완료된 Journey의 전체 시간 |
| reservation_success | PENDING_PAYMENT 선점 성공 수 / Reservation success/s |
| payment_applied | verify에서 APPLIED/CONFIRMED 확인 수 |
| booking_completed | 최종 예약 상세와 RESERVED 좌석까지 확인된 수 / Booking completed/s |
| business_failures / system_failures | Journey당 최대 1회 집계하는 실패 수 |
| api_business_failures / api_system_failures | API 요청 단위 실패 수 |
| dropped_iterations | 시작하지 못한 iteration. HTTP 실패나 서버 Business Failure와 별도 |

summary/report의 `/s`는 **k6 testRunDuration 전체를 분모로 한 평균**이다. 클라이언트가 시도한 HTTP 요청 수를 서버 처리량으로 해석하지 않는다. 시간대별 증가/둔화는 `k6-points.json`의 원본 timestamp/tag와 Prometheus 원본 series를 사용한다. 중간 단계에서 실패하면 후속 HTTP 요청은 발생하지 않으므로 Journey starts/s에 고정된 6을 곱해 Attempted RPS로 만들지 않는다. `http_attempted`는 기존 `http_offered`의 이름을 명확하게 바꾼 것으로 집계 위치/분모는 동일하다. 과거 raw summary/points는 그대로 보존하며 report 재생성 시 기존 이름도 읽는다.

`http_req_failed`는 현재 `responseCallback: http.expectedStatuses(200)`에 따라 409도 HTTP 실패로 집계하며 System Failure 지표를 대신하지 않는다. k6 1.x raw summary의 Rate는 `passes`가 0이 아닌 샘플 수이고 `fails`가 0인 샘플 수다. 따라서 `http_req_failed`에서는 raw `passes`가 실패 요청 수, raw `fails`가 기대한 응답의 요청 수다. custom text summary는 이 지표와 그 submetric만 `failed_requests`/`expected_requests`로 표시하며, `rate`는 실패 비율(0–1)이다. `expected_requests`는 HTTP 기대 응답 기준이며 DTO/예매 성공 여부는 `http_contract_success`/`booking_completed`에서 확인한다. `checks`의 `passes`/`fails`와 raw summary JSON의 값/키는 바꾸지 않는다.

### 실패 분류

- `pre-reserve`의 HTTP 409, `success:false`, `errorCode:SEAT_UNAVAILABLE`: Business Failure.
- verify의 HTTP 200, `paymentStatus:REFUND_PENDING`, `reservationStatus:EXPIRED`: 코드에서 확인한 Business Failure. `expired-refund-pending`은 클라이언트 분류 reason이며 서버 errorCode가 아니다.
- HTTP 5xx, timeout, network error, JSON/DTO 불일치, 예상하지 못한 4xx/상태: System Failure.
- 잘못된 토큰/회차/fixture와 unsupported provider도 실패 원인을 구분해 기록한다. 이 수치를 서버 내부 오류만으로 해석하지 않는다.

일반 BusinessException 전체나 모든 409를 Business Failure로 묶지 않는다. `IDEMPOTENCY_CONFLICT`, `SEAT_NOT_FOUND`, `INVALID_REQUEST` 등은 현재 유효 fixture의 정상 Journey에서 기대하지 않는 응답이다. 재시도 없이 실패한 단계에서 끝낸다. 선점/준비 멱등 키는 run + VU + iteration 조합으로 만들며 서로 다른 접두사를 사용한다.

Business Failure의 개별 console/error 로그는 출력하지 않고 metric과 raw outcome/reason으로 보존한다. System Failure는 개별 error 로그를 유지한다. Pilot report의 `HTTP Expected-Response Result`는 `System Failures`와 분리하며 `http_req_failed`에 HTTP 409 Business Failure도 포함된다는 점, 분모가 HTTP sample 수라는 점을 표시한다.

## Profile과 Single Attempt 실행 모델

`config/profiles.json`에서 요청한 10개 profile의 좌석 수, 참고 시도자 수, Peak Journey Arrival Rate, hot/선택 비율을 한 곳에서 관리한다.

- `referenceAttemptors`: 규모 참고값. exact iteration count가 아니다.
- `peakJourneyArrivalRate`: **새 예매 Journey starts/s**. 개별 HTTP req/s가 아니다.
- `seatSelectionRatio`: 1/2/3석 비율. 합은 1.
- `hotSeatRatio`: ID 오름차순 전체 좌석 중 앞쪽 `floor(seatCount × ratio)`를 hot 영역으로 정의.
- `hotTrafficRatio`: 한 Journey가 hot 영역에서 모든 선택 좌석을 뽑을 확률. cold 요청은 나머지 영역에서 선택.

`selectWorkloadSeats`는 1/2/3석을 확률로 뽑고, 해당 영역에서 한 Journey 내 중복 없이 선택한다. 인접 좌석을 보장하지 않는다. AVAILABLE 상태로 미리 필터링하지 않으며 사용자 사이 좌석 재선택을 허용한다. 고정 seed의 PRNG helper도 준비했다. profile의 실제 좌석 수가 맞지 않으면 실패한다. 이 helper를 smoke의 좌석 분리 전략과 혼합하지 않는다.

Pilot은 `pilot.js`와 `config/pilot.example.json`으로 **ramping-arrival-rate**를 사용한다. `config/ramping-arrival-rate.template.json`의 미설정 template도 유지한다. 두 모델의 Baseline 실행기와 3회 반복 구조를 구현했으며 본 측정 stage duration/VU는 미확정 값으로 남겼다.

Pilot config에서 startRate, 각 stage의 duration/target, preAllocatedVUs/maxVUs/gracefulStop을 명시적으로 입력받는다. profile peak나 referenceAttemptors로 시간을 역산하지 않는다. stage target은 timeUnit당 Journey 시작 수이며 예제 timeUnit은 1s다. 각 stage target까지 선형으로 유입률이 변하므로 peak 유지에는 같은 target의 별도 stage를 지정한다. k6 [ramping-arrival-rate 문서](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-arrival-rate/).

Pilot의 warm-up과 measurement는 별도 fixture/k6 프로세스/run directory로 분리한다. `warmup.enabled/execution`으로 설정하고 smoke의 `--phase warmup`과 혼합하지 않는다. Baseline의 `--repeat 3`은 같은 model/config/profile에서 각 회차별 새 warm-up/measured fixture를 준비한다. 실서버 Reselection Smoke/Pilot이나 Baseline을 자동으로 시작하지 않는다.

## 결과 보존과 Prometheus

아래 directory와 자동 metric discovery는 기존 Smoke 실행기 기준이다. 새 Pilot/Baseline은 model을 directory/metadata에 기록하고 기존 검증된 37개 Prometheus query를 재사용한다. [모델별 결과·inventory·시간 구간](docs/baseline-workload-models.md)을 참고한다.

매번 다음 디렉터리를 고유하게 만든다. 새 results timestamp는 한국 표준시(KST, UTC+09:00)이며 마이크로초와 `KST` 접미사를 포함한다. 예: `20261011T213000.123456KST_musical-low_reselection_pilot_run-01/`. metadata와 report의 기록 시각도 `+09:00`으로 표시한다. k6 원본 timestamp, 복사한 fixture manifest와 Prometheus epoch는 원본을 보존하고, 조회 구간은 같은 epoch로 계산한다. 기존 결과 폴더는 변경하지 않는다.

```text
k6/results/{timestamp}_{profile}_run-{n}/
  run-metadata.json
  fixture-manifest.json
  fixture-initial-seats.json
  k6-effective-options.json
  k6-options.json
  preflight.log
  k6.log
  k6-summary.json
  k6-summary.txt
  k6-points.json
  prometheus/
    collection-status.json
    actuator-prometheus.txt
    verified-metric-names.json
    <verified metric name>.json
  report.md
```

metadata에는 요청한 실험 조건, profile snapshot, token pool 크기/선택 방식, fixture 생성 ID/초기 조건/준비 시간, 실제 좌석 수와 시작/완료 횟수, k6 버전, Git revision/작업 트리 상태/파일 hash를 기록한다. fixture manifest와 초기 좌석 snapshot도 run directory에 복사한다. smoke에서는 적용 arrival rate가 없으므로 `arrival_rate:null`이며 profile peak와 구분한다. 실험 시작·종료 시각은 k6 프로세스 기준으로 보존하고 원본 points에서 첫 `journey_started`와 마지막 `journey_finished` timestamp도 별도 추출한다.

부하 종료 뒤에만 `/actuator/prometheus`를 **한 번** 읽는다. 실제 sample line에 나타난 metric name을 저장한 뒤, 각 이름으로 `{__name__="실제이름",job="현재job"}` selector를 생성해 **해당 Journey 시간 구간**의 query_range를 요청한다. 구체 metric 이름을 코드에서 가정하거나 미리 정의하지 않는다. 원본 응답을 그대로 보존하며 rate/quantile/병목 원인을 후처리에서 추정하지 않는다. API 형식은 [Prometheus HTTP API 문서](https://prometheus.io/docs/prometheus/latest/querying/api/)를 따른다.

현재 monitoring 설정은 app의 `/actuator/prometheus`만 scrape한다. Grafana baseline에 HTTP/Hikari/Tomcat/JVM 관련 query가 있으나, dashboard에 이름이 있다는 이유만으로 수집기에 넣지 않는다. DB Lock Wait / MySQL Host / OCI Host metric은 **현재 자동 수집 불가**다. exporter나 새 monitoring component를 추가하지 않는다.

결과가 없으면 `empty`, discovery 실패면 `discovery-failed`, 일부 query 실패면 `partial`로 남긴다. 짧은 smoke는 5초 scrape 사이에 끝날 수 있어 서버 시계열이 비어 있을 수 있다. 필요하면 scrape가 저장된 뒤 **부하를 재실행하지 않고** 같은 구간을 재수집한다. 앱 노출 metric과 Prometheus job/target의 대응, 서버·부하 발생기 시각 동기화는 실제 환경에서 확인해야 한다.

```bash
python3 k6/scripts/collect_prometheus.py '<RUN_DIRECTORY>'
python3 k6/scripts/report.py '<RUN_DIRECTORY>'
```

독립 수집 명령은 현재 환경변수 BASE_URL/PROMETHEUS_URL을 우선 사용하고, 없으면 기존 metadata URL을 사용한다. 재수집은 해당 run의 Prometheus 파일을 갱신하며 k6 summary는 바꾸지 않는다. Prometheus 수집 실패는 smoke API 성공 여부와 별도로 report에 표시한다.

report는 조건, 시간 구간, count/rate, p50/p95/p99만 기록한다. 해석이나 원인 추정은 생성하지 않는다. 실행하지 않은 API의 latency는 N/A로 표시한다. 초기화 실패·중단으로 summary가 없으면 metadata/report에서 누락 상태를 확인할 수 있고, 존재하지 않는 결과를 생성하지 않는다.

## Harness 검증

First Batch 이후의 고정 조건 arrival 탐색은 [load-delivery bracketing](docs/load-delivery-bracketing.md)을 사용한다. 모델별 VU는 256/512로 유지하고 candidate peak만 변경한다. Low 20/s PASS를 High 조건의 anchor로 재사용하지 않는다. 현재 Mac generator preflight에서 실제 실행을 보류했으며 `bash k6/scripts/load-delivery-bracket.sh --plan`으로 설정을 확인할 수 있다.

```bash
node --test k6/tests/*.test.js
python3 -m unittest discover -s k6/tests -p test_pilot.py -v
python3 -m unittest discover -s k6/tests -p test_reselection.py -v
python3 -m unittest discover -s k6/tests -p test_report.py -v
python3 -m unittest discover -s k6/tests -p test_result_timezone.py -v
python3 -m unittest discover -s k6/tests -p test_reselection_http.py -v
python3 -m unittest discover -s k6/tests -p test_pilot_http.py -v

# 설정된 실제 테스트 서버에서 예약/초기화/재예매/삭제/롤백 검증
source k6/config/env.local.sh
unset PROMETHEUS_URL
K6_SERVER_TESTS=1 python3 -m unittest discover -s k6/tests -p 'test_server_environment.py' -v
```

Node는 분류/선택 비율, iteration별 token 재사용, CONFIRMED의 expiredAt=null을 검증한다. Python은 localhost의 임시 계약 stub에서 fixture 생성 → 재검증 → k6 HTTP Journey → 후처리 순서를 확인한다. 계정 pool 생성·확장·재사용, 불명확한 회원가입 응답 복구, token 파일 권한, Prometheus 연결 실패 시 조기 종료도 검증한다. stub은 테스트 프로세스 안에서만 존재하고 앱 서버 API나 인프라를 추가하지 않는다. stub의 fixture/결과 디렉터리는 검증 후 삭제한다. 실제 서버 테스트는 별도 K6_SERVER_TESTS=1로 실행하며, 2 VU/각 3석 예매 후 SQL 실패 시 롤백, 예약/결제 삭제와 AVAILABLE 복원, 같은 fixture 재예매, 별도 fixture 보존, 부분 생성 데이터 정리를 확인한다. 실제 서버 테스트의 결과 디렉터리는 보존하고 테스트 fixture는 정리한다. 이 smoke 결과를 대량 동시 예약 성능의 증거로 사용하지 않는다.
## Target VU Baseline

The Target VU Baseline models a fixed user population entering a booking Journey. It uses `per-vu-iterations` with one iteration per VU, and the profile `targetUsers` value is the configured concurrent user count. The existing `ramping-arrival-rate` results and configs remain a separate experiment model.

Initial 400-user validation (rapper-low):

```bash
bash k6/scripts/baseline.sh --profile rapper-low --config k6/config/vu-baseline/rapper-low.single-attempt.json --workload-model single-attempt --run-number 1
bash k6/scripts/baseline.sh --profile rapper-low --config k6/config/vu-baseline/rapper-low.reselection.json --workload-model reselection --run-number 1
```

Each measured VU has one distinct pre-authenticated Member/JWT and one Journey. Warm-up and measurement use separate fixtures and separate account pools. Target VU reports store observed Journey start spread, completed throughput, HTTP RPS, latency, outcomes, load-generator snapshots, Prometheus data, and DB inventory/oversell verification. See [Target VU Baseline design](docs/vu-based-baseline.md).
