# rag-server (RAG 적재 서버)

RAG 수집·적재 파이프라인을 API로 노출하는 서버. **화면 없음** — 모니터링 서버
(또는 사람이 프론트 버튼으로)가 `POST /rag/ingest`를 호출하면 크롤→빌드→
pgvector/OpenSearch 적재를 백그라운드로 실행한다.

적재 대상 DB(Postgres/pgvector·OpenSearch)는 `A360-Assistant-Backend`와 **동일
인스턴스**를 가리킨다 — 여기서 적재한 게 실서비스 검색에 그대로 반영된다.

```
rag-server/
  app/
    main.py     # FastAPI 진입점: /health, /rag/ingest, /rag/ingest/status
    rag/        # 수집·적재 파이프라인 (crawl→build→ingest, CLI 포함)
  requirements.txt
  .env.example  # → .env로 복사해 DB/OpenSearch/임베딩/소스 키 설정
```

## 실행

```bash
cd rag-server
python -m venv .venv
.venv\Scripts\activate        # Windows / source .venv/bin/activate (macOS/Linux)
pip install -r requirements.txt
cp .env.example .env          # 값 채우기
uvicorn app.main:app --reload --port 8200
```

포트는 8200을 쓴다 (8000=메인 백엔드, 8100=모니터링 백엔드와 충돌 회피).

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 상태 확인 |
| POST | `/rag/ingest?option=1\|2` | 적재 파이프라인 백그라운드 실행 시작 (옵션 1: JAR 있는 패키지만 / 옵션 2: + JAR 없는 리프 참고용) |
| GET | `/rag/ingest/status` | 마지막/현재 실행 상태·로그 |

## CLI (파이프라인 직접 실행)

```bash
cd rag-server
python -m app.rag.pipeline --help
```

`bots` / `export-packages`(Control Room 계정 필요), `parse-jars`(zip 경로 필요)는
자동 실행(`/rag/ingest`)에 들어가지 않는다 — 사람이 한 번 따로 실행해 `data/ingest/`에
`packages.json`·`bots.jsonl`을 준비해 둔 뒤 적재를 돌린다.
