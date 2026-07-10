import streamlit as st

from components.sidebar import render_sidebar
from views import eval_results, home, rag_ingest

st.set_page_config(page_title="A360 Assistant Ops", layout="wide")

render_sidebar()

pages = [
    st.Page(home.render, title="홈", url_path="home", default=True),
    st.Page(rag_ingest.render, title="RAG 데이터 적재", url_path="rag-ingest"),
    st.Page(eval_results.render, title="평가 결과", url_path="eval-results"),
]

st.navigation(pages, position="sidebar").run()
