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
# created_at/updated_at은 2026-07-20에 추가된 필드라 그 이전 케이스는 값이 없다(None) —
# 정렬 키에서 빈 문자열로 취급하면 "최신순"에서 자연스럽게 맨 뒤로 밀린다.
_CASE_SORT_OPTIONS: dict[str, tuple[str, bool]] = {
    "등록시간 최신순": ("created_at", True),
    "등록시간 오래된순": ("created_at", False),
    "수정시간 최신순": ("updated_at", True),
    "수정시간 오래된순": ("updated_at", False),
    "케이스 ID": ("case_id", False),
}


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "-"
    return value[:16].replace("T", " ")

_CHATGPT_PROMPT_TEMPLATE = """다음 공식 문서를 근거로 RAGAS 평가 문항 1개를 작성하라.

작성 기준:
- 공식 문서에 명시된 내용만 쓴다(추측·보완 금지).
- 질문은 짧고 자연스러운 한 문장으로 목적 하나만 묻는다. 파라미터·선택지를 전부
  나열해서 채우지 않는다(질문이 스펙 요약이 되면 안 됨).
- 정답(액션 이름)이 질문의 목적 설명과 사실상 같은 말이면 그 질문은 버린다. 예: "창을
  활성화하려면 어떤 작업을 쓰나요?" → 정답 "창의 활성화 작업" — 액션 이름을 몰라도 뜻으로
  다 맞힐 수 있어 문서를 안 봐도 풀리므로 RAG 검색을 전혀 테스트하지 못한다. 이럴 땐
  "어떤 액션을 쓰나" 대신 그 액션의 파라미터·제약·동작 방식처럼 문서를 실제로 봐야만
  알 수 있는 내용을 정답으로 삼는 질문으로 바꾼다.
- 질문만 읽고도 무엇에 대한 질문인지 알 수 있어야 한다. "가져온 값", "이 작업"처럼 대상을
  안 밝히는 표현만 쓰지 말고, 어느 패키지·액션·상황에 대한 질문인지 반드시 명시한다.
- 회사·프로젝트·개인 상황 등 문서에 없는 맥락은 넣지 않는다.
- 질문 하나 = 평가 대상 하나. 정답은 질문에 직접 답하되 문서 범위를 안 벗어난다.
- 정답 근거는 원문 그대로 발췌(단어 변경 금지, 줄바꿈·공백만 자연스러운 한 문장으로 이음).
  따옴표는 \\", 역슬래시는 \\\\로 이스케이프(예: Inbox\\\\folder1). 여러 구간이면
  reference_contexts에 나눠 담는다.
- 문항 유형(하나만 선택):
  - 단순 조회: 명칭·기능·지원여부 등 사실 하나를 직접 물음
  - 조건 조회: 특정 조건에 필요한 파라미터·값·설정을 물음("어떤 값/옵션/자료/패키지를
    써야 하나"도 여기 — 비교·판단 아님)
  - 절차 설명: 순서·설정 방법을 설명하도록 요구
  - 비교·판단: 원문에 선택지+선택 기준이 함께 있어 상황별로 뭘 고를지 판단(A/B 차이·
    상황별 권장이 원문에 있을 때만 — 없으면 만들지 않음)
  - 애매하면 문서 근거와 질문 의도에 가장 맞는 쪽으로. 현재 분포는 참고만(부족한 유형에
    억지로 안 맞춤): {question_type_status}
- 출력은 설명 없이 아래 형식의 유효한 JSON 하나만.

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


def _find_similar_question(
    source_document_id: str, question: str, existing_cases: list[dict]
) -> tuple[str, float] | None:
    """같은 문서를 근거로 쓴 기존 케이스들 중 문자열 유사도가 높은 질문을 찾는다 —
    저장 폼 실시간 경고와 저장 시점 차단 둘 다 이 함수 하나로 판단해야 기준이
    어긋나지 않는다."""
    if not question.strip():
        return None
    same_doc_questions = [
        c["question"] for c in existing_cases
        if source_document_id in c.get("reference_doc_ids", [])
        or any(
            context.get("source_document_id") == source_document_id
            for context in c.get("reference_contexts", [])
        )
    ]
    return _most_similar(question, same_doc_questions)


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


def _effective_prompt_template() -> str:
    """세션에서 수정한 프롬프트가 있으면 그걸, 없으면 기본 템플릿을 쓴다 — 파일을 안
    고치고도 문구를 바로 시험해볼 수 있게. 브라우저 세션이 끝나면 사라진다(영구 저장
    아님, 계속 쓸 문구면 코드 자체를 고쳐달라고 요청해야 함)."""
    return st.session_state.get("ragas_prompt_template_override") or _CHATGPT_PROMPT_TEMPLATE


def _render_prompt_tab(question_type_status: str, content: str) -> None:
    editing = st.session_state.get("ragas_prompt_editing", False)
    has_override = bool(st.session_state.get("ragas_prompt_template_override"))

    col_edit, col_reset, _ = st.columns([1, 1, 2])
    if col_edit.button("수정 취소" if editing else "프롬프트 수정", key="ragas_prompt_edit_toggle"):
        st.session_state["ragas_prompt_editing"] = not editing
        st.rerun()
    if has_override and col_reset.button("기본값으로 되돌리기", key="ragas_prompt_reset"):
        st.session_state.pop("ragas_prompt_template_override", None)
        st.session_state["ragas_prompt_editing"] = False
        st.rerun()

    if editing:
        st.caption("{content}와 {question_type_status}는 실제 값으로 자동 치환되는 자리표시자입니다 — 지우지 마세요.")
        edited = st.text_area(
            "프롬프트 지침 편집", value=_effective_prompt_template(), height=400,
            key="ragas_prompt_edit_area", label_visibility="collapsed",
        )
        if st.button("이 프롬프트 적용", key="ragas_prompt_apply", type="primary"):
            st.session_state["ragas_prompt_template_override"] = edited
            st.session_state["ragas_prompt_editing"] = False
            st.rerun()
        return

    if has_override:
        st.caption("이번 세션에서 수정한 프롬프트를 쓰는 중입니다.")
    try:
        full_prompt = _effective_prompt_template().format(
            question_type_status=question_type_status, content=content,
        )
    except (KeyError, ValueError) as e:
        # 편집 중 {content}/{question_type_status}를 지우거나 JSON 예시의 {{ }} 이스케이프를
        # 깨뜨리면 format()이 죽는다 — 화면 전체가 죽지 않고 여기서 바로 원인을 보여준다.
        st.error(
            f"프롬프트 형식이 깨졌습니다: {e} — {{content}}/{{question_type_status}} 자리표시자와 "
            "JSON 예시의 중괄호({{ }})가 그대로 있는지 확인하세요."
        )
        return
    st.code(full_prompt, language=None, wrap_lines=True, height=490)


# ── 작성 탭 ──────────────────────────────────────────────────────────────


def _render_write_tab() -> None:
    # schema_source(jar/llm_agent)는 action_schema/package_overview 문서에만 있는
    # 구분이라, 그 두 유형을 골랐을 때만 필터를 보여준다 — 직전 렌더의 선택값
    # (session_state)으로 미리 판단한다.
    current_source_type = st.session_state.get("ragas_write_source_type", _SOURCE_TYPES[0])
    show_schema_source_filter = current_source_type in ("action_schema", "package_overview")
    if show_schema_source_filter:
        col_type, col_schema, col_exclude, col_btn = st.columns([2, 2, 2, 1])
    else:
        col_type, col_exclude, col_btn = st.columns([2, 2, 1])
        col_schema = None

    with col_type:
        source_type = st.selectbox("문서 유형", _SOURCE_TYPES, key="ragas_write_source_type")

    schema_source_filter = None
    if col_schema is not None:
        with col_schema:
            schema_source_label = st.selectbox(
                "출처(jar/llm_agent)",
                ["(전체)", "jar", "llm_agent"],
                key="ragas_write_schema_source",
                help="action_schema/package_overview 문서에만 있는 구분입니다. "
                "jar = JAR 파일 파싱, llm_agent = JAR 없는 패키지를 LLM이 파싱.",
            )
            schema_source_filter = None if schema_source_label == "(전체)" else schema_source_label

    with col_exclude:
        st.markdown("<div style='height: 2.1rem'></div>", unsafe_allow_html=True)
        exclude_used = st.checkbox("이미 작성된 문서 제외", value=True, key="ragas_write_exclude_used")
    with col_btn:
        st.markdown("<div style='height: 2.1rem'></div>", unsafe_allow_html=True)
        if st.button("무작위 선택", key="ragas_write_sample_btn", type="primary", width="stretch"):
            filter_type = None if source_type == "(전체)" else source_type
            params = {"source_type": filter_type, "limit": 5, "exclude_used": str(exclude_used).lower()}
            if schema_source_filter:
                params["schema_source"] = schema_source_filter
            docs, err = _get("/eval/ragas/source-documents/random", params)
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

    def _doc_option_label(d: dict) -> str:
        schema_tag = f"/{d['schema_source']}" if d.get("schema_source") else ""
        return f"[{d['source_type']}{schema_tag}] {d['title']}"

    options = {_doc_option_label(d): d["id"] for d in sampled}
    selected_label = st.selectbox("문서 선택", list(options.keys()), key="ragas_write_doc_select")
    st.session_state.ragas_selected_doc_id = options[selected_label]
    selected_doc = next(d for d in sampled if d["id"] == st.session_state.ragas_selected_doc_id)
    existing_cases, cases_error = _get("/eval/ragas/cases")
    if cases_error:
        # 조회 실패를 조용히 빈 목록으로 넘기면 문항유형 안내가 틀리게 보일 뿐 아니라,
        # 저장 시점 중복검사(다른 함수)도 같은 API를 쓰므로 여기서부터 사용자가 문제를
        # 알아야 한다(CodeRabbit #42 지적).
        st.warning(f"기존 케이스 목록을 불러오지 못했습니다 — 문항유형 안내가 부정확할 수 있습니다: {cases_error}")
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
            if selected_doc.get("schema_source"):
                st.caption(f"schema_source: {selected_doc['schema_source']}")
            tab_raw, tab_prompt = st.tabs(["원문만", "ChatGPT용 프롬프트"])
            with tab_raw:
                st.code(selected_doc.get("content", ""), language=None, wrap_lines=True, height=520)
            with tab_prompt:
                st.caption("지침 + 원문이 합쳐진 상태 — 그대로 복사해서 ChatGPT에 붙여넣으면 됩니다.")
                _render_prompt_tab(question_type_status, selected_doc.get("content", ""))

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

    # 최상위가 객체인 것만으론 부족하다 — question이 null이거나 reference_contexts의
    # text가 문자열이 아니면 폼에 채운 뒤 .strip() 호출에서 화면이 죽는다(CodeRabbit
    # #42 지적). 폼에 채우기 전에 필드 타입을 검증한다.
    for field_name in ("question", "ground_truth"):
        value = parsed_data.get(field_name)
        if value is not None and not isinstance(value, str):
            st.error(f"JSON의 '{field_name}' 값은 문자열이어야 합니다(받은 값: {type(value).__name__}).")
            return None

    raw_snippets = parsed_data.get("reference_contexts")
    if raw_snippets is not None:
        if not isinstance(raw_snippets, list):
            st.error("JSON의 'reference_contexts' 값은 배열이어야 합니다.")
            return None
        for snippet in raw_snippets:
            if not isinstance(snippet, dict):
                st.error("'reference_contexts'의 각 항목은 객체({ \"text\": ... })여야 합니다.")
                return None
            text_value = snippet.get("text")
            if text_value is not None and not isinstance(text_value, str):
                st.error(
                    f"'reference_contexts'의 'text' 값은 문자열이어야 합니다"
                    f"(받은 값: {type(text_value).__name__})."
                )
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
    else:
        # 이전 문서에서 고른 유형이 새 JSON을 불러온 뒤에도 그대로 남으면 안 된다 —
        # 유형이 없거나 허용 목록 밖 값이면 반드시 미선택으로 되돌린다.
        st.session_state[f"ragas_question_type_{source_document_id}"] = "선택하세요"
        if question_type is not None:
            _queue_flash_message(
                "write", "warning",
                f"JSON의 문항 유형 값이 올바르지 않아 초기화했습니다: {question_type!r}",
            )

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

    save_status = st.session_state.get(f"ragas_save_status_{source_document_id}", "승인")
    reference_snippets = [snippet.strip() for snippet in snippet_inputs if snippet.strip()]
    if save_status == "승인" and not reference_snippets:
        # 승인 상태인데 근거가 하나도 없으면 "원문 검증을 통과한 문항"이라는 승인의
        # 의미 자체가 깨진다(CodeRabbit #42 지적) — 반려는 근거 없이도 저장 가능(편집상
        # 문제로 반려하는 경우엔 근거를 아예 안 적었을 수 있어서).
        st.error("승인 상태로 저장하려면 정답 근거를 최소 1개 입력해야 합니다.")
        return

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

    existing_cases, cases_error = _get("/eval/ragas/cases")
    if cases_error:
        # 조용히 빈 목록으로 넘기면 안 된다 — 중복 질문 검사가 우회돼서 같은 질문이
        # 두 번 저장될 수 있다(CodeRabbit #42 지적). 저장 자체를 막는다.
        st.error(f"기존 케이스 목록을 불러오지 못해 중복 검사를 할 수 없습니다 — 저장을 중단합니다: {cases_error}")
        return
    existing_cases = existing_cases or []

    normalized_new = _normalize_question(question)
    if any(_normalize_question(c["question"]) == normalized_new for c in existing_cases):
        st.error("동일한 질문이 이미 골드셋에 있습니다.")
        _log_validation_attempt(source_document, question, "failure", "동일 질문 중복", inline=True)
        return

    # 유사 질문은 하드 차단하지 않는다(문자열 유사도일 뿐 의미 중복 판정이 아니라서) —
    # 대신 저장 "전"에 경고하고, 폼 아래 확인 체크박스(_render_case_form)를 체크해야만
    # 통과시킨다. 예전엔 저장을 먼저 끝내고 나서야 경고가 떴는데, 그건 차단이 아니라
    # 사후 통지라 의미가 없었다.
    similar_ack_key = f"ragas_similar_ack_{source_document_id}"
    similar = _find_similar_question(source_document_id, question, existing_cases)
    if similar is not None and not st.session_state.get(similar_ack_key, False):
        similar_q, ratio = similar
        st.error(
            f"비슷한 질문이 이미 있습니다(유사도 {ratio:.0%}): {similar_q[:60]} "
            "— 확인 체크박스를 선택한 뒤 다시 저장하세요."
        )
        return

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
    if save_status == "승인":
        # 승인 저장 == 곧바로 실험 세트(active) 편입이 아니다 — 새 문항이 계속 쌓이면
        # 기존 실험 세트가 의도 없이 커진다. 일단 candidate(후보)로 넣고, 전체목록 탭의
        # 후보 화면에서 사람이 골라 실험 세트에 편입한다.
        payload["dataset_membership"] = "candidate"
    if save_status == "반려":
        payload["review_note"] = reject_reason
    case_saved, error_message = _post_json("/eval/ragas/cases", payload)
    if case_saved:
        # 저장 성공 후 폼을 비운다 — 안 비우면 이미 저장된 내용이 계속 남아있어서
        # 다음 케이스를 쓰다가 실수로 중복 저장하기 쉽다.
        st.session_state.pop(f"ragas_question_{source_document_id}", None)
        st.session_state.pop(f"ragas_ground_truth_{source_document_id}", None)
        st.session_state.pop(f"ragas_question_type_{source_document_id}", None)
        st.session_state.pop(f"ragas_save_status_{source_document_id}", None)
        st.session_state.pop(f"ragas_save_reject_reason_{source_document_id}", None)
        st.session_state.pop(similar_ack_key, None)
        for snippet_index in range(snippet_count):
            st.session_state.pop(f"ragas_snippet_{source_document_id}_{snippet_index}", None)
        st.session_state[snippet_count_key] = 1
        if save_status == "승인":
            success_message = (
                f"{case_id}를 승인 후보로 저장했습니다. "
                "전체목록 탭 > 후보에서 실험 세트에 추가할 수 있습니다."
            )
        else:
            success_message = f"{case_id}를 저장했습니다. 상태: {save_status}"
        _queue_flash_message("write", "success", success_message)
        _log_validation_attempt(source_document, question, "success", None)
        st.rerun()
    else:
        st.error(f"저장 실패: {error_message}")
        _log_validation_attempt(
            source_document, question, "failure", f"케이스 저장 API 실패: {error_message}", inline=True,
        )


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
        if st.button("불러오기", key=f"ragas_json_load_{source_document_id}", width="stretch"):
            _load_chatgpt_json_into_form(source_document, snippet_count_key, json_paste)

    # 저장 함수(_save_current_case_form)는 이 위젯들의 key로 session_state에서 값을 읽는다.
    st.text_area("질문", height=80, key=f"ragas_question_{source_document_id}")
    st.text_area("정답", height=100, key=f"ragas_ground_truth_{source_document_id}")

    # 유사 질문 경고는 저장을 누르기 전에 실시간으로 보여준다 — 저장 시점 차단
    # (_save_current_case_form)과 판정 기준(_find_similar_question)을 공유한다.
    similar_ack_key = f"ragas_similar_ack_{source_document_id}"
    current_question = st.session_state.get(f"ragas_question_{source_document_id}", "")
    similar = _find_similar_question(source_document_id, current_question, existing_cases)
    if similar is not None:
        similar_q, ratio = similar
        st.warning(f"비슷한 질문이 이미 있습니다(유사도 {ratio:.0%}): {similar_q[:60]}")
        st.checkbox("유사 질문을 확인했으며 그래도 저장합니다", key=similar_ack_key)
    else:
        st.session_state.pop(similar_ack_key, None)

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

# dataset_membership(현재 데이터셋 소속)과 used_in_experiment(과거 실행 이력)는 서로 다른
# 축이다 — 전자는 "앞으로 뭘 실험에 쓸지"를 사람이 정하는 값, 후자는 eval_runs.jsonl에서
# 그냥 계산되는 값. 하나로 섞으면 "신규 후보를 추가했더니 기존 실험 세트까지 같이
# 흔들린다" 같은 문제가 생긴다(2026-07-20).
_MEMBERSHIP_LABELS = {"active": "실험 세트", "candidate": "후보", "excluded": "제외"}


def _bulk_set_membership(case_ids: list[str], new_membership: str) -> tuple[int, list[str]]:
    """선택된 케이스들의 dataset_membership을 일괄 변경한다. (성공 건수, 실패한 case_id 목록)."""
    succeeded = 0
    failed_ids = []
    for case_id in case_ids:
        ok, _err = _patch_json(f"/eval/ragas/cases/{case_id}", {"dataset_membership": new_membership})
        if ok:
            succeeded += 1
        else:
            failed_ids.append(case_id)
    return succeeded, failed_ids


def _render_membership_bulk_view(cases: list[dict], from_membership: str) -> None:
    """실험 세트/후보/제외 화면 — 여러 건을 한 번에 다른 소속으로 옮기는 용도라
    단일 행 선택이 아니라 체크박스 다중 선택(st.data_editor)을 쓴다."""
    if not cases:
        st.caption("해당하는 케이스가 없습니다.")
        return

    rows = [
        {
            "선택": False,
            "케이스 ID": c["case_id"],
            "문항 유형": c.get("question_type") or "-",
            "질문": c["question"],
            "문서 유형": c.get("source_type") or "-",
            "등록시간": _format_timestamp(c.get("created_at")),
        }
        for c in cases
    ]
    edited_rows = st.data_editor(
        rows, hide_index=True, width="stretch", height=320,
        disabled=["케이스 ID", "문항 유형", "질문", "문서 유형", "등록시간"],
        column_config={
            "선택": st.column_config.CheckboxColumn(width="small"),
            "질문": st.column_config.TextColumn(width="large"),
        },
        key=f"ragas_bulk_editor_{from_membership}",
    )
    selected_ids = [row["케이스 ID"] for row in edited_rows if row["선택"]]
    st.caption(f"{len(selected_ids)}건 선택됨")

    def _run_bulk_move(case_ids: list[str], new_membership: str, verb: str) -> None:
        succeeded, failed_ids = _bulk_set_membership(case_ids, new_membership)
        if failed_ids:
            _queue_flash_message(
                "list", "warning",
                f"{succeeded}건 {verb} 완료, {len(failed_ids)}건 실패: {', '.join(failed_ids)}",
            )
        else:
            _queue_flash_message("list", "success", f"{succeeded}건 {verb} 완료")
        st.rerun()

    if from_membership == "active":
        if st.button(
            f"선택 항목({len(selected_ids)}건)을 후보로 이동", disabled=not selected_ids,
            key=f"ragas_bulk_to_candidate_{from_membership}",
        ):
            _run_bulk_move(selected_ids, "candidate", "후보로 이동")
    elif from_membership == "candidate":
        col_to_active, col_to_excluded = st.columns(2)
        if col_to_active.button(
            f"선택 항목({len(selected_ids)}건)을 실험 세트에 추가", disabled=not selected_ids,
            key="ragas_bulk_to_active", type="primary", width="stretch",
        ):
            _run_bulk_move(selected_ids, "active", "실험 세트 편입")
        if col_to_excluded.button(
            f"선택 항목({len(selected_ids)}건) 제외", disabled=not selected_ids,
            key="ragas_bulk_to_excluded", width="stretch",
        ):
            _run_bulk_move(selected_ids, "excluded", "제외")
    elif from_membership == "excluded":
        if st.button(
            f"선택 항목({len(selected_ids)}건)을 후보로 복원", disabled=not selected_ids,
            key="ragas_bulk_to_candidate_from_excluded",
        ):
            _run_bulk_move(selected_ids, "candidate", "후보로 복원")


def _render_all_cases_view(cases: list[dict]) -> None:
    """상태/문서유형/출처/데이터셋소속/과거사용이력 필터 + 검색 + 정렬 + 단일 케이스
    상세(승인·반려·소속변경·삭제) — 개별 케이스를 자세히 들여다볼 때 쓰는 화면."""
    current_type_filter = st.session_state.get("ragas_review_type_filter", _SOURCE_TYPES[0])
    show_schema_filter = current_type_filter in ("action_schema", "package_overview")

    if show_schema_filter:
        col_status, col_type, col_schema, col_search = st.columns([1, 1, 1, 2])
    else:
        col_status, col_type, col_search = st.columns([1, 1, 2])
        col_schema = None

    with col_status:
        status_filter = st.selectbox(
            "상태", ["전체"] + [_STATUS_LABELS[s] for s in _STATUS_OPTIONS], key="ragas_review_status_filter",
        )
    with col_type:
        type_filter = st.selectbox("문서 유형", _SOURCE_TYPES, key="ragas_review_type_filter")
    schema_filter = None
    if col_schema is not None:
        with col_schema:
            schema_filter_label = st.selectbox(
                "출처(jar/llm_agent)", ["(전체)", "jar", "llm_agent"], key="ragas_review_schema_filter",
            )
            schema_filter = None if schema_filter_label == "(전체)" else schema_filter_label
    with col_search:
        search_query = st.text_input(
            "질문 검색", key="ragas_review_search", placeholder="질문에 포함된 단어로 검색",
        )

    col_membership, col_history, col_sort = st.columns([1, 1, 1])
    with col_membership:
        membership_filter_label = st.selectbox(
            "데이터셋 소속", ["전체", "실험 세트", "후보", "제외", "(미지정)"], key="ragas_review_membership_filter",
        )
    with col_history:
        history_filter = st.selectbox(
            "과거 사용 이력", ["전체", "사용 이력 있음", "사용 이력 없음"], key="ragas_review_history_filter",
            help="chunk_size 실험(eval_runs.jsonl)에 실제로 쓰인 적이 있는지 — "
            "지금 데이터셋 소속과는 별개로, 과거에 한 번이라도 실행됐는지만 본다.",
        )
    with col_sort:
        sort_label = st.selectbox("정렬", list(_CASE_SORT_OPTIONS.keys()), key="ragas_review_sort")

    filtered = cases
    if status_filter != "전체":
        filtered = [c for c in filtered if _STATUS_LABELS.get(c.get("status", "draft")) == status_filter]
    if type_filter != "(전체)":
        filtered = [c for c in filtered if c.get("source_type") == type_filter]
    if schema_filter:
        filtered = [c for c in filtered if c.get("schema_source") == schema_filter]
    if search_query.strip():
        q = search_query.strip().lower()
        filtered = [c for c in filtered if q in c["question"].lower()]
    if membership_filter_label == "(미지정)":
        filtered = [c for c in filtered if not c.get("dataset_membership")]
    elif membership_filter_label != "전체":
        target_membership = next(k for k, v in _MEMBERSHIP_LABELS.items() if v == membership_filter_label)
        filtered = [c for c in filtered if c.get("dataset_membership") == target_membership]
    if history_filter == "사용 이력 있음":
        filtered = [c for c in filtered if c.get("used_in_experiment")]
    elif history_filter == "사용 이력 없음":
        filtered = [c for c in filtered if not c.get("used_in_experiment")]

    sort_field, sort_desc = _CASE_SORT_OPTIONS[sort_label]
    filtered = sorted(filtered, key=lambda c: c.get(sort_field) or "", reverse=sort_desc)

    st.caption(f"전체 {len(cases)}건 · 표시 {len(filtered)}건")

    if not filtered:
        return

    table_rows = [
        {
            "케이스 ID": c["case_id"],
            "상태": _STATUS_LABELS.get(c.get("status", "draft"), c.get("status", "draft")),
            "문서 유형": c.get("source_type") or "-",
            "출처": c.get("schema_source") or "-",
            "데이터셋 소속": _MEMBERSHIP_LABELS.get(c.get("dataset_membership"), "-"),
            "과거 사용": "이력 있음" if c.get("used_in_experiment") else "이력 없음",
            "질문": c["question"],
            "정답 근거": f"{len(c.get('reference_contexts', []))}개",
            "등록시간": _format_timestamp(c.get("created_at")),
            "수정시간": _format_timestamp(c.get("updated_at")),
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
        # review_note를 명시적으로 비운다 — 안 비우면 update_case의 부분병합(merge) 특성상
        # 예전 반려 사유가 그대로 남아, 승인된 케이스인데 반려 사유가 계속 보인다
        # (CodeRabbit #42 지적).
        action_succeeded, error_message = _patch_json(
            f"/eval/ragas/cases/{target_id}",
            {"status": "approved", "review_note": None},
        )
        _handle_review_action(action_succeeded, error_message, f"{target_id}를 승인했습니다.")
    if reject_button_column.button("반려", key=f"ragas_reject_{target_id}", type="primary", width="stretch"):
        patch = {"status": "rejected", "review_note": review_note}
        action_succeeded, error_message = _patch_json(f"/eval/ragas/cases/{target_id}", patch)
        _handle_review_action(action_succeeded, error_message, f"{target_id}를 반려했습니다.")

    case_used_in_experiment = target.get("used_in_experiment", False)
    with delete_button_column.popover(
        "삭제", width="stretch", disabled=case_used_in_experiment,
        help="실험에 사용된 케이스는 결과 재현성을 위해 삭제할 수 없습니다." if case_used_in_experiment else None,
    ):
        st.write("이 평가 케이스를 삭제하시겠습니까?")
        st.caption("삭제한 데이터는 복구할 수 없습니다.")
        if st.button("삭제 확정", key=f"ragas_delete_confirm_{target_id}", type="primary"):
            action_succeeded, error_message = _delete(f"/eval/ragas/cases/{target_id}")
            _handle_review_action(action_succeeded, error_message, f"{target_id}를 삭제했습니다.")

    if target_status == "approved":
        st.text("데이터셋 소속 변경")
        col_membership_select, col_membership_btn = st.columns([2, 1])
        current_membership = target.get("dataset_membership")
        membership_options = ["active", "candidate", "excluded"]
        new_membership = col_membership_select.selectbox(
            "데이터셋 소속 선택", membership_options,
            index=membership_options.index(current_membership) if current_membership in membership_options else 1,
            format_func=lambda m: _MEMBERSHIP_LABELS[m],
            key=f"ragas_membership_select_{target_id}", label_visibility="collapsed",
        )
        if col_membership_btn.button("변경", key=f"ragas_membership_apply_{target_id}", width="stretch"):
            action_succeeded, error_message = _patch_json(
                f"/eval/ragas/cases/{target_id}", {"dataset_membership": new_membership},
            )
            _handle_review_action(
                action_succeeded, error_message,
                f"{target_id}를 {_MEMBERSHIP_LABELS[new_membership]}(으)로 옮겼습니다.",
            )


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

    active_cases = [c for c in cases if c.get("dataset_membership") == "active"]
    candidate_cases = [c for c in cases if c.get("dataset_membership") == "candidate"]
    excluded_cases = [c for c in cases if c.get("dataset_membership") == "excluded"]
    rejected_cases = [c for c in cases if c.get("status") == "rejected"]

    st.caption(
        f"실험 세트 {len(active_cases)} · 후보 {len(candidate_cases)} · "
        f"제외 {len(excluded_cases)} · 반려 {len(rejected_cases)} · 전체 {len(cases)}"
    )

    view = st.radio(
        "보기", ["실험 세트", "후보", "제외", "전체"], key="ragas_list_view", horizontal=True,
    )

    if view == "실험 세트":
        _render_membership_bulk_view(active_cases, from_membership="active")
    elif view == "후보":
        _render_membership_bulk_view(candidate_cases, from_membership="candidate")
    elif view == "제외":
        _render_membership_bulk_view(excluded_cases, from_membership="excluded")
    else:
        _render_all_cases_view(cases)


def _handle_review_action(action_succeeded: bool, error_message: str, success_message: str) -> None:
    """승인/반려/삭제 결과 — 성공 시 바로 rerun하는데 st.success를 여기서 쓰면 뒤이은
    rerun에 지워져서(작성 탭과 같은 이유) flash 메시지로 예약해야 다음 화면에서 보인다.
    실패는 rerun이 없으니 바로 그린다."""
    if action_succeeded:
        _queue_flash_message("list", "success", success_message)
        st.rerun()
    else:
        st.error(f"처리 실패: {error_message}")
