"""RAGAS 골드셋 관리 — 1단계(수동 작성): AI 자동 초안 생성은 아직 없음. 사람이
ChatGPT 웹 등으로 초안을 만들어와서 이 화면에서 원문과 대조하며 입력한다.

탭 2개:
- 작성: source_documents에서 랜덤 문서를 뽑아 원문 보며 question/ground_truth/
  reference_contexts를 입력해 저장한다. 저장 자체가 이미 사람이 ChatGPT 결과를
  원문과 대조해 검수한 뒤 누르는 행동이라 기본값은 approved다 — 저장 후 별도
  탭에서 다시 승인하는 2차 확인은 안 둔다. 다만 원문 대조로는 못 잡는 문제
  (문항이 원문과 안 틀리지만 변별력이 없는 등)를 저장 시점에 바로 반려로
  남길 수 있게 상태 선택도 같이 둔다 — 사유는 자유 입력 대신 정해진 항목
  중에서 고른다(집계 가능하게).
- 전체목록: 저장된 케이스 목록 — 상태별 필터, 반려/삭제(승인은 반려를 되돌릴 때만 씀).
"""

import difflib
import json
import re
from collections import Counter
from uuid import uuid4

import requests
import streamlit as st

from components.layout import page_header
from config import OPS_BACKEND_URL

_SESSION = requests.Session()

_SOURCE_TYPES = ["(전체)", "doc_page", "action_schema", "package_overview"]
_STATUS_OPTIONS = ["draft", "approved", "rejected"]
_STATUS_LABELS = {"draft": "작성 중", "approved": "승인", "rejected": "반려"}
_STATUS_BADGE_COLORS = {"draft": "gray", "approved": "green", "rejected": "red"}
# 반려 사유는 사람이 매번 타이핑하기보다(문구가 제각각이면 나중에 집계가 안 됨) 정해진
# 항목 중 고르게 한다 — "없음"을 기본값으로 둬서 사유를 안 고르고 넘어가도(귀찮을 때) 저장은
# 막지 않는다. 지금까지 실제로 마주친 반려 사유 2가지(변별력 없음, 원문 불일치)만 담았고
# 나머지는 "기타"로 흡수한다 — 근거 없이 항목을 늘리지 않는다.
_REJECT_REASONS = ["없음", "문서 변별력 없음(일반적 패턴)", "근거가 원문과 불일치", "기타"]
_QUESTION_TYPES = ["단순 조회", "조건 조회", "절차 설명", "비교·판단"]

_CHATGPT_PROMPT_TEMPLATE = """다음 공식 문서를 근거로 RAGAS 평가 문항 1개를 작성하라.

작성 기준:
- 공식 문서에 명시된 내용만 사용하고 추측하거나 보완하지 않는다.
- 질문은 문서 제목을 그대로 베끼는 퀴즈보다 실제 사용자가 검색창이나 챗봇에 물어볼 법한
  자연스러운 표현으로 작성한다.
- 단, 실제 사용자처럼 쓰더라도 회사·프로젝트·개인 상황처럼 문서에 없는 맥락은 넣지 않는다.
- 질문 하나에 핵심 평가 대상 하나만 담는다.
- 정답은 질문에 직접 답하되, 공식 문서 범위를 벗어나지 않는다.
- 정답 근거는 정답을 실제로 뒷받침하는 원문만 발췌하되, 단어는 바꾸지 말고 줄바꿈·공백만
  자연스러운 한 문장으로 이어 적는다(원문의 줄바꿈을 그대로 따라 적지 않는다).
- 정답 근거 안에 따옴표(")가 들어가면 JSON 문자열 규칙에 맞게 \\\"로 적는다.
- 파일 경로처럼 역슬래시가 들어가는 내용은 JSON 문자열 규칙에 맞게 역슬래시를 두 번 겹쳐 적는다.
  예: Inbox\\\\folder1;Inbox\\\\folder2
- 문항 유형은 아래 기준으로 하나만 선택한다.
  - 단순 조회: 명칭, 기능, 지원 여부, 선택지 등 하나의 사실을 직접 묻는 질문
  - 조건 조회: 특정 조건에 필요한 파라미터, 값 또는 설정을 묻는 질문
  - 절차 설명: 작업 순서나 설정 방법을 설명하도록 요구하는 질문
  - 비교·판단: 원문에 여러 선택지와 선택 기준이 함께 있고, 상황에 따라 무엇을 고르는 게 나은지 판단하는 질문
- "어떤 값/옵션/자료/패키지를 사용해야 하나"처럼 하나의 정답을 묻는 질문은 비교·판단이 아니라 조건 조회다.
- 비교·판단은 A/B 차이, 상황별 권장 선택, 선택 기준이 원문에 있을 때만 만든다.
- 현재 분포에서 부족한 유형은 참고만 한다. 문서 내용이 자연스럽게 맞지 않으면 억지로 맞추지 않는다.
  현재 분포: {question_type_status}
- 유형이 애매하면 문서 근거와 질문 의도에 가장 직접적으로 맞는 유형을 선택한다.
- 정답 근거가 여러 구간에 있으면 reference_contexts 배열에 각각 나눈다.
- 출력은 설명 없이 유효한 JSON 하나만 제공한다.

출력 형식:
{{
"question_type": "단순 조회 | 조건 조회 | 절차 설명 | 비교·판단",
"question": "평가 질문",
"ground_truth": "기대 정답",
"reference_contexts": [
  {{"text": "정답을 뒷받침하는 공식 문서 원문"}}
]
}}

공식 문서:
{content}
"""


def _queue_flash_message(section_name: str, message_type: str, message: str) -> None:
    """rerun 뒤에도 지정한 위치에 알림을 보여주기 위해 session_state에 임시 저장한다."""
    flash_key = f"_ragas_flash_{section_name}"
    st.session_state.setdefault(flash_key, []).append((message_type, message))


def _render_flash_messages(section_name: str) -> None:
    flash_key = f"_ragas_flash_{section_name}"
    for message_type, message in st.session_state.pop(flash_key, []):
        getattr(st, message_type)(message)


def render() -> None:
    page_header("RAGAS 평가 데이터셋", "")
    tab_write, tab_list = st.tabs(["작성", "전체목록"])
    with tab_write:
        _render_write_tab()
    with tab_list:
        _render_list_tab()


# ── 공통 API 헬퍼 ────────────────────────────────────────────────────────


def _get(path: str, params: dict | None = None) -> tuple[object | None, str | None]:
    try:
        resp = _SESSION.get(f"{OPS_BACKEND_URL}{path}", params=params or {}, timeout=10)
        resp.raise_for_status()
        return resp.json(), None
    except (requests.RequestException, ValueError) as exc:
        return None, str(exc)


def _post_json(path: str, payload: dict) -> tuple[bool, str]:
    try:
        resp = _SESSION.post(f"{OPS_BACKEND_URL}{path}", json=payload, timeout=10)
        if resp.status_code == 200:
            return True, ""
        return False, resp.json().get("detail", resp.text)
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)


def _patch_json(path: str, payload: dict) -> tuple[bool, str]:
    try:
        resp = _SESSION.patch(f"{OPS_BACKEND_URL}{path}", json=payload, timeout=10)
        if resp.status_code == 200:
            return True, ""
        return False, resp.json().get("detail", resp.text)
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)


def _delete(path: str) -> tuple[bool, str]:
    try:
        resp = _SESSION.delete(f"{OPS_BACKEND_URL}{path}", timeout=10)
        if resp.status_code == 200:
            return True, ""
        return False, resp.json().get("detail", resp.text)
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)


def _normalize_question(text: str) -> str:
    """정확 중복 판정용 — 공백만 다른 걸 다른 질문으로 오판하지 않게 정규화."""
    return re.sub(r"\s+", " ", text.strip())


def _content_contains(content: str, snippet: str) -> bool:
    """정답 근거가 원문에 실제로 있는지 검사할 때만 쓴다 — 공백을 한 칸으로 접는 게
    아니라 아예 다 지우고 비교한다. 원문이 HTML에서 굵게/링크로 강조된 부분마다
    줄바꿈으로 쪼개져 저장돼 있어서("작업 실행 ID" 다음 줄에 "를 지정합니다." 식으로),
    한 칸으로만 접으면 "ID 를"처럼 원문에 없던 공백이 생겨 ChatGPT가 준 "ID를"과
    어긋나 오탐이 났다(실측 확인됨). 한국어는 공백 유무로 다른 단어가 되는 경우가
    거의 없어서 이 용도로는 공백 전부 제거가 더 안전하다."""
    return re.sub(r"\s+", "", snippet) in re.sub(r"\s+", "", content)


def _most_similar(question: str, candidates: list[str], threshold: float = 0.6) -> tuple[str, float] | None:
    """의미 유사도가 아니라 문자열 유사도(difflib, 표준 라이브러리)다 — 임베딩/LLM 호출
    없이 최소 비용으로 "거의 같은 질문 재입력"만 잡아내는 용도. threshold 이상 중
    가장 비슷한 것 하나만 반환(없으면 None)."""
    best: tuple[str, float] | None = None
    normalized_q = _normalize_question(question)
    for c in candidates:
        ratio = difflib.SequenceMatcher(None, normalized_q, _normalize_question(c)).ratio()
        if ratio >= threshold and (best is None or ratio > best[1]):
            best = (c, ratio)
    return best


def _build_question_type_status(cases: list[dict]) -> str:
    type_counts = Counter(case.get("question_type") for case in cases if case.get("question_type"))
    smallest_type_count = min(type_counts.get(question_type, 0) for question_type in _QUESTION_TYPES)
    recommended_question_types = {
        question_type for question_type in _QUESTION_TYPES
        if type_counts.get(question_type, 0) == smallest_type_count
    }
    return " · ".join(
        f"{question_type} {type_counts.get(question_type, 0)}개"
        + ("(권장)" if question_type in recommended_question_types else "")
        for question_type in _QUESTION_TYPES
    )


# ── 작성 탭 ──────────────────────────────────────────────────────────────


def _render_write_tab() -> None:
    col_type, col_exclude, col_btn = st.columns([2, 2, 1])
    with col_type:
        source_type = st.selectbox("문서 유형", _SOURCE_TYPES, key="ragas_write_source_type")
    with col_exclude:
        st.markdown("<div style='height: 2.1rem'></div>", unsafe_allow_html=True)
        exclude_used = st.checkbox("이미 작성된 문서 제외", value=True, key="ragas_write_exclude_used")
    with col_btn:
        st.markdown("<div style='height: 2.1rem'></div>", unsafe_allow_html=True)
        if st.button("무작위 선택", key="ragas_write_sample_btn", type="primary", width="stretch"):
            filter_type = None if source_type == "(전체)" else source_type
            docs, err = _get(
                "/eval/ragas/source-documents/random",
                {"source_type": filter_type, "limit": 5, "exclude_used": str(exclude_used).lower()},
            )
            if err:
                st.warning(f"추출 실패: {err}")
            else:
                st.session_state.ragas_sampled_docs = docs
                st.session_state.ragas_selected_doc_id = docs[0]["id"] if docs else None

    sampled = st.session_state.get("ragas_sampled_docs")
    if not sampled:
        st.caption(
            "문서를 무작위로 선택해 문항을 작성합니다. "
            "(결과가 없으면 scripts/ragas_eval/datasets/build_source_documents.py를 먼저 실행하세요.)"
        )
        return

    options = {f"[{d['source_type']}] {d['title']}": d["id"] for d in sampled}
    selected_label = st.selectbox("문서 선택", list(options.keys()), key="ragas_write_doc_select")
    st.session_state.ragas_selected_doc_id = options[selected_label]
    selected_doc = next(d for d in sampled if d["id"] == st.session_state.ragas_selected_doc_id)
    existing_cases, _ = _get("/eval/ragas/cases")
    existing_cases = existing_cases or []
    question_type_status = _build_question_type_status(existing_cases)

    col_doc, col_form = st.columns([0.58, 0.42])

    with col_doc:
        with st.container(border=True):
            col_doc_title, col_doc_hint = st.columns([2, 3])
            col_doc_title.markdown("**원문**")
            col_doc_hint.markdown(
                "<div style='text-align:right; color:gray; font-size:0.8rem; padding-top:0.3rem;'>"
                "마우스를 올리면 복사 버튼이 표시됩니다</div>",
                unsafe_allow_html=True,
            )
            st.markdown(selected_doc["title"])
            if selected_doc.get("path_titles"):
                st.caption(" > ".join(selected_doc["path_titles"]))
            if selected_doc.get("package_name"):
                st.caption(
                    f"package: {selected_doc['package_name']}"
                    + (f" / action: {selected_doc['action_name']}" if selected_doc.get("action_name") else "")
                )
            tab_raw, tab_prompt = st.tabs(["원문만", "ChatGPT용 프롬프트"])
            with tab_raw:
                st.code(selected_doc.get("content", ""), language=None, wrap_lines=True, height=520)
            with tab_prompt:
                st.caption("지침 + 원문이 합쳐진 상태 — 그대로 복사해서 ChatGPT에 붙여넣으면 됩니다.")
                full_prompt = _CHATGPT_PROMPT_TEMPLATE.format(
                    question_type_status=question_type_status,
                    content=selected_doc.get("content", ""),
                )
                st.code(full_prompt, language=None, wrap_lines=True, height=490)

    with col_form:
        with st.container(border=True):
            st.markdown("**평가 문항 작성**")
            _render_case_form(selected_doc, existing_cases)


def _repair_json_backslashes(json_text: str) -> str:
    """JSON 문자열 안의 잘못된 Windows 경로 백슬래시를 보정한다."""
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", json_text)


def _parse_chatgpt_response_json(source_document: dict, json_text: str) -> dict | None:
    """ChatGPT 응답 JSON을 dict로 바꾼다. 실패하면 화면에 이유를 보여준다."""
    if not json_text.strip():
        st.error("ChatGPT 응답 JSON을 먼저 붙여넣어 주세요.")
        return None
    try:
        parsed_data = json.loads(json_text)
    except json.JSONDecodeError as first_error:
        try:
            parsed_data = json.loads(_repair_json_backslashes(json_text))
        except json.JSONDecodeError:
            st.error(f"JSON 파싱 실패: {first_error}")
            _log_validation_attempt(
                source_document,
                "",
                "failure",
                f"JSON 파싱 실패: {first_error}",
                inline=True,
            )
            return None
    if not isinstance(parsed_data, dict):
        st.error("JSON 최상위 값은 객체({ ... })여야 합니다.")
        return None
    return parsed_data


def _fill_case_form_from_chatgpt_json(
    source_document_id: str,
    snippet_count_key: str,
    parsed_data: dict,
) -> None:
    """파싱된 ChatGPT JSON 값을 현재 작성 폼에 채운다."""
    st.session_state[f"ragas_question_{source_document_id}"] = parsed_data.get("question", "")
    st.session_state[f"ragas_ground_truth_{source_document_id}"] = parsed_data.get("ground_truth", "")

    question_type = parsed_data.get("question_type")
    if question_type in _QUESTION_TYPES:
        st.session_state[f"ragas_question_type_{source_document_id}"] = question_type

    previous_snippet_count = st.session_state.get(snippet_count_key, 1)
    for snippet_index in range(previous_snippet_count):
        st.session_state.pop(f"ragas_snippet_{source_document_id}_{snippet_index}", None)

    raw_snippets = parsed_data.get("reference_contexts", [])
    snippet_texts = [
        snippet.get("text", "")
        for snippet in raw_snippets
        if isinstance(snippet, dict) and snippet.get("text")
    ]
    st.session_state[snippet_count_key] = max(1, len(snippet_texts))
    for snippet_index, snippet_text in enumerate(snippet_texts):
        st.session_state[f"ragas_snippet_{source_document_id}_{snippet_index}"] = snippet_text


def _load_chatgpt_json_into_form(source_document: dict, snippet_count_key: str, json_text: str) -> None:
    """'불러오기' 버튼 동작: JSON을 폼에 채우고 사용자가 검토할 수 있게 한다."""
    parsed_data = _parse_chatgpt_response_json(source_document, json_text)
    if parsed_data is None:
        return
    _fill_case_form_from_chatgpt_json(source_document["id"], snippet_count_key, parsed_data)
    st.rerun()


def _save_chatgpt_json_directly(source_document: dict, snippet_count_key: str, json_text: str) -> None:
    """JSON 옆 '저장' 버튼 동작: 불러오기를 누르지 않아도 바로 저장한다."""
    parsed_data = _parse_chatgpt_response_json(source_document, json_text)
    if parsed_data is None:
        return
    _fill_case_form_from_chatgpt_json(source_document["id"], snippet_count_key, parsed_data)
    _save_current_case_form(source_document, snippet_count_key)


def _log_validation_attempt(
    source_document: dict,
    question: str,
    outcome: str,
    failed_snippets: str | None,
    *,
    inline: bool = False,
) -> None:
    """근거 검증 시도 결과를 통계용 로그로 남긴다."""
    log_saved, error_message = _post_json(
        "/eval/ragas/validation-log",
        {
            "doc_id": source_document["id"],
            "doc_title": source_document.get("title"),
            "question": question,
            "outcome": outcome,
            "failed_snippets": failed_snippets,
        },
    )
    if inline:
        if log_saved:
            st.caption("검증 로그를 적재했습니다.")
        else:
            st.warning(f"검증 로그 적재에 실패했습니다. 저장에는 영향 없습니다. {error_message}")
    else:
        if log_saved:
            _queue_flash_message("write", "caption", "검증 로그를 적재했습니다.")
        else:
            _queue_flash_message(
                "write",
                "warning",
                f"검증 로그 적재에 실패했습니다. 저장에는 영향 없습니다. {error_message}",
            )


def _save_current_case_form(source_document: dict, snippet_count_key: str) -> None:
    """현재 폼 값을 읽어 검증하고 RAGAS 케이스로 저장한다."""
    source_document_id = source_document["id"]
    question = st.session_state.get(f"ragas_question_{source_document_id}", "")
    ground_truth = st.session_state.get(f"ragas_ground_truth_{source_document_id}", "")
    question_type = st.session_state.get(f"ragas_question_type_{source_document_id}", "선택하세요")
    snippet_count = st.session_state.get(snippet_count_key, 1)
    snippet_inputs = [
        st.session_state.get(f"ragas_snippet_{source_document_id}_{snippet_index}", "")
        for snippet_index in range(snippet_count)
    ]

    if not question.strip() or not ground_truth.strip():
        st.error("질문과 정답은 필수입니다.")
        return
    if question_type == "선택하세요":
        st.error("문항 유형을 선택해야 합니다.")
        return

    reference_snippets = [snippet.strip() for snippet in snippet_inputs if snippet.strip()]
    source_content = source_document.get("content", "")
    for snippet_text in reference_snippets:
        if not _content_contains(source_content, snippet_text):
            # 여러 줄짜리 근거는 어느 줄이 문제인지 콕 집어 보여준다 — 통째로
            # 잘라서 보여주면(예전 방식) 6줄 중 어디가 틀렸는지 알 수가 없었다.
            snippet_lines = [line.strip() for line in snippet_text.split("\n") if line.strip()]
            missing_lines = [
                line for line in snippet_lines if not _content_contains(source_content, line)
            ] if len(snippet_lines) > 1 else [snippet_text]
            st.error("다음 정답 근거가 원문에 없습니다(내용 확인 필요):")
            st.code("\n".join(missing_lines), language=None)
            _log_validation_attempt(source_document, question, "failure", "\n".join(missing_lines), inline=True)
            return

    existing_cases, _ = _get("/eval/ragas/cases")
    existing_cases = existing_cases or []

    normalized_new = _normalize_question(question)
    if any(_normalize_question(c["question"]) == normalized_new for c in existing_cases):
        st.error("동일한 질문이 이미 골드셋에 있습니다.")
        _log_validation_attempt(source_document, question, "success", None, inline=True)
        return

    same_doc_questions = [
        c["question"] for c in existing_cases
        if source_document_id in c.get("reference_doc_ids", [])
        or any(
            context.get("source_document_id") == source_document_id
            for context in c.get("reference_contexts", [])
        )
    ]
    similar = _most_similar(question, same_doc_questions)

    save_status = st.session_state.get(f"ragas_save_status_{source_document_id}", "승인")
    reject_reason = st.session_state.get(f"ragas_save_reject_reason_{source_document_id}", "없음")

    case_id = f"rag_{source_document_id[:8]}_{uuid4().hex[:6]}"
    payload = {
        "case_id": case_id,
        "question": question.strip(),
        "ground_truth": ground_truth.strip(),
        "reference_doc_ids": [source_document_id],
        "reference_contexts": [
            {"source_document_id": source_document_id, "snippet": snippet_text}
            for snippet_text in reference_snippets
        ],
        "status": "approved" if save_status == "승인" else "rejected",
        "question_type": question_type,
    }
    if save_status == "반려":
        payload["review_note"] = reject_reason
    case_saved, error_message = _post_json("/eval/ragas/cases", payload)
    if case_saved:
        st.session_state.pop("/eval/ragas/cases", None)
        # 저장 성공 후 폼을 비운다 — 안 비우면 이미 저장된 내용이 계속 남아있어서
        # 다음 케이스를 쓰다가 실수로 중복 저장하기 쉽다.
        st.session_state.pop(f"ragas_question_{source_document_id}", None)
        st.session_state.pop(f"ragas_ground_truth_{source_document_id}", None)
        st.session_state.pop(f"ragas_question_type_{source_document_id}", None)
        st.session_state.pop(f"ragas_save_status_{source_document_id}", None)
        st.session_state.pop(f"ragas_save_reject_reason_{source_document_id}", None)
        for snippet_index in range(snippet_count):
            st.session_state.pop(f"ragas_snippet_{source_document_id}_{snippet_index}", None)
        st.session_state[snippet_count_key] = 1
        _queue_flash_message("write", "success", f"{case_id}를 저장했습니다. 상태: {save_status}")
        if similar is not None:
            similar_q, ratio = similar
            _queue_flash_message(
                "write",
                "warning",
                f"비슷한 질문이 이미 있습니다. 유사도 {ratio:.0%}: {similar_q[:40]}",
            )
        _log_validation_attempt(source_document, question, "success", None)
        st.rerun()
    else:
        st.error(f"저장 실패: {error_message}")
        if similar is not None:
            similar_q, ratio = similar
            st.warning(f"비슷한 질문이 이미 있습니다. 유사도 {ratio:.0%}: {similar_q[:40]}")
        _log_validation_attempt(source_document, question, "success", None, inline=True)


def _render_case_form(source_document: dict, existing_cases: list[dict]) -> None:
    source_document_id = source_document["id"]
    snippet_count_key = f"ragas_snippet_count_{source_document_id}"
    if snippet_count_key not in st.session_state:
        st.session_state[snippet_count_key] = 1

    with st.expander("ChatGPT 응답(JSON) 붙여넣어 자동 채우기"):
        json_paste = st.text_area(
            "JSON 붙여넣기",
            height=100,
            key=f"ragas_json_paste_{source_document_id}",
            label_visibility="collapsed",
        )
        load_button_column, quick_save_button_column = st.columns([3, 1])
        if load_button_column.button("불러오기", key=f"ragas_json_load_{source_document_id}", width="stretch"):
            _load_chatgpt_json_into_form(source_document, snippet_count_key, json_paste)
        if quick_save_button_column.button(
            "저장",
            key=f"ragas_quick_save_{source_document_id}",
            type="primary",
            width="stretch",
        ):
            _save_chatgpt_json_directly(source_document, snippet_count_key, json_paste)

    # 저장 함수는 JSON 빠른 저장 버튼과 일반 저장 버튼이 같이 쓰므로 session_state에서 값을 읽는다.
    st.text_area("질문", height=80, key=f"ragas_question_{source_document_id}")
    st.text_area("정답", height=100, key=f"ragas_ground_truth_{source_document_id}")

    st.caption(_build_question_type_status(existing_cases))
    st.selectbox(
        "문항 유형", ["선택하세요", *_QUESTION_TYPES], key=f"ragas_question_type_{source_document_id}",
        help=(
            "겹칠 때 우선순위: 비교·판단 > 절차 설명 > 조건 조회 > 단순 조회\n"
            "단순 조회: 사실 하나를 직접 답함 / 조건 조회: 파라미터·설정값 / "
            "절차 설명: 순서·방법을 단계로 설명 / 비교·판단: 옵션 비교·선택"
        ),
    )

    for snippet_index in range(st.session_state[snippet_count_key]):
        st.text_area(
            f"정답 근거 {snippet_index + 1}",
            height=70,
            key=f"ragas_snippet_{source_document_id}_{snippet_index}",
            help="정답을 뒷받침하는 원문 구간을 입력합니다." if snippet_index == 0 else None,
        )
    if st.button("+ 근거 추가", key=f"ragas_add_snippet_{source_document_id}"):
        st.session_state[snippet_count_key] += 1
        st.rerun()

    status_column, reason_column = st.columns(2)
    save_status = status_column.selectbox(
        "저장 상태", ["승인", "반려"], key=f"ragas_save_status_{source_document_id}",
        help="원문 대조는 통과했지만 변별력 없는 문항처럼 편집상 문제가 보이면 반려로 저장하세요.",
    )
    if save_status == "반려":
        reason_column.selectbox(
            "반려 사유",
            _REJECT_REASONS,
            key=f"ragas_save_reject_reason_{source_document_id}",
        )

    _, save_button_column = st.columns([3, 1])
    if save_button_column.button("저장", key=f"ragas_save_{source_document_id}", type="primary", width="stretch"):
        _save_current_case_form(source_document, snippet_count_key)

    # 저장 버튼들 바로 아래에 결과를 보여준다 — 페이지 맨 위에서 한 번에 보여주면
    # 버튼과 메시지가 멀어져서(실제 리포트됨) rerun 뒤에도 이 자리에서 그린다.
    _render_flash_messages("write")


# ── 전체목록 탭 ───────────────────────────────────────────────────────────


def _render_list_tab() -> None:
    # 승인/반려/삭제 뒤 결과를 여기서 보여준다 — 목록 아래(선택된 행 상세)에서 보여주면
    # 삭제로 목록이 줄어 선택이 풀렸을 때 그 아래 코드가 안 돌아 메시지가 영영 안 뜰
    # 위험이 있다(작성 탭에서 이미 겪은 "메시지가 큐에 남아 미아가 되는" 문제와 같은
    # 종류). 조회 결과와 무관하게 항상 도달하는 자리라 여기서 소비한다.
    _render_flash_messages("list")

    cases, err = _get("/eval/ragas/cases")
    if err:
        st.warning(f"불러오지 못했습니다: {err}")
        return

    col_filter, col_count = st.columns([1, 3])
    with col_filter:
        status_filter = st.selectbox(
            "상태", ["전체"] + [_STATUS_LABELS[s] for s in _STATUS_OPTIONS], key="ragas_review_status_filter",
        )
    filtered = cases if status_filter == "전체" else [
        c for c in cases if _STATUS_LABELS.get(c.get("status", "draft")) == status_filter
    ]
    with col_count:
        st.markdown("<div style='height: 2.1rem'></div>", unsafe_allow_html=True)
        st.caption(f"전체 {len(cases)}건 · 표시 {len(filtered)}건")

    if not filtered:
        return

    table_rows = [
        {
            "케이스 ID": c["case_id"],
            "상태": _STATUS_LABELS.get(c.get("status", "draft"), c.get("status", "draft")),
            "질문": c["question"],
            "정답 근거": f"{len(c.get('reference_contexts', []))}개",
        }
        for c in filtered
    ]
    # height 고정 — 케이스가 늘어나도 표 자체가 스크롤되고 페이지가 한없이 안 길어짐.
    event = st.dataframe(
        table_rows, width="stretch", hide_index=True, height=320,
        on_select="rerun", selection_mode="single-row", key="ragas_review_table",
        column_config={"질문": st.column_config.TextColumn(width="large")},
    )

    selected_rows = event["selection"]["rows"] if event else []
    if not selected_rows:
        return

    target = filtered[selected_rows[0]]
    target_id = target["case_id"]
    target_status = target.get("status", "draft")

    st.divider()
    st.subheader("평가 케이스 상세")

    col_id, col_status, col_evidence = st.columns(3)
    col_id.text("케이스 ID")
    col_id.text(target_id)
    with col_status:
        st.text("상태")
        st.badge(_STATUS_LABELS[target_status], color=_STATUS_BADGE_COLORS[target_status])
    col_evidence.text("정답 근거")
    col_evidence.text(f"{len(target.get('reference_contexts', []))}개")

    st.text("질문")
    st.text_area(
        "질문", value=target["question"], height=80, disabled=True,
        key=f"ragas_detail_question_{target_id}", label_visibility="collapsed",
    )

    st.text("기준 답변")
    st.text_area(
        "기준 답변", value=target["ground_truth"], height=100, disabled=True,
        key=f"ragas_detail_answer_{target_id}", label_visibility="collapsed",
    )

    if target.get("reference_contexts"):
        st.text("정답 근거 내용")
        for rc in target["reference_contexts"]:
            st.caption(rc["snippet"])

    if target.get("review_note"):
        st.text("반려 사유")
        st.caption(target["review_note"])

    st.text("반려 사유(반려 시에만 반영)")
    review_note = st.selectbox(
        "반려 사유 선택", _REJECT_REASONS, key=f"ragas_review_note_{target_id}",
        label_visibility="collapsed",
    )

    # 저장 시점에 이미 approved라 승인 버튼은 주 동작이 아니다 — 반려를 되돌릴 때만 쓴다.
    approve_button_column, reject_button_column, _, delete_button_column = st.columns([1, 1, 2, 1])
    if approve_button_column.button("승인", key=f"ragas_approve_{target_id}", width="stretch"):
        action_succeeded, error_message = _patch_json(
            f"/eval/ragas/cases/{target_id}",
            {"status": "approved"},
        )
        _handle_review_action(action_succeeded, error_message, f"{target_id}를 승인했습니다.")
    if reject_button_column.button("반려", key=f"ragas_reject_{target_id}", type="primary", width="stretch"):
        patch = {"status": "rejected", "review_note": review_note}
        action_succeeded, error_message = _patch_json(f"/eval/ragas/cases/{target_id}", patch)
        _handle_review_action(action_succeeded, error_message, f"{target_id}를 반려했습니다.")

    with delete_button_column.popover("삭제", width="stretch"):
        st.write("이 평가 케이스를 삭제하시겠습니까?")
        st.caption("삭제한 데이터는 복구할 수 없습니다.")
        if st.button("삭제 확정", key=f"ragas_delete_confirm_{target_id}", type="primary"):
            action_succeeded, error_message = _delete(f"/eval/ragas/cases/{target_id}")
            _handle_review_action(action_succeeded, error_message, f"{target_id}를 삭제했습니다.")


def _handle_review_action(action_succeeded: bool, error_message: str, success_message: str) -> None:
    """승인/반려/삭제 결과 — 성공 시 바로 rerun하는데 st.success를 여기서 쓰면 뒤이은
    rerun에 지워져서(작성 탭과 같은 이유) flash 메시지로 예약해야 다음 화면에서 보인다.
    실패는 rerun이 없으니 바로 그린다."""
    if action_succeeded:
        st.session_state.pop("/eval/ragas/cases", None)
        _queue_flash_message("list", "success", success_message)
        st.rerun()
    else:
        st.error(f"처리 실패: {error_message}")
