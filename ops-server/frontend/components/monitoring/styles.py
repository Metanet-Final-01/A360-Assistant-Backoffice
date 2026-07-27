import streamlit as st

_CSS = """
<style>
.obs-card {
    background: #ffffff;
    border: 1px solid #e4e7ec;
    border-radius: 16px;
    padding: 18px 20px;
}
.obs-card__title { font-size: 0.98rem; font-weight: 800; color: #172026; }
.obs-card__subtitle { font-size: 0.76rem; color: #8a94a0; margin-top: 2px; }
.obs-card__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 14px;
}
.obs-legend { display: flex; gap: 14px; align-items: center; }
.obs-legend__item { display: flex; align-items: center; gap: 6px; font-size: 0.76rem; color: #667085; font-weight: 600; }
.obs-legend__dot { width: 8px; height: 8px; border-radius: 999px; display: inline-block; }

/* KPI 카드 */
.obs-kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 0 0 1.1rem; }
@media (max-width: 900px) { .obs-kpi-row { grid-template-columns: repeat(2, 1fr); } }
.obs-kpi-card__top { display: flex; align-items: center; justify-content: space-between; }
.obs-kpi-card__label { color: #667085; font-size: 0.82rem; font-weight: 700; }
.obs-kpi-card__icon { display: inline-flex; opacity: 0.9; }
.obs-kpi-card__value {
    font-family: "Consolas", "SFMono-Regular", Menlo, monospace;
    font-variant-numeric: tabular-nums;
    font-size: 1.8rem;
    font-weight: 800;
    color: #172026;
    margin-top: 8px;
}
.obs-kpi-card__unit { font-size: 1rem; font-weight: 700; color: #8a94a0; margin-left: 3px; }
.obs-kpi-card__sub { font-size: 0.76rem; color: #8a94a0; margin-top: 4px; }

/* 상태 코드 분포 — 개별 코드별 미터 */
.obs-meter { margin-bottom: 12px; }
.obs-meter:last-child { margin-bottom: 0; }
.obs-meter__top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-family: "Consolas", "SFMono-Regular", Menlo, monospace;
    font-size: 0.86rem;
    margin-bottom: 5px;
}
.obs-meter__code { font-weight: 800; color: #172026; }
.obs-meter__stat { color: #8a94a0; font-size: 0.78rem; }
.obs-meter__track { background: #eef1f4; border-radius: 999px; height: 6px; overflow: hidden; }
.obs-meter__fill { height: 100%; border-radius: 999px; }

/* 지연 통계 박스 */
.obs-latency-row { display: flex; gap: 10px; }
.obs-latency-item { flex: 1; background: #fafbfc; border: 1px solid #e4e7ec; border-radius: 12px; padding: 12px; text-align: center; }
.obs-latency-item__label { color: #8a94a0; font-size: 0.74rem; font-weight: 700; }
.obs-latency-item__value {
    font-family: "Consolas", "SFMono-Regular", Menlo, monospace;
    font-variant-numeric: tabular-nums;
    font-size: 1.2rem;
    font-weight: 800;
    color: #172026;
    margin-top: 3px;
}

/* 헤더/컨트롤 행 필 버튼 — Streamlit이 위젯 컨테이너에 붙여주는 st-key-<key> 클래스로
target한다(버튼 자체엔 class를 직접 못 준다). 버튼은 그라데이션보다 단색이 더 깔끔해 보여서
section_header()와 같은 계열의 단색(--brand-teal, layout.py)만 쓴다. */
.obs-pill-btn button { border-radius: 999px !important; font-weight: 700 !important; }
div[class*="st-key-obs_deploy_btn"] button,
div[class*="st-key-obs_refresh_btn"] button {
    background: var(--brand-teal) !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 999px !important;
    font-weight: 700 !important;
}
div[class*="st-key-obs_deploy_btn"] button:hover,
div[class*="st-key-obs_refresh_btn"] button:hover {
    filter: brightness(1.12);
    color: #ffffff !important;
}
div[class*="st-key-obs_live_btn"] button,
div[class*="st-key-obs_kebab_btn"] button {
    background: #ffffff !important;
    color: #172026 !important;
    border: 1px solid #e4e7ec !important;
    border-radius: 999px !important;
    font-weight: 700 !important;
}
div[class*="st-key-obs_count_minus"] button,
div[class*="st-key-obs_count_plus"] button {
    border-radius: 999px !important;
    font-weight: 700 !important;
}
</style>
"""


def inject_dashboard_styles() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
