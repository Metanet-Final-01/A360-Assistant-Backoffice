import altair as alt
import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from config import BACKEND_URL

FIXED_METRICS = (
    "pm4py_fitness",
    "pm4py_precision",
    "worfbench_precision",
    "worfbench_recall",
    "worfbench_f1_score",
)


def render() -> None:
    page_header("EVALUATION", "평가 결과", "저장된 실행을 조회하고 동일 케이스 기준으로 버전을 비교합니다.")
    runs = _load_runs()
    metric_strip(
        [("전체 결과", len(runs)), ("평가 버전", len({r.get("agent_label") for r in runs if r.get("agent_label")})),
         ("데이터셋", len({(r.get("dataset_id"), r.get("dataset_version")) for r in runs if r.get("dataset_id")}))]
    )
    _render_runs(runs)
    _render_version_comparison(runs)


def _load_runs() -> list[dict]:
    if st.button("결과 새로고침", type="secondary"):
        st.session_state.pop("eval_runs", None)
    if "eval_runs" not in st.session_state:
        try:
            response = requests.get(f"{BACKEND_URL}/eval/runs", timeout=5)
            response.raise_for_status()
            st.session_state["eval_runs"] = response.json()
        except (requests.RequestException, ValueError) as exc:
            st.error(f"평가 결과를 불러오지 못했습니다: {exc}")
            st.session_state["eval_runs"] = []
    return st.session_state["eval_runs"]


def _metrics_of(run: dict) -> dict[str, float]:
    return {item["name"]: item["value"] for item in run.get("metrics", [])}


def _label(run: dict) -> str:
    return f"{run['case_id']} · {run['source']} · {run.get('agent_label') or '-'} · {(run.get('run_id') or '-')[:8]}"


def _render_runs(runs: list[dict]) -> None:
    with card("eval_runs"):
        section_header("결과 로그", "케이스별 원본과 공통 지표를 확인합니다.")
        if not runs:
            st.info("저장된 평가 결과가 없습니다. 먼저 ‘평가 준비’에서 데이터셋을 등록하세요.")
            return

        cols = st.columns(4)
        case_filter = cols[0].text_input("케이스", placeholder="case_id 포함")
        source_filter = cols[1].selectbox("채점 방식", ["전체"] + sorted({r["source"] for r in runs}))
        agent_filter = cols[2].selectbox("버전", ["전체"] + sorted({r["agent_label"] for r in runs if r.get("agent_label")}))
        dataset_filter = cols[3].selectbox("데이터셋", ["전체"] + sorted({r["dataset_id"] for r in runs if r.get("dataset_id")}))

        filtered = [r for r in runs if not case_filter or case_filter in r["case_id"]]
        if source_filter != "전체":
            filtered = [r for r in filtered if r["source"] == source_filter]
        if agent_filter != "전체":
            filtered = [r for r in filtered if r.get("agent_label") == agent_filter]
        if dataset_filter != "전체":
            filtered = [r for r in filtered if r.get("dataset_id") == dataset_filter]

        rows = [{
            "case_id": r["case_id"], "source": r["source"], "버전": r.get("agent_label") or "-",
            "데이터셋": f"{r.get('dataset_id') or '-'}@{r.get('dataset_version') or '-'}",
            "score": r.get("score"), "passed": r.get("passed"),
            "기록 시각": r["logged_at"][:19].replace("T", " "),
        } for r in filtered]
        event = st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, on_select="rerun", selection_mode="multi-row")
        selected = [filtered[index] for index in event.selection.rows if index < len(filtered)]
        if len(selected) == 2:
            _render_two_run_comparison(selected)
        elif len(selected) > 2:
            st.info("두 결과만 선택하면 공통 지표를 비교합니다.")


def _render_two_run_comparison(selected: list[dict]) -> None:
    section_header("선택 결과 비교")
    a, b = selected
    ma, mb = _metrics_of(a), _metrics_of(b)
    shared = sorted(set(ma) & set(mb))
    if shared:
        _render_delta_table(shared, ma, mb, _label(a), _label(b))
    else:
        st.info("두 결과에 공통 지표가 없습니다.")
    left, right = st.columns(2)
    with left.expander(f"A · {_label(a)} 원본"):
        st.json(a.get("raw") or {})
    with right.expander(f"B · {_label(b)} 원본"):
        st.json(b.get("raw") or {})


def _paired_averages(runs: list[dict], label_a: str, label_b: str) -> tuple[dict, dict, dict]:
    def grouped(label: str) -> dict[str, dict[str, float]]:
        buckets: dict[str, dict[str, list[float]]] = {}
        for run in runs:
            if run.get("agent_label") != label:
                continue
            for name, value in _metrics_of(run).items():
                if name in FIXED_METRICS:
                    buckets.setdefault(run["case_id"], {}).setdefault(name, []).append(value)
        return {case: {name: sum(values) / len(values) for name, values in metrics.items()} for case, metrics in buckets.items()}

    ga, gb = grouped(label_a), grouped(label_b)
    paired: dict[str, list[tuple[str, float, float]]] = {}
    for case_id in sorted(set(ga) & set(gb)):
        for metric in FIXED_METRICS:
            if metric in ga[case_id] and metric in gb[case_id]:
                paired.setdefault(metric, []).append((case_id, ga[case_id][metric], gb[case_id][metric]))
    avg_a = {name: sum(a for _, a, _ in values) / len(values) for name, values in paired.items()}
    avg_b = {name: sum(b for _, _, b in values) / len(values) for name, values in paired.items()}
    return avg_a, avg_b, paired


def _render_version_comparison(runs: list[dict]) -> None:
    with card("version_comparison"):
        section_header("버전 비교", "지표별로 A와 B가 모두 존재하는 동일 case_id만 계산합니다.")
        labels = sorted({r["agent_label"] for r in runs if r.get("agent_label")})
        if len(labels) < 2:
            st.info("서로 다른 agent_label 결과가 두 개 이상 필요합니다.")
            return
        left, right = st.columns(2)
        label_a = left.selectbox("버전 A", labels, index=0)
        label_b = right.selectbox("버전 B", labels, index=min(1, len(labels) - 1))
        if label_a == label_b:
            st.warning("서로 다른 버전을 선택하세요.")
            return
        avg_a, avg_b, paired = _paired_averages(runs, label_a, label_b)
        shared = [name for name in FIXED_METRICS if name in paired]
        if not shared:
            st.info("동일 케이스에서 짝지을 수 있는 공통 지표가 없습니다.")
            return
        st.caption(" · ".join(f"{name}: {len(paired[name])}쌍" for name in shared))
        _render_delta_table(shared, avg_a, avg_b, f"A ({label_a})", f"B ({label_b})")
        _render_export(label_a, label_b)


def _render_delta_table(names: list[str], a: dict, b: dict, label_a: str, label_b: str) -> None:
    rows = []
    for name in names:
        delta = b[name] - a[name]
        rows.append({"지표": name, label_a: round(a[name], 4), label_b: round(b[name], 4), "Δ B-A": round(delta, 4),
                     "변화율": f"{delta / a[name] * 100:+.1f}%" if a[name] else "n/a"})
    frame = pd.DataFrame(rows)
    st.dataframe(frame.style.map(lambda value: "color:#17845b;font-weight:700" if isinstance(value, float) and value > 0 else ("color:#c33d32;font-weight:700" if isinstance(value, float) and value < 0 else ""), subset=["Δ B-A"]), width="stretch", hide_index=True)
    chart_rows = [{"지표": name, "버전": label, "값": values[name]} for name in names for label, values in ((label_a, a), (label_b, b))]
    chart = alt.Chart(pd.DataFrame(chart_rows)).mark_bar().encode(x=alt.X("버전:N", title=None), y=alt.Y("값:Q"), color=alt.Color("버전:N"), column=alt.Column("지표:N", title=None), tooltip=["지표", "버전", "값"]).properties(width=120)
    st.altair_chart(chart, width="content")


def _render_export(label_a: str, label_b: str) -> None:
    if st.button("Excel 보고서 생성"):
        try:
            response = requests.get(f"{BACKEND_URL}/eval/export/comparison-xlsx", params={"label_a": label_a, "label_b": label_b}, timeout=15)
            response.raise_for_status()
            st.session_state["xlsx_export"] = (label_a, label_b, response.content)
        except requests.RequestException as exc:
            st.error(f"Excel 생성 실패: {exc}")
    cached = st.session_state.get("xlsx_export")
    if cached and cached[:2] == (label_a, label_b):
        st.download_button("Excel 내려받기", cached[2], file_name=f"comparison_{label_a}_vs_{label_b}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
