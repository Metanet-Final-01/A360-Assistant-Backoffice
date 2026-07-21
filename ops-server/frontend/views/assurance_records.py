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
    "refused": "보증 불충족",
}

_CONTROL_STATUS_LABELS = {
    "pass": "통과",
    "fail": "실패",
    "deny": "거부",
    "unassured": "추가 검토 필요",
    "error": "검사 오류",
}
_CONTROL_REASON_LABELS = {
    "MANIFEST_DERIVED_FROM_GIT": "변경 목록을 Git 기준으로 생성함",
    "RISK_PROFILE_DERIVED": "변경 내용에서 위험도를 산정함",
    "DEPENDENCY_CLOSURE_DENIED": "의존성 검증을 통과하지 못함",
    "DEPENDENCY_EVIDENCE_INCOMPLETE": "의존성 취약점·라이선스 증거가 부족함",
    "PROTECTED_ORACLE_REVIEW_REQUIRED": "보호 대상 변경에 별도 사람 리뷰가 필요함",
    "PROTECTED_ORACLE_REVIEW_VERIFIED": "현재 커밋에 대한 별도 사람 리뷰를 확인함",
    "SUBJECT_BOUND": "판정 대상 커밋과 증거가 일치함",
    "EVIDENCE_DIGESTS_VERIFIED": "증거 파일 지문이 검증됨",
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
        return "보증 불충족"
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


def _human_review(row: dict) -> dict:
    value = row.get("human_review")
    if isinstance(value, dict):
        return value
    payload = row.get("receipt_payload")
    if isinstance(payload, dict) and isinstance(payload.get("human_review"), dict):
        return payload["human_review"]
    return {}


def _human_review_text(row: dict) -> str:
    status = _human_review(row).get("status")
    return {
        "approved": "검토 완료",
        "missing": "검토 필요",
        "stale": "재검토 필요",
        "dismissed": "승인 취소",
        "rejected": "검토 불인정",
    }.get(status, "해당 없음")


def _human_review_summary(row: dict) -> dict:
    human_review = _human_review(row)
    review = human_review.get("review")
    review = review if isinstance(review, dict) else {}
    return {
        "현재 사람 검토 상태": _human_review_text(row),
        "승인자": review.get("reviewer_login"),
        "승인 시각": review.get("submitted_at"),
        "승인 대상 커밋": review.get("commit_id"),
        "상태 사유": human_review.get("reason_code"),
    }


def _change_control_rows(payload: dict) -> list[dict]:
    controls = payload.get("controls", [])
    if not isinstance(controls, list):
        return []
    rows = []
    for control in controls:
        if not isinstance(control, dict):
            continue
        reason_code = control.get("reason_code")
        rows.append({
            "통제": control.get("control_id"),
            "상태": _CONTROL_STATUS_LABELS.get(
                control.get("status"), control.get("status")
            ),
            "판정 설명": _CONTROL_REASON_LABELS.get(reason_code, reason_code),
            "사유 코드": reason_code,
            "증거 위치": control.get("evidence_uri"),
            "증거 지문": control.get("evidence_digest"),
        })
    return rows


def _change_subject(payload: dict) -> dict:
    subject = payload.get("subject")
    subject = subject if isinstance(subject, dict) else {}
    provenance = payload.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    return {
        "repository": subject.get("repository"),
        "pull_request_number": subject.get("pull_request_number"),
        "workflow_name": provenance.get("workflow_name"),
        "workflow_run_id": subject.get("workflow_run_id"),
        "run_attempt": subject.get("run_attempt"),
        "base_sha": subject.get("base_sha"),
        "head_sha": subject.get("head_sha"),
    }


def _status_notice(detail: dict) -> tuple[str, str]:
    status = _status_text(detail)
    integrity = detail.get("integrity_valid")
    decision = detail.get("decision")
    verdict = detail.get("assurance_verdict")
    if integrity is False:
        return "error", status
    if integrity is not True:
        return "warning", status
    if detail.get("harness") == "change" and verdict == "refused":
        mode = detail.get("rollout_mode") or "unknown"
        effect = detail.get("enforcement_effect")
        suffix = (
            "현재 Observe 모드이므로 PR 병합을 자동 차단하지 않습니다."
            if mode == "observe" and effect != "blocked"
            else "통제별 판정과 적용 모드를 확인하세요."
        )
        return "warning", f"보증 조건이 충족되지 않아 추가 검토가 필요합니다. {suffix}"
    if decision == "deny" or verdict == "deny":
        return "error", status
    if decision == "allow_candidate" and verdict == "observed":
        return "info", "관찰 기록입니다. 승인·인증 또는 배포 허가를 의미하지 않습니다."
    return "warning", status


def _render_summary(rows: list[dict]) -> None:
    metric_strip([
        ("조회 기록", f"{len(rows)}건"),
        ("관찰됨", sum(_status_text(row) == "관찰됨" for row in rows)),
        ("계약 위반", sum(_status_text(row) == "계약 위반" for row in rows)),
        (
            "판단 불가·불충족",
            sum(_status_text(row) in {"판단 불가", "보증 불충족"} for row in rows),
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
            "사람 검토": _human_review_text(row) if row.get("harness") == "change" else "해당 없음",
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
    notice_level, notice_message = _status_notice(detail)
    getattr(st, notice_level)(notice_message)

    payload = detail.get("receipt_payload")
    payload = payload if isinstance(payload, dict) else {}
    decision_summary = {
        "decision": detail.get("decision"),
        "assurance_verdict": detail.get("assurance_verdict"),
        "evidence_valid": detail.get("evidence_valid"),
        "completeness_status": detail.get("completeness_status"),
        "missing_evidence": detail.get("missing_evidence"),
        "integrity_valid": detail.get("integrity_valid"),
        "rollout_mode": detail.get("rollout_mode"),
        "enforcement_effect": detail.get("enforcement_effect"),
    }

    left, right = st.columns(2)
    if detail.get("harness") == "change":
        left.json(_change_subject(payload))
        right.json(decision_summary)

        section_header("통제별 판정")
        control_rows = _change_control_rows(payload)
        if control_rows:
            st.dataframe(pd.DataFrame(control_rows), width="stretch", hide_index=True)
        else:
            st.warning("저장된 통제별 판정이 없습니다. 증거 기록을 확인하세요.")

        section_header("사람 검토 상태")
        review_summary = _human_review_summary(detail)
        if review_summary["현재 사람 검토 상태"] == "검토 완료":
            st.success("현재 PR 커밋에 대한 별도 사람 검토가 완료되었습니다.")
        elif review_summary["현재 사람 검토 상태"] != "해당 없음":
            st.warning("현재 PR 커밋에는 유효한 사람 승인이 없습니다.")
        st.json(review_summary)

        st.json({
            "validator_version": detail.get("validator_version"),
            "policy_digest": detail.get("policy_digest"),
            "payload_digest": detail.get("payload_digest"),
            "source_observation_id": payload.get("source_observation_id"),
        })
    else:
        left.json({
            "request_id": detail.get("request_id"),
            "session_id": detail.get("session_id"),
            "recommendation_id": detail.get("recommendation_id"),
            "recommendation_version": detail.get("recommendation_version"),
            "candidate_id": detail.get("candidate_id"),
            "business_persisted": detail.get("business_persisted"),
        })
        right.json(decision_summary)
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
    page_header("AI 보증 판정 기록", "코드 변경과 AI 출력의 검증 판정 이력")
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
