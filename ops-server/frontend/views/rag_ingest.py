from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import requests
import streamlit as st

from components.layout import card, metric_strip, page_header, section_header
from config import OPS_BACKEND_URL


RUNNING_STATUSES = {"QUEUED", "RUNNING", "CANCEL_REQUESTED"}
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED", "INTERRUPTED"}

MODE_OPTIONS = {
    "standard": {
        "title": "Standard",
        "summary": "JAR가 있는 패키지만 action_schema로 적재",
        "steps": "crawl, en-US crawl, action tree, build, ingest",
        "llm": "사용 안 함",
        "fit": "권장 기본값",
    },
    "extended": {
        "title": "Extended",
        "summary": "Standard + JAR 없는 리프 문서를 action_candidate로 적재",
        "steps": "crawl, en-US crawl, action tree, naive leaf, build, ingest",
        "llm": "사용 안 함",
        "fit": "검색 범위 확장",
    },
}


def _api_get(path: str, **params) -> tuple[Any | None, str | None]:
    try:
        resp = requests.get(f"{OPS_BACKEND_URL}{path}", params={k: v for k, v in params.items() if v is not None}, timeout=10)
    except requests.RequestException as exc:
        return None, f"Ops Backend 연결 실패: {exc}"
    if resp.status_code != 200:
        return None, _error_text(resp)
    try:
        return resp.json(), None
    except ValueError:
        return resp.text, None


def _api_post(path: str, payload: dict | None = None) -> tuple[Any | None, str | None, int | None]:
    try:
        resp = requests.post(f"{OPS_BACKEND_URL}{path}", json=payload or {}, timeout=10)
    except requests.RequestException as exc:
        return None, f"Ops Backend 연결 실패: {exc}", None
    if resp.status_code not in (200, 201):
        return None, _error_text(resp), resp.status_code
    try:
        return resp.json(), None, resp.status_code
    except ValueError:
        return resp.text, None, resp.status_code


def _api_text(path: str, **params) -> tuple[str, str | None]:
    try:
        resp = requests.get(f"{OPS_BACKEND_URL}{path}", params=params, timeout=10)
    except requests.RequestException as exc:
        return "", f"Ops Backend 연결 실패: {exc}"
    if resp.status_code != 200:
        return "", _error_text(resp)
    return resp.text, None


def _error_text(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


def render() -> None:
    page_header(
        "RAG 데이터 적재",
        "문서 수집부터 pgvector 및 OpenSearch 반영까지의 적재 작업을 관리합니다.",
    )

    health, health_error = _api_get("/ops/rag/ingest/health")
    capabilities, cap_error = _api_get("/ops/rag/ingest/capabilities")
    jobs, jobs_error = _api_get("/ops/rag/ingest/jobs", limit=20)
    jobs = jobs or []
    active_job = next((job for job in jobs if job.get("status") in RUNNING_STATUSES), None)
    selected_job_id = active_job["job_id"] if active_job else st.session_state.get("rag_ingest_selected_job_id")

    _render_system_summary(health, health_error, active_job, jobs[:1])

    if health_error or cap_error or jobs_error:
        with card("rag_ingest_errors"):
            for message in (health_error, cap_error, jobs_error):
                if message:
                    st.error(message)
        return

    mode, clean, can_start = _render_execution_settings(capabilities or {}, active_job)
    _render_start_controls(mode, clean, can_start, active_job)

    if selected_job_id:
        _render_live_area(selected_job_id)
    else:
        with card("rag_ingest_progress_empty"):
            section_header("실시간 진행 상황")
            st.info("아직 선택된 적재 작업이 없습니다.")

    _render_history(jobs)


def _render_system_summary(health: dict | None, health_error: str | None, active_job: dict | None, latest_jobs: list[dict]) -> None:
    server_status = "정상" if health and not health_error else "연결 실패"
    if active_job:
        execution_status = active_job.get("status", "RUNNING")
    elif latest_jobs:
        execution_status = latest_jobs[0].get("status", "대기")
    else:
        execution_status = "대기"

    metric_strip([
        ("RAG Server", server_status),
        ("실행 상태", execution_status),
        ("현재 job_id", active_job.get("job_id", "-") if active_job else "-"),
    ])

    if active_job:
        with card("rag_ingest_active_summary"):
            section_header("현재 실행 상태 요약")
            cols = st.columns(6)
            cols[0].metric("모드", _mode_title(active_job.get("mode")))
            cols[1].metric("상태", active_job.get("status", "-"))
            cols[2].metric("현재 단계", active_job.get("current_stage") or "-")
            cols[3].metric("경과", _duration_label(active_job))
            cols[4].metric("전체 재구축", "예" if active_job.get("clean") else "아니오")
            cols[5].metric("Agent 제한", active_job.get("agent_parse_limit") or "-")
            st.warning("현재 다른 RAG 적재 작업이 실행 중입니다. 완료 또는 취소 후 새 작업을 시작할 수 있습니다.")


def _render_execution_settings(capabilities: dict, active_job: dict | None) -> tuple[str, bool, bool]:
    with card("rag_ingest_settings"):
        section_header("적재 방식 선택")
        mode = st.radio(
            "기본 적재 모드",
            ["standard", "extended"],
            format_func=lambda value: MODE_OPTIONS[value]["title"],
            horizontal=True,
            disabled=bool(active_job),
        )
        mode_cols = st.columns(2)
        for index, value in enumerate(["standard", "extended"]):
            spec = MODE_OPTIONS[value]
            with mode_cols[index]:
                st.subheader(spec["title"])
                st.write(spec["summary"])
                st.caption(f"추가 처리 단계: {spec['steps']}")
                st.caption(f"LLM 사용: {spec['llm']} · 추천 용도: {spec['fit']}")

        agent_available = bool(capabilities.get("openai_api_key_configured"))
        agent_limit = capabilities.get("agent_parse_limit")
        with st.expander("고급 설정: AI-assisted Agent Parse", expanded=False):
            st.caption("JAR 없는 패키지 문서를 LLM으로 파싱해 액션과 파라미터를 추출합니다.")
            agent_cols = st.columns(4)
            agent_cols[0].metric("OPENAI_API_KEY", "설정됨" if agent_available else "없음")
            agent_cols[1].metric("LLM 비용", "발생")
            agent_cols[2].metric("실행 시간", "길어질 수 있음")
            agent_cols[3].metric("AGENT_PARSE_LIMIT", agent_limit or "미설정")
            agent_confirmed = st.checkbox(
                "AI 파싱 비용과 긴 실행 시간을 확인했습니다.",
                disabled=bool(active_job) or not agent_available,
            )
            use_agent = st.checkbox(
                "Agent Parse로 실행",
                disabled=bool(active_job) or not agent_available or not agent_confirmed,
            )
            if not agent_available:
                st.warning("OPENAI_API_KEY가 없어 Agent Parse를 실행할 수 없습니다.")
            if use_agent:
                mode = "agent_parse"

        st.divider()
        section_header("위험 작업 설정")
        clean = st.checkbox("전체 재구축", disabled=bool(active_job))
        rebuild_confirmed = True
        if clean:
            st.warning(
                "기존 rag_documents와 OpenSearch 검색 인덱스를 교체하고 전체 데이터를 다시 적재합니다. "
                "현재 구조는 staging index/table 교체 방식이 아니므로 실패 시 검색 결과가 불완전할 수 있습니다."
            )
            confirm_text = st.text_input("확인 문구 입력", placeholder="REBUILD", disabled=bool(active_job))
            rebuild_confirmed = confirm_text == "REBUILD"
            if not rebuild_confirmed:
                st.info("전체 재구축은 확인 문구가 정확히 REBUILD일 때만 실행할 수 있습니다.")

    can_start = not active_job and rebuild_confirmed
    return mode, clean, can_start


def _render_start_controls(mode: str, clean: bool, can_start: bool, active_job: dict | None) -> None:
    with card("rag_ingest_start"):
        section_header("작업 시작")
        cols = st.columns([2, 1, 1])
        cols[0].write(f"선택 모드: **{_mode_title(mode)}** · 전체 재구축: **{'예' if clean else '아니오'}**")
        start_clicked = cols[1].button("적재 작업 시작", type="primary", use_container_width=True, disabled=not can_start)
        if active_job:
            cancel_clicked = cols[2].button("실행 취소", use_container_width=True)
        else:
            cancel_clicked = False

        if start_clicked:
            payload = {
                "mode": mode,
                "clean": clean,
                "requested_by": st.session_state.get("ops_user_email") or "ops",
            }
            data, error, status_code = _api_post("/ops/rag/ingest/jobs", payload)
            if error:
                if status_code == 409:
                    st.warning(f"이미 실행 중인 작업이 있습니다: {error}")
                else:
                    st.error(f"작업 시작 실패: {error}")
            else:
                st.session_state["rag_ingest_selected_job_id"] = data["job_id"]
                st.success(f"작업을 시작했습니다. job_id={data['job_id']}")
                st.rerun()

        if cancel_clicked and active_job:
            data, error, _ = _api_post(f"/ops/rag/ingest/jobs/{active_job['job_id']}/cancel")
            if error:
                st.error(f"취소 요청 실패: {error}")
            else:
                st.warning(f"취소를 요청했습니다. 상태={data.get('status')}")
                st.rerun()


@st.fragment(run_every="2s")
def _render_live_area(job_id: str) -> None:
    job, job_error = _api_get(f"/ops/rag/ingest/jobs/{job_id}")
    if job_error:
        with card("rag_ingest_live_error"):
            section_header("실시간 진행 상황")
            st.error(job_error)
        return

    with card("rag_ingest_progress"):
        section_header("실시간 진행 상황")
        stages = job.get("stages") or []
        current_index = _current_stage_index(stages)
        total = len(stages) or 1
        progress_value = min(1.0, max(0.0, (current_index + 1) / total if job.get("status") not in TERMINAL_STATUSES else 1.0))
        st.progress(progress_value)
        cols = st.columns(5)
        cols[0].metric("상태", job.get("status", "-"))
        cols[1].metric("현재 단계", job.get("current_stage") or "-")
        cols[2].metric("단계", f"{min(current_index + 1, total)} / {total}")
        cols[3].metric("경과 시간", _duration_label(job))
        cols[4].metric("exit code", job.get("exit_code") if job.get("exit_code") is not None else "-")

        for stage in stages:
            label = stage["label"]
            state = stage["state"]
            if state == "completed":
                st.success(label)
            elif state == "running":
                st.info(f"진행 중: {label}")
            elif state == "failed":
                st.error(f"실패 단계: {label}")
            else:
                st.write(label)

        if job.get("error_message"):
            st.error(f"오류 요약: {job['error_message']} · job_id={job_id}")

    _render_logs(job_id)


def _render_logs(job_id: str) -> None:
    with card("rag_ingest_logs"):
        section_header("실시간 로그")
        controls = st.columns([1, 1, 3])
        level_filter = controls[0].selectbox("레벨", ["전체", "INFO", "WARNING", "ERROR"], key="rag_log_level")
        tail = controls[1].number_input("최근 줄", min_value=100, max_value=5000, value=1200, step=100)
        search = controls[2].text_input("검색", key="rag_log_search")

        text, error = _api_text(f"/ops/rag/ingest/jobs/{job_id}/logs", tail=int(tail))
        if error:
            st.error(error)
            return
        lines = _filter_log_lines(text.splitlines(), level_filter, search)
        st.caption(f"표시 {len(lines)}줄 · 마지막 갱신 {datetime.now().strftime('%H:%M:%S')}")
        st.code("\n".join(lines) or "(표시할 로그 없음)", language="text")
        st.download_button(
            "전체 로그 다운로드",
            data=text,
            file_name=f"{job_id}.log",
            mime="text/plain",
            use_container_width=True,
        )


def _render_history(jobs: list[dict]) -> None:
    with card("rag_ingest_history"):
        section_header("최근 실행 이력")
        if not jobs:
            st.info("저장된 실행 이력이 없습니다.")
            return
        rows = [
            {
                "실행 시각": job.get("started_at") or job.get("created_at"),
                "모드": _mode_title(job.get("mode")),
                "전체 재구축": "예" if job.get("clean") else "아니오",
                "실행자": job.get("requested_by") or "-",
                "결과": job.get("status"),
                "소요 시간": _duration_label(job),
                "Agent Parse 제한": job.get("agent_parse_limit") or "-",
                "job_id": job.get("job_id"),
            }
            for job in jobs
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        selected = st.selectbox(
            "상세 보기",
            [job["job_id"] for job in jobs],
            format_func=lambda job_id: f"{job_id} · {_mode_title(next(j for j in jobs if j['job_id'] == job_id).get('mode'))}",
        )
        st.session_state["rag_ingest_selected_job_id"] = selected
        detail = next(job for job in jobs if job["job_id"] == selected)
        with st.expander("작업 상세", expanded=False):
            st.json(detail)


def _filter_log_lines(lines: list[str], level_filter: str, search: str) -> list[str]:
    filtered = lines
    if level_filter != "전체":
        filtered = [line for line in filtered if level_filter in line.upper()]
    if search:
        needle = search.lower()
        filtered = [line for line in filtered if needle in line.lower()]
    return filtered


def _mode_title(mode: str | None) -> str:
    if mode == "agent_parse":
        return "AI-assisted Agent Parse"
    if mode in MODE_OPTIONS:
        return MODE_OPTIONS[mode]["title"]
    return mode or "-"


def _current_stage_index(stages: list[dict]) -> int:
    for index, stage in enumerate(stages):
        if stage.get("state") == "running":
            return index
    completed = [i for i, stage in enumerate(stages) if stage.get("state") == "completed"]
    return completed[-1] if completed else 0


def _duration_label(job: dict) -> str:
    seconds = job.get("duration_seconds")
    if seconds is None:
        return "-"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"
