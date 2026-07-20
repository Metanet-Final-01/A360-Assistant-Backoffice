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
    ragas_datasets,
    runtime_settings,
    trace,
)

st.set_page_config(page_title="A360 Assistant Ops", layout="wide")

home_page = st.Page(home.render, title="홈", icon=":material/home:", default=True)

# 사이드바에 카테고리별로 묶어 접었다 펼 수 있게 보여주려고, st.navigation의 기본 목록 UI
# 대신(position="hidden") components.sidebar.render_sidebar()가 st.expander + st.page_link로
# 직접 그린다. 아래 목록은 그 그룹핑 순서·구성의 기준이 된다.
nav_sections = [
    ("RAG · 평가", [
        st.Page(rag_ingest.render, title="RAG 데이터 적재", url_path="rag-ingest", icon=":material/database:"),
        st.Page(evaluation.render, title="평가", url_path="evaluation", icon=":material/assessment:"),
        st.Page(datasets.render, title="데이터셋 관리", url_path="datasets", icon=":material/folder_managed:"),
        st.Page(ragas_datasets.render, title="RAGAS 데이터셋", url_path="ragas-datasets", icon=":material/description:"),
    ]),
    ("로그 · 추적", [
        st.Page(monitoring_logs.render, title="모니터링 로그", url_path="monitoring-logs", icon=":material/monitoring:"),
        st.Page(assurance_records.render, title="AI 보증 판정 기록", url_path="assurance-records", icon=":material/verified_user:"),
        st.Page(trace.render, title="사건 추적", url_path="trace", icon=":material/search:"),
        st.Page(log_eda.render, title="로그 탐색(EDA)", url_path="log-eda", icon=":material/biotech:"),
    ]),
    ("운영", [
        st.Page(cost_report.render, title="비용 리포트", url_path="cost-report", icon=":material/payments:"),
        st.Page(loadtest.render, title="부하테스트", url_path="loadtest", icon=":material/bolt:"),
        st.Page(runtime_settings.render, title="런타임 설정", url_path="runtime-settings", icon=":material/tune:"),
    ]),
]
all_pages = [home_page] + [page for _, pages in nav_sections for page in pages]

apply_global_styles()
current_page = st.navigation(all_pages, position="hidden")
render_sidebar(home_page, nav_sections, current_page)

current_page.run()
