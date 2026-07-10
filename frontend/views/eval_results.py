import json as _json

import pandas as pd
import requests
import streamlit as st

from components.layout import card, page_header
from config import BACKEND_URL


def render() -> None:
    page_header("EVAL", "평가 결과 (워크플로우 평가)")
    st.caption("채점 방법(rule_check/pm4py/수작업 등)은 가리지 않는다 — 결과를 아래 형식으로 기록하면 조회·비교된다.")

    with card("eval_record"):
        with st.expander("결과 기록하기"):
            with st.form("record_eval_run"):
                case_id = st.text_input("case_id", placeholder="예: web_excel_email_001")
                source = st.text_input("source (채점 방법)", placeholder="예: rule_check, pm4py, manual")
                agent_label = st.text_input("agent_label (평가 대상 버전, 선택)", placeholder="예: dev")
                passed = st.selectbox("passed", ["(선택 안 함)", "True", "False"])
                score = st.number_input("score (0~1, 선택)", min_value=0.0, max_value=1.0, value=0.0, step=0.01)
                raw_text = st.text_area("raw (원본 결과 JSON, 선택)", placeholder="{}")
                submitted = st.form_submit_button("기록")

            if submitted:
                try:
                    raw = _json.loads(raw_text) if raw_text.strip() else None
                except _json.JSONDecodeError:
                    st.error("raw는 올바른 JSON이어야 합니다.")
                    raw = None
                    submitted = False

                if submitted and case_id and source:
                    payload = {
                        "run_id": "",
                        "case_id": case_id,
                        "source": source,
                        "agent_label": agent_label or None,
                        "passed": {"True": True, "False": False}.get(passed),
                        "score": score if score > 0 else None,
                        "metrics": [],
                        "raw": raw,
                    }
                    try:
                        resp = requests.post(f"{BACKEND_URL}/eval/runs", json=payload, timeout=5)
                        if resp.status_code == 200:
                            st.success(f"기록됨: run_id={resp.json()['run_id']}")
                        else:
                            st.error(resp.text)
                    except requests.RequestException as e:
                        st.error(f"백엔드 연결 실패: {e}")
                elif submitted:
                    st.warning("case_id와 source는 필수입니다.")

    with card("eval_query"):
        col_a, col_b, col_c = st.columns(3)
        filter_case_id = col_a.text_input("case_id로 필터", key="filter_case_id")
        filter_source = col_b.text_input("source로 필터", key="filter_source")
        filter_agent = col_c.text_input("agent_label로 필터", key="filter_agent")

        if st.button("평가 로그 조회"):
            try:
                params = {k: v for k, v in {
                    "case_id": filter_case_id, "source": filter_source, "agent_label": filter_agent,
                }.items() if v}
                resp = requests.get(f"{BACKEND_URL}/eval/runs", params=params, timeout=5)
                runs = resp.json()
                st.session_state["eval_runs"] = runs
            except requests.RequestException as e:
                st.error(f"백엔드 연결 실패: {e}")

        runs = st.session_state.get("eval_runs", [])
        if runs:
            df = pd.DataFrame([
                {
                    "run_id": r["run_id"], "logged_at": r["logged_at"], "case_id": r["case_id"],
                    "source": r["source"], "agent_label": r["agent_label"],
                    "passed": r["passed"], "score": r["score"],
                }
                for r in runs
            ])
            st.dataframe(df, use_container_width=True)

            st.markdown("**비교할 run_id 선택**")
            selected_ids = st.multiselect("run_id", options=[r["run_id"] for r in runs])
            if selected_ids:
                selected_runs = [r for r in runs if r["run_id"] in selected_ids]
                compare_rows = []
                for r in selected_runs:
                    row = {"run_id": r["run_id"], "case_id": r["case_id"], "source": r["source"], "score": r["score"]}
                    for m in r.get("metrics", []):
                        row[f"metric:{m['name']}"] = m["value"]
                    compare_rows.append(row)
                st.dataframe(pd.DataFrame(compare_rows), use_container_width=True)

                for r in selected_runs:
                    with st.expander(f"{r['run_id']} 원본(raw) 보기"):
                        st.json(r.get("raw") or {})
        else:
            st.write("조회된 로그가 없습니다.")
