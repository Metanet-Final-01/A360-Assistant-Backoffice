import json as _json

import altair as alt
import pandas as pd
import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import BACKEND_URL

# "개선사항 비교"에서 항상 같은 지표로 비교하기 위한 고정 목록 — 실제 데이터에 있는
# 것만 표에 나타난다(없는 지표는 자동으로 빠짐).
#
# "score"는 일부러 뺐다: EvalRunRecord.score는 채점 엔진마다 다른 지표를 대표값으로
# 복사해 넣은 별칭일 뿐이다(예: import_sandbox_ab.py — pm4py 행은 score=fitness,
# worfbench 행은 score=f1_score). 여러 source가 섞인 목록을 그대로 평균 내면 서로 다른
# 지표를 같은 이름으로 섞어버리게 되므로, 의미가 고정된 지표 이름만 비교 대상으로 쓴다.
_FIXED_METRICS = [
    "pm4py_fitness", "pm4py_precision",
    "worfbench_precision", "worfbench_recall", "worfbench_f1_score",
]


def render() -> None:
    page_header("EVAL", "평가 결과 (워크플로우 평가)")
    st.caption(
        "이렇게 쓰세요: 아래 목록에서 행을 2개 체크하면 바로 비교됩니다. 특정 두 버전을 "
        "고정된 지표로 비교하고 싶다면 맨 아래 \"개선사항 비교\"를 쓰세요."
    )

    _render_format_guide()
    _render_record_section()
    runs = _load_runs_once()
    _render_query_and_compare(runs)
    _render_improvement_comparison(runs)


def _load_runs_once() -> list[dict]:
    """페이지 첫 진입 시 자동으로 한 번 불러온다 — "조회" 버튼을 눌러야만 뭔가 보이는
    빈 화면으로 시작하지 않게 한다."""
    if "eval_runs" not in st.session_state:
        try:
            resp = requests.get(f"{BACKEND_URL}/eval/runs", timeout=5)
            st.session_state["eval_runs"] = resp.json()
        except requests.RequestException as e:
            st.error(f"백엔드 연결 실패: {e}")
            st.session_state["eval_runs"] = []
    return st.session_state["eval_runs"]


def _render_record_section() -> None:
    with card("eval_record"):
        with st.expander("결과 기록하기"):
            st.caption("source가 pm4py 또는 worfbench면 raw를 위 \"채점 포맷 안내\"의 출력 예시 형식으로 엄격 검증합니다.")
            with st.form("record_eval_run"):
                case_id = st.text_input("case_id", placeholder="예: web_excel_email_001")
                source = st.text_input("source (채점 방법)", placeholder="예: rule_check, pm4py, worfbench, manual")
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
                        "run_id": "", "case_id": case_id, "source": source,
                        "agent_label": agent_label or None,
                        "passed": {"True": True, "False": False}.get(passed),
                        "score": score if score > 0 else None,
                        "metrics": [], "raw": raw,
                    }
                    try:
                        resp = requests.post(f"{BACKEND_URL}/eval/runs", json=payload, timeout=5)
                        if resp.status_code == 200:
                            st.session_state.pop("eval_runs", None)  # 다음 렌더에서 새로 불러오게
                            st.success(f"기록됨: run_id={resp.json()['run_id']}")
                        else:
                            _show_record_error(resp)
                    except requests.RequestException as e:
                        st.error(f"백엔드 연결 실패: {e}")
                elif submitted:
                    st.warning("case_id와 source는 필수입니다.")


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


def _runs_to_dataframe(runs: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "case_id": r["case_id"], "source": r["source"], "agent_label": r["agent_label"] or "-",
            "score": r["score"], "passed": r["passed"], "logged_at": r["logged_at"][:19].replace("T", " "),
            "run_id": r["run_id"],
        }
        for r in runs
    ])


def _render_query_and_compare(runs: list[dict]) -> None:
    with card("eval_query"):
        section_header("평가 로그")
        if st.button("새로고침"):
            st.session_state.pop("eval_runs", None)
            st.rerun()

        if not runs:
            st.write("기록된 로그가 없습니다.")
            return

        with st.expander("필터"):
            cols = st.columns(3)
            f_case = cols[0].text_input("case_id 포함", key="filter_case_id")
            f_source = cols[1].text_input("source", key="filter_source")
            f_agent = cols[2].text_input("agent_label", key="filter_agent")

        filtered = runs
        if f_case:
            filtered = [r for r in filtered if f_case in r["case_id"]]
        if f_source:
            filtered = [r for r in filtered if f_source in r["source"]]
        if f_agent:
            filtered = [r for r in filtered if f_agent in (r["agent_label"] or "")]

        st.caption(f"{len(filtered)}건 — 행을 체크해 비교할 2건을 고르세요.")
        df = _runs_to_dataframe(filtered)
        # 필터가 바뀌면 filtered 목록의 길이/순서가 달라지므로, 이전 필터에서 선택했던
        # 행 인덱스가 새 목록 범위를 벗어날 수 있다 — 위젯 key를 필터 조합에 묶어서
        # 필터가 바뀌면 선택 상태가 자동으로 초기화되게 한다(IndexError 방지).
        table_key = f"eval_table_{f_case}_{f_source}_{f_agent}"
        event = st.dataframe(
            df.drop(columns=["run_id"]),
            width="stretch", hide_index=True,
            on_select="rerun", selection_mode="multi-row", key=table_key,
        )
        selected_idx = event.selection.rows if event else []
        selected_runs = [filtered[i] for i in selected_idx if i < len(filtered)]

        if len(selected_runs) == 2:
            _render_comparison(selected_runs)
        elif len(selected_runs) > 2:
            st.info("정확히 2건을 선택하면 비교 차트가 뜹니다.")


def _run_label(r: dict) -> str:
    """case_id·source·agent_label이 같은 두 로그(같은 케이스를 재실행한 경우 등)도
    run_id로 구분되게 한다 — 이 라벨이 비교 표/차트에서 dict 키로도 쓰이기 때문에
    두 선택 항목의 라벨이 같아지면 한쪽 값이 조용히 덮어써진다."""
    agent = r.get("agent_label") or "-"
    return f"{r['case_id']} · {r['source']} · {agent} · {r['run_id'][:8]}"


def _render_comparison(selected_runs: list[dict]) -> None:
    section_header("선택한 2건 비교")
    for r in selected_runs:
        with st.expander(f"{_run_label(r)} 원본(raw) 보기"):
            st.json(r.get("raw") or {})
    _render_delta_chart(selected_runs[0], selected_runs[1])


def _metrics_of(r: dict) -> dict[str, float]:
    values = {m["name"]: m["value"] for m in r.get("metrics", [])}
    if r.get("score") is not None:
        values.setdefault("score", r["score"])
    return values


def _render_delta_chart(run_a: dict, run_b: dict) -> None:
    ma, mb = _metrics_of(run_a), _metrics_of(run_b)
    shared_metrics = sorted(set(ma) & set(mb))
    if not shared_metrics:
        st.info("두 항목이 공통으로 가진 지표(metrics)가 없어 비교 차트를 그릴 수 없습니다.")
        return

    label_a, label_b = _run_label(run_a), _run_label(run_b)
    chart_rows = []
    for name in shared_metrics:
        chart_rows.append({"metric": name, "run": label_a, "value": ma[name]})
        chart_rows.append({"metric": name, "run": label_b, "value": mb[name]})

    chart = (
        alt.Chart(pd.DataFrame(chart_rows))
        .mark_bar()
        .encode(
            x=alt.X("run:N", title=None, axis=alt.Axis(labels=False, ticks=False)),
            y=alt.Y("value:Q", title="값"),
            color=alt.Color("run:N", title=None, scale=alt.Scale(range=["#1f6f8b", "#2f9ab2"])),
            column=alt.Column("metric:N", title=None, header=alt.Header(labelAngle=0)),
            tooltip=["metric", "run", "value"],
        )
        .properties(width=110)
    )
    st.altair_chart(chart, width="content")
    _render_delta_table(shared_metrics, ma, mb, label_a, label_b)


# backend/app/eval/xlsx_report.py의 GREEN_FILL/RED_FILL/델타 임계값과 값을 맞춰뒀다 —
# Excel로 내보낸 결과와 화면에 보이는 색이 서로 다르게 보이지 않도록. 프론트/백엔드가
# 별도 프로세스라 상수를 직접 import로 공유할 수 없어 값만 동일하게 유지한다.
_DELTA_THRESHOLD = 0.0001
_GREEN_BG = "#e4f5e9"
_RED_BG = "#fbe4e4"


def _highlight_delta(val):
    if not isinstance(val, (int, float)):
        return ""
    if val > _DELTA_THRESHOLD:
        return f"background-color: {_GREEN_BG}; color: #1f9d55; font-weight: 700;"
    if val < -_DELTA_THRESHOLD:
        return f"background-color: {_RED_BG}; color: #d84a3a; font-weight: 700;"
    return ""


def _render_delta_table(metric_names: list[str], ma: dict, mb: dict, label_a: str, label_b: str) -> None:
    rows = []
    for name in metric_names:
        delta = mb[name] - ma[name]
        pct = f"{delta / ma[name] * 100:+.1f}%" if ma[name] else "n/a"
        rows.append({
            "지표": name, label_a: round(ma[name], 4), label_b: round(mb[name], 4),
            "delta (B - A)": round(delta, 4), "변화율": pct,
        })
    st.dataframe(pd.DataFrame(rows).style.map(_highlight_delta, subset=["delta (B - A)"]), width="stretch", hide_index=True)


def _render_improvement_comparison(runs: list[dict]) -> None:
    """항상 같은(고정) 지표로 두 버전(agent_label)을 비교하는 표 — 개별 행을 고르는 대신
    "dev 대 rpa27처럼 버전 A 대 버전 B를 통째로 비교하고 싶을 때" 쓴다."""
    with card("eval_improvement"):
        section_header("개선사항 비교 (버전 A vs 버전 B, 고정 지표)")
        st.caption("두 agent_label을 고르면, 같은 case_id끼리 짝지어 고정 지표 평균을 비교합니다.")

        if not runs:
            st.write("비교할 로그가 없습니다.")
            return

        labels = sorted({r["agent_label"] for r in runs if r["agent_label"]})
        if len(labels) < 2:
            st.info("agent_label이 서로 다른 로그가 2개 이상 있어야 비교할 수 있습니다.")
            return

        version_counts = pd.DataFrame(
            [{"버전": label, "건수": sum(1 for r in runs if r["agent_label"] == label)} for label in labels]
        )
        st.dataframe(version_counts, width="stretch", hide_index=True)

        cols = st.columns(2)
        label_a = cols[0].selectbox("버전 A (개편 전)", labels, index=0, key="improve_a")
        label_b = cols[1].selectbox("버전 B (개편 후)", labels, index=min(1, len(labels) - 1), key="improve_b")
        if label_a == label_b:
            st.warning("서로 다른 버전을 골라야 비교됩니다.")
            return

        runs_a = [r for r in runs if r["agent_label"] == label_a]
        runs_b = [r for r in runs if r["agent_label"] == label_b]
        common_case_ids = {r["case_id"] for r in runs_a} & {r["case_id"] for r in runs_b}
        st.caption(f"버전 A {len(runs_a)}건 · 버전 B {len(runs_b)}건 · 공통 case_id {len(common_case_ids)}개로 평균 계산")

        def avg_metrics(records: list[dict]) -> dict[str, float]:
            # case_id별로 먼저 평균 낸 뒤 케이스 간 평균을 낸다 — 같은 case_id로 여러 번
            # 기록된 로그가 있어도 그 케이스가 부당하게 더 큰 가중치를 갖지 않게 한다
            # (backend/app/eval/xlsx_report.py의 _group_by_case와 같은 방식 — 화면 숫자와
            # 다운로드한 엑셀 숫자가 서로 달라지지 않도록 맞춰둠).
            by_case: dict[str, dict[str, list[float]]] = {}
            for r in records:
                if r["case_id"] not in common_case_ids:
                    continue
                bucket = by_case.setdefault(r["case_id"], {})
                for name, value in _metrics_of(r).items():
                    if name in _FIXED_METRICS:
                        bucket.setdefault(name, []).append(value)
            per_case_avg = {
                case_id: {name: sum(vals) / len(vals) for name, vals in metrics.items()}
                for case_id, metrics in by_case.items()
            }
            sums: dict[str, list[float]] = {}
            for metrics in per_case_avg.values():
                for name, value in metrics.items():
                    sums.setdefault(name, []).append(value)
            return {name: sum(vals) / len(vals) for name, vals in sums.items() if vals}

        avg_a, avg_b = avg_metrics(runs_a), avg_metrics(runs_b)
        shared = [m for m in _FIXED_METRICS if m in avg_a and m in avg_b]
        if not shared:
            st.info("두 버전이 공통으로 가진 고정 지표가 없습니다 (metrics가 비어있는 로그일 수 있음).")
            return

        _render_delta_table(shared, avg_a, avg_b, f"버전 A ({label_a})", f"버전 B ({label_b})")

        # 버튼을 눌렀을 때만 엑셀을 생성한다 — 그렇지 않으면 이 페이지의 다른 위젯(필터,
        # 위 목록의 행 체크 등)을 건드릴 때마다 Streamlit이 스크립트를 처음부터 다시
        # 실행하면서 매번 불필요하게 엑셀을 새로 만들어 버린다.
        if st.button("Excel 생성", key="gen_xlsx_btn"):
            try:
                xlsx_resp = requests.get(
                    f"{BACKEND_URL}/eval/export/comparison-xlsx",
                    params={"label_a": label_a, "label_b": label_b},
                    timeout=15,
                )
                if xlsx_resp.status_code == 200:
                    st.session_state["xlsx_export"] = (label_a, label_b, xlsx_resp.content)
                else:
                    st.session_state.pop("xlsx_export", None)
                    st.error(f"Excel 생성 실패: {xlsx_resp.text}")
            except requests.RequestException as e:
                st.session_state.pop("xlsx_export", None)
                st.error(f"Excel 생성 실패 (백엔드 연결 실패: {e})")

        cached = st.session_state.get("xlsx_export")
        if cached and cached[0] == label_a and cached[1] == label_b:
            st.download_button(
                "Excel로 내보내기",
                data=cached[2],
                file_name=f"comparison_{label_a}_vs_{label_b}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def _render_format_guide() -> None:
    with card("eval_format_guide"):
        with st.expander("채점 포맷 안내 (pm4py / WorFBench)"):
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
                    st.caption(section["input_example"]["note"])
                    st.json(section["input_example"]["value"])
                    st.caption(section["output_example"]["note"])
                    st.json(section["output_example"]["value"])
