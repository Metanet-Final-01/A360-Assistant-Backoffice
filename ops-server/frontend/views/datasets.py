"""평가/입력 데이터셋 관리 (RPA-136) — BFCL/RAGAS/Workflow 골드셋과 Workflow 입력
데이터셋(업무정의서 원문)이 리포 내 정적 JSON 파일로만 존재해 팀원이 직접 편집해야
했다. 조회·업로드(파일 교체)·수동 입력(폼) 세 가지 방식으로 웹에서 관리한다.

BFCL은 스키마가 turns/expected_targets로 중첩돼 있어 폼으로는 흔한 형태
(단일 턴, target 최대 2개 x 파라미터 최대 2개)만 지원한다 — multi_turn_state/
response_based처럼 턴이 여러 개거나 파라미터가 더 많은 케이스는 업로드를 쓴다."""

import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import OPS_BACKEND_URL

_SESSION = requests.Session()


def render() -> None:
    page_header(
        "DATASETS", "평가/입력 데이터셋 관리",
        "BFCL·RAGAS·Workflow 골드셋과 Workflow 입력 데이터셋(업무정의서 원문)을 조회·업로드·수동 등록합니다.",
    )
    tab_bfcl, tab_ragas, tab_wf_goldset, tab_wf_input = st.tabs(
        ["BFCL 평가 데이터셋", "RAGAS 평가 데이터셋", "Workflow 평가 데이터셋", "Workflow 입력 데이터셋"]
    )
    with tab_bfcl:
        _render_bfcl_tab()
    with tab_ragas:
        _render_ragas_tab()
    with tab_wf_goldset:
        _render_workflow_goldset_tab()
    with tab_wf_input:
        _render_workflow_input_tab()


def _get(path: str) -> tuple[object | None, str | None]:
    try:
        resp = _SESSION.get(f"{OPS_BACKEND_URL}{path}", timeout=10)
        resp.raise_for_status()
        return resp.json(), None
    except (requests.RequestException, ValueError) as exc:
        return None, str(exc)


def _post_json(path: str, payload: dict) -> tuple[bool, str]:
    try:
        resp = _SESSION.post(f"{OPS_BACKEND_URL}{path}", json=payload, timeout=10)
        if resp.status_code == 200:
            return True, ""
        return False, resp.json().get("detail", resp.text)
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)


def _post_upload(path: str, file) -> tuple[bool, str]:
    try:
        resp = _SESSION.post(
            f"{OPS_BACKEND_URL}{path}",
            files={"file": (file.name, file.getvalue(), "application/json")}, timeout=10,
        )
        if resp.status_code == 200:
            return True, f"{resp.json().get('saved', '?')}건 저장됨"
        return False, resp.json().get("detail", resp.text)
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)


def _render_upload_section(card_key: str, list_path: str, upload_path: str, help_text: str) -> None:
    with card(card_key):
        section_header("파일 업로드로 교체", help_text)
        file = st.file_uploader("JSON 파일", type=["json"], key=f"{card_key}_uploader")
        if file is not None and st.button("업로드해서 교체", key=f"{card_key}_upload_btn"):
            ok, msg = _post_upload(upload_path, file)
            if ok:
                st.session_state.pop(list_path, None)
                st.success(f"업로드 완료 — {msg}")
                st.rerun()
            else:
                st.error(f"업로드 실패: {msg}")


# ── BFCL ──────────────────────────────────────────────────────────────

_BFCL_CATEGORIES = [
    "simple", "multiple", "irrelevance", "missing_parameters", "missing_functions",
    "multi_turn_state", "response_based",
]
_BFCL_CHECKS = ["exact", "enum", "contains", "nonempty", "bool_true", "bool_false"]


def _render_bfcl_tab() -> None:
    with card("bfcl_goldset_view"):
        section_header("조회", "골드셋 케이스 목록 — 채점에 쓰이는 원본 그대로.")
        data, err = _get("/eval/bfcl/cases")
        if err:
            st.warning(f"불러오지 못했습니다: {err}")
        else:
            st.caption(f"{len(data)}개 케이스")
            st.dataframe(
                [{"case_id": c["case_id"], "category": c["category"], "turns": len(c["turns"]),
                  "질문": (c.get("document_text") or c["turns"][0]["message"])[:80]} for c in data],
                width="stretch", hide_index=True,
            )

    _render_upload_section(
        "bfcl_upload", "/eval/bfcl/cases", "/eval/bfcl/cases/upload",
        "전체 골드셋 배열([...])을 통째로 교체합니다 — BFCLCase 스키마 검증을 통과해야 저장됩니다.",
    )

    with card("bfcl_manual_add"):
        section_header(
            "수동 입력으로 생성",
            "단일 턴 / target 최대 2개 / target당 파라미터 최대 2개만 지원합니다. "
            "multi_turn_state·response_based나 더 복잡한 케이스는 위 업로드를 쓰세요.",
        )
        with st.form("bfcl_manual_add_form"):
            col1, col2 = st.columns(2)
            case_id = col1.text_input("case_id", placeholder="simple_send_mail_001")
            category = col2.selectbox("category", _BFCL_CATEGORIES)
            document_text = st.text_area("업무 설명(document_text, 선택)", placeholder="irrelevance는 비워둡니다", height=80)
            message = st.text_input("사용자 메시지(turn message)", value="이 업무를 분석해서 자동화 워크플로우로 추천해줘.")
            expect_no_action = st.checkbox("이 턴에서는 액션이 전혀 없어야 정답(irrelevance 등)")

            st.markdown("**expected target (최대 2개, 첫 번째는 필수 — package를 비워두면 미사용)**")
            targets_input = []
            for i in range(2):
                st.caption(f"target {i + 1}")
                tcol1, tcol2 = st.columns(2)
                pkg = tcol1.text_input("package", key=f"bfcl_target_pkg_{i}")
                act = tcol2.text_input("action", key=f"bfcl_target_act_{i}")
                params = []
                for j in range(2):
                    pcol1, pcol2, pcol3 = st.columns(3)
                    pname = pcol1.text_input("param name", key=f"bfcl_param_name_{i}_{j}")
                    pcheck = pcol2.selectbox("check", _BFCL_CHECKS, key=f"bfcl_param_check_{i}_{j}")
                    pexpected = pcol3.text_input("expected", key=f"bfcl_param_expected_{i}_{j}")
                    if pname.strip():
                        params.append({"name": pname.strip(), "check": pcheck, "expected": pexpected.strip() or None})
                targets_input.append((pkg.strip(), act.strip(), params))

            submitted = st.form_submit_button("BFCL 케이스 등록", type="primary")

        if submitted:
            expected_targets = [
                {"package": pkg, "action": act, "params": params}
                for pkg, act, params in targets_input if pkg and act
            ]
            payload = {
                "case_id": case_id.strip(), "category": category,
                "document_text": document_text.strip() or None,
                "turns": [{
                    "message": message.strip(), "expected_targets": expected_targets,
                    "expect_no_action": expect_no_action,
                }],
            }
            ok, msg = _post_json("/eval/bfcl/cases", payload)
            if ok:
                st.session_state.pop("/eval/bfcl/cases", None)
                st.success("등록했습니다.")
                st.rerun()
            else:
                st.error(f"등록 실패: {msg}")


# ── RAGAS ─────────────────────────────────────────────────────────────


def _render_ragas_tab() -> None:
    with card("ragas_goldset_view"):
        section_header("조회", "골드셋 케이스 목록.")
        data, err = _get("/eval/ragas/cases")
        if err:
            st.warning(f"불러오지 못했습니다: {err}")
        else:
            st.caption(f"{len(data)}개 케이스")
            st.dataframe(data, width="stretch", hide_index=True)

    _render_upload_section(
        "ragas_upload", "/eval/ragas/cases", "/eval/ragas/cases/upload",
        "전체 골드셋 배열([...])을 통째로 교체합니다 — RagasCase 스키마 검증을 통과해야 저장됩니다.",
    )

    with card("ragas_manual_add"):
        section_header("수동 입력으로 생성", "")
        with st.form("ragas_manual_add_form"):
            case_id = st.text_input("case_id", placeholder="rag_case_011")
            question = st.text_area("question", height=80)
            ground_truth = st.text_area("ground_truth (사람이 검증한 정답 요약)", height=80)
            ref_docs = st.text_input("reference_doc_ids (쉼표로 구분, 선택)", placeholder="doc-001, doc-002")
            submitted = st.form_submit_button("RAGAS 케이스 등록", type="primary")
        if submitted:
            payload = {
                "case_id": case_id.strip(), "question": question.strip(), "ground_truth": ground_truth.strip(),
                "reference_doc_ids": [d.strip() for d in ref_docs.split(",") if d.strip()],
            }
            ok, msg = _post_json("/eval/ragas/cases", payload)
            if ok:
                st.session_state.pop("/eval/ragas/cases", None)
                st.success("등록했습니다.")
                st.rerun()
            else:
                st.error(f"등록 실패: {msg}")


# ── Workflow 평가 데이터셋 ────────────────────────────────────────────


def _render_workflow_goldset_tab() -> None:
    with card("workflow_goldset_view"):
        section_header("조회", "실제 커뮤니티 봇 기반 골드셋 — pm4py/WorFBench 채점의 정답(expected).")
        data, err = _get("/eval/workflow/cases")
        if err:
            st.warning(f"불러오지 못했습니다: {err}")
        else:
            st.caption(f"{len(data)}개 케이스")
            st.dataframe(
                [{"id": c["id"], "source_bot": c["source_bot"], "difficulty": c.get("difficulty"),
                  "task": c["input"]["task"][:80], "액션 수": len(c["expected"]["actions"])} for c in data],
                width="stretch", hide_index=True,
            )

    _render_upload_section(
        "workflow_goldset_upload", "/eval/workflow/cases", "/eval/workflow/cases/upload",
        "전체 골드셋 배열([...])을 통째로 교체합니다 — WorkflowCase 스키마 검증을 통과해야 저장됩니다.",
    )

    with card("workflow_goldset_manual_add"):
        section_header(
            "수동 입력으로 생성",
            "정답 액션(최대 8개)의 package/action 쌍을 입력합니다 — in_catalog는 등록 시 전부 True로 저장되니, "
            "실제 카탈로그에 없는 액션이면 업로드로 in_catalog=false를 직접 지정하세요.",
        )
        with st.form("workflow_goldset_manual_add_form"):
            col1, col2, col3 = st.columns(3)
            case_id = col1.text_input("id", placeholder="bot-18")
            source_bot = col2.text_input("source_bot", placeholder="MyNewBot")
            difficulty = col3.selectbox("difficulty", ["easy", "medium", "hard"], index=1)
            task = st.text_area("task (업무 한 줄 요약)", height=80)

            st.markdown("**정답 액션 (최대 8개, package를 비워두면 미사용)**")
            action_rows = []
            for i in range(8):
                acol1, acol2 = st.columns(2)
                pkg = acol1.text_input("package", key=f"wf_action_pkg_{i}")
                act = acol2.text_input("action", key=f"wf_action_act_{i}")
                if pkg.strip() and act.strip():
                    action_rows.append((pkg.strip(), act.strip()))

            submitted = st.form_submit_button("Workflow 케이스 등록", type="primary")

        if submitted:
            packages = sorted({pkg for pkg, _ in action_rows})
            payload = {
                "id": case_id.strip(), "source_bot": source_bot.strip(), "difficulty": difficulty,
                "input": {"task": task.strip()},
                "expected": {
                    "packages": packages, "packages_in_catalog": packages,
                    "actions": [{"package": pkg, "action": act, "in_catalog": True} for pkg, act in action_rows],
                },
                "scoreable": True,
            }
            ok, msg = _post_json("/eval/workflow/cases", payload)
            if ok:
                st.session_state.pop("/eval/workflow/cases", None)
                st.success("등록했습니다.")
                st.rerun()
            else:
                st.error(f"등록 실패: {msg}")


# ── Workflow 입력 데이터셋 ────────────────────────────────────────────


def _render_workflow_input_tab() -> None:
    with card("workflow_input_view"):
        section_header(
            "조회", "source_bot별 상세 업무정의서 원문 — Workflow 라이브 러너가 골드셋 한 줄 요약보다 "
            "우선 사용합니다(RPA-135, 과거 결과와 공정 비교를 위해).",
        )
        data, err = _get("/eval/workflow/input-dataset")
        if err:
            st.warning(f"불러오지 못했습니다: {err}")
            data = {}
        else:
            st.caption(f"{len(data)}개 봇")
            st.dataframe(
                [{"source_bot": k, "길이": len(v), "미리보기": v[:80]} for k, v in data.items()],
                width="stretch", hide_index=True,
            )

    _render_upload_section(
        "workflow_input_upload", "/eval/workflow/input-dataset", "/eval/workflow/input-dataset/upload",
        '전체 {"source_bot": "원문"} 객체를 통째로 교체합니다.',
    )

    with card("workflow_input_manual_add"):
        section_header("수동 입력으로 등록/수정", "이미 있는 source_bot을 입력하면 원문을 덮어씁니다.")
        with st.form("workflow_input_manual_add_form"):
            existing_bots = list(data.keys()) if isinstance(data, dict) else []
            source_bot = st.selectbox(
                "source_bot", ["(새로 입력)"] + existing_bots, key="workflow_input_source_bot_select",
            )
            if source_bot == "(새로 입력)":
                source_bot = st.text_input("새 source_bot 이름")
            text = st.text_area("업무정의서 원문", height=220)
            submitted = st.form_submit_button("등록/수정", type="primary")
        if submitted:
            ok, msg = _post_json(
                "/eval/workflow/input-dataset", {"source_bot": source_bot.strip(), "text": text.strip()},
            )
            if ok:
                st.session_state.pop("/eval/workflow/input-dataset", None)
                st.success("저장했습니다.")
                st.rerun()
            else:
                st.error(f"저장 실패: {msg}")
