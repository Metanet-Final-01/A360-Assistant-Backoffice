import streamlit as st

from components.layout import apply_global_styles
from components.sidebar import render_sidebar
from views import (
    assurance_records,
    cost_report,
    datasets,
    evaluation,
    home,
    loadtest,
    log_eda,
    monitoring_logs,
    rag_ingest,
    runtime_settings,
    trace,
)

st.set_page_config(page_title="A360 Assistant Ops", layout="wide")

apply_global_styles()
render_sidebar()

pages = [
    st.Page(home.render, title="홈", icon=":material/home:", default=True),
    st.Page(rag_ingest.render, title="RAG 데이터 적재", url_path="rag-ingest", icon=":material/database:"),
    st.Page(evaluation.render, title="평가", url_path="evaluation", icon=":material/assessment:"),
    st.Page(datasets.render, title="데이터셋 관리", url_path="datasets", icon=":material/folder_managed:"),
    st.Page(monitoring_logs.render, title="모니터링 로그", url_path="monitoring-logs", icon=":material/monitoring:"),
    st.Page(assurance_records.render, title="AI 출력 검증 기록", url_path="assurance-records", icon=":material/verified_user:"),
    st.Page(trace.render, title="사건 추적", url_path="trace", icon=":material/search:"),
    st.Page(cost_report.render, title="비용 리포트", url_path="cost-report", icon=":material/payments:"),
    st.Page(loadtest.render, title="부하테스트", url_path="loadtest", icon=":material/bolt:"),
    st.Page(log_eda.render, title="로그 탐색(EDA)", url_path="log-eda", icon=":material/biotech:"),
    st.Page(runtime_settings.render, title="런타임 설정", url_path="runtime-settings", icon=":material/tune:"),
]

st.navigation(pages, position="sidebar").run()
