import json as _json

import altair as alt
import pandas as pd
import requests
import streamlit as st

from components.layout import badge, card, page_header, score_badge_kind, section_header
from config import BACKEND_URL


def _run_label(r: dict) -> str:
    agent = r.get("agent_label") or "-"
    return f"{r['case_id']} · {r['source']} · {agent} · {r['run_id']}"


def render() -> None:
    page_header("EVAL", "평가 결과 (워크플로우 평가)")
    st.caption("채점 방법(rule_check/pm4py/worfbench/수작업 등)은 가리지 않는다 — 결과를 아래 형식으로 기록하면 조회·비교된다.")

    _render_format_guide()

    with card("eval_record"):
        section_header("결과 기록하기")
        with st.expander("펼치기", expanded=False):
            with st.form("record_eval_run"):
                case_id = st.text_input("case_id", placeholder="예: web_excel_email_001")
                source = st.text_input("source (채점 방법)", placeholder="예: rule_check, pm4py, worfbench, manual")
                st.caption("source가 pm4py 또는 worfbench면 raw를 위 '채점 포맷 안내'의 출력 예시 형식으로 엄격 검증합니다.")
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
                            _show_record_error(resp)
                    except requests.RequestException as e:
                        st.error(f"백엔드 연결 실패: {e}")
                elif submitted:
                    st.warning("case_id와 source는 필수입니다.")

    with card("eval_query"):
        section_header("평가 로그 조회")
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
        if not runs:
            st.write("조회된 로그가 없습니다.")
            return

        st.caption(f"{len(runs)}건")
        for r in runs:
            _render_run_card(r)

        with st.expander("표로 보기"):
            df = pd.DataFrame([
                {
                    "run_id": r["run_id"], "logged_at": r["logged_at"], "case_id": r["case_id"],
                    "source": r["source"], "agent_label": r["agent_label"],
                    "passed": r["passed"], "score": r["score"],
                }
                for r in runs
            ])
            st.dataframe(df, width='stretch')

        section_header("비교")
        options = [r["run_id"] for r in runs]
        selected_ids = st.multiselect(
            "비교할 항목 선택 (2개를 고르면 지표 비교 차트가 그려집니다)",
            options=options,
            format_func=lambda rid: _run_label(next(r for r in runs if r["run_id"] == rid)),
        )
        if selected_ids:
            selected_runs = [r for r in runs if r["run_id"] in selected_ids]
            _render_comparison(selected_runs)


def _show_record_error(resp: requests.Response) -> None:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict) and detail.get("errors"):
        st.error(detail.get("message", "검증 실패"))
        for e in detail["errors"]:
            st.markdown(f"- {e}")
    else:
        st.error(resp.text)


def _render_run_card(r: dict) -> None:
    with st.container(border=True):
        cols = st.columns([3, 1.4, 1.4, 1.4])
        with cols[0]:
            st.markdown(f'<div class="op-run-card__title">{r["case_id"]}</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="op-run-card__meta">{r["logged_at"][:19].replace("T", " ")} · run_id={r["run_id"]}</div>',
                unsafe_allow_html=True,
            )
        with cols[1]:
            st.markdown(badge(r["source"], "mid"), unsafe_allow_html=True)
        with cols[2]:
            st.markdown(badge(r.get("agent_label") or "-", "neutral"), unsafe_allow_html=True)
        with cols[3]:
            kind = score_badge_kind(r.get("passed"), r.get("score"))
            if r.get("score") is not None:
                label = f"score {r['score']:.2f}"
            elif r.get("passed") is True:
                label = "pass"
            elif r.get("passed") is False:
                label = "fail"
            else:
                label = "-"
            st.markdown(badge(label, kind), unsafe_allow_html=True)
        if r.get("metrics"):
            metric_text = " · ".join(f"{m['name']}={m['value']:.3f}" for m in r["metrics"])
            st.markdown(f'<div class="op-run-card__meta">{metric_text}</div>', unsafe_allow_html=True)


def _render_comparison(selected_runs: list[dict]) -> None:
    compare_rows = []
    for r in selected_runs:
        row = {"run_id": r["run_id"], "case_id": r["case_id"], "source": r["source"], "score": r["score"]}
        for m in r.get("metrics", []):
            row[f"metric:{m['name']}"] = m["value"]
        compare_rows.append(row)
    st.dataframe(pd.DataFrame(compare_rows), width='stretch')

    if len(selected_runs) == 2:
        _render_delta_chart(selected_runs[0], selected_runs[1])

    for r in selected_runs:
        with st.expander(f"{r['run_id']} 원본(raw) 보기"):
            st.json(r.get("raw") or {})


def _render_delta_chart(run_a: dict, run_b: dict) -> None:
    """AB_comparison_report.xlsx가 하던 '지표별 A/B 델타 색상 표시'를 웹 차트로."""

    def metrics_of(r: dict) -> dict[str, float]:
        values = {m["name"]: m["value"] for m in r.get("metrics", [])}
        if r.get("score") is not None:
            values.setdefault("score", r["score"])
        return values

    ma, mb = metrics_of(run_a), metrics_of(run_b)
    shared_metrics = sorted(set(ma) & set(mb))
    if not shared_metrics:
        st.info("두 항목이 공통으로 가진 지표(metrics)가 없어 비교 차트를 그릴 수 없습니다.")
        return

    label_a, label_b = _run_label(run_a), _run_label(run_b)
    chart_rows = []
    delta_rows = []
    for name in shared_metrics:
        va, vb = ma[name], mb[name]
        chart_rows.append({"metric": name, "run": label_a, "value": va})
        chart_rows.append({"metric": name, "run": label_b, "value": vb})
        delta = vb - va
        delta_rows.append({"지표": name, label_a: round(va, 4), label_b: round(vb, 4), "delta (B - A)": round(delta, 4)})

    chart_df = pd.DataFrame(chart_rows)
    chart = (
        alt.Chart(chart_df)
        .mark_bar()
        .encode(
            x=alt.X("run:N", title=None, axis=alt.Axis(labels=False, ticks=False)),
            y=alt.Y("value:Q", title="값"),
            color=alt.Color(
                "run:N",
                title=None,
                scale=alt.Scale(range=["#1f6f8b", "#2f9ab2"]),
            ),
            column=alt.Column("metric:N", title=None, header=alt.Header(labelAngle=0)),
            tooltip=["metric", "run", "value"],
        )
        .properties(width=110)
    )
    st.altair_chart(chart, width='content')

    delta_df = pd.DataFrame(delta_rows)

    def _highlight_delta(val):
        if not isinstance(val, (int, float)):
            return ""
        if val > 0.0001:
            return "background-color: #e8f8ee; color: #1f9d55; font-weight: 700;"
        if val < -0.0001:
            return "background-color: #fbeceb; color: #d84a3a; font-weight: 700;"
        return ""

    st.dataframe(
        delta_df.style.map(_highlight_delta, subset=["delta (B - A)"]),
        width='stretch',
    )


def _render_format_guide() -> None:
    with card("eval_format_guide"):
        section_header("채점 포맷 안내 (pm4py / WorFBench)")
        st.caption(
            "pm4py/worfbench로 채점한 결과를 기록하려면 raw가 아래 출력 예시와 같은 형식이어야 합니다 "
            "(엄격 검증됨). 예시 원본은 backend/app/eval/format_examples/에 있습니다."
        )
        try:
            resp = requests.get(f"{BACKEND_URL}/eval/format-guide", timeout=5)
        except requests.RequestException as e:
            st.warning(f"포맷 안내를 불러오지 못했습니다 (백엔드 연결 실패: {e})")
            return
        if resp.status_code != 200:
            st.warning("포맷 안내를 불러오지 못했습니다.")
            return

        guide = resp.json()
        tab_pm4py, tab_worfbench = st.tabs(["pm4py", "WorFBench"])
        for tab, engine in ((tab_pm4py, "pm4py"), (tab_worfbench, "worfbench")):
            with tab:
                section = guide[engine]
                st.write(section["summary"])
                with st.expander("입력 예시 (에이전트 → 채점 엔진)"):
                    st.caption(section["input_example"]["note"])
                    st.json(section["input_example"]["value"])
                with st.expander("출력 예시 (채점 엔진 → 이 앱의 raw)"):
                    st.caption(section["output_example"]["note"])
                    st.json(section["output_example"]["value"])
