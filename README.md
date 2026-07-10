# a360-assistant-backoffice

프론트(Streamlit)와 백엔드(FastAPI)를 분리한 최소 튜토리얼 구조. 각각 따로 배포할 예정이라
폴더도 완전히 독립적(가상환경/requirements.txt 각자 보유)으로 구성했다.

```
A360-Assistant-Ops/
  backend/            # FastAPI
    app/main.py
    app/rag/          # A360-Assistant-Backend의 RAG "적재" 파이프라인 이식본
    requirements.txt
    .env              # DB/임베딩 설정 (백엔드와 동일한 DB를 가리킴, git 미포함)
  frontend/           # Streamlit
    app.py
    requirements.txt
```

## 실행 방법

### 백엔드

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8100
```

> 8000번 포트는 A360-Assistant-Backend(메인 백엔드)가 이미 쓰고 있어서 8100번을 쓴다.

### 프론트엔드

```bash
cd frontend
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
streamlit run app.py
```

백엔드를 먼저 켠 뒤 프론트엔드에서 "백엔드 상태 확인" 버튼을 누르면 `GET /health` 호출 결과가 표시된다.

## RAG 데이터 적재

`app/rag/`는 A360-Assistant-Backend의 RAG 수집 파이프라인 중 **적재(crawl→build→ingest) 부분만**
그대로 옮겨온 것이다. 검색/서빙(hybrid_search, rerank)은 옮기지 않았다 — 그건 A360-Assistant-Backend가
실시간 에이전트 추천에 계속 쓰는 코드라 원본에 그대로 남아있다. `.env`가 백엔드와 같은
로컬 Postgres(pgvector)/OpenSearch를 가리키므로, 여기서 적재한 데이터는 실제 서비스에
그대로 반영된다.

Streamlit에서 "옵션 1"/"옵션 2" 버튼을 누르면 백엔드가 `POST /rag/ingest?option=1|2`를 받아
백그라운드로 파이프라인을 실행한다(`GET /rag/ingest/status`로 진행 상태 확인). CLI로 직접
실행할 수도 있다:

```bash
cd backend
python -m app.rag.pipeline --help
```

