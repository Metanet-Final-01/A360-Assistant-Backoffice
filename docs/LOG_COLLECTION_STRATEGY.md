# 로그 수집 고도화 + EDA 로드맵

작성일: 2026-07-12 (팀 회의 22:00 자료)

## RPA-109 — 지금 채워야 할 설정값 (사람이 직접 해야 함, 코드 아님)

코드는 이미 다 돼 있어서 아래 값만 채우면 감사 로그·LLM 사용량 자동 수집이 켜진다.

1. **A360-Assistant-Backend** `.env`에 `ADMIN_EMAILS` 추가 — 관리자로 쓸 계정 이메일
   (콤마로 여러 개 가능). 예: `ADMIN_EMAILS=you@example.com`. 값이 비어 있으면
   `require_admin`이 항상 403을 낸다(fail-closed). 채운 뒤 Backend 재시작 필요(env는
   프로세스 시작 시 1회만 읽음).
2. 그 이메일로 A360-Assistant-Frontend(실서비스)에 실제 회원가입/로그인이 돼 있어야
   함 — `ADMIN_EMAILS`는 화이트리스트일 뿐, 계정 자체가 없으면 로그인이 안 됨.
3. **A360-Assistant-Ops** `ops-server/backend/.env`에 그 계정의
   `A360_BACKEND_ADMIN_EMAIL` / `A360_BACKEND_ADMIN_PASSWORD` 채우기(1번과 같은 계정).
   `A360_BACKEND_URL`도 실제 Backend 주소(로컬이면 `http://127.0.0.1:8000`)로 맞는지 확인.
4. Ops backend 재시작 후, "모니터링 로그" 화면에서 감사 로그·LLM 사용량 수집 버튼으로
   403 없이 건수가 뜨는지 확인.

## 왜 이 문서가 필요했나

Ops(Streamlit) 평가 페이지의 "버전 비교" 차트가 오히려 불편하다는 피드백에서 출발해,
실제 로그 데이터(`ops-server/backend/data/observability_rag_logs.jsonl`)를 열어봤더니
96건 중 95건이 하루(2026-07-10)에 몰린 단발성 스냅샷이었다. 자동/주기 수집이 아예
없고, 수동 "새로고침" 버튼 클릭 1~2회로 생긴 데이터라 EDA·ML·시계열 분석을 지금
해봐야 의미 있는 인사이트가 나올 수 없다. 그래서 코드를 먼저 짜지 않고, 뭘 왜
언제 할지부터 정리한다.

## Ops의 역할을 어떻게 정의할 것인가

Grafana/Kibana도 결국 "수집된 데이터를 시각화"하는 도구라, Ops(Streamlit)가 자체적으로
표·차트를 그리는 것과 역할이 겹친다. 검토한 세 가지:

**A. Ops가 수집+저장+시각화를 전부 담당** — 도구 하나로 충분하지만, 방금 "버전 비교"
차트를 걷어낸 이유가 정확히 이 방식의 한계를 보여준다. Streamlit은 드릴다운·알림·
다중 사용자 실시간 갱신이 안 돼서 로그 규모가 커지면 한계가 뚜렷하다.

**B. Ops는 수집/저장만, Grafana(Loki+Prometheus)나 Kibana(ELK)가 시각화 전담** —
역할은 명확히 나뉘지만, JSONL 파일 → Prometheus/Loki/Elasticsearch로 넘기는 파이프라인을
새로 깔아야 해서 지금 로그량(96건) 대비 과한 인프라 투자다.

**C. 역할 분리, 단 지금은 B의 인프라 없이 최소화한 형태로 (채택)** — Ops는 "운영자
조작판"에 집중한다: 평가 실행·RAG 적재 트리거 같은 액션 버튼, 그리고 차트 없이
표(테이블) 기반의 간단한 QA 조회만 담당. "진짜 시각화·추세 분석·알림"이 필요해지는
시점(로그량 증가로 파일 조회가 느려짐 / 여러 사람이 동시에 대시보드를 봐야 함 /
다른 서비스와 한 화면에서 상관관계를 봐야 함)이 오면 그때 Grafana를 **별도 도구로**
붙인다. 처음부터 두 개를 동시에 구축하지 않는다.

## Prometheus/Loki/Grafana나 ELK 도입은?

**지금은 시기상조.** 로그 총량 96건, 자동 수집도 없는 단발성 스냅샷 상태에서 운영
가능한 시계열 DB나 검색 클러스터를 굴릴 실익이 없다 — 인프라 운영 부담이 지금 얻을
수 있는 가치보다 크다. 이미 있는 경량 구조(JSONL + Streamlit)가 지금 규모에서는
충분하다.

**재검토 시점**: (a) 자동 수집 이후 로그량이 늘어 파일 조회/필터가 느려지거나, (b)
여러 사람이 동시에 실시간 대시보드·알림이 필요해지거나, (c) A360-Assistant-Backend/
Frontend 등 여러 서비스를 한 대시보드에서 같이 봐야 할 때 — 이때 Grafana(Loki/
Prometheus 조합, ELK보다 운영 부담이 가벼움)를 우선 검토.

## 수집 대상 — 세 트랙으로 쪼개짐

A360-Assistant-Backend `dev` 브랜치를 최신화하며 확인한 실제 코드 기준.

### 트랙 A — 감사 로그 + LLM 사용량 (설정만 하면 됨) → [RPA-109](https://metanetfinal.atlassian.net/browse/RPA-109)

`AuditLog`/`LlmUsage`는 메타데이터성이고, Ops의 `backend_client.py`/`collector.py`는
JWT 로그인·재시도까지 이미 완성돼 있다. `GET /api/admin/audit-logs`,
`GET /api/admin/llm-usage/stats`도 Backend에 이미 있다. 막힌 이유는 코드가 아니라
설정값 2개(Backend `.env`의 `ADMIN_EMAILS`, Ops `.env`의 admin 계정)뿐.

### 트랙 B — 이미 쌓이고 있는 롤업 데이터 (조회 창구만 필요) → [RPA-110](https://metanetfinal.atlassian.net/browse/RPA-110)

가장 큰 발견: 최근 머지된 RPA-103(request_metrics)/RPA-104(metrics_daily·usage_daily
일별 롤업, APScheduler로 이미 Backend 안에서 매시간 자동 실행 중)/RPA-105(turn_events,
에이전트 턴 노드 타임라인)로 **Backend가 이미 자동으로 관측 데이터를 수집·롤업하고
있다.** `MetricsDaily` 모델 docstring에 "Streamlit(별도 레포)이 raw 대신 이걸 읽는다"고
명시돼 있어, Ops가 이 데이터를 읽는 건 이미 의도된 설계다. 그런데 `admin.py`에 이
셋을 조회하는 엔드포인트가 아직 없다 — 데이터는 쌓이는데 꺼낼 창구만 없는 상태.
작은 엔드포인트 3개 + Ops 연동만 하면 됨.

### 트랙 C — 세션·문서·추천·채팅 전체 원문 (신규 개발, 민감) → [RPA-111](https://metanetfinal.atlassian.net/browse/RPA-111)

`Document.parsed_content`/`ChatMessage.content`/`RecommendationVersion.payload`는
메타데이터가 아니라 사용자 업로드 문서 원문 파싱 결과·대화 원문·생성된 워크플로우
전체다. 현재 Backend에 관리자가 남의 세션을 조회하는 admin/벌크 엔드포인트가 하나도
없어(전부 소유자 본인만 조회 가능) 트랙 A/B와 달리 **신규 개발**이 필요하다. 전부
EDA에 필요하다는 건 확인됐지만, 이 내부 도구로 사용자 원문 전체가 복제되는 것에
대한 접근권한·보관기간·마스킹 여부는 착수 전 팀 논의가 필요(RPA-111에 체크리스트로
명시).

## 진행 순서

1. A360-Assistant-Backend 브랜치 최신화 (완료 — 이 문서의 트랙 B 발견이 그 결과)
2. 트랙 A 설정 완료 → 감사 로그·LLM 사용량 자동 수집 시작
3. 트랙 B 개발 → 이미 쌓인 롤업 데이터 Ops에서 조회 가능하게
4. 실서비스 트래픽 관찰(양·속도) → 그 실측치로 Grafana 등 아키텍처 고도화 필요 여부
   재검토
5. 트랙 C 개발(팀 논의 후 접근권한 설계 확정되면)
6. 위 데이터가 실제로 쌓인 뒤 EDA 시작 — 에러율 추이, 엔드포인트별 지연시간 분포,
   트래픽 패턴, LLM 비용/토큰 추이 등 pandas 기반 기초 통계부터. ML/시계열 모델(이상
   탐지, 사용량 예측 등)은 EDA로 실제 패턴이 확인된 다음에만 착수 — 리포 전체에
   아직 ML/시계열 라이브러리 사용 사례가 없어 이번이 사실상 첫 도입이라 과설계를
   피한다.
7. EDA 결과는 원본 로그와 분리된 별도 위치에 저장/기록 — 재실행해도 원본 로그가
   안 섞이게.

## 관련 이슈

- [RPA-109](https://metanetfinal.atlassian.net/browse/RPA-109) — 감사 로그·LLM 사용량 자동 수집 활성화
- [RPA-110](https://metanetfinal.atlassian.net/browse/RPA-110) — metrics_daily/usage_daily/turn_events 조회 엔드포인트 + Ops 연동
- [RPA-111](https://metanetfinal.atlassian.net/browse/RPA-111) — 세션·문서·추천·채팅 전체 원문 데이터 수집 파이프라인 (보안 고려사항 포함)
