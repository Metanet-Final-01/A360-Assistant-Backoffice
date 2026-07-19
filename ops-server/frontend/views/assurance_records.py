"""AI 출력 검증 판정 기록 read-only 조회 화면."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from config import OPS_BACKEND_URL

_TIMEOUT = 15
_STATE_ROWS = "assurance_record_rows"
_STATE_CURSOR = "assurance_record_cursor"
_STATE_FILTERS = "assurance_record_filters"

_DECISION_LABELS = {
    "allow_candidate": "허용 후보",
    "deny": "계약 위반",
    "unassured": "판단 불가",
}
_VERDICT_LABELS = {
    "observed": "관찰됨",
    "deny": "거부",
    "refused": "보증 거절",
}


def _value(options: dict[str, str], label: str) -> str | None:
    return next((key for key, value in options.items() if value == label), None)


def _since(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _safe_message(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
        if isinstance(detail, str):
            return detail
    except (ValueError, AttributeError):
        pass
    return "요청을 처리하지 못했습니다."


def _get(
    path: str,
    params: dict | None = None,
    *,
    not_found_message: str = "Backend에 검증 판정 기록 조회 API가 아직 배포되지 않았습니다.",
) -> dict | None:
    try:
        response = requests.get(f"{OPS_BACKEND_URL}{path}", params=params, timeout=_TIMEOUT)
    except requests.RequestException:
        st.error("Ops Backend에 연결할 수 없습니다.")
        return None
    if response.status_code == 403:
        st.error("조회 권한이 없습니다. Backend 운영 인증 설정을 확인하세요.")
        return None
    if response.status_code == 404:
        st.warning(not_found_message)
        return None
    if response.status_code == 502:
        st.error("Ops Backend가 A360 Backend에 연결하지 못했습니다.")
        return None
    if response.status_code != 200:
        st.error(f"조회 실패({response.status_code}): {_safe_message(response)}")
        return None
    try:
        data = response.json()
    except ValueError:
        st.error("Backend 응답 형식이 올바르지 않습니다.")
        return None
    if not isinstance(data, dict):
        st.error("Backend 응답 형식이 올바르지 않습니다.")
        return None
    return data


def _fetch(filters: dict, *, append: bool) -> bool:
    params = dict(filters)
    if append:
        cursor = st.session_state.get(_STATE_CURSOR)
        if not cursor:
            return False
        params.pop("since", None)
        params["cursor"] = cursor
    data = _get("/assurance/records", params)
    if data is None:
        return False
    records = data.get("receipts", [])
    if not isinstance(records, list):
        st.error("검증 판정 기록 목록 형식이 올바르지 않습니다.")
        return False
    current = st.session_state.get(_STATE_ROWS, []) if append else []
    known = {row.get("receipt_digest") for row in current if isinstance(row, dict)}
    current.extend(
        row
        for row in records
        if isinstance(row, dict) and row.get("receipt_digest") not in known
    )
    st.session_state[_STATE_ROWS] = current
    st.session_state[_STATE_CURSOR] = data.get("next_cursor")
    return True


def _render_filters() -> tuple[dict, tuple]:
    with card("assurance_filters"):
        c1, c2, c3, c4 = st.columns([1.1, 1.2, 1.2, 1.0])
        period = c1.selectbox("기간", ["최근 24시간", "최근 7일", "최근 30일"], index=1)
        harness_label = c2.selectbox("검사 경계", ["전체", "Output", "Change"])
        decision_label = c3.selectbox("판정", ["전체", *_DECISION_LABELS.values()])
        verdict_label = c4.selectbox("검증 결과", ["전체", *_VERDICT_LABELS.values()])
        c5, c6, c7 = st.columns([1.5, 2.5, 1.0])
        request_id = c5.text_input("request_id", max_chars=32)
        session_id = c6.text_input("session_id")
        limit = c7.number_input("조회 단위", min_value=10, max_value=500, value=100, step=10)

    period_hours = {"최근 24시간": 24, "최근 7일": 24 * 7, "최근 30일": 24 * 30}
    harness = {"전체": None, "Output": "output", "Change": "change"}[harness_label]
    decision = _value(_DECISION_LABELS, decision_label)
    verdict = _value(_VERDICT_LABELS, verdict_label)
    request_id = request_id.strip() or None
    session_id = session_id.strip() or None
    filters = {
        key: value
        for key, value in {
            "limit": int(limit),
            "harness": harness,
            "decision": decision,
            "assurance_verdict": verdict,
            "request_id": request_id,
            "session_id": session_id,
            "since": _since(period_hours[period]),
        }.items()
        if value is not None
    }
    filter_key = (
        period,
        harness,
        decision,
        verdict,
        request_id,
        session_id,
        int(limit),
    )
    return filters, filter_key


def _status_text(row: dict) -> str:
    integrity = row.get("integrity_valid")
    if integrity is False:
        return "무결성 실패"
    if integrity is not True:
        return "무결성 미확인"
    verdict = row.get("assurance_verdict")
    decision = row.get("decision")
    if verdict == "refused":
        return "보증 거절"
    if decision == "deny" or verdict == "deny":
        return "계약 위반"
    if decision == "unassured":
        return "판단 불가"
    if decision == "allow_candidate" and verdict == "observed":
        return "관찰됨"
    return "판단 불가"


def _business_persisted_text(row: dict) -> str:
    value = row.get("business_persisted")
    if value is True:
        return "저장"
    if value is False:
        return "미저장"
    return "미확인"


def _render_summary(rows: list[dict]) -> None:
    metric_strip([
        ("조회 기록", f"{len(rows)}건"),
        ("관찰됨", sum(_status_text(row) == "관찰됨" for row in rows)),
        ("계약 위반", sum(_status_text(row) == "계약 위반" for row in rows)),
        (
            "판단 불가·거절",
            sum(_status_text(row) in {"판단 불가", "보증 거절"} for row in rows),
        ),
        (
            "무결성 이상",
            sum(_status_text(row) in {"무결성 실패", "무결성 미확인"} for row in rows),
        ),
    ])


def _table_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "시각": row.get("created_at"),
            "상태": _status_text(row),
            "검사 경계": row.get("harness"),
            "판정": _DECISION_LABELS.get(row.get("decision"), row.get("decision")),
            "증거": row.get("completeness_status"),
            "업무 저장": _business_persisted_text(row),
            "요청": row.get("request_id"),
            "세션": row.get("session_id"),
            "추천 버전": row.get("recommendation_version"),
            "기록 지문": row.get("receipt_digest"),
        }
        for row in rows
    ]


def _render_detail(row: dict) -> None:
    digest = row.get("receipt_digest")
    if not isinstance(digest, str):
        return
    detail = _get(
        f"/assurance/records/{digest}",
        not_found_message="해당 검증 판정 기록을 찾을 수 없습니다. 목록을 새로고침하세요.",
    )
    if detail is None:
        return

    section_header("검증 판정 상세")
    status = _status_text(detail)
    if status == "관찰됨":
        st.info("관찰 기록입니다. 승인·인증 또는 배포 허가를 의미하지 않습니다.")
    elif status in {"계약 위반", "무결성 실패"}:
        st.error(status)
    else:
        st.warning(status)

    left, right = st.columns(2)
    left.json({
        "request_id": detail.get("request_id"),
        "session_id": detail.get("session_id"),
        "recommendation_id": detail.get("recommendation_id"),
        "recommendation_version": detail.get("recommendation_version"),
        "candidate_id": detail.get("candidate_id"),
        "business_persisted": detail.get("business_persisted"),
    })
    right.json({
        "decision": detail.get("decision"),
        "assurance_verdict": detail.get("assurance_verdict"),
        "evidence_valid": detail.get("evidence_valid"),
        "completeness_status": detail.get("completeness_status"),
        "missing_evidence": detail.get("missing_evidence"),
        "integrity_valid": detail.get("integrity_valid"),
    })

    payload = detail.get("receipt_payload")
    payload = payload if isinstance(payload, dict) else {}
    st.json({
        "validator_version": detail.get("validator_version"),
        "policy_digest": detail.get("policy_digest"),
        "catalog_digest": detail.get("catalog_digest"),
        "payload_digest": detail.get("payload_digest"),
        "requested_agent_version": detail.get("requested_agent_version"),
        "resolved_agent_version": detail.get("resolved_agent_version"),
        "findings": payload.get("findings", []),
    })


def render() -> None:
    page_header("AI 출력 검증 기록", "Backend 저장 경계에서 생성된 검증 판정 이력")
    filters, filter_key = _render_filters()
    changed = st.session_state.get(_STATE_FILTERS) != filter_key
    if changed:
        st.session_state[_STATE_FILTERS] = filter_key
        st.session_state.pop(_STATE_ROWS, None)
        st.session_state.pop(_STATE_CURSOR, None)

    refresh_col, _ = st.columns([1, 5])
    refresh = refresh_col.button(
        "새로고침", icon=":material/refresh:", width="stretch"
    )
    fetch_succeeded = None
    if changed or refresh or _STATE_ROWS not in st.session_state:
        fetch_succeeded = _fetch(filters, append=False)

    rows = st.session_state.get(_STATE_ROWS, [])
    if fetch_succeeded is False and not rows:
        return
    if not rows:
        st.info("선택한 조건에 해당하는 검증 판정 기록이 없습니다.")
        return

    _render_summary(rows)
    with card("assurance_records"):
        section_header("검증 판정 이력")
        st.dataframe(pd.DataFrame(_table_rows(rows)), width="stretch", hide_index=True)
        if st.session_state.get(_STATE_CURSOR):
            if st.button("다음 기록", icon=":material/expand_more:"):
                if _fetch(filters, append=True):
                    st.rerun()

    choices = {
        f"{row.get('created_at', '-')} · {_status_text(row)} · {str(row.get('receipt_digest', ''))[:20]}…": row
        for row in rows
    }
    selected = st.selectbox("상세 조회", ["선택 안 함", *choices.keys()])
    if selected != "선택 안 함":
        with card("assurance_record_detail"):
            _render_detail(choices[selected])
