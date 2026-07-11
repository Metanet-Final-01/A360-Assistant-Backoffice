"""주기적으로 rag-server에 적재 파이프라인 실행을 요청하는 스케줄러 — 자리표시(stub).

현재는 뼈대만 있고 실제 주기 실행은 아직 붙이지 않았다(다음 작업). 지금은 모니터링
프론트의 "RAG 데이터 적재" 버튼이 사용자가 직접 rag-server로 트리거한다.

다음 작업에서 실제로 붙일 때:
- APScheduler(BackgroundScheduler) 등으로 SCHEDULE_INTERVAL_MINUTES마다 trigger_rag_ingest 호출.
- app/main.py의 startup 이벤트에서 start()를 부르거나, 별도 프로세스로 띄운다.
- 적재는 몇 분~몇십 분 걸리므로, 겹치기 방지(직전 실행 중이면 skip)는 rag-server의
  /rag/ingest가 이미 409로 막아준다.
"""

import os

import httpx

# rag-server의 주소 — 기본은 로컬 개발 포트(:8200). 배포 시 .env(RAG_SERVER_URL)로 덮어쓴다.
RAG_SERVER_URL = (os.getenv("RAG_SERVER_URL") or "http://localhost:8200").rstrip("/")

# 몇 분마다 적재를 돌릴지 (기본 24시간). 실제 스케줄러를 붙일 때 사용.
SCHEDULE_INTERVAL_MINUTES = int(os.getenv("RAG_INGEST_INTERVAL_MINUTES", "1440"))

# 어떤 옵션으로 돌릴지 (1: JAR 있는 패키지만, 2: + JAR 없는 리프 참고용).
INGEST_OPTION = int(os.getenv("RAG_INGEST_OPTION", "1"))


def trigger_rag_ingest(option: int = INGEST_OPTION) -> dict:
    """rag-server에 적재 파이프라인 실행을 요청한다(POST /rag/ingest).

    지금도 프로그램에서 직접 호출하면 동작한다 — 다만 아직 이 함수를 '주기적으로'
    불러주는 스케줄러 본체가 없다(다음 작업).
    """
    resp = httpx.post(f"{RAG_SERVER_URL}/rag/ingest", params={"option": option}, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def start() -> None:
    """주기 스케줄러 시작 자리표시. 다음 작업에서 APScheduler 등으로 구현한다."""
    raise NotImplementedError(
        "주기 스케줄러는 아직 구현 전입니다 — 지금은 프론트의 'RAG 데이터 적재' 버튼으로 "
        "수동 트리거하세요. (다음 작업: APScheduler로 trigger_rag_ingest를 주기 실행)"
    )
