# a360-assistant-backoffice

A360-Assistant 운영 도구. **2개 서버**로 구성되며, 각 서버는 독립된 가상환경/
requirements.txt로 따로 배포한다.

```
a360-assistant-backoffice/
  rag-server/          # RAG 적재 서버 (화면 없음, FastAPI :8200)
                       #   모니터링 서버의 요청(POST /rag/ingest)을 받아 수집·적재 파이프라인 실행
  monitoring-server/   # 모니터링 서버 (화면 있음)
    backend/           #   FastAPI :8100 — observability(로그 수집/조회) + eval + 스케줄러(stub)
    frontend/          #   Streamlit :8501 — 모니터링·평가 화면
```

## 두 서버의 역할

| 서버 | 화면 | 역할 |
|---|---|---|
| **rag-server** | ✕ | RAG 수집·적재 파이프라인을 API로 노출. 모니터링 서버(또는 사람이 프론트 버튼으로)가 `POST /rag/ingest`를 호출하면 크롤→빌드→pgvector/OpenSearch 적재를 백그라운드로 실행. 적재 DB는 `A360-Assistant-Backend`와 동일 인스턴스 → 실서비스에 그대로 반영. |
| **monitoring-server** | ○ | ①모니터링: `A360-Assistant-Backend`의 감사·LLM·RAG 요청 로그를 수집·조회. ②평가(eval): 데이터셋·결과·pm4py/WorFBench 변환·A/B 비교·xlsx. ③(예정) 일정 주기마다 rag-server로 적재 요청. |

**흐름**: `monitoring-server`(스케줄러/버튼) ──`POST /rag/ingest`──▶ `rag-server`(파이프라인 실행) ──적재──▶ 공유 DB(pgvector/OpenSearch) ◀──검색── `A360-Assistant-Backend`

## 실행

각 서버 폴더의 README를 참고한다.

- **rag-server**: [rag-server/README.md](rag-server/README.md)
  ```bash
  cd rag-server && pip install -r requirements.txt && uvicorn app.main:app --port 8200
  ```
- **monitoring-server**: [monitoring-server/README.md](monitoring-server/README.md)
  ```powershell
  cd monitoring-server ; pip install -r requirements.txt ; .\start.ps1   # 백엔드(:8100)+프론트(:8501)
  ```

포트: `8000`=메인 백엔드(A360-Assistant-Backend), `8100`=모니터링 백엔드,
`8200`=RAG 적재 서버, `8501`=모니터링 프론트(Streamlit).

## 서버 간 데이터 참고사항

`monitoring-server`의 eval WorFBench 변환은 rag-server 적재 산출물
(`data/ingest/packages.json`)이 필요하다. 자세한 내용은
[monitoring-server/README.md](monitoring-server/README.md) 참고.
