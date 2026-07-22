import streamlit as st

from components.auth import is_authenticated, render_login_screen
from components.layout import apply_global_styles
from components.sidebar import render_sidebar
from views import (
    action_rules,
    cost_report,
    home,
    loadtest,
    log_eda,
    monitoring_logs,
    rag_ingest,
    ragas_datasets,
    ragas_evaluation,
    runtime_settings,
    trace,
    workflow_evaluation,
    workflow_goldset_pipeline,
)

st.set_page_config(page_title="A360 Assistant Ops", layout="wide")

if not is_authenticated():
    apply_global_styles()
    render_login_screen()
    st.stop()

apply_global_styles()
render_sidebar()

pages = [
    st.Page(home.render, title="홈", icon=":material/home:", default=True),
    st.Page(rag_ingest.render, title="RAG 데이터 적재", url_path="rag-ingest", icon=":material/database:"),
    st.Page(ragas_datasets.render, title="RAGAS 데이터셋", url_path="ragas-datasets", icon=":material/description:"),
    st.Page(ragas_evaluation.render, title="RAGAS 평가", url_path="ragas-evaluation", icon=":material/online_prediction:"),
    st.Page(workflow_evaluation.render, title="Workflow 평가", url_path="workflow-evaluation", icon=":material/assessment:"),
    st.Page(
        workflow_goldset_pipeline.render, title="Workflow 정답셋", url_path="workflow-goldset-pipeline",
        icon=":material/account_tree:",
    ),
    st.Page(action_rules.render, title="액션 동치 규칙", url_path="action-rules", icon=":material/rule:"),
    st.Page(monitoring_logs.render, title="모니터링 로그", url_path="monitoring-logs", icon=":material/monitoring:"),
    st.Page(trace.render, title="사건 추적", url_path="trace", icon=":material/search:"),
    st.Page(cost_report.render, title="비용 리포트", url_path="cost-report", icon=":material/payments:"),
    st.Page(loadtest.render, title="부하테스트", url_path="loadtest", icon=":material/bolt:"),
    st.Page(log_eda.render, title="로그 탐색(EDA)", url_path="log-eda", icon=":material/biotech:"),
    st.Page(runtime_settings.render, title="런타임 설정", url_path="runtime-settings", icon=":material/tune:"),
]

# expanded=True: 기본값(False)이면 페이지가 많을 때 Streamlit이 사이드바 목록을
# "더보기/접기" 토글로 줄여버린다 — 전체 목록이 항상 다 보이게 강제로 펼쳐둔다.
st.navigation(pages, position="sidebar", expanded=True).run()
