# ops-server (Ops 운영 도구 서버)

운영 모니터링 + 평가(eval) 화면을 제공하고, rag-server에 적재 파이프라인 실행을
요청하는 서버 — 평가는 모니터링(로그 관찰)이 아니라 능동적 QA 작업이라 이름을
"모니터링 서버"가 아니라 "ops-server"로 뒀다. **화면 있음.** 내부적으로 **2개
프로세스**로 구성된다:

- **백엔드 (FastAPI, :8100)** — `/observability/*`(감사·LLM·RAG 로그 수집/조회),
  `/eval/*`(데이터셋·결과·pm4py/WorFBench 변환·A/B·xlsx), 그리고 `app/scheduler`
  (rag-server로 주기 트리거 — 현재 stub).
- **프론트 (Streamlit, :8501)** — 홈(대시보드) / RAG 데이터 적재 / 평가(결과 조회·비교,
  실행, 데이터셋 관리 3탭으로 통합된 한 페이지) / 모니터링 로그.

```
ops-server/
  backend/
    app/
      main.py            # FastAPI: /observability/*, /eval/*
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
