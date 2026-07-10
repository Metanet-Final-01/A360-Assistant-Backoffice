# a360-assistant-backoffice

A360-Assistant 운영 도구. Streamlit(프론트) + FastAPI(백엔드), 각각 독립된
가상환경/requirements.txt로 따로 배포 가능하게 구성.

```
A360-Assistant-Ops/
  backend/
    app/main.py     # API 진입점
    app/rag/        # RAG 데이터 적재 (A360-Assistant-Backend와 같은 DB에 반영)
    app/eval/        # 워크플로우 평가 결과 로그/조회/비교
      format_schemas.py     # pm4py/worfbench 입력·출력 형식 엄격 검증 스키마
      format_guide.py       # GET /eval/format-guide가 돌려줄 안내 데이터
      format_examples/      # pm4py/worfbench가 요구하는 형식의 예시 데이터셋(커밋됨, README 참고)
      scripts/import_sandbox_ab.py  # a360-eval-sandbox의 A/B 결과를 이 앱 로그로 가져오는 CLI
      workflow/recommendation.py    # agent 추천안(Recommendation) 스키마
      workflow/adapters.py          # Recommendation → pm4py/worfbench 입력 변환기
    .env            # DB/임베딩 설정 (git 미포함, 아래 참고)
  frontend/
    app.py               # 진입점 — 사이드바 + 페이지 네비게이션만
    components/          # 사이드바, 공통 스타일(page_header/badge/section_header 등)
    views/               # 화면별 로직 (홈 / RAG 적재 / 평가 결과)
    .streamlit/config.toml  # 브랜드 컬러(네이비/틸) 테마 — A360-Assistant-Frontend와 톤 통일
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
- **평가 결과 기록·조회·비교**: "평가 결과" 섹션에서 결과 기록(폼) → 조회(카드형 목록,
  펼치면 표로도 확인 가능) → 2건을 고르면 지표별 비교 차트(막대 그래프 + A/B 델타
  색상 표시, `AB_comparison_report.xlsx`가 하던 것의 웹 버전)가 그려진다. 채점 방법
  (수작업/자동화 채점기 등)은 안 가림 — `source` 필드로만 구분하되, `source`가
  `pm4py`/`worfbench`처럼 이미 알려진 채점 엔진이면 `raw`를 그 엔진의 실제 출력
  형식으로 엄격 검증한다(`backend/app/eval/format_schemas.py`).
- **채점 포맷 안내**: "평가 결과" 페이지 위쪽에서 pm4py/WorFBench가 어떤 입력·출력
  형식을 요구하는지, 실제 예시 데이터셋과 함께 바로 확인할 수 있다
  (`GET /eval/format-guide`). 예시 원본은 `backend/app/eval/format_examples/`에
  있고 리포에 커밋되어 있다 — 전체 골드셋(a360-eval-sandbox)이 아니라 "이런 형식으로
  준비하면 채점된다"를 보여주는 예시 데이터셋이라는 점을 `format_examples/README.md`에
  명시해 뒀다.
- **agent 워크플로우 → 채점 입력 변환**: `POST /eval/convert/pm4py`,
  `POST /eval/convert/worfbench`에 agent가 만든 추천안(`Recommendation`, 트리
  구조)을 보내면 pm4py/WorFBench가 요구하는 입력 형식으로 변환해 돌려준다
  (`backend/app/eval/workflow/adapters.py`). WorFBench 변환은 액션 설명·파라미터가
  필요해서 RAG 적재 산출물(`backend/data/ingest/packages.json`)이 있어야 한다 —
  없으면 먼저 RAG 적재를 한 번 돌리라는 에러를 명확히 낸다.
- **eval-sandbox A/B 결과 가져오기**: `a360-eval-sandbox`(별도 리포)에서 pm4py/
  WorFBench로 미리 돌려둔 A/B 비교 결과를 이 앱의 평가 로그로 가져와 조회·비교
  화면에서 볼 수 있다. `cd backend && python -m app.eval.scripts.import_sandbox_ab
  <a360-eval-sandbox의 eval_runs 폴더 경로> [--dry-run]`. 가져온 데이터는
  `backend/data/eval_runs.jsonl`(로컬 전용, git 미포함)에 쌓인다 — sandbox 원본이
  이 리포에 커밋되는 게 아니라 조회용 파생 데이터만 로컬에 남는다.

## 더 자세히 알아보려면

- RAG 적재/평가 인프라를 왜 이렇게 설계했는지, 평가를 실제로 돌리려면 어떤
  입력(scoring.yaml, actual.json 등)을 준비해야 하는지는
  `A360-Session-Notes/`(리포 상위 폴더) 참고.
- CLI로 직접 실행: `cd backend && python -m app.rag.pipeline --help`
- 예시 데이터셋 자체 검증: `cd backend && python -m app.eval.format_examples.validate_examples`
