"""사건 추적(상관관계) 뷰 — 한 request_id/session_id를 감사·성능·턴·RAG로 엮어 본다 (대시보드 #5).

"우리가 로그를 쌓는다"를 "한 사건이 시스템을 어떻게 통과했나"로 바꾸는 화면. 관측 데이터를
사건 단위로 추적할 수 있어야 운영에서 의미가 있다.
"""

import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from config import OPS_BACKEND_URL


def render() -> None:
    page_header("사건 추적")
    st.caption(
        "request_id로 조회하면 한 요청이 남긴 감사·성능·에이전트 턴·RAG 로그를 한 타임라인으로, "
        "session_id로 조회하면 그 대화의 모든 턴을 순서대로 보여줍니다. "
        "(모니터링 로그 화면에서 먼저 로그를 수집해 두어야 조회됩니다.)"
    )

    with card("trace_input"):
        key_type = st.radio("조회 축", ["request_id", "session_id"], horizontal=True, key="trace_key_type")
        value = st.text_input(f"{key_type} 입력", key="trace_value", placeholder="예: 5f2a3c309fd1")
        go = st.button("추적", key="trace_go")

    if not (go and value.strip()):
        st.info("조회 축을 고르고 id를 입력한 뒤 \"추적\"을 누르세요.")
        return

    params = {key_type: value.strip()}
    data = _safe_get(OPS_BACKEND_URL, "/observability/trace", params)
    if data is None:
        st.error("조회에 실패했습니다 — 모니터링 백엔드가 켜져 있는지 확인하세요.")
        return

    audit = data.get("audit_logs", [])
    metrics = data.get("request_metrics", [])
    turns = data.get("turn_events", [])
    rag = data.get("rag_logs", [])
    total = len(audit) + len(metrics) + len(turns) + len(rag)
    if total == 0:
        st.warning("연결된 로그가 없습니다 — 해당 id의 로그가 수집돼 있는지(모니터링 로그 화면), id가 맞는지 확인하세요.")
        return

    with card("trace_summary"):
        section_header("연결된 기록")
        metric_strip([
            ("감사 로그", f"{len(audit)}건"),
            ("성능 메트릭", f"{len(metrics)}건"),
            ("에이전트 턴", f"{len(turns)}건"),
            ("RAG 로그", f"{len(rag)}건"),
        ])

    if key_type == "request_id":
        with card("trace_timeline"):
            section_header("통합 타임라인", "이 요청이 시스템을 통과한 순서")
            rows = []
            for r in audit:
                rows.append({"시각": r.get("created_at"), "종류": "감사", "내용": f'{r.get("method")} {r.get("path")} → {r.get("status_code")} ({r.get("latency_ms")}ms)'})
            for r in metrics:
                rows.append({"시각": r.get("created_at"), "종류": "성능", "내용": f'{r.get("method")} {r.get("path")} {r.get("latency_ms")}ms'})
            for r in rag:
                raw = r.get("raw", {})
                rows.append({"시각": raw.get("started_at") or raw.get("timestamp"), "종류": "RAG", "내용": f'{raw.get("event")} {raw.get("path") or ""}'})
            for t in turns:  # 에이전트 턴도 통합 타임라인에 포함(CodeRabbit #13)
                rows.append({"시각": t.get("created_at"), "종류": "턴",
                             "내용": f'{t.get("stage") or t.get("kind")} · {t.get("message") or ""}'})
            df = pd.DataFrame(rows).sort_values("시각", na_position="last") if rows else pd.DataFrame()
            if not df.empty:
                st.dataframe(df, use_container_width=True, hide_index=True)

    _render_turns(turns)


def _render_turns(turns: list) -> None:
    if not turns:
        return
    with card("trace_turns"):
        section_header("에이전트 턴 타임라인", "노드별 진행·소요(elapsed_ms는 턴 시작부터 누적)")
        df = pd.DataFrame([
            {
                "req": (t.get("request_id") or "")[:8],
                "seq": t.get("seq"),
                "kind": t.get("kind"),
                "stage": t.get("stage"),
                "elapsed_ms": t.get("elapsed_ms"),
                "message": t.get("message"),
                "detail": (t.get("detail") or "")[:200],
            }
            for t in turns
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)


def _safe_get(base_url: str, path: str, params: dict) -> dict | None:
    try:
        resp = requests.get(f"{base_url}{path}", params=params, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except requests.RequestException:
        return None
