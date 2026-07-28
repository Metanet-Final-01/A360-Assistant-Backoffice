"""AI 출력 검증 판정 기록 read-only 조회 화면."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from components.time_display import format_kst
from config import OPS_BACKEND_URL

_TIMEOUT = 15
_STATE_ROWS = "assurance_record_rows"
_STATE_CURSOR = "assurance_record_cursor"
_STATE_FILTERS = "assurance_record_filters"
_STATE_SELECTED_PR = "assurance_selected_pr"

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
    "not_applicable": "검사 대상 아님",
}
_CONTROL_REASON_LABELS = {
    "MANIFEST_DERIVED_FROM_GIT": "변경 목록을 Git 기준으로 생성함",
    "RISK_PROFILE_DERIVED": "변경 내용에서 위험도를 산정함",
    "DEPENDENCY_CLOSURE_DENIED": "의존성 검증을 통과하지 못함",
    "DEPENDENCY_EVIDENCE_INCOMPLETE": "의존성 취약점·라이선스 증거가 부족함",
    "PROTECTED_ORACLE_REVIEW_REQUIRED": "보호 대상 변경에 별도 사람 리뷰가 필요함",
    "PROTECTED_ORACLE_REVIEW_VERIFIED": "현재 커밋에 대한 별도 사람 리뷰를 확인함",
    "PROTECTED_ORACLE_UNCHANGED": "보호 대상 파일이 변경되지 않아 별도 리뷰가 필요하지 않음",
    "SUBJECT_BOUND": "판정 대상 커밋과 증거가 일치함",
    "EVIDENCE_DIGESTS_VERIFIED": "증거 파일 지문이 검증됨",
    "DEPENDENCY_DETECTOR_ERROR": "의존성 검사 도중 오류가 발생함",
    "DEPENDENCY_CHANGE_NOT_APPLICABLE": "의존성 변경이 없어 검사가 적용되지 않음",
    "DEPENDENCY_CLOSURE_VERIFIED": "의존성 선언·버전·취약점·라이선스 검증을 통과함",
    "SUBJECT_BINDING_INCOMPLETE": "판정 대상 커밋과 체크아웃 상태가 일치하지 않음",
    "EVIDENCE_DIGEST_MISMATCH": "증거 파일이 누락됐거나 지문이 일치하지 않음",
    "DETECTOR_EXECUTION_ERROR": "신뢰된 판정기를 실행하지 못함",
}
_CONTROL_GUIDANCE = {
    "DEPENDENCY_CLOSURE_DENIED": (
        "변경된 의존성의 선언·고정 버전·취약점·라이선스·설치 경로 중 하나 이상이 정책을 통과하지 못했습니다.",
        "증거 위치의 dependency 규칙별 사유를 확인하고 직접 의존성 선언, 정확한 버전 고정 또는 승인 정책을 보완하세요.",
    ),
    "DEPENDENCY_EVIDENCE_INCOMPLETE": (
        "의존성 검사를 끝내는 데 필요한 검토된 취약점·라이선스 증거가 부족합니다.",
        "대상 패키지 버전에 맞는 검토 증거와 정책 상태를 보완한 뒤 다시 실행하세요.",
    ),
    "DEPENDENCY_DETECTOR_ERROR": (
        "의존성 파일이나 import를 파싱하는 과정에서 검사를 완료하지 못했습니다.",
        "dependency-evidence.json의 오류 사유와 변경된 manifest 형식을 확인하세요.",
    ),
    "PROTECTED_ORACLE_REVIEW_REQUIRED": (
        "테스트·워크플로·보증 정책 등 판정 기준 자체가 변경됐지만 최신 커밋에 대한 독립 승인이 없습니다.",
        "PR 작성자와 다른 사람이 최신 HEAD에 Approve 리뷰를 제출하세요.",
    ),
    "SUBJECT_BINDING_INCOMPLETE": (
        "검사한 체크아웃과 PR 최신 커밋 또는 추적 파일 상태가 일치하지 않습니다.",
        "최신 PR HEAD를 깨끗하게 체크아웃한 상태에서 Change Assurance를 다시 실행하세요.",
    ),
    "EVIDENCE_DIGEST_MISMATCH": (
        "판정이 참조하는 증거 파일이 없거나 SHA-256 지문이 달라 무결성을 확인할 수 없습니다.",
        "artifact 생성·업로드 과정과 SHA256SUMS를 확인한 뒤 다시 실행하세요.",
    ),
    "DETECTOR_EXECUTION_ERROR": (
        "신뢰된 판정기가 실행을 완료하지 못해 어떤 통제도 통과로 확정할 수 없습니다.",
        "Actions 로그의 판정기 오류와 detector-error.json을 확인한 뒤 다시 실행하세요.",
    ),
}
_HUMAN_REVIEW_REASON_LABELS = {
    "HUMAN_REVIEW_VERIFIED": "현재 커밋에 대한 사람 승인을 확인함",
    "HUMAN_REVIEW_NOT_SUBMITTED": "이 기록 생성 시점에는 사람 승인이 없었음",
    "HUMAN_REVIEW_NOT_APPROVED": "사람 리뷰가 승인 상태가 아니었음",
    "HUMAN_REVIEW_SUBJECT_MISMATCH": "승인 대상 저장소 또는 커밋이 일치하지 않음",
    "HUMAN_REVIEW_HEAD_MISMATCH": "승인 후 새 커밋이 추가되어 재승인이 필요함",
    "HUMAN_REVIEW_NOT_INDEPENDENT": "작성자와 승인자가 같아 독립 승인이 아님",
    "HUMAN_REVIEW_NOT_HUMAN": "사람 계정의 승인이 아님",
    "HUMAN_REVIEW_TIME_MISSING": "승인 시각 증거가 없음",
    "HUMAN_REVIEW_DISMISSED": "기존 승인이 취소됨",
}


def _value(options: dict[str, str], label: str) -> str | None:
    return next((key for key, value in options.items() if value == label), None)


def _since(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _reconcile_pr_selection(valid_tokens: list[str]) -> str | None:
    if not valid_tokens:
        st.session_state.pop(_STATE_SELECTED_PR, None)
        return None
    selected = st.session_state.get(_STATE_SELECTED_PR)
    if selected not in valid_tokens:
        selected = valid_tokens[0]
        st.session_state[_STATE_SELECTED_PR] = selected
    return selected


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


def _current_status_text(row: dict) -> str:
    status = _status_text(row)
    mode = row.get("rollout_mode")
    effect = row.get("enforcement_effect")
    if status == "관찰됨":
        return f"통과 ({'Warn' if mode == 'warn' else 'Observe'})"
    if mode == "warn" and effect == "warned":
        return f"경고 (Warn): {status}"
    if status in {"판단 불가", "보증 불충족"}:
        return "검토 필요"
    return status


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
    if status == "missing":
        return (
            "검토 불필요"
            if _status_text(row) == "관찰됨"
            else "검토 필요"
        )
    return {
        "approved": "검토 완료",
        "stale": "재검토 필요",
        "dismissed": "승인 취소",
        "rejected": "검토 불인정",
    }.get(status, "해당 없음")


def _human_review_summary(row: dict) -> dict:
    human_review = _human_review(row)
    review = human_review.get("review")
    review = review if isinstance(review, dict) else {}
    status = human_review.get("status")
    reason_code = human_review.get("reason_code")
    reason_label = _HUMAN_REVIEW_REASON_LABELS.get(
        reason_code, reason_code or "-"
    )
    if status == "missing" and _human_review_text(row) == "검토 불필요":
        reason_label = "보호 대상 변경이 없어 별도 승인 증거가 필요하지 않음"
    missing_label = (
        "승인 전 기록"
        if status == "missing" and _human_review_text(row) == "검토 필요"
        else "-"
    )
    return {
        "이 기록의 사람 검토 상태": _human_review_text(row),
        "승인자": review.get("reviewer_login") or missing_label,
        "승인 시각": (
            format_kst(review.get("submitted_at"))
            if review.get("submitted_at")
            else missing_label
        ),
        "승인 대상 커밋": review.get("commit_id") or missing_label,
        "상태 사유": reason_label,
    }


def _format_kst(value: object) -> str:
    """RPA-323 타임라인의 기존 내부 계약을 공용 KST 변환기로 연결한다."""
    return format_kst(value)


def _change_subject_from_row(row: dict) -> dict:
    subject = row.get("change_subject")
    if isinstance(subject, dict):
        return subject
    payload = row.get("receipt_payload")
    if isinstance(payload, dict):
        nested = payload.get("subject")
        if isinstance(nested, dict):
            return nested
    return {}


def _change_group_key(row: dict) -> tuple[str, int] | None:
    if row.get("harness") != "change":
        return None
    subject = _change_subject_from_row(row)
    repository = subject.get("repository")
    pull_request_number = subject.get("pull_request_number")
    if (
        not isinstance(repository, str)
        or not repository.strip()
        or not isinstance(pull_request_number, int)
        or isinstance(pull_request_number, bool)
        or pull_request_number < 1
    ):
        return None
    return repository, pull_request_number


def _created_at_sort_key(row: dict) -> tuple[datetime, str]:
    value = row.get("created_at")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        parsed = datetime.min.replace(tzinfo=timezone.utc)
    return parsed, str(row.get("receipt_digest") or "")


def _group_change_records(
    rows: list[dict],
) -> tuple[list[tuple[tuple[str, int], list[dict]]], list[dict]]:
    grouped: dict[tuple[str, int], list[dict]] = {}
    ungrouped = []
    for row in rows:
        key = _change_group_key(row)
        if key is None:
            ungrouped.append(row)
            continue
        grouped.setdefault(key, []).append(row)
    groups = []
    for key, records in grouped.items():
        groups.append((key, sorted(records, key=_created_at_sort_key)))
    groups.sort(
        key=lambda item: _created_at_sort_key(item[1][-1]),
        reverse=True,
    )
    return groups, ungrouped


def _timeline_stage(row: dict, previous_head_sha: str | None) -> str:
    review_status = _human_review(row).get("status")
    if review_status == "approved":
        return "사람 승인 반영"
    if review_status == "dismissed":
        return "승인 취소 반영"
    if review_status == "stale":
        return "재검토 필요"
    subject = _change_subject_from_row(row)
    if subject.get("source_event") == "pull_request_review":
        return "사람 검토 반영"
    head_sha = subject.get("head_sha")
    if previous_head_sha and head_sha and head_sha != previous_head_sha:
        return "새 커밋 검사"
    if review_status == "missing":
        return "승인 전 검사"
    return "PR 검사"


def _timeline_rows(rows: list[dict]) -> list[dict]:
    timeline = []
    previous_head_sha = None
    for index, row in enumerate(sorted(rows, key=_created_at_sort_key), start=1):
        subject = _change_subject_from_row(row)
        human_review = _human_review(row)
        review = human_review.get("review")
        review = review if isinstance(review, dict) else {}
        head_sha = subject.get("head_sha")
        timeline.append({
            "순서": index,
            "시각": _format_kst(row.get("created_at")),
            "단계": _timeline_stage(row, previous_head_sha),
            "커밋": str(head_sha)[:8] if head_sha else "-",
            "판정": (
                "승인 전 검토 필요"
                if human_review.get("status") == "missing"
                and _status_text(row) in {"판단 불가", "보증 불충족"}
                else _current_status_text(row)
            ),
            "사람 검토": _human_review_text(row),
            "승인자": review.get("reviewer_login") or "-",
            "승인 시각": _format_kst(review.get("submitted_at")),
            "기록 지문": row.get("receipt_digest"),
        })
        if head_sha:
            previous_head_sha = str(head_sha)
    return timeline


def _timeline_choices(records: list[dict]) -> list[tuple[str, str, dict]]:
    choices = []
    for row in reversed(records):
        digest = str(row.get("receipt_digest") or "")
        subject = _change_subject_from_row(row)
        label = (
            f"{_format_kst(row.get('created_at'))} · "
            f"{str(subject.get('head_sha') or '')[:8]} · "
            f"{_human_review_text(row)} · {digest[:16]}"
        )
        choices.append((digest, label, row))
    return choices


def _latest_change_rows(
    groups: list[tuple[tuple[str, int], list[dict]]],
) -> list[dict]:
    return [_current_change_record(records) for _key, records in groups if records]


def _current_change_record(records: list[dict]) -> dict:
    """Return the effective current record for the latest PR head.

    A plain pull_request rerun can append a `missing` review record after an
    approved record without invalidating that approval. An approval is only
    carried forward for the exact same head SHA and stops applying when a
    later review explicitly invalidates it or a new head appears.
    """
    ordered = sorted(records, key=_created_at_sort_key)
    latest = ordered[-1]
    latest_head = _change_subject_from_row(latest).get("head_sha")
    if not latest_head:
        return latest

    for row in reversed(ordered):
        if _change_subject_from_row(row).get("head_sha") != latest_head:
            continue
        review_status = _human_review(row).get("status")
        if review_status in {"approved", "dismissed", "rejected", "stale"}:
            return row
    return latest


def _pr_summary_rows(
    groups: list[tuple[tuple[str, int], list[dict]]],
) -> list[dict]:
    summaries = []
    for (repository, pull_request_number), records in groups:
        current = _current_change_record(records)
        latest_audit = records[-1]
        subject = _change_subject_from_row(current)
        review_status = _human_review(current).get("status")
        summaries.append({
            "저장소": repository,
            "PR": f"#{pull_request_number}",
            "현재 커밋": str(subject.get("head_sha") or "")[:8] or "-",
            "현재 상태": _current_status_text(current),
            "사람 검토": _human_review_text(current),
            "최근 판정 시각": _format_kst(latest_audit.get("created_at")),
            "감사 기록": len(records),
            "승인 후속 기록": "있음" if review_status == "approved" else "없음",
        })
    return summaries


def _pr_choice_label(
    key: tuple[str, int], records: list[dict]
) -> str:
    repository, pull_request_number = key
    latest = _current_change_record(records)
    subject = _change_subject_from_row(latest)
    latest_sha = str(subject.get("head_sha") or "")[:8] or "-"
    return (
        f"{repository} · PR #{pull_request_number} · "
        f"{latest_sha} · {_current_status_text(latest)} · {_human_review_text(latest)}"
    )


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


def _change_warning_details(payload: dict) -> list[dict]:
    controls = payload.get("controls", [])
    if not isinstance(controls, list):
        return []
    details = []
    for control in controls:
        if (
            not isinstance(control, dict)
            or control.get("status") in {"pass", "not_applicable"}
        ):
            continue
        explanation = control.get("explanation")
        explanation = explanation if isinstance(explanation, dict) else {}
        reason_code = control.get("reason_code")
        fallback_impact, fallback_action = _CONTROL_GUIDANCE.get(
            reason_code,
            (
                "이 통제를 통과하지 못해 현재 변경을 완전히 보증할 수 없습니다.",
                "원본 사유 코드와 증거 파일을 확인한 뒤 다시 실행하세요.",
            ),
        )
        details.append({
            "통제": control.get("control_id"),
            "상태": _CONTROL_STATUS_LABELS.get(
                control.get("status"), control.get("status")
            ),
            "판정 설명": _CONTROL_REASON_LABELS.get(reason_code, reason_code),
            "발견 내용": (
                explanation.get("finding")
                or control.get("reason")
                or _CONTROL_REASON_LABELS.get(reason_code, reason_code)
            ),
            "왜 통과가 아닌가": explanation.get("impact") or fallback_impact,
            "확인/조치": explanation.get("action") or fallback_action,
            "사유 코드": reason_code,
            "증거 위치": control.get("evidence_uri"),
        })
    return details


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
        "source_event": subject.get("source_event"),
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
    if (
        detail.get("harness") == "change"
        and detail.get("rollout_mode") == "warn"
        and detail.get("enforcement_effect") == "warned"
    ):
        return (
            "warning",
            "Change Assurance 경고가 발생했습니다. 통제별 상세 사유와 확인/조치를 확인하세요. "
            "현재 Warn 단계이므로 PR 병합은 자동 차단하지 않습니다.",
        )
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


def _render_summary(
    rows: list[dict],
    change_groups: list[tuple[tuple[str, int], list[dict]]],
) -> None:
    latest_change_rows = _latest_change_rows(change_groups)
    metric_strip([
        ("전체 감사 기록", f"{len(rows)}건"),
        ("조회 PR", f"{len(latest_change_rows)}개"),
        (
            "현재 관찰 통과",
            sum(_status_text(row) == "관찰됨" for row in latest_change_rows),
        ),
        (
            "현재 계약 위반",
            sum(_status_text(row) == "계약 위반" for row in latest_change_rows),
        ),
        (
            "현재 검토 필요",
            sum(
                _status_text(row) in {"판단 불가", "보증 불충족"}
                for row in latest_change_rows
            ),
        ),
        (
            "현재 무결성 이상",
            sum(
                _status_text(row) in {"무결성 실패", "무결성 미확인"}
                for row in latest_change_rows
            ),
        ),
    ])
    st.caption(
        "현재 상태는 PR의 최신 HEAD와 같은 커밋에 결속된 유효 승인까지 반영합니다. "
        "승인 전 기록을 포함한 모든 과거 판정은 감사 이력에 그대로 보존됩니다."
    )


def _table_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "시각": format_kst(row.get("created_at")),
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
        warning_details = _change_warning_details(payload)
        if warning_details:
            section_header("비통과 판정 상세")
            for warning_detail in warning_details:
                with st.expander(
                    f"{warning_detail['통제']} · {warning_detail['상태']} · "
                    f"{warning_detail['판정 설명']}"
                ):
                    st.markdown(f"**발견 내용**  \n{warning_detail['발견 내용']}")
                    st.markdown(
                        f"**왜 통과가 아닌가**  \n{warning_detail['왜 통과가 아닌가']}"
                    )
                    st.markdown(f"**확인/조치**  \n{warning_detail['확인/조치']}")
                    st.caption(
                        f"사유 코드: {warning_detail['사유 코드']} · "
                        f"증거: {warning_detail['증거 위치'] or '-'}"
                    )

        section_header("이 기록 생성 시점의 사람 검토")
        review_summary = _human_review_summary(detail)
        if review_summary["이 기록의 사람 검토 상태"] == "검토 완료":
            st.success("이 기록은 해당 커밋에 대한 사람 승인을 포함합니다.")
        elif review_summary["이 기록의 사람 검토 상태"] == "검토 필요":
            st.info(
                "이 기록은 승인 전에 생성된 과거 기록입니다. "
                "같은 PR의 최신 상태와 승인 후속 기록은 위 타임라인에서 확인하세요."
            )
        elif review_summary["이 기록의 사람 검토 상태"] == "검토 불필요":
            st.info("보호 대상 변경이 없어 이 기록에는 별도 사람 승인이 필요하지 않습니다.")
        elif review_summary["이 기록의 사람 검토 상태"] != "해당 없음":
            st.warning("이 기록에는 현재 커밋에 유효한 사람 승인이 없습니다.")
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

    change_groups, ungrouped_rows = _group_change_records(rows)
    _render_summary(rows, change_groups)
    with card("assurance_records"):
        section_header("PR별 Change 판정 이력")
        if change_groups:
            st.dataframe(
                pd.DataFrame(_pr_summary_rows(change_groups)),
                width="stretch",
                height=min(320, 38 + 35 * len(change_groups)),
                hide_index=True,
            )
            groups_by_token = {
                f"{key[0]}#{key[1]}": (key, records)
                for key, records in change_groups
            }
            labels_by_token = {
                token: _pr_choice_label(key, records)
                for token, (key, records) in groups_by_token.items()
            }
            group_tokens = list(groups_by_token)
            _reconcile_pr_selection(group_tokens)
            selected_group_token = st.selectbox(
                "상세 조회할 PR",
                group_tokens,
                format_func=lambda token: labels_by_token[token],
                key=_STATE_SELECTED_PR,
            )
            selected_key, selected_records = groups_by_token[selected_group_token]
            selected_repository, selected_pr_number = selected_key
            current = _current_change_record(selected_records)
            latest_audit = selected_records[-1]
            current_subject = _change_subject_from_row(current)
            current_review = _human_review_summary(current)

            current_tab, timeline_tab, raw_tab = st.tabs([
                "현재 상태",
                f"승인·변경 이력 ({len(selected_records)})",
                "원본 판정",
            ])
            with current_tab:
                section_header(f"PR #{selected_pr_number} 현재 상태")
                status_level, status_message = _status_notice(current)
                getattr(st, status_level)(status_message)
                st.dataframe(
                    pd.DataFrame([{
                        "저장소": selected_repository,
                        "현재 커밋": (
                            str(current_subject.get("head_sha") or "")[:8] or "-"
                        ),
                        "현재 판정": _current_status_text(current),
                        "사람 검토": current_review["이 기록의 사람 검토 상태"],
                        "승인자": current_review["승인자"],
                        "승인 시각": current_review["승인 시각"],
                        "최근 판정 시각": _format_kst(
                            latest_audit.get("created_at")
                        ),
                    }]),
                    width="stretch",
                    height=74,
                    hide_index=True,
                )
                if current_review["이 기록의 사람 검토 상태"] == "검토 완료":
                    st.success(
                        f"{current_review['승인자']} 님이 "
                        f"{current_review['승인 시각']}에 현재 커밋을 승인했습니다."
                    )
                elif current_review["이 기록의 사람 검토 상태"] == "검토 불필요":
                    st.info("보호 대상 변경이 없어 별도 사람 승인이 필요하지 않습니다.")

            with timeline_tab:
                st.caption(
                    "승인 전 검사도 삭제하거나 덮어쓰지 않습니다. "
                    "승인·새 커밋·재승인 결과가 시간순으로 추가됩니다."
                )
                st.dataframe(
                    pd.DataFrame(_timeline_rows(selected_records)),
                    width="stretch",
                    height=min(300, 38 + 35 * len(selected_records)),
                    hide_index=True,
                )
            with raw_tab:
                timeline_choices = _timeline_choices(selected_records)
                rows_by_token = {
                    token: row for token, _label, row in timeline_choices
                }
                record_labels = {
                    token: label for token, label, _row in timeline_choices
                }
                selected_token = st.selectbox(
                    "감사 기록 상세",
                    [None, *rows_by_token],
                    format_func=lambda token: (
                        "선택 안 함"
                        if token is None
                        else record_labels[token]
                    ),
                    key=(
                        f"assurance_pr_{selected_repository}_"
                        f"{selected_pr_number}"
                    ),
                )
                if selected_token is not None:
                    _render_detail(rows_by_token[selected_token])
        else:
            st.info("PR 정보가 포함된 Change 판정 기록이 없습니다.")

        if ungrouped_rows:
            section_header("Output 및 PR 식별 정보가 없는 기록")
            st.dataframe(
                pd.DataFrame(_table_rows(ungrouped_rows)),
                width="stretch",
                hide_index=True,
            )
        if st.session_state.get(_STATE_CURSOR):
            if st.button("다음 기록", icon=":material/expand_more:"):
                if _fetch(filters, append=True):
                    st.rerun()

    choices = {
        f"{format_kst(row.get('created_at'))} · {_status_text(row)} · {str(row.get('receipt_digest', ''))[:20]}…": row
        for row in ungrouped_rows
    }
    if choices:
        selected = st.selectbox("기타 기록 상세 조회", ["선택 안 함", *choices.keys()])
        if selected != "선택 안 함":
            with card("assurance_record_detail"):
                _render_detail(choices[selected])
