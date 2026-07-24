import os
import re
from pathlib import Path

# postgres/postgresql 스킴 뒤의 SQLAlchemy 드라이버 접미사(+psycopg, +psycopg2 등)를 벗긴다.
_PG_DRIVER_SUFFIX = re.compile(r"^(postgres(?:ql)?)\+\w+://")

# 프로젝트 루트의 .env를 있으면 로드 (python-dotenv 없거나 파일 없으면 조용히 통과)
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

DOCS_BASE_URL = os.getenv("AA_DOCS_BASE_URL", "https://docs.automationanywhere.com")

DATA_DIR = Path(os.getenv("INGEST_DATA_DIR", "data/ingest"))
DOCS_JSONL = DATA_DIR / "docs.jsonl"  # 기본 로케일(ko-KR) — 서비스에 실제 쓰이는 본진 콘텐츠


def docs_jsonl_for_locale(locale: str) -> Path:
    """로케일별 문서 크롤 결과 경로. ko-KR은 기존 DOCS_JSONL 그대로(하위호환), 그 외
    로케일(en-US 등)은 별 파일로 — 동시 크롤 시 같은 파일에 동시쓰기해서 깨지는 걸 방지하고,
    en-US는 action_name 매칭 보조용일 뿐 서비스 콘텐츠로 이중 적재하지 않을 것이므로 구분한다."""
    if locale == "ko-KR":
        return DOCS_JSONL
    return DATA_DIR / f"docs_{locale}.jsonl"
PACKAGES_JSON = DATA_DIR / "packages.json"
BOTS_JSONL = DATA_DIR / "bots.jsonl"
EXPORTS_DIR = DATA_DIR / "exports"
RAG_DOCUMENTS_JSONL = DATA_DIR / "rag_documents.jsonl"
EDA_REPORT_JSON = DATA_DIR / "eda_report.json"
# doc_action_tree 트리 해석 결과 요약(패키지별 리프/카테고리 수) — export-for-agent
# 실행마다 갱신되는 감사용 사이드카 파일. build가 읽지 않음, 사람이 검토하는 용도.
DOC_ACTION_TREE_REPORT_JSON = DATA_DIR / "doc_action_tree_report.json"
# 패키지 판별 + 메뉴 계층(루트/카테고리/리프, JAR 유무와 무관하게 전체) 확정된 구조를
# 그대로 남기는 산출물 — build-action-tree 산출, JAR/Agent 어느 쪽도 없이도 "이
# 패키지엔 이런 하위 구조가 있다"를 바로 확인 가능.
PACKAGE_ACTION_TREE_JSON = DATA_DIR / "package_action_tree.json"
# JAR이 없는 패키지들의 리프 문서(구조화 HTML 포함)를, "이 리프가 진짜 액션인지"를
# 판단할 향후 LLM 기반 파싱 Agent에게 그대로 넘기기 위한 산출물
# (export-for-agent 산출, app/rag/pipeline.py::cmd_export_for_agent 참고).
AGENT_HANDOFF_JSONL = DATA_DIR / "agent_handoff.jsonl"
# 리프=진짜 액션 여부를 필터링하지 않고 전부 액션 후보로 나열하는 단순 베이스라인
# (export-naive-leaf-actions 산출, app/rag/build/naive_leaf_actions.py 참고).
# 파라미터 스키마 없음 — action_schema로 쓰지 않음, merge.py가 조회하지 않음.
NAIVE_LEAF_ACTIONS_JSONL = DATA_DIR / "naive_leaf_actions.jsonl"

# 적재 파이프라인 각 단계 로그 (JSON Lines, 날짜별 파일) — observability.py가 씀
LOG_DIR = Path(os.getenv("RAG_LOG_DIR") or "app/rag/logs")
INGEST_JOB_STATE_JSON = LOG_DIR / "ingest_state.json"
INGEST_JOB_LOCK_DIR = LOG_DIR / "ingest.lock"
INGEST_RUN_LOG_DIR = LOG_DIR / "ingest_runs"

# 청킹: chunk_size 초과 문서만 분할한다. 기본값은 NongSabu DocumentChunker 프라이어(1200/200) —
# `pipeline.py eda`로 실제 문서 길이 분포를 확인한 뒤 필요시 .env에서 조정한다.
# 아래 CHUNK_PARAMS_BY_SOURCE_TYPE에 없는 소스 타입이 이 값을 쓴다(package_overview 등).
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))

# 소스 타입별 청킹 기본값 — 텍스트 성격이 달라 한 값으로 맞추면 한쪽이 손해다.
#   doc_page      1200 / 10%(120) — 크롤링한 산문. 문단 경계가 촘촘해 겹침이 적어도
#                 문맥이 이어지고, 겹침을 키우면 같은 문단이 여러 청크에 중복 색인돼
#                 검색 결과가 한 문서로 쏠린다.
#   action_schema 1500 / 20%(300) — "라벨: 값" 정형에 ko 본문까지 붙어 훨씬 길다.
#                 파라미터 목록이 경계에서 잘리면 뒤 청크가 어떤 액션의 무슨 필드인지
#                 잃으므로, 폭을 넓히고 겹침도 크게 잡아 경계 손실을 복구한다.
# 값은 (chunk_size, chunk_overlap). .env로 타입별 오버라이드 가능 — 스윕할 때 쓴다.
CHUNK_PARAMS_BY_SOURCE_TYPE = {
    "doc_page": (
        int(os.getenv("CHUNK_SIZE_DOC_PAGE", "1200")),
        int(os.getenv("CHUNK_OVERLAP_DOC_PAGE", "120")),
    ),
    "action_schema": (
        int(os.getenv("CHUNK_SIZE_ACTION_SCHEMA", "1500")),
        int(os.getenv("CHUNK_OVERLAP_ACTION_SCHEMA", "300")),
    ),
}

OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST") or "http://127.0.0.1:9200"
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "rag_documents")
OPENSEARCH_USERNAME = os.getenv("OPENSEARCH_USERNAME", "")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "")

# voyage(기본) 또는 openai. Anthropic은 임베딩 API가 없어 Voyage AI를 공식 권장함.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "voyage")
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "voyage-3.5" if EMBEDDING_PROVIDER == "voyage" else "text-embedding-3-small",
)
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024" if EMBEDDING_PROVIDER == "voyage" else "1536"))
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# OpenAI 클라이언트 자동 재시도 횟수. 429(TPM/RPM 한도)에 SDK가 지수 백오프 + Retry-After로
# 대기 후 재시도한다 — 동시 파싱이 순간 한도를 넘겨도 요청을 버리지 않고 흡수한다(기본 2는 부족).
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "8"))

# ── 문서 파싱 에이전트 (JAR 없는 패키지의 리프 문서 → 액션 스키마) ──
# 구조화 출력(JSON mode)을 지원하는 챗 모델. 백엔드 OPENAI_MODEL과 같은 기본값을 쓴다.
AGENT_PARSE_MODEL = os.getenv("AGENT_PARSE_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5.4-mini"
# 파싱 대상 리프 수 상한 (비용/시간 통제용). 0 이하면 무제한.
AGENT_PARSE_LIMIT = int(os.getenv("AGENT_PARSE_LIMIT", "0"))
# 리프를 몇 개씩 묶어 한 번의 LLM 호출로 파싱할지 (배치). 호출 수·반복 시스템프롬프트 토큰을 줄인다.
AGENT_PARSE_BATCH_SIZE = int(os.getenv("AGENT_PARSE_BATCH_SIZE", "6"))
# 배치 파싱을 동시에 몇 개까지 돌릴지 (LLM은 I/O 대기라 병렬로 벽시계 시간을 단축). 1이면 순차.
# gpt-5.4-mini 200K TPM 기준, 배치당 ~15~25K 토큰이라 3이면 순간 폭주가 한도 아래로 유지된다
# (초과분은 OPENAI_MAX_RETRIES 백오프가 흡수). 한도가 오르면 올려도 된다.
AGENT_PARSE_WORKERS = int(os.getenv("AGENT_PARSE_WORKERS", "3"))
# 관측 전용 DB(llm_usage 기록 대상). 미설정 시 앱/RAG DB로 폴백하지 않는다 — 관측 기록은
# best-effort로 건너뛴다(app/core/llm.py _observability_dsn). 백엔드 RPA-260과 동일 계약.
OBSERVABILITY_DATABASE_URL = os.getenv("OBSERVABILITY_DATABASE_URL", "").strip()


class RagDatabaseConfigurationError(RuntimeError):
    """RAG 전용 DB 설정이 없어 서비스 DB 격리를 보장할 수 없음."""


def database_dsn() -> str:
    """RAG 저장소(pgvector) 접속 문자열.

    RAG_DATABASE_URL만 사용한다. 미설정/빈값이면 기동을 거부해 RAG 코퍼스가 앱 DB(users/
    sessions)에 조용히 섞이는 구성을 막는다(백엔드 RPA-260과 동일 계약, RDS 3분리). 예전에는
    DATABASE_*로 폴백했으나, 그 폴백이 2026-07-18 로컬 ingest의 공유 DB 오염 같은 조용한
    사고의 뿌리였다. 배포는 docker-compose/Secret이 URL을 명시 주입하고, 로컬 러너
    (scripts/run_local*.py)는 명시적 로컬 DSN을 주입한다 — 폴백 없이도 전부 도는 구조.
    """
    url = (os.getenv("RAG_DATABASE_URL") or "").strip()
    if not url:
        raise RagDatabaseConfigurationError("RAG_DATABASE_URL is required")
    # RAG store는 raw psycopg라 libpq URL(postgresql://)만 받는다 — SQLAlchemy용
    # 'postgresql+psycopg://' 접두사를 그대로 넘기면 psycopg가 스킴을 못 읽는다.
    # 관측 URL 형식을 복붙해도 동작하도록 드라이버 접미사(+psycopg 등)를 방어적으로 벗긴다.
    # (libpq 키워드 포맷 'host=... '이 오면 매칭 안 돼 그대로 통과 — 로컬 러너 경로.)
    return _PG_DRIVER_SUFFIX.sub(r"\1://", url, count=1)
