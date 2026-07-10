# a360-assistant-backoffice

A360-Assistant 운영 도구. Streamlit(프론트) + FastAPI(백엔드), 각각 독립된
가상환경/requirements.txt로 따로 배포 가능하게 구성.

```
A360-Assistant-Ops/
  backend/
    app/main.py     # API 진입점
    app/rag/        # RAG 데이터 적재 (A360-Assistant-Backend와 같은 DB에 반영)
    app/eval/        # 워크플로우 평가 결과 로그/조회/비교
    .env            # DB/임베딩 설정 (git 미포함, 아래 참고)
  frontend/
    app.py          # Streamlit 화면
```

## 실행 방법

**백엔드** (먼저 실행)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows / source .venv/bin/activate (macOS/Linux)
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8100
```

`.env`는 `A360-Assistant-Backend`와 동일한 DB(Postgres/OpenSearch)를 가리키도록
설정한다 (`DATABASE_*`, `OPENSEARCH_*`, `EMBEDDING_*`, `VOYAGE_API_KEY`/`OPENAI_API_KEY`).
8000번 포트는 메인 백엔드가 쓰고 있어서 여기는 8100번을 쓴다.

**프론트엔드**

```bash
cd frontend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## 무엇을 할 수 있는지

- **백엔드 상태 확인**: `GET /health` 호출 결과 표시.
- **RAG 데이터 적재**: 옵션 1(JAR 있는 패키지만)/옵션 2(+ JAR 없는 패키지 리프 참고용)
  버튼 → `POST /rag/ingest?option=` → 백그라운드로 crawl→build→ingest 실행,
  "진행 상태 확인" 버튼으로 완료 여부 확인. 여기서 적재한 데이터는 메인 백엔드
  실서비스에 그대로 반영된다.
- **평가 결과 기록·조회·비교**: "평가 결과" 섹션에서 결과 기록(폼) → 조회(필터 테이블)
  → 여러 건 선택해 비교. 채점 방법(수작업/자동화 채점기 등)은 안 가림 — `source`
  필드로만 구분.

## 더 자세히 알아보려면

- RAG 적재/평가 인프라를 왜 이렇게 설계했는지, 평가를 실제로 돌리려면 어떤
  입력(scoring.yaml, actual.json 등)을 준비해야 하는지는
  `A360-Session-Notes/`(리포 상위 폴더) 참고.
- CLI로 직접 실행: `cd backend && python -m app.rag.pipeline --help`
