# 모니터링 백엔드 (observability 수집/조회, eval) — FastAPI :8100
# localhost가 아니라 127.0.0.1을 쓴다 — 이 머신에서 "localhost" DNS/연결이 비정상적으로
# 느려서(실측 ~2s vs 127.0.0.1 ~ms 단위) 요청마다 지연이 누적됐다.
# docs/local/PERF_OPS_EVAL_PAGE.md 참고.
OPS_BACKEND_URL = "http://127.0.0.1:8100"

# RAG 적재 서버 (rag-server) — FastAPI :8200. 'RAG 데이터 적재'는 이쪽으로 트리거한다.
RAG_SERVER_URL = "http://127.0.0.1:8200"
