import streamlit as st

from components.layout import apply_global_styles
from components.sidebar import render_sidebar
from views import (
    cost_report,
    evaluation,
    home,
    loadtest,
    log_eda,
    monitoring_logs,
    rag_ingest,
    trace,
)

st.set_page_config(page_title="A360 Assistant Ops", layout="wide")

apply_global_styles()
render_sidebar()

pages = [
    st.Page(home.render, title="홈", url_path="home", default=True),
    st.Page(rag_ingest.render, title="RAG 데이터 적재", url_path="rag-ingest"),
    st.Page(evaluation.render, title="평가", url_path="evaluation"),
    st.Page(monitoring_logs.render, title="모니터링 로그", url_path="monitoring-logs"),
    st.Page(trace.render, title="사건 추적", url_path="trace"),
    st.Page(cost_report.render, title="비용 리포트", url_path="cost-report"),
    st.Page(loadtest.render, title="부하테스트", url_path="loadtest"),
    st.Page(log_eda.render, title="로그 탐색(EDA)", url_path="log-eda"),
]

st.navigation(pages, position="sidebar").run()
