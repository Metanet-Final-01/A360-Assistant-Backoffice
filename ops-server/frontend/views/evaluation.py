from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from config import OPS_BACKEND_URL

FIXED_METRICS = (
    "pm4py_fitness",
    "pm4py_precision",
    "worfbench_precision",
    "worfbench_recall",
    "worfbench_f1_score",
)

# 요청마다 새 TCP 연결을 맺지 않고 재사용한다(keep-alive) — 로컬 벤치마크로
# 확인한 최적화 조합(세션 재사용 + 병렬 호출) 중 하나. docs/local/PERF_OPS_EVAL_PAGE.md 참고.
_SESSION = requests.Session()

# render() 최초 진입 시 이 4개를 병렬로 미리 채워 둔다 — 순차 요청 대비 벤치마크상
# 유의미하게 빠르다. execution/status는 "새로고침 눌러야 최신"이 의도된 동작이라
# 제외(그때그때 fragment 안에서 새로 요청).
_PREFETCH_TARGETS: tuple[tuple[str, str], ...] = (
    ("eval_runs", "/eval/runs"),
    ("eval_datasets", "/eval/datasets"),
    ("eval_execution_options", "/eval/execution/options"),
    ("eval_format_guide", "/eval/format-guide"),
)


def _prefetch_initial_data() -> None:
    missing = [(key, path) for key, path in _PREFETCH_TARGETS if key not in st.session_state]
    if not missing:
        return

    def fetch(item: tuple[str, str]) -> tuple[str, dict | list | None]:
        key, path = item
        try:
            response = _SESSION.get(f"{OPS_BACKEND_URL}{path}", timeout=5)
            response.raise_for_status()
            return key, response.json()
        except (requests.RequestException, ValueError):
            return key, None

    with ThreadPoolExecutor(max_workers=len(missing)) as pool:
        for key, data in pool.map(fetch, missing):
            if data is None:
                continue  # 실패분은 캐싱 안 함 — 아래 각 _load_*()가 순차 재시도하며 에러 메시지를 보여준다.
            if key == "eval_execution_options":
                data = data.get("prediction_labels", [])
            st.session_state[key] = data


def render() -> None:
    page_header(
        "EVALUATION", "평가",
        "데이터셋을 등록하고 pm4py/WorFBench로 채점한 뒤, 같은 화면에서 결과를 조회·비교합니다.",
    )
    _prefetch_initial_data()
    runs = _load_runs()
    datasets = _load_datasets()
    metric_strip([
        ("전체 결과", len(runs)),
        ("평가 버전", len({r.get("agent_label") for r in runs if r.get("agent_label")})),
        ("등록된 데이터셋", len(datasets)),
    ])

    tab_results, tab_run, tab_datasets, tab_ragas = st.tabs(
        ["결과 조회 · 비교", "평가 실행", "데이터셋 관리", "RAG 품질(RAGAS)"]
    )
    with tab_results:
        _render_results_tab(runs)
    with tab_run:
        _render_execution_tab(datasets)
    with tab_datasets:
        _render_datasets_tab(datasets)
    with tab_ragas:
        _render_ragas_tab(runs)


# st.fragment로 탭별 재실행을 분리한다 — 이게 없으면 위젯 하나만 건드려도(예: 결과
# 탭의 필터 입력) 페이지 스크립트 전체가 다시 실행되면서, 다른 탭의 네트워크 호출
# (execution/options, execution/status, format-guide)까지 매번 다시 쏘게 되어 눈에
# 띄게 느려진다. 각 탭 안의 위젯 상호작용은 이제 그 탭의 fragment만 재실행한다.
@st.fragment
def _render_results_tab(runs: list[dict]) -> None:
    _render_runs(runs)
    _render_version_comparison(runs)


@st.fragment
def _render_execution_tab(datasets: list[dict]) -> None:
    _render_evaluation_execution(datasets)
    _render_format_guide()


@st.fragment
def _render_datasets_tab(datasets: list[dict]) -> None:
    _render_dataset_registry(datasets)


@st.fragment
def _render_ragas_tab(runs: list[dict]) -> None:
    _render_ragas_execution()
    _render_ragas_results(runs)


# ── 결과 조회 · 비교 (구 eval_results.py) ──────────────────────────────


def _load_runs() -> list[dict]:
    if st.button("결과 새로고침", type="secondary"):
        st.session_state.pop("eval_runs", None)
    if "eval_runs" not in st.session_state:
        try:
            response = _SESSION.get(f"{OPS_BACKEND_URL}/eval/runs", timeout=5)
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
            st.info("저장된 평가 결과가 없습니다. ‘평가 실행’ 탭에서 먼저 평가를 실행하세요.")
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
        event = st.dataframe(
            pd.DataFrame(rows), width="stretch", hide_index=True, on_select="rerun", selection_mode="multi-row",
            key=f"eval_runs_table_{case_filter}_{source_filter}_{agent_filter}_{dataset_filter}",
        )
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


def _render_export(label_a: str, label_b: str) -> None:
    if st.button("Excel 보고서 생성"):
        try:
            response = _SESSION.get(f"{OPS_BACKEND_URL}/eval/export/comparison-xlsx", params={"label_a": label_a, "label_b": label_b}, timeout=15)
            response.raise_for_status()
            st.session_state["xlsx_export"] = (label_a, label_b, response.content)
        except requests.RequestException as exc:
            st.error(f"Excel 생성 실패: {exc}")
    cached = st.session_state.get("xlsx_export")
    if cached and cached[:2] == (label_a, label_b):
        st.download_button("Excel 내려받기", cached[2], file_name=f"comparison_{label_a}_vs_{label_b}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── 평가 실행 (구 eval_setup.py) ──────────────────────────────────────


def _load_datasets() -> list[dict]:
    if "eval_datasets" not in st.session_state:
        try:
            response = _SESSION.get(f"{OPS_BACKEND_URL}/eval/datasets", timeout=5)
            response.raise_for_status()
            st.session_state["eval_datasets"] = response.json()
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"데이터셋 목록을 불러오지 못했습니다: {exc}")
            st.session_state["eval_datasets"] = []
    return st.session_state["eval_datasets"]


def _load_execution_options() -> list[str] | None:
    if "eval_execution_options" not in st.session_state:
        try:
            response = _SESSION.get(f"{OPS_BACKEND_URL}/eval/execution/options", timeout=5)
            response.raise_for_status()
            st.session_state["eval_execution_options"] = response.json().get("prediction_labels", [])
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"평가 입력 목록을 불러오지 못했습니다: {exc}")
            return None
    return st.session_state["eval_execution_options"]


def _render_evaluation_execution(datasets: list[dict]) -> None:
    with card("evaluation_execution"):
        section_header("평가 실행", "pm4py와 WorFBench를 순서대로 실행하고 선택한 데이터셋 결과를 자동 저장합니다.")
        if not datasets:
            st.info("먼저 ‘데이터셋 관리’ 탭에서 평가 데이터셋을 등록하세요.")
            return
        prediction_labels = _load_execution_options()
        if prediction_labels is None:
            return
        if not prediction_labels:
            st.info("a360-eval-sandbox/Metadata에 predictions_from_agent_<label>.json 파일이 없습니다.")
            return

        dataset_options = {f"{item['name']} · {item['dataset_id']}@{item['version']}": item for item in datasets}
        with st.form("execution_form"):
            selected_dataset_name = st.selectbox("평가 데이터셋", list(dataset_options), key="execute_dataset")
            prediction_label = st.selectbox("예측 입력", prediction_labels, format_func=lambda value: f"predictions_from_agent_{value}.json")
            cols = st.columns(3)
            evaluation_id = cols[0].text_input("evaluation_id", placeholder="eval-2026-07-11-v2", key="execute_id")
            agent_label = cols[1].text_input("결과 버전", value=prediction_label, key="execute_agent")
            commit_sha = cols[2].text_input("commit SHA", placeholder="선택", key="execute_commit")
            start = st.form_submit_button("평가 시작", type="primary", use_container_width=True)

        if start:
            dataset = dataset_options[selected_dataset_name]
            payload = {
                "prediction_label": prediction_label,
                "evaluation_id": evaluation_id,
                "dataset_id": dataset["dataset_id"],
                "dataset_version": dataset["version"],
                "agent_label": agent_label,
                "commit_sha": commit_sha or None,
            }
            try:
                response = _SESSION.post(f"{OPS_BACKEND_URL}/eval/execution", json=payload, timeout=5)
                if response.status_code == 200:
                    st.success("평가를 시작했습니다. 아래 상태 새로고침으로 진행 상황을 확인하세요.")
                else:
                    st.error(response.json().get("detail", response.text))
            except (requests.RequestException, ValueError) as exc:
                st.error(f"평가 시작 실패: {exc}")

        if st.button("평가 상태 새로고침", key="execution_refresh"):
            st.session_state.pop("eval_execution_status", None)
        try:
            status_response = _SESSION.get(f"{OPS_BACKEND_URL}/eval/execution/status", timeout=5)
            status_response.raise_for_status()
            status = status_response.json()
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"평가 상태를 불러오지 못했습니다: {exc}")
            return

        if status.get("running"):
            stage_labels = {"pm4py": "pm4py 채점", "worfbench": "WorFBench 채점", "saving": "결과 저장"}
            st.info(f"실행 중 · {stage_labels.get(status.get('stage'), status.get('stage'))}")
        elif status.get("returncode") == 0:
            st.success(f"평가 완료 · 결과 {status.get('saved', 0)}건 저장")
        elif status.get("returncode"):
            st.error(f"평가 실패: {status.get('error')}")
        else:
            st.caption("아직 실행한 평가가 없습니다.")
        if status.get("log"):
            with st.expander("평가 로그"):
                st.code(status["log"][-8000:], language="text")


def _load_format_guide() -> dict | None:
    if "eval_format_guide" not in st.session_state:
        try:
            response = _SESSION.get(f"{OPS_BACKEND_URL}/eval/format-guide", timeout=5)
            response.raise_for_status()
            st.session_state["eval_format_guide"] = response.json()
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"포맷 안내를 불러오지 못했습니다: {exc}")
            return None
    return st.session_state["eval_format_guide"]


def _render_format_guide() -> None:
    with card("format_guide"):
        section_header("채점 입력·출력 형식", "외부 채점 결과를 저장할 때는 아래 원본 형식을 유지합니다.")
        guide = _load_format_guide()
        if guide is None:
            return
        tabs = st.tabs(["pm4py", "WorFBench"])
        for tab, engine in zip(tabs, ("pm4py", "worfbench")):
            with tab:
                section = guide[engine]
                st.write(section["summary"])
                left, right = st.columns(2)
                with left:
                    st.caption(section["input_example"]["note"])
                    st.json(section["input_example"]["value"], expanded=False)
                with right:
                    st.caption(section["output_example"]["note"])
                    st.json(section["output_example"]["value"], expanded=False)


# ── 데이터셋 관리 (구 eval_setup.py) ──────────────────────────────────


def _render_dataset_registry(datasets: list[dict]) -> None:
    with card("dataset_registry"):
        section_header("평가 데이터셋", "한 줄에 하나씩 case_id를 입력해 재현 가능한 평가 범위를 고정합니다.")
        if datasets:
            st.dataframe([{"dataset_id": d["dataset_id"], "이름": d["name"], "버전": d["version"], "케이스 수": len(d["case_ids"]), "설명": d.get("description") or ""} for d in datasets], width="stretch", hide_index=True)
        with st.expander("새 데이터셋 등록", expanded=not datasets):
            with st.form("dataset_form"):
                col1, col2, col3 = st.columns(3)
                dataset_id = col1.text_input("dataset_id", placeholder="workflow-goldset")
                name = col2.text_input("표시 이름", placeholder="워크플로우 골드셋")
                version = col3.text_input("버전", placeholder="2026.07")
                description = st.text_input("설명", placeholder="평가 범위와 변경 이유")
                case_text = st.text_area("case_id 목록", placeholder="web_excel_email_001\ninvoice_processing_001", height=150)
                submitted = st.form_submit_button("데이터셋 등록", type="primary")
            if submitted:
                payload = {"dataset_id": dataset_id, "name": name, "version": version, "description": description or None,
                           "case_ids": [line.strip() for line in case_text.splitlines() if line.strip()]}
                try:
                    response = _SESSION.post(f"{OPS_BACKEND_URL}/eval/datasets", json=payload, timeout=5)
                    if response.status_code == 200:
                        st.session_state.pop("eval_datasets", None)
                        st.success("데이터셋을 등록했습니다.")
                        st.rerun()
                    else:
                        st.error(response.json().get("detail", response.text))
                except (requests.RequestException, ValueError) as exc:
                    st.error(f"등록 실패: {exc}")


# ── RAG 품질(RAGAS) ─────────────────────────────────────────────────

_RAGAS_METRICS = ("ragas_faithfulness", "ragas_answer_relevancy", "ragas_context_precision", "ragas_context_recall")


def _render_ragas_execution() -> None:
    with card("ragas_execution"):
        section_header(
            "RAG 검색 품질 평가 실행",
            "실제 색인 문서 기반 골드셋(10개)으로 Backend RAG 검색 → 답변 생성 → RAGAS 4개 지표(faithfulness/answer_relevancy/context_precision/context_recall) 채점.",
        )
        try:
            cases_resp = _SESSION.get(f"{OPS_BACKEND_URL}/eval/ragas/cases", timeout=5)
            cases_resp.raise_for_status()
            n_cases = len(cases_resp.json())
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"골드셋을 불러오지 못했습니다: {exc}")
            n_cases = 0
        st.caption(f"골드셋 케이스 {n_cases}개")

        with st.form("ragas_execution_form"):
            agent_label = st.text_input("결과 버전(agent_label)", value="rag-default", key="ragas_agent_label")
            start = st.form_submit_button("RAGAS 평가 시작", type="primary")
        if start:
            try:
                resp = _SESSION.post(
                    f"{OPS_BACKEND_URL}/eval/ragas/execution", json={"agent_label": agent_label.strip() or "rag-default"}, timeout=5,
                )
                if resp.status_code == 200:
                    st.success("RAGAS 평가를 시작했습니다 — OpenAI 호출이 여러 번 나가서 1~2분 걸립니다. 아래 새로고침으로 확인하세요.")
                else:
                    st.error(resp.json().get("detail", resp.text))
            except (requests.RequestException, ValueError) as exc:
                st.error(f"평가 시작 실패: {exc}")

        if st.button("RAGAS 상태 새로고침", key="ragas_status_refresh"):
            st.session_state.pop("ragas_status_cache", None)
        try:
            status_resp = _SESSION.get(f"{OPS_BACKEND_URL}/eval/ragas/execution/status", timeout=5)
            status_resp.raise_for_status()
            status = status_resp.json()
        except (requests.RequestException, ValueError) as exc:
            st.warning(f"상태를 불러오지 못했습니다: {exc}")
            return

        if status.get("running"):
            st.info("실행 중...")
        elif status.get("error"):
            st.error(f"평가 실패: {status['error']}")
        elif status.get("finished_at"):
            st.success(f"평가 완료 · {status.get('saved', 0)}/{status.get('cases', 0)}건 저장")
        else:
            st.caption("아직 실행한 RAGAS 평가가 없습니다.")


def _render_ragas_results(runs: list[dict]) -> None:
    with card("ragas_results"):
        section_header("RAG 품질 결과", "버전(agent_label)별 평균 — 지표는 전부 0~1, 높을수록 좋음.")
        ragas_runs = [r for r in runs if r.get("source") == "ragas"]
        if not ragas_runs:
            st.info("아직 RAGAS 결과가 없습니다 — 위에서 평가를 실행하세요.")
            return

        labels = sorted({r["agent_label"] for r in ragas_runs if r.get("agent_label")})
        rows = []
        for label in labels:
            label_runs = [r for r in ragas_runs if r.get("agent_label") == label]
            row = {"버전": label, "케이스 수": len(label_runs)}
            for metric_name in _RAGAS_METRICS:
                values = [m["value"] for r in label_runs for m in r.get("metrics", []) if m["name"] == metric_name]
                row[metric_name] = round(sum(values) / len(values), 3) if values else None
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        with st.expander("케이스별 원본 보기"):
            case_rows = []
            for r in ragas_runs:
                metrics_by_name = {m["name"]: m["value"] for m in r.get("metrics", [])}
                case_rows.append({
                    "case_id": r["case_id"], "버전": r.get("agent_label") or "-",
                    **{name: round(metrics_by_name.get(name, 0), 3) if name in metrics_by_name else None for name in _RAGAS_METRICS},
                    "질문": (r.get("raw") or {}).get("question", ""),
                })
            st.dataframe(pd.DataFrame(case_rows), width="stretch", hide_index=True)
