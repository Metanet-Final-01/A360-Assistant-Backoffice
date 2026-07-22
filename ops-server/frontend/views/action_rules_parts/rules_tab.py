"""규칙 목록 조회, 새 규칙 등록, 승인/폐기/반려."""

import pandas as pd
import streamlit as st

from components.layout import card, section_header

from .api import fetch_current_version, fetch_rules, post_json

EVIDENCE_TYPES = ("공식 문서", "액션 카탈로그", "실제 Bot 파일", "골드셋-예측 비교", "수동 동작 검증", "기타")
STATUS_LABELS = {"draft": "검토 전", "approved": "승인됨", "deprecated": "폐기됨", "rejected": "반려됨"}


def render_rules_tab() -> None:
    current_version = fetch_current_version()
    version_text = f"v{current_version}" if current_version is not None else "-"
    st.caption(f"현재 규칙 세트 버전: {version_text}")

    render_create_rule_form()
    st.divider()
    render_rule_table_and_detail()


def render_create_rule_form() -> None:
    with card("action_rules_create"):
        section_header(
            "새 규칙 등록",
            "이름은 다르지만 같은 A360 액션인 것들을 하나의 그룹으로 등록합니다. "
            "등록 직후에는 '검토 전' 상태이고, 아래에서 따로 승인해야 채점에 반영됩니다.",
        )
        with st.form("action_rules_create_form"):
            canonical = st.text_input("대표 액션 이름(canonical)", placeholder="String.stringPackageAssignAction")
            members_text = st.text_area(
                "같은 액션으로 볼 이름들 (한 줄에 하나, 대표 이름도 포함)",
                placeholder="String.assign\nString.stringPackageAssignAction",
                height=100,
            )
            rationale = st.text_area("근거 설명", placeholder="왜 같은 액션으로 보는지 설명", height=80)

            st.caption("근거 자료 (최대 3개, 필요한 만큼만 채우면 됩니다)")
            evidence_rows = st.data_editor(
                pd.DataFrame([{"종류": "", "참조": "", "메모": ""} for _ in range(3)]),
                column_config={"종류": st.column_config.SelectboxColumn(options=["", *EVIDENCE_TYPES])},
                hide_index=True,
                key="action_rules_create_evidence_editor",
            )

            submitted = st.form_submit_button("규칙 등록", type="primary")

        if not submitted:
            return
        _submit_new_rule(canonical, members_text, rationale, evidence_rows)


def _submit_new_rule(canonical: str, members_text: str, rationale: str, evidence_rows: pd.DataFrame) -> None:
    members = [line.strip() for line in members_text.splitlines() if line.strip()]
    if not canonical.strip():
        st.error("대표 액션 이름을 입력하세요.")
        return
    if len(members) < 2:
        st.error("같은 액션으로 볼 이름을 최소 2개(대표 이름 포함) 입력하세요.")
        return
    if canonical.strip() not in members:
        members.append(canonical.strip())
    if not rationale.strip():
        st.error("근거 설명을 입력하세요.")
        return

    evidence = [
        {"evidence_type": row["종류"], "reference": row["참조"], "note": row["메모"]}
        for row in evidence_rows.to_dict("records")
        if row["종류"]
    ]

    result, error_message = post_json("/eval/workflow/action-rules", {
        "canonical": canonical.strip(),
        "members": members,
        "rationale": rationale.strip(),
        "evidence": evidence,
    })
    if result:
        st.success(f"규칙을 등록했습니다 (rule_id={result['rule_id']}, 검토 전 상태).")
        st.rerun()
    else:
        st.error(f"등록 실패: {error_message}")


def render_rule_table_and_detail() -> None:
    rules = fetch_rules()
    with card("action_rules_table"):
        section_header("규칙 목록", "행을 선택하면 아래에서 상세 내용을 보고 승인/폐기/반려할 수 있습니다.")
        if not rules:
            st.info("등록된 규칙이 없습니다.")
            return

        status_filter = st.selectbox("상태 필터", ["전체", *STATUS_LABELS.keys()], format_func=lambda s: STATUS_LABELS.get(s, s))
        filtered_rules = rules if status_filter == "전체" else [rule for rule in rules if rule["status"] == status_filter]

        table_rows = [_build_rule_row(rule) for rule in filtered_rules]
        selection = st.dataframe(
            pd.DataFrame(table_rows), width="stretch", hide_index=True,
            on_select="rerun", selection_mode="single-row", key=f"action_rules_table_{status_filter}",
        )
        selected_rows = selection.selection.rows if selection else []
        if selected_rows:
            render_rule_detail(filtered_rules[selected_rows[0]])


def _build_rule_row(rule: dict) -> dict:
    return {
        "대표 액션": rule["canonical"],
        "포함 개수": len(rule["members"]),
        "상태": STATUS_LABELS.get(rule["status"], rule["status"]),
        "근거 개수": len(rule.get("evidence") or []),
        "등록자": rule.get("created_by") or "-",
        "버전": rule.get("ruleset_version"),
    }


def render_rule_detail(rule: dict) -> None:
    st.divider()
    st.subheader(f"상세 - {rule['canonical']} ({STATUS_LABELS.get(rule['status'], rule['status'])})")

    left_column, right_column = st.columns(2)
    with left_column:
        st.text("포함된 액션 이름들")
        st.code("\n".join(rule["members"]), language="text")
        st.text("근거 설명")
        st.write(rule.get("rationale") or "(없음)")
    with right_column:
        st.text("근거 자료")
        st.json(rule.get("evidence") or [])
        st.caption(f"등록: {rule.get('created_by') or '-'} · 승인: {rule.get('approved_by') or '-'}")

    _render_status_change_buttons(rule)


def _render_status_change_buttons(rule: dict) -> None:
    actor = st.text_input("처리자 이름", key=f"action_rules_actor_{rule['rule_id']}")
    button_columns = st.columns(4)
    button_specs = [
        ("승인", "approved", button_columns[0]),
        ("폐기", "deprecated", button_columns[1]),
        ("반려", "rejected", button_columns[2]),
        ("검토 전으로 되돌리기", "draft", button_columns[3]),
    ]
    for label, new_status, column in button_specs:
        if column.button(label, key=f"action_rules_{new_status}_{rule['rule_id']}", disabled=rule["status"] == new_status):
            _change_rule_status(rule["rule_id"], new_status, actor)


def _change_rule_status(rule_id: str, new_status: str, actor: str) -> None:
    result, error_message = post_json(f"/eval/workflow/action-rules/{rule_id}/status", {"status": new_status, "actor": actor})
    if result:
        st.success("상태를 변경했습니다.")
        st.rerun()
    else:
        st.error(f"상태 변경 실패: {error_message}")
