"""로그 EDA — 수집된 관측 로그를 직접 필터링·탐색하는 화면.

2026-07-12 밤에 로컬 스크립트(pandas)로 수동으로 했던 EDA(감사 로그 상태코드 분포,
RAG 검색 지연시간, 롤업 엔드포인트별 성능 등)를 반복 가능한 화면으로 만든다. 다른
관측 페이지(monitoring_logs.py)는 "운영자 조작판" 성격이라 표 위주로 두지만, 여기는
탐색·분석이 목적이라 필터·차트를 적극적으로 쓴다.

고정된 뷰가 아니라 자유 탐색형 — 컬럼 dtype에 따라 필터를 자동 생성한다(범주형은
multiselect, 숫자는 범위 슬라이더, 시각 컬럼은 날짜 범위). 소스가 늘어도 이 로직은
그대로 재사용된다.
"""

import pandas as pd
import altair as alt
import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import OPS_BACKEND_URL

_SESSION = requests.Session()

# (표시 이름, API 경로, limit 파라미터명, 기본 limit, "raw" 평탄화 필요 여부)
_SOURCES = {
    "감사 로그 (audit_logs)": ("audit-logs", "limit", 2000, False),
    "RAG 요청 로그 (rag_logs)": ("rag-logs", "limit", 2000, True),
    "요청 성능 롤업 (metrics_daily)": ("metrics-daily", "limit", 2000, False),
    "LLM 사용량 롤업 (usage_daily)": ("usage-daily", "limit", 2000, False),
    "에이전트 턴 (turn_events)": ("turn-events", "limit", 2000, False),
}

# EDA 대상에서 뺄 컬럼 — 값 자체가 길거나(원문 텍스트) 카디널리티가 높아 필터/차트에 안 맞음.
_EXCLUDE_COLS = {"detail", "message", "raw"}
_MAX_CATEGORY_UNIQUES = 30


def render() -> None:
    page_header("EDA", "로그 탐색", "관측 로그를 직접 필터링·집계해봅니다 — 고정된 대시보드가 아니라 자유 탐색용입니다.")

    with card("eda_source"):
        section_header("데이터 소스")
        source_label = st.selectbox("소스", list(_SOURCES))
        limit = st.number_input("최대 조회 건수", min_value=100, max_value=10000, value=2000, step=100)
        if st.button("불러오기", type="primary") or f"eda_df_{source_label}" not in st.session_state:
            df = _load(source_label, limit)
            st.session_state[f"eda_df_{source_label}"] = df

    df = st.session_state.get(f"eda_df_{source_label}")
    if df is None or df.empty:
        st.info("데이터가 없습니다 — 위에서 '불러오기'를 눌러주세요.")
        return

    with card("eda_filter"):
        section_header(f"필터 ({len(df)}건 로드됨)")
        view = _apply_filters(df)
        st.caption(f"필터 적용 후 {len(view)}건")
        st.dataframe(view, width="stretch", hide_index=True, height=320)

    with card("eda_chart"):
        section_header("차트")
        _render_chart(view)


def _load(source_label: str, limit: int) -> pd.DataFrame:
    path, limit_param, _, flatten_raw = _SOURCES[source_label]
    try:
        resp = _SESSION.get(f"{OPS_BACKEND_URL}/observability/{path}", params={limit_param: limit}, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
    except (requests.RequestException, ValueError) as exc:
        st.error(f"불러오기 실패: {exc}")
        return pd.DataFrame()
    if not isinstance(rows, list):
        st.error(f"예상치 못한 응답 형식입니다: {rows}")
        return pd.DataFrame()
    if flatten_raw:
        rows = [{**r.get("raw", {}), "fetched_at": r.get("fetched_at")} for r in rows]
    df = pd.DataFrame(rows)
    for col in df.columns:
        if col.endswith(("_at", "_day")) or col == "day":
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    view = df.copy()
    cols = [c for c in df.columns if c not in _EXCLUDE_COLS]
    with st.expander("필터 조건", expanded=True):
        for col in cols:
            series = df[col]
            if pd.api.types.is_datetime64_any_dtype(series):
                valid = series.dropna()
                if valid.empty:
                    continue
                lo, hi = valid.min().date(), valid.max().date()
                if lo == hi:
                    continue
                date_range = st.date_input(f"{col} 범위", value=(lo, hi), min_value=lo, max_value=hi, key=f"eda_date_{col}")
                # 사용자가 range 입력 중 아직 두 번째 날짜를 안 고르면 date_input이 길이 1
                # 튜플을 반환한다 — 그대로 언패킹하면 ValueError가 난다.
                if not isinstance(date_range, tuple) or len(date_range) != 2:
                    continue
                start, end = date_range
                mask = (series.dt.date >= start) & (series.dt.date <= end)
                view = view[mask.reindex(view.index, fill_value=True)]
            elif pd.api.types.is_numeric_dtype(series):
                valid = series.dropna()
                if valid.empty or valid.min() == valid.max():
                    continue
                lo, hi = float(valid.min()), float(valid.max())
                selected = st.slider(col, min_value=lo, max_value=hi, value=(lo, hi), key=f"eda_num_{col}")
                mask = series.between(selected[0], selected[1])
                view = view[mask.reindex(view.index, fill_value=True)]
            else:
                uniques = series.dropna().unique().tolist()
                if not uniques or len(uniques) > _MAX_CATEGORY_UNIQUES:
                    continue  # 카디널리티 높은 텍스트 컬럼(예: request_id)은 필터 대상에서 제외
                selected = st.multiselect(col, sorted(uniques, key=str), default=sorted(uniques, key=str), key=f"eda_cat_{col}")
                view = view[series.isin(selected)]
    return view


def _render_chart(view: pd.DataFrame) -> None:
    if view.empty:
        st.info("필터 결과가 없어 차트를 그릴 수 없습니다.")
        return
    numeric_cols = [c for c in view.columns if pd.api.types.is_numeric_dtype(view[c])]
    category_cols = [c for c in view.columns if c not in _EXCLUDE_COLS and not pd.api.types.is_numeric_dtype(view[c])]
    if not category_cols:
        st.info("집계 기준(X축)으로 쓸 범주형/시각 컬럼이 없습니다.")
        return

    cols = st.columns(3)
    x_col = cols[0].selectbox("X축(집계 기준)", category_cols)
    agg = cols[1].selectbox("집계", ["건수"] + numeric_cols)
    chart_type = cols[2].selectbox("차트 종류", ["막대", "선"])

    if agg == "건수":
        grouped = view.groupby(x_col, dropna=False).size().reset_index(name="값")
        y_field = "값"
    else:
        grouped = view.groupby(x_col, dropna=False)[agg].mean().reset_index()
        y_field = agg

    is_datetime = pd.api.types.is_datetime64_any_dtype(view[x_col])
    x_enc = alt.X(f"{x_col}:T" if is_datetime else f"{x_col}:N", title=x_col, sort=None if is_datetime else "-y")
    mark = alt.Chart(grouped).mark_bar() if chart_type == "막대" else alt.Chart(grouped).mark_line(point=True)
    chart = mark.encode(x=x_enc, y=alt.Y(f"{y_field}:Q"), tooltip=[x_col, y_field]).properties(height=320)
    st.altair_chart(chart, width="stretch")
