"""평가/입력 데이터셋 관리 (RPA-136) — Workflow 골드셋과 Workflow 입력
데이터셋(업무정의서 원문)이 리포 내 정적 JSON 파일로만 존재해 팀원이 직접 편집해야
했다. 조회·업로드(파일 교체)·수동 입력(폼) 세 가지 방식으로 웹에서 관리한다.
RAGAS 골드셋은 ragas_datasets.py의 별도 페이지로 분리됐다(문서 브라우저·ChatGPT
JSON 자동 채우기·검증 로그 등 RAGAS 전용 흐름이 커져서)."""

import json

import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import OPS_BACKEND_URL

_SESSION = requests.Session()


def render() -> None:
    # RAGAS 탭은 ragas_datasets.py의 별도 페이지로 옮겼다 — 여기서는 Workflow만 관리한다.
    page_header(
        "평가/입력 데이터셋 관리",
        "Workflow 골드셋과 Workflow 입력 데이터셋(업무정의서 원문)을 조회·업로드·수동 등록합니다.",
    )
    tab_wf_goldset, tab_wf_input = st.tabs(["Workflow 평가 데이터셋", "Workflow 입력 데이터셋"])
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


def _search_box(card_key: str) -> str:
    return st.text_input("검색(모든 필드 대상 부분 일치)", key=f"{card_key}_search", placeholder="예: excel, RSS...")


def _search_filter(rows: list[dict], query: str) -> list[dict]:
    if not query.strip():
        return rows
    q = query.strip().lower()
    return [r for r in rows if q in json.dumps(r, ensure_ascii=False).lower()]


def _render_delete_section(card_key: str, delete_path_prefix: str, ids: list[str], list_path: str, id_label: str) -> None:
    with card(f"{card_key}_delete"):
        section_header("삭제", f"{id_label}를 골라 케이스 하나를 삭제합니다.")
        if not ids:
            st.caption("삭제할 케이스가 없습니다.")
            return
        target = st.selectbox(id_label, ids, key=f"{card_key}_delete_select")
        confirmed = st.checkbox("정말 삭제하겠습니다", key=f"{card_key}_delete_confirm")
        if st.button("삭제", key=f"{card_key}_delete_btn", disabled=not confirmed, type="primary"):
            try:
                resp = _SESSION.delete(f"{OPS_BACKEND_URL}{delete_path_prefix}/{target}", timeout=10)
                if resp.status_code == 200:
                    st.session_state.pop(list_path, None)
                    st.success(f"{target} 삭제했습니다.")
                    st.rerun()
                else:
                    st.error(resp.json().get("detail", resp.text))
            except (requests.RequestException, ValueError) as exc:
                st.error(f"삭제 실패: {exc}")


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


# ── Workflow 평가 데이터셋 ────────────────────────────────────────────


def _render_workflow_goldset_tab() -> None:
    filtered: list[dict] = []
    with card("workflow_goldset_view"):
        section_header("조회", "실제 커뮤니티 봇 기반 골드셋 — pm4py/WorFBench 채점의 정답(expected).")
        data, err = _get("/eval/workflow/cases")
        if err:
            st.warning(f"불러오지 못했습니다: {err}")
        else:
            query = _search_box("workflow_goldset")
            filtered = _search_filter(data, query)
            st.caption(f"{len(filtered)}/{len(data)}개 케이스")
            st.dataframe(
                [{"id": c["id"], "source_bot": c["source_bot"], "difficulty": c.get("difficulty"),
                  "task": c["input"]["task"][:80], "액션 수": len(c["expected"]["actions"])} for c in filtered],
                width="stretch", hide_index=True,
            )

    _render_delete_section("workflow_goldset", "/eval/workflow/cases", [c["id"] for c in filtered], "/eval/workflow/cases", "id")

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
    filtered_rows: list[dict] = []
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
            rows = [{"source_bot": k, "text": v} for k, v in data.items()]
            query = _search_box("workflow_input")
            filtered_rows = _search_filter(rows, query)
            st.caption(f"{len(filtered_rows)}/{len(rows)}개 봇")
            st.dataframe(
                [{"source_bot": r["source_bot"], "길이": len(r["text"]), "미리보기": r["text"][:80]} for r in filtered_rows],
                width="stretch", hide_index=True,
            )

    _render_delete_section(
        "workflow_input", "/eval/workflow/input-dataset",
        [r["source_bot"] for r in filtered_rows], "/eval/workflow/input-dataset", "source_bot",
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
