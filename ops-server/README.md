# ops-server (Ops 운영 도구 서버)

운영 모니터링 + 평가(eval) 화면을 제공하고, rag-server에 적재 파이프라인 실행을
요청하는 서버 — 평가는 모니터링(로그 관찰)이 아니라 능동적 QA 작업이라 이름을
"모니터링 서버"가 아니라 "ops-server"로 뒀다. **화면 있음.** 내부적으로 **2개
프로세스**로 구성된다:

- **백엔드 (FastAPI, :8100)** — `/observability/*`(감사·LLM·RAG 로그 수집/조회,
  metrics-daily/usage-daily/turn-events 롤업 조회), `/assurance/*`(Backend의 AI 출력
  검증 판정 기록을 저장 없이 읽기 전용 중계), `/eval/*`(데이터셋·결과·
  pm4py/WorFBench 변환·A/B·xlsx, RAGAS 기반 RAG 검색 품질 평가), 그리고
  `app/scheduler`(rag-server로 주기 트리거 — 현재 stub).
- **프론트 (Streamlit, :8501)** — 홈(대시보드) / RAG 데이터 적재 / 평가(결과 조회·비교,
  실행, 데이터셋 관리, RAG 품질(RAGAS) 4탭으로 통합된 한 페이지) / 모니터링 로그 /
  AI 출력 검증 기록.

```
ops-server/
  backend/
    app/
      main.py            # FastAPI: /observability/*, /assurance/*, /eval/*
      observability/     # A360-Assistant-Backend 로그 수집/조회
      eval/              # 평가 데이터셋·결과·변환·비교
      scheduler/         # rag-server 주기 트리거 (stub — 다음 작업)
    tests/               # eval 단위 테스트
    .env.example         # → backend/.env로 복사
  frontend/
    app.py               # 진입점 (사이드바 + 페이지 네비게이션)
    components/          # 사이드바·공통 스타일
    views/               # 화면별 로직
    config.py            # OPS_BACKEND_URL(:8100) / RAG_SERVER_URL(:8200)
  requirements.txt       # 백엔드+프론트 통합 1개
  start.ps1              # 백엔드+프론트 동시 기동
```

## 실행

```powershell
cd ops-server
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy backend\.env.example backend\.env    # 값 채우기
.\start.ps1                               # 백엔드(:8100) + 프론트(:8501) 동시 기동
```

개별 실행이 필요하면:

```powershell
# 백엔드
cd ops-server\backend ; uvicorn app.main:app --reload --port 8100
# 프론트 (다른 터미널)
cd ops-server\frontend ; streamlit run app.py --server.port 8501
```

## AI 출력 검증 판정 기록

`AI 출력 검증 기록` 화면은 Backend의 Output/Change 하네스가 남긴 판정 이력을 운영자가
조회하는 화면이다. 여기서 `관찰됨`은 검증기가 후보를 관찰했다는 뜻이며, 사람의 승인·배포
허가·업무 결과의 정당성을 의미하지 않는다.

- Ops Backend는 `GET /assurance/records`와 `GET /assurance/records/{receipt_digest}`만
  제공한다. 생성·수정·삭제 API는 없다.
- 판정 기록의 원본은 A360-Assistant-Backend에 있으며, Ops 서버는 로컬 JSONL이나 별도 DB에
  복제하지 않는다.
- Change 하네스의 최초 판정과 사람 승인 후 후속 판정은 서로 다른 append-only 기록이다. 화면은
  최신 기록을 먼저 보여주며, 상세에서 해당 판정 시점의 승인자·승인 시각·대상 커밋을 확인할 수 있다.
- Backend 운영 API 인증은 `A360_BACKEND_OPS_API_KEY`를 우선 사용하고, 관리자 JWT 로그인은
  하위 호환 경로로만 사용한다.

## rag-server 연동

- **RAG 데이터 적재** 화면의 버튼은 `RAG_SERVER_URL`(:8200)의 `/rag/ingest`를 직접 호출한다.
- 주기 자동 적재(스케줄러)는 `backend/app/scheduler`에 자리만 잡혀 있고 실제 주기 실행은
  다음 작업이다. 지금은 위 버튼으로 수동 트리거한다.

## ⚠️ eval WorFBench 변환의 크로스서버 데이터 의존

`/eval/convert/worfbench`(및 `app/eval/workflow/build_catalog.py`)는 액션 파라미터
설명을 위해 `backend/data/ingest/packages.json`을 읽는다. 이 파일은 **rag-server**의
적재 산출물이다. WorFBench 변환을 쓰려면 rag-server의 `data/ingest/packages.json`을
`ops-server/backend/data/ingest/`로 복사(또는 공유 볼륨)해 둔다. 파일이 없으면
해당 엔드포인트가 "먼저 RAG 적재를 돌리라"는 에러를 명확히 낸다 — 나머지 eval 기능
(데이터셋·결과·A/B·xlsx)은 이 파일과 무관하게 동작한다.
