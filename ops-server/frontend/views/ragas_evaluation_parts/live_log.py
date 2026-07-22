import html

import streamlit as st
import streamlit.components.v1 as components

from components.layout import card, section_header
from config import OPS_BACKEND_URL

from .api import get_json


def render_status_panel(status_path: str, running_label: str, failure_label: str, ready_label: str) -> None:
    status, error_message = get_json(status_path)
    if error_message:
        st.warning(f"상태를 불러오지 못했습니다: {error_message}")
        return

    if status.get("running"):
        st.info(running_label)
    elif status.get("error"):
        st.error(f"{failure_label}: {status['error']}")
    elif status.get("finished_at"):
        st.success(f"완료 - {status.get('saved', 0)}/{status.get('cases', 0)}건 저장")
    else:
        st.caption(ready_label)


def render_sse_log(card_key: str, events_path: str) -> None:
    with card(card_key):
        section_header("실시간 로그(SSE)", "백엔드 실행 상태를 브라우저에서 직접 구독합니다.")
        events_url = html.escape(f"{OPS_BACKEND_URL}{events_path}", quote=True)
        components.html(build_sse_html(events_url), height=430)


def build_sse_html(events_url: str) -> str:
    return f"""
    <div style="font-family: ui-monospace, SFMono-Regular, Consolas, monospace;">
      <div id="state" style="margin-bottom:8px;color:#475467;font-family:sans-serif;">연결 중...</div>
      <pre id="log" style="
        height: 360px;
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
        const header = [
          "running=" + status.running,
          "saved=" + (status.saved || 0) + "/" + (status.cases || 0),
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
    status, error_message = get_json(status_path)
    if error_message:
        st.caption(f"로그를 불러오지 못했습니다: {error_message}")
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
