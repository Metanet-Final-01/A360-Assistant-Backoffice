"""부하테스트(k6) 결과 이력·추세.

처음엔 Ops가 k6를 대신 실행해주는 버튼을 만들었는데, 터미널에서 `k6 run`을 직접
치는 것보다 Ops UI 폼을 거치는 게 오히려 더 번거롭다는 판단으로 방향을 바꿨다 — k6는
로컬에서 그대로 CLI로 돌리고(scripts/loadtest.js), 끝나면 handleSummary()가 결과를
자동으로 Ops(POST /loadtest/upload)에 전송한다. 이 화면은 그렇게 쌓인 이력을
시간순 추세로 보여주는 것만 담당한다 — 다른 관측 페이지와 달리 여기는 "여러 실행
간 추세 비교"가 목적이라 표보다 차트를 앞세운다.
"""

import altair as alt
import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from config import OPS_BACKEND_URL

_SESSION = requests.Session()

_CLI_EXAMPLE = """k6 run ops-server/backend/app/loadtest/scripts/loadtest.js \\
  -e TARGET_URL="http://127.0.0.1:8000/api/rag/search?q=엑셀" \\
  -e LABEL="rag-search" \\
  -e PEAK_VUS=20
  # POST를 테스트하려면: -e METHOD=POST -e BODY='{"message":"..."}'
  # Ops가 이 기기가 아닌 곳에서 돈다면: -e OPS_URL="http://<ops-host>:8100" """


def render() -> None:
    page_header(
        "부하테스트",
        "k6는 로컬에서 CLI로 직접 실행합니다 — 끝나면 결과가 자동으로 여기에 쌓입니다.",
    )
    _render_cli_guide()
    _render_history()


def _render_cli_guide() -> None:
    with card("loadtest_guide"):
        section_header("실행 방법", "GET/POST 아무 엔드포인트나 대상으로 쓸 수 있습니다. 5%→20%→60%→100% VU로 약 2분간 단계적 부하를 겁니다.")
        st.code(_CLI_EXAMPLE, language="bash")
        st.caption(
            "k6가 로컬에 설치돼 있어야 합니다(k6.io/docs/get-started/installation). "
            "실행이 끝나면 scripts/loadtest.js의 handleSummary()가 결과를 이 Ops 백엔드로 자동 전송합니다 "
            "— 별도로 결과를 복사해 붙여넣을 필요가 없습니다."
        )


def _fetch_runs() -> list[dict]:
    if "loadtest_runs" not in st.session_state:
        try:
            resp = _SESSION.get(f"{OPS_BACKEND_URL}/loadtest/runs", timeout=5)
            resp.raise_for_status()
            st.session_state["loadtest_runs"] = resp.json()
        except (requests.RequestException, ValueError) as exc:
            st.error(f"이력을 불러오지 못했습니다: {exc}")
            st.session_state["loadtest_runs"] = []
    return st.session_state["loadtest_runs"]


def _render_history() -> None:
    with card("loadtest_history"):
        section_header("실행 이력 · 추세")
        if st.button("새로고침", key="loadtest_refresh", type="primary"):
            st.session_state.pop("loadtest_runs", None)
        runs = _fetch_runs()
        if not runs:
            st.info("아직 업로드된 실행 결과가 없습니다 — 위 명령으로 먼저 한 번 돌려보세요.")
            return

        df = pd.DataFrame(runs)
        df["created_at"] = pd.to_datetime(df["created_at"])
        df = df.sort_values("created_at")

        labels = sorted(df["label"].unique().tolist())
        selected_labels = st.multiselect("라벨 필터", labels, default=labels)
        view = df[df["label"].isin(selected_labels)] if selected_labels else df
        if view.empty:
            st.info("선택한 라벨의 결과가 없습니다.")
            return

        latest = view.iloc[-1]
        metric_strip([
            ("최근 p95", f"{latest['p95_ms']:.0f} ms"),
            ("최근 평균", f"{latest['avg_ms']:.0f} ms"),
            ("최근 처리량", f"{latest['throughput_rps']:.2f} req/s"),
            ("최근 에러율", f"{latest['error_rate'] * 100:.1f}%"),
        ])

        long_df = view.melt(
            id_vars=["created_at", "label", "peak_vus"],
            value_vars=["avg_ms", "p50_ms", "p90_ms", "p95_ms", "max_ms"],
            var_name="지표", value_name="ms",
        )
        latency_chart = (
            alt.Chart(long_df)
            .mark_line(point=True)
            .encode(
                x=alt.X("created_at:T", title="실행 시각"),
                y=alt.Y("ms:Q", title="지연시간(ms)"),
                color=alt.Color("지표:N"),
                strokeDash=alt.StrokeDash("label:N"),
                tooltip=["created_at:T", "label:N", "지표:N", "ms:Q", "peak_vus:Q"],
            )
            .properties(height=280)
        )
        st.altair_chart(latency_chart, width="stretch")

        throughput_chart = (
            alt.Chart(view)
            .mark_bar()
            .encode(
                x=alt.X("created_at:T", title="실행 시각"),
                y=alt.Y("throughput_rps:Q", title="처리량(req/s)"),
                color=alt.Color("label:N"),
                tooltip=["created_at:T", "label:N", "throughput_rps:Q", "error_rate:Q", "peak_vus:Q"],
            )
            .properties(height=200)
        )
        st.altair_chart(throughput_chart, width="stretch")

        st.dataframe(
            view[["created_at", "label", "method", "target_url", "peak_vus", "avg_ms", "p50_ms", "p95_ms", "max_ms", "throughput_rps", "error_rate"]]
            .sort_values("created_at", ascending=False),
            width="stretch", hide_index=True,
        )
