"""실행 상태를 텍스트 로그로 보여주는 부분. SSE(실시간)와 2초 폴링(백업) 둘 다 둔다
-- ragas_evaluation_parts/live_log.py와 같은 발상이지만, 이 페이지의 state 모양
(stage_index/stages)에 맞춰 다시 썼다."""

import html

import streamlit as st
import streamlit.components.v1 as components

from components.layout import card, section_header
from config import OPS_BACKEND_URL

from .api import fetch_status


def render_status_summary(status: dict | None) -> None:
    if status is None:
        return

    if status.get("running"):
        stage_names = status.get("stages", [])
        stage_index = status.get("stage_index", -1)
        current_stage_name = stage_names[stage_index] if 0 <= stage_index < len(stage_names) else "시작 중"
        st.info(f"실행 중 - 지금 단계: {current_stage_name}")
    elif status.get("error"):
        st.error(f"실패: {status['error']}")
    elif status.get("finished_at"):
        result_count = len(status.get("results", []))
        st.success(f"완료 - 워크플로우 파일 {result_count}개 변환됨")
    else:
        st.caption("아직 실행된 적이 없습니다.")


def render_sse_log(card_key: str, events_path: str) -> None:
    with card(card_key):
        section_header("실시간 로그(SSE)", "백엔드 실행 상태를 브라우저에서 직접 구독합니다.")
        events_url = html.escape(f"{OPS_BACKEND_URL}{events_path}", quote=True)
        components.html(_build_sse_html(events_url), height=380)


def _build_sse_html(events_url: str) -> str:
    return f"""
    <div style="font-family: ui-monospace, SFMono-Regular, Consolas, monospace;">
      <div id="state" style="margin-bottom:8px;color:#475467;font-family:sans-serif;">연결 중...</div>
      <pre id="log" style="
        height: 320px;
        overflow: auto;
        background: #101828;
        color: #d0f5df;
        border-radius: 8px;
        padding: 12px;
        white-space: pre-wrap;
        margin: 0;
      "></pre>
    </div>
    <script>
      const stateElement = document.getElementById("state");
      const logElement = document.getElementById("log");
      const eventSource = new EventSource("{events_url}");

      eventSource.addEventListener("open", () => {{
        stateElement.textContent = "SSE 연결됨";
      }});

      eventSource.addEventListener("status", (event) => {{
        const status = JSON.parse(event.data);
        const lines = status.log || [];
        const stageIndex = status.stage_index;
        const stageNames = status.stages || [];
        const currentStageName = (stageIndex >= 0 && stageIndex < stageNames.length) ? stageNames[stageIndex] : "-";
        const header = [
          "running=" + status.running,
          "stage=" + currentStageName,
          status.error ? "error=" + status.error : ""
        ].filter(Boolean).join(" · ");
        logElement.textContent = header + "\\n\\n" + (lines.length ? lines.join("\\n") : "(아직 로그 없음)");
        logElement.scrollTop = logElement.scrollHeight;
      }});

      eventSource.addEventListener("error", () => {{
        stateElement.textContent = "SSE 연결 대기 또는 재시도 중";
      }});
    </script>
    """


def render_polling_log(card_key: str, status_path: str) -> None:
    status = fetch_status(status_path)
    if status is None:
        return

    log_lines = status.get("log") or []
    if not log_lines and not status.get("running"):
        return

    with card(card_key):
        title = "진행 로그(백업)"
        if status.get("running"):
            title = f"{title} - 실행 중"
        section_header(title, "SSE가 막힐 때를 대비해 2초마다 상태를 다시 읽습니다.")
        st.code("\n".join(log_lines[-120:]) or "(아직 로그 없음)", language="text")
