-- 요청 단위 통합 뷰 2개 + 병목 자동 분류 쿼리.
-- 지금은 로컬 샌드박스 DB(a360_observability_test, 포트 5433)에만 만들어져 있다.
-- 원격 관측 DB(Neon)에 실제로 만들지는 별도 결정 필요 — 운영 DB 스키마 객체라
-- 확인 없이 만들지 않는다. docs/local/LOG_SCHEMA_FIXES_2026-07-19.md 참고.

-- 요청 하나당 한 행: HTTP/RAG/LLM/턴 정보를 다 모은 요약.
CREATE OR REPLACE VIEW request_trace_summary AS
WITH llm_agg AS (
    SELECT request_id, count(*) AS llm_calls,
           sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
           sum(cost_usd) AS cost_usd, sum(latency_ms) AS llm_latency_ms
    FROM llm_usage WHERE request_id IS NOT NULL GROUP BY request_id
),
rag_agg AS (
    SELECT request_id, count(*) AS rag_events,
           sum(duration_ms) AS rag_latency_ms,
           sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS rag_errors
    FROM rag_events WHERE request_id IS NOT NULL GROUP BY request_id
),
turn_agg AS (
    SELECT request_id,
           sum(elapsed_ms) FILTER (WHERE kind = 'stage') AS turn_elapsed_ms,
           count(*) FILTER (WHERE stage = 'verifying') AS verify_rounds,
           sum(elapsed_ms) FILTER (WHERE stage = 'verifying') AS verify_ms,
           bool_or(kind = 'done') AS turn_done,
           bool_or(kind = 'error') AS turn_error
    FROM turn_events WHERE request_id IS NOT NULL GROUP BY request_id
)
SELECT
    request_metrics.request_id, request_metrics.path, request_metrics.status_code,
    request_metrics.latency_ms AS http_latency_ms, request_metrics.created_at,
    COALESCE(llm_agg.llm_calls, 0) AS llm_calls,
    COALESCE(llm_agg.input_tokens, 0) AS input_tokens,
    COALESCE(llm_agg.output_tokens, 0) AS output_tokens,
    COALESCE(llm_agg.cost_usd, 0) AS cost_usd,
    llm_agg.llm_latency_ms,
    COALESCE(rag_agg.rag_events, 0) AS rag_events,
    rag_agg.rag_latency_ms,
    COALESCE(rag_agg.rag_errors, 0) AS rag_errors,
    turn_agg.turn_elapsed_ms,
    COALESCE(turn_agg.verify_rounds, 0) AS verify_rounds,
    turn_agg.verify_ms,
    turn_agg.turn_done,
    turn_agg.turn_error
FROM request_metrics
LEFT JOIN llm_agg ON llm_agg.request_id = request_metrics.request_id
LEFT JOIN rag_agg ON rag_agg.request_id = request_metrics.request_id
LEFT JOIN turn_agg ON turn_agg.request_id = request_metrics.request_id;


-- 요청 하나 안의 이벤트들(rag_events/llm_usage/turn_events)을 시간순으로 합친 타임라인.
CREATE OR REPLACE VIEW request_timeline AS
SELECT request_id, 'rag' AS source, event AS stage_or_event, created_at,
       duration_ms, status, detail
FROM rag_events WHERE request_id IS NOT NULL
UNION ALL
SELECT request_id, 'llm' AS source, component || '/' || purpose AS stage_or_event, created_at,
       latency_ms AS duration_ms, 'ok' AS status, model AS detail
FROM llm_usage WHERE request_id IS NOT NULL
UNION ALL
SELECT request_id, 'turn' AS source, COALESCE(stage, kind) AS stage_or_event, created_at,
       elapsed_ms AS duration_ms, kind AS status, message AS detail
FROM turn_events WHERE request_id IS NOT NULL;


-- 턴이 있는 요청을 병목 원인으로 자동 분류(GPT 제안 3단계). 임계값(0.4/0.6)은
-- 첫 시도값 — 실제로 걸러지는 분포 보고 나중에 조정 필요.
-- SELECT
--   CASE
--     WHEN turn_elapsed_ms IS NULL THEN 'UNINSTRUMENTED'
--     WHEN verify_ms::float / NULLIF(turn_elapsed_ms, 0) >= 0.4 THEN 'VERIFY_BOUND'
--     WHEN COALESCE(llm_latency_ms, 0)::float / NULLIF(turn_elapsed_ms, 0) >= 0.6 THEN 'LLM_BOUND'
--     WHEN COALESCE(rag_latency_ms, 0)::float / NULLIF(turn_elapsed_ms, 0) >= 0.4 THEN 'RAG_BOUND'
--     ELSE 'MIXED'
--   END AS bottleneck_class,
--   count(*), round(avg(turn_elapsed_ms)::numeric / 1000, 1) AS avg_turn_seconds
-- FROM request_trace_summary
-- WHERE turn_elapsed_ms IS NOT NULL
-- GROUP BY 1 ORDER BY 2 DESC;
