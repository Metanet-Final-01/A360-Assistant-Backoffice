"""k6 요약 결과 파싱 — 직접 실행하지 않는다.

처음엔 Ops가 subprocess로 k6를 대신 실행해주는 방향으로 만들었는데, 터미널에서
`k6 run`을 직접 치는 것보다 Ops UI 폼을 거치는 게 오히려 더 번거롭다는 판단으로
방향을 바꿨다 — k6는 로컬에서 그대로 CLI로 돌리고, scripts/loadtest.js의
handleSummary()가 끝난 뒤 결과를 여기(POST /loadtest/upload)로 자동 전송한다.
이 모듈은 그 결과(k6 --summary-export와 동일한 JSON 구조)를 저장용 레코드로
변환하는 순수 파싱 로직만 남는다.
"""


def _metric_values(metrics: dict, name: str) -> dict:
    """k6가 요약을 두 가지 다른 모양으로 내보낸다는 걸 실측으로 확인했다:
    --summary-export 파일은 metrics.<name>에 avg/min/med/... 필드가 바로 있고,
    handleSummary(data)로 받는 data는 metrics.<name>.values 안에 한 겹 더 감싸져
    있다(게다가 실패율 키도 "value"가 아니라 "rate"). 둘 다 지원해야 어느 경로로
    들어와도 안 깨진다 — 처음엔 export 형식만 보고 짐작했다가 handleSummary
    쪽에서 전부 0으로 파싱되는 버그를 냈다."""
    m = metrics.get(name, {})
    return m.get("values", m)


def extract_summary(summary: dict, total_requests_fallback: int = 0) -> dict:
    metrics = summary.get("metrics", {})
    duration = _metric_values(metrics, "http_req_duration")
    reqs = _metric_values(metrics, "http_reqs")
    failed = _metric_values(metrics, "http_req_failed")
    return {
        "total_requests": int(reqs.get("count", total_requests_fallback)),
        "avg_ms": round(duration.get("avg", 0.0), 1),
        "p50_ms": round(duration.get("med", 0.0), 1),
        "p90_ms": round(duration.get("p(90)", 0.0), 1),
        "p95_ms": round(duration.get("p(95)", 0.0), 1),
        "max_ms": round(duration.get("max", 0.0), 1),
        "throughput_rps": round(reqs.get("rate", 0.0), 2),
        "error_rate": round(failed.get("rate", failed.get("value", 0.0)), 4),
    }
