# Imticket-v2

티켓 예매 흐름을 다시 검증하기 위한 Spring Boot 기반 프로젝트입니다.

## 기술 스택

- Java 21
- Spring Boot 3.4.4
- Spring Data JPA
- Spring Security
- MySQL 8.4
- Spring Boot Actuator / Micrometer
- Prometheus / Grafana
- JDK Flight Recorder / async-profiler
- Docker / Docker Compose
- GitHub Actions

## 현재 구현 범위

- 아이디/비밀번호 기반 최소 회원 인증과 JWT 발급
- 공연장 좌석 템플릿, 공연, 회차, 좌석 생성
- 회차별 좌석 현황 DB 조회
- 선택 좌석 정보와 총액 확인
- MySQL 비관적 잠금 기반 좌석 선점
- 7분 결제 대기 예약과 만료 처리
- 멱등 키 기반 예약 중복 요청 처리
- 결제 주문 생성과 Fake/PortOne 결제 검증 adapter
- 결제 완료와 예약 만료가 경합할 때 예약/좌석/결제 상태 반영
- 본인 예약 상세 정보 조회
- Actuator 기반 애플리케이션 및 JVM 메트릭 수집
- Prometheus 수집과 Grafana baseline 대시보드
- 필요 시 JFR과 async-profiler로 CPU, allocation, lock 분석

현재 단계에는 Waiting Room, Nginx 유입 제어, Seat Availability Cache를 포함하지 않습니다.

## 실행

```bash
cp .env.example .env
docker compose up --build
```

실행 후 접근 주소:

- 애플리케이션: `http://localhost:8080`
- Actuator health: `http://localhost:8080/actuator/health`
- Prometheus metrics: `http://localhost:8080/actuator/prometheus`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (기본 계정 `admin/admin`)
- MySQL: `localhost:3306`

Grafana의 `Imticket-v2 Baseline` 대시보드에서 HTTP 처리량과 p50/p95/p99, Hikari, Tomcat, JVM CPU/heap/allocation/GC/thread 지표를 함께 확인할 수 있습니다.

로컬 Gradle 실행:

```bash
./gradlew test
./gradlew bootRun
```

## JVM 진단

JFR과 async-profiler는 기본적으로 실행하지 않습니다. Grafana에서 이상 징후를 확인한 뒤 필요한 구간에서만 기록합니다.

60초 JFR 기록:

```bash
./scripts/diagnostics/jfr-record.sh 60s
```

async-profiler:

```bash
./scripts/diagnostics/profile.sh cpu 30
./scripts/diagnostics/profile.sh wall 30
./scripts/diagnostics/profile.sh alloc 30
./scripts/diagnostics/profile.sh lock 30
```

결과 파일은 `profiles/`에 생성되며 Git에는 포함되지 않습니다. Docker Desktop 환경에서 Linux perf event 접근이 제한되면 CPU profiling 대신 `wall` 또는 JFR을 사용할 수 있습니다.

## 기본 API 흐름

1. `POST /api/auth/register` 또는 `POST /api/auth/login`
2. `POST /api/catalog/halls`
3. `POST /api/catalog/performances`
4. `POST /api/catalog/performances/{performanceId}/times`
5. `GET /api/seats/{performanceTimeId}`
6. `POST /api/seats/selection-summary`
7. `POST /api/reservations/pre-reserve`
8. `POST /api/payments/prepare`
9. 외부 결제
10. `POST /api/payments/{paymentOrderId}/verify`
11. `GET /api/reservations/{reservationId}`

선택 좌석 정보 확인은 조회 시점의 상태를 보여주며 좌석을 선점하지 않습니다. 실제 좌석 상태 검증과 잠금은 `/api/reservations/pre-reserve`에서 다시 수행합니다.

예약과 결제 요청은 `Idempotency-Key` 헤더를 사용합니다.

## CI

`develop`, `main` push 및 Pull Request에서 다음을 검증합니다.

- 테스트
- Spring Boot 실행 JAR 빌드
- Docker 이미지 빌드
