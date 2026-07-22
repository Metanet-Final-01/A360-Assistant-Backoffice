"""옵션 4: khub 정본 v2 카탈로그 파이프라인 (웹 크롤 전용 — JAR/GitHub 미사용).

registry(등기) → build-v2(규칙 + LLM 발견: 액션 판별·표 추출·파라미터 보강) →
validate(게이트) → ingest 를 한 번에 순서대로 실행한다. run_steps가 한 단계라도 실패하면
그 자리에서 멈추므로, **validate가 실패하면 ingest는 돌지 않는다**(반쯤 된 데이터 적재 방지).

크롤: `crawl-khub`이 khub 주 맵('Automation 360') 전수 + 보조 맵을 순회해 DUMP를 갱신한다
(toc_*.json + bodies_*.jsonl[html]). 이미 html이 있는 content_id는 건너뛴다(이어받기) —
전수 재크롤이라 15~25분 걸리지만 중단에 강하다. (기존 CLI `crawl`은 v1 docs.jsonl 전용이라
여기선 안 쓴다.)

설정(환경변수):
  KHUB_DUMP_DIR   v2 덤프 위치. 없으면 <repo>/data/ingest/khub-dump.
  AGENT_PARSE_MODEL   build-v2의 LLM 모델. 풀 적재는 gpt-5-mini로 두는 걸 전제(서버 .env).
  INGEST_DATA_DIR   산출물(rag_documents.jsonl 등)·캐시 위치. 서브프로세스에 그대로 상속된다.

`--clean`이면 마지막 ingest가 완전 재적재, 인자 없으면 upsert만(옵션 1~3과 동일 토글).
rag-server main.py가 `/rag/ingest?option=4&clean=…`를 받아 이 스크립트를 서브프로세스로 띄운다.
"""

import os
from pathlib import Path

from _run_steps import run_steps, wants_clean

_REPO_ROOT = Path(__file__).resolve().parents[3]
DUMP = os.getenv("KHUB_DUMP_DIR") or str(_REPO_ROOT / "data" / "ingest" / "khub-dump")

if __name__ == "__main__":
    ingest_step = ["ingest", "--clean"] if wants_clean() else ["ingest"]
    run_steps([
        ["crawl-khub", "--dump-dir", DUMP],  # khub 전수 크롤(주 맵 + 보조 맵) — DUMP 갱신
        ["registry", "--dump-dir", DUMP],
        ["build-v2", "--dump-dir", DUMP, "--llm-tables", "--judge", "--enrich"],
        ["validate", "--dump-dir", DUMP],
        ingest_step,
    ])
