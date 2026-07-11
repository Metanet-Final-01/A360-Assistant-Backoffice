# a360-assistant-backoffice

A360-Assistant 운영 도구. Streamlit(프론트) + FastAPI(백엔드), 각각 독립된
가상환경/requirements.txt로 따로 배포 가능하게 구성.

```
A360-Assistant-Ops/
  backend/
    app/main.py     # API 진입점
    app/rag/        # RAG 데이터 적재 (A360-Assistant-Backend와 같은 DB에 반영)
    app/eval/        # 평가 데이터셋·결과 로그·동일 케이스 A/B 비교
      format_schemas.py     # pm4py/worfbench 입력·출력 형식 엄격 검증 스키마
      format_guide.py       # GET /eval/format-guide가 돌려줄 안내 데이터
      format_examples/      # pm4py/worfbench가 요구하는 형식의 예시 데이터셋(커밋됨, README 참고)
      scripts/import_sandbox_ab.py  # a360-eval-sandbox의 A/B 결과를 이 앱 로그로 가져오는 CLI
      workflow/recommendation.py    # agent 추천안(Recommendation) 스키마
      workflow/adapters.py          # Recommendation → pm4py/worfbench 입력 변환기
    app/observability/  # A360-Assistant-Backend 감사 로그·LLM 사용량·RAG 로그 수집/조회
    .env            # DB/임베딩 설정 (git 미포함, 아래 참고)
  frontend/
    app.py               # 진입점 — 사이드바 + 페이지 네비게이션만
    components/          # 사이드바, 공통 스타일(page_header/badge/section_header 등)
    views/               # 화면별 로직 (홈 / RAG 적재 / 평가 준비 / 평가 결과 / 모니터링 로그)
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

모니터링 로그 수집(아래 "무엇을 할 수 있는지" 참고)에는 추가로 다음 키가 필요하다:

| 변수 | 설명 |
|---|---|
| `A360_BACKEND_URL` | 메인 백엔드 주소, 기본 `http://localhost:8000` |
| `A360_BACKEND_ADMIN_EMAIL` / `A360_BACKEND_ADMIN_PASSWORD` | 감사 로그·LLM 사용량 조회용 관리자 계정 — 메인 백엔드의 `ADMIN_EMAILS`에 등록돼 있어야 함 |

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
- **평가 준비**: 데이터셋을 `dataset_id + version + case_id 목록`으로 등록하고 pm4py/
  WorFBench 입력·출력 예시를 확인한다. 데이터셋 정의는 로컬 전용
  `backend/data/evaluation_datasets.json`에 저장된다.
- **평가 결과 조회·비교**: 외부 채점 결과를 `POST /eval/runs`로 기록하면 페이지 진입 시
  로그 목록을 보여준다. pm4py/WorFBench `raw`는 엄격 검증하며, 알려진 원본 지표는
  비교용 `metrics`와 대표 `score`로 자동 정규화한다. 결과에는 `evaluation_id`,
  `dataset_id/version`, `commit_sha`, 실행 `config`를 함께 기록할 수 있다.
- **개선사항 비교(버전 A vs 버전 B)**: 지표마다 A와 B 양쪽에 값이 있는 동일
  `case_id`만 짝지어 평균과 변화율을 계산한다. 화면에 지표별 실제 paired-case 수를
  표시해 표본 범위를 확인할 수 있다.
- **Excel로 내보내기**: 개선사항 비교에서 두 버전을 고르면 `AB_comparison_report.xlsx`
  (a360-eval-sandbox)와 같은 스타일(Overview 집계표 + Per-Case 비교, 델타 색상)의
  엑셀 파일을 `GET /eval/export/comparison-xlsx?label_a=&label_b=`로 내려받을 수
  있다(`backend/app/eval/xlsx_report.py`).
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
- **모니터링 로그 수집·조회**: 현재 화면은 메인 백엔드의 RAG 요청 로그
  (`GET /api/rag/logs/recent`)를 가져와 경로·상태·평균 응답시간을 조회한다. 감사 로그와
  LLM 사용량 수집 API는 백엔드에 준비되어 있으나 관리자 계정 연동 UI는 아직 제공하지 않는다.
  요청 메타데이터만 다루며 agent가 만든 실제 워크플로우 내용은 포함하지 않는다 —
  워크플로우 생성 호출(`/turn` 경로)은 배지로 표시만 해 둔다. 데이터는
  `backend/data/observability_*.jsonl`(로컬 전용, git 미포함)에 쌓인다.

## CLI

- RAG 파이프라인: `cd backend && python -m app.rag.pipeline --help`
- 예시 데이터셋 자체 검증: `cd backend && python -m app.eval.format_examples.validate_examples`
