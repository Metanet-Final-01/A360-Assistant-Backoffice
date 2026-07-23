"""평가/입력 데이터셋 관리 (RPA-136) — Workflow 골드셋과 Workflow 입력
데이터셋(업무정의서 원문)이 리포 내 정적 JSON 파일로만 존재해 팀원이 직접 편집해야
했다. 조회·업로드(파일 교체)·수동 입력(폼) 세 가지 방식으로 웹에서 관리한다.
RAGAS 골드셋은 ragas_datasets.py의 별도 페이지로 분리됐다(문서 브라우저·ChatGPT
JSON 자동 채우기·검증 로그 등 RAGAS 전용 흐름이 커져서).

2개 탭(Workflow 평가/Workflow 입력) 전부 공통 레이아웃을 쓴다 — 검색창 →
페이지네이션(10개씩, 내부 스크롤 없음)된 목록(체크박스로 다중 선택 → 목록 우측
상단 "삭제" 버튼 → 확인 팝업) → 목록 하단 좌측 "교체"(업로드 팝업) / 우측
"생성"(수동 입력 팝업). 팝업은 전부 st.dialog — 위젯 상호작용은 다이얼로그
자체만 rerun하고(st.fragment와 동일 동작), 성공 시 dialog 안에서 st.rerun()을
불러야 닫힌다."""

import json
from collections.abc import Callable
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import OPS_BACKEND_URL

_SESSION = requests.Session()
_PAGE_SIZE = 10


def render() -> None:
    # RAGAS 탭은 ragas_datasets.py의 별도 페이지로 옮겼다 — 여기서는 Workflow만 관리한다.
    page_header(
        "평가/입력 데이터셋 관리",
        "Workflow 골드셋과 Workflow 입력 데이터셋(업무정의서 원문)을 조회·업로드·수동 등록합니다.",
    )
    # 페이지네이션(pills) 가운데 정렬 CSS는 여기 한 번만 넣는다 — st.markdown 자체가 그 자리에
    # 높이 0짜리 element-container를 만들고 Streamlit이 형제 사이에 16px gap을 넣어서,
    # 탭 안 버튼 행 바로 옆에 넣었더니 그 행 전체가 아래로 밀렸었다(실제로 겪은 버그).
    # 페이지 맨 위 같은 눈에 안 띄는 자리에 한 번만 넣어서 그 여백이 다른 곳에 영향 없게 한다.
    st.markdown(
        """
        <style>
        div[class*="_pills_wrap"] { align-items: center !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    tab_wf_goldset, tab_wf_input = st.tabs(["Workflow 평가 데이터셋", "Workflow 입력 데이터셋"])
    with tab_wf_goldset:
        _render_workflow_goldset_tab()
    with tab_wf_input:
        _render_workflow_input_tab()


def _get(path: str) -> tuple[object | None, str | None]:
    try:
        resp = _SESSION.get(f"{OPS_BACKEND_URL}{path}", timeout=10)
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


def _post_upload(path: str, file) -> tuple[bool, str]:
    try:
        resp = _SESSION.post(
            f"{OPS_BACKEND_URL}{path}",
            files={"file": (file.name, file.getvalue(), "application/json")}, timeout=10,
        )
        if resp.status_code == 200:
            return True, f"{resp.json().get('saved', '?')}건 저장됨"
        return False, resp.json().get("detail", resp.text)
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)


def _search_box(card_key: str) -> str:
    return st.text_input("검색(모든 필드 대상 부분 일치)", key=f"{card_key}_search", placeholder="예: excel, RSS...")


def _search_filter(rows: list[dict], query: str) -> list[dict]:
    if not query.strip():
        return rows
    q = query.strip().lower()
    return [r for r in rows if q in json.dumps(r, ensure_ascii=False).lower()]


# ── 공통: 검색 → 페이지네이션 목록 → 삭제/교체/생성 ─────────────────────


def _paginate(card_key: str, rows: list[dict], query: str) -> tuple[list[dict], int, int]:
    query_key, page_key = f"{card_key}_last_query", f"{card_key}_page"
    if st.session_state.get(query_key) != query:
        st.session_state[query_key] = query
        st.session_state[page_key] = 1  # 검색어가 바뀌면 이전 페이지 번호가 무의미해진다.
    total_pages = max(1, -(-len(rows) // _PAGE_SIZE))
    page = min(max(st.session_state.get(page_key, 1), 1), total_pages)
    st.session_state[page_key] = page
    start = (page - 1) * _PAGE_SIZE
    return rows[start:start + _PAGE_SIZE], page, total_pages


_PAGE_WINDOW = 10  # 페이지 번호를 한 번에 이만큼(10개) 묶어서 보여준다 — «‹›»는 이 묶음(블록) 단위로 이동한다.

# 삭제·교체·생성은 각자 열을 width="stretch"로 꽉 채운다. 페이지네이션(«‹ 번호들 ›»)은
# st.pills 위젯 하나가 통째로 들어가는데, pills는 내용이 열 폭보다 넓으면 다음 줄로
# 줄바꿈돼 버려서(«‹ 최대 14개» 옵션이 한 줄에 다 들어가야 한다) 그 열에 압도적으로
# 넓은 비중을 준다 — 나머지(삭제/교체/생성/스페이서)는 상대적으로 작게.
_ACTION_WEIGHT = 6
_SPACER_WEIGHT = 2
_PILLS_WEIGHT = 78  # 6*3(액션 3개) + 2*2(스페이서 2개) + 78 = 100 중 78 — 페이지네이션에 폭 대부분을 몰아준다.


def _page_window(page: int, total_pages: int) -> list[int]:
    """현재 페이지가 속한 10개 단위 블록(1-10, 11-20, ...)을 반환 — «‹›»는 이 블록
    경계를 기준으로 처음/이전 블록/다음 블록/끝으로 이동한다(슬라이딩이 아니라 고정 블록)."""
    if total_pages <= 1:
        return []
    block_start = (page - 1) // _PAGE_WINDOW * _PAGE_WINDOW + 1
    block_end = min(total_pages, block_start + _PAGE_WINDOW - 1)
    return list(range(block_start, block_end + 1))


def _render_bottom_row(
    card_key: str,
    page: int,
    total_pages: int,
    selected_ids: list[str],
    delete_prefix: str,
    list_cache_key: str,
    upload_help: str,
    upload_path: str,
    create_dialog: Callable[[], None],
) -> None:
    """삭제(좌) · 페이지네이션(중앙) · 교체+생성(우, 서로 붙여서)을 한 행에 그린다.
    항상 그려지는 행이라(페이지네이션이 없어도 교체/생성은 남는다) 삭제 버튼이 있고
    없고에 따라 이 행 자체의 높이가 바뀌지 않는다 — 목록이 밀리지 않는다.
    좌우 스페이서 열 비중을 맞춰 페이지네이션이 가운데 오게 하고, 각 액션 버튼은
    width="stretch"로 자기 열을 꽉 채워 목록의 좌/우 경계에 붙는다.

    페이지 번호/화살표는 st.button이 아니라 st.pills(세그먼트 선택 위젯)로 그린다 —
    button(width=int)으로 좁혔을 때 안쪽 글자가 중앙에 오지 않는 문제가 있었는데,
    pills는 그 자체가 "칩 여러 개 중 하나 고르기"용으로 만들어진 위젯이라 라벨이
    항상 제대로 가운데에 온다. 옵션 목록(«‹번호들›»)을 그대로 클릭 대상으로 쓰고,
    페이지가 바뀔 때마다 key에 페이지 번호를 넣어 위젯을 새로 만들어서(default가
    매번 새로 적용되어) 화살표로 이동해도 선택 표시가 항상 실제 현재 페이지를
    따라가게 한다. «/»는 처음/끝 페이지로, ‹/›는 10페이지 블록 단위로 이전/다음
    블록의 첫 페이지로 이동한다(_page_window와 동일한 블록 경계)."""
    page_numbers = _page_window(page, total_pages)

    def _goto(target: int) -> None:
        st.session_state[f"{card_key}_page"] = target
        st.rerun()

    # 우측엔 액션 버튼이 2개(교체+생성), 좌측엔 1개(삭제 자리)뿐이라 스페이서를 그냥 똑같이
    # 두면 페이지네이션 열 자체가 좌우 비대칭이 되어 화면 중앙보다 왼쪽으로 치우친다 —
    # 좌측 스페이서에 액션 버튼 하나 몫을 더해 좌우 총 비중을 맞춘다.
    left_spacer = _SPACER_WEIGHT + _ACTION_WEIGHT
    if page_numbers:
        weights = [_ACTION_WEIGHT, left_spacer, _PILLS_WEIGHT, _SPACER_WEIGHT, _ACTION_WEIGHT, _ACTION_WEIGHT]
    else:
        # 페이지네이션이 없을 때(항목 0개 등)도 가운데 스페이서를 위와 똑같은 총 비중으로
        # 맞춰야 한다 — 안 그러면 전체 가중치 합이 확 줄어서(22 vs 106) 교체/생성 열이
        # 상대적으로 훨씬 넓어지고, width="stretch"라 버튼도 그만큼 비정상적으로 커진다.
        mid_spacer = left_spacer + _PILLS_WEIGHT + _SPACER_WEIGHT
        weights = [_ACTION_WEIGHT, mid_spacer, _ACTION_WEIGHT, _ACTION_WEIGHT]

    with st.container(key=f"{card_key}_bottom_row"):
        cols = st.columns(weights, gap="xxsmall")

        if selected_ids:  # 선택이 없으면 자리만 비워두고 버튼 자체를 그리지 않는다(흐릿하게 두지 않음).
            if cols[0].button("삭제", key=f"{card_key}_delete_open", type="primary", width="stretch"):
                _confirm_delete_dialog(selected_ids, delete_prefix, list_cache_key)

        if page_numbers:
            with cols[2]:
                pills_wrap = st.container(key=f"{card_key}_pills_wrap")
                current_label = str(page)
                block_start = page_numbers[0]
                options = ["«", "‹", *(str(p) for p in page_numbers), "›", "»"]
                with pills_wrap:
                    selected = st.pills(
                        "페이지", options=options, default=current_label,
                        key=f"{card_key}_pills_p{page}", label_visibility="collapsed",
                    )
                if selected == "«":
                    _goto(1)
                elif selected == "‹":
                    _goto(max(1, block_start - _PAGE_WINDOW))
                elif selected == "›":
                    _goto(min(total_pages, block_start + _PAGE_WINDOW))
                elif selected == "»":
                    _goto(total_pages)
                elif selected and selected != current_label:
                    _goto(int(selected))

        if cols[-2].button("교체", key=f"{card_key}_replace_open", width="stretch"):
            _upload_dialog(upload_help, list_cache_key, upload_path, card_key)
        if cols[-1].button("생성", key=f"{card_key}_create_open", type="primary", width="stretch"):
            create_dialog()


def _render_dataset_card(
    card_key: str,
    title: str,
    description: str,
    rows: list[dict],
    id_field: str,
    columns_fn: Callable[[dict], dict],
    empty_columns: list[str],
    column_widths: dict[str, str | int],
    delete_prefix: str,
    list_cache_key: str,
    upload_path: str,
    upload_help: str,
    create_dialog: Callable[[], None],
) -> None:
    with card(card_key):
        section_header(title, description)
        query = _search_box(card_key)
        filtered = _search_filter(rows, query)
        page_rows, page, total_pages = _paginate(card_key, filtered, query)

        count_slot = st.container()  # 선택 개수는 아래 dataframe을 그려야 알 수 있어서, 자리만 검색창 밑에 먼저 잡아두고 나중에 채운다.

        df = pd.DataFrame([columns_fn(r) for r in page_rows]) if page_rows else pd.DataFrame(columns=empty_columns)
        event = st.dataframe(
            df, width="stretch", hide_index=True, on_select="rerun", selection_mode="multi-row",
            column_config={name: st.column_config.Column(width=w) for name, w in column_widths.items()},
            key=f"{card_key}_table_p{page}_{query}",
        )
        selected_ids = [page_rows[i][id_field] for i in event.selection.rows if i < len(page_rows)]

        with count_slot:
            selected_suffix = f" · {len(selected_ids)}개 선택됨" if selected_ids else ""
            st.caption(f"전체 {len(filtered)}개 중 {len(page_rows)}개 표시{selected_suffix}")

        _render_bottom_row(
            card_key, page, total_pages, selected_ids, delete_prefix, list_cache_key,
            upload_help, upload_path, create_dialog,
        )


@st.dialog("삭제 확인")
def _confirm_delete_dialog(ids: list[str], delete_prefix: str, list_cache_key: str) -> None:
    st.write(f"정말 삭제하시겠습니까? ({len(ids)}건)")
    with st.expander("대상 목록", expanded=len(ids) <= 10):
        st.write(", ".join(str(i) for i in sorted(ids, key=str)))

    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("취소", width="stretch"):
        st.rerun()
    if confirm_col.button("확인", type="primary", width="stretch"):
        deleted, already_gone, errors = 0, 0, []
        for target in ids:
            try:
                resp = _SESSION.delete(f"{OPS_BACKEND_URL}{delete_prefix}/{quote(str(target), safe='')}", timeout=10)
                # 404(대상 없음=이미 삭제됨)는 실패로 치지 않는다 — 부분 실패 후 다시 "확인"을
                # 누르면 이 목록(ids)이 그대로 재사용돼 이미 지워진 대상까지 다시 삭제 요청을
                # 보내는데, 그걸 에러로 잡으면 재시도할 때마다 실패 목록이 늘어나기만 한다. 다만
                # 실제 삭제(200)와 이미 없었음(404)을 결과 건수에 뭉뚱그리면 정말 문제(잘못된
                # id 선택 등)가 있어도 "정상 삭제"로 보여 놓칠 수 있어 메시지에서는 구분한다.
                if resp.status_code == 200:
                    deleted += 1
                elif resp.status_code == 404:
                    already_gone += 1
                else:
                    errors.append(f"{target}: {resp.json().get('detail', resp.text)}")
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{target}: {exc}")
        st.session_state.pop(list_cache_key, None)
        if errors:
            st.error("일부 삭제에 실패했습니다 — " + "; ".join(errors))
        elif deleted == 0:
            st.warning(f"선택한 {already_gone}건이 이미 삭제된 상태였습니다 — 새로 삭제된 건 없습니다.")
            st.rerun()
        else:
            suffix = f" (이미 삭제된 {already_gone}건 제외)" if already_gone else ""
            st.success(f"{deleted}건 삭제했습니다{suffix}.")
            st.rerun()  # 전부 성공했을 때만 닫는다 — 실패분이 있으면 메시지를 보고 다시 시도/취소하게 남겨둔다.


@st.dialog("파일로 교체")
def _upload_dialog(help_text: str, list_cache_key: str, upload_path: str, key: str) -> None:
    st.caption(help_text)
    file = st.file_uploader("JSON 파일", type=["json"], key=f"{key}_dialog_uploader")
    if file is not None and st.button("업로드해서 교체", type="primary"):
        ok, msg = _post_upload(upload_path, file)
        if ok:
            st.session_state.pop(list_cache_key, None)
            st.success(f"업로드 완료 — {msg}")
            st.rerun()
        else:
            st.error(f"업로드 실패: {msg}")


# ── Workflow 평가 데이터셋 ────────────────────────────────────────────


def _render_workflow_goldset_tab() -> None:
    data, err = _get("/eval/workflow/cases")
    if err:
        st.warning(f"불러오지 못했습니다: {err}")
        return
    _render_dataset_card(
        card_key="workflow_goldset",
        title="Workflow 평가 데이터셋",
        description="실제 커뮤니티 봇 기반 골드셋 — pm4py/WorFBench 채점의 정답(expected).",
        rows=data,
        id_field="id",
        columns_fn=lambda c: {
            "id": c["id"], "source_bot": c["source_bot"], "difficulty": c.get("difficulty"),
            "task": c["input"]["task"][:80], "액션 수": len(c["expected"]["actions"]),
        },
        empty_columns=["id", "source_bot", "difficulty", "task", "액션 수"],
        column_widths={"id": "small", "source_bot": "medium", "difficulty": "small", "액션 수": "small", "task": 500},
        delete_prefix="/eval/workflow/cases",
        list_cache_key="/eval/workflow/cases",
        upload_path="/eval/workflow/cases/upload",
        upload_help="전체 골드셋 배열([...])을 통째로 교체합니다 — WorkflowCase 스키마 검증을 통과해야 저장됩니다.",
        create_dialog=_workflow_goldset_create_dialog,
    )


@st.dialog("Workflow 케이스 생성", width="large")
def _workflow_goldset_create_dialog() -> None:
    st.caption(
        "정답 액션(최대 8개)의 package/action 쌍을 입력합니다 — in_catalog는 등록 시 전부 True로 저장되니, "
        "실제 카탈로그에 없는 액션이면 '교체' 업로드로 in_catalog=false를 직접 지정하세요."
    )
    # 액션 행이 최대 8개라 다이얼로그 전체가 화면 밖으로 넘칠 수 있다 — 내용만 고정 높이
    # 컨테이너 안에 넣어 그 안에서 스크롤되게 한다(다이얼로그 자체는 뷰포트 안에 유지).
    # st.container(height=)는 정수 픽셀만 받고 %/vh는 지원하지 않는다 — 화면 크기에 진짜
    # 반응하게 CSS/JS로 덮어써보려 했지만(Playwright로 직접 확인), Streamlit이 이 높이를
    # React 상태로 들고 있어서 강제로 바꿔도 즉시 원래 값(450px)으로 되돌아갔다. 정적
    # 값으로 두되 Streamlit 권장 상한(500px)에 맞춘다.
    with st.container(height=500, key="workflow_goldset_create_scroll"):
        with st.form("workflow_goldset_manual_add_form"):
            col1, col2, col3 = st.columns(3)
            case_id = col1.text_input("id", placeholder="bot-18")
            source_bot = col2.text_input("source_bot", placeholder="MyNewBot")
            difficulty = col3.selectbox("difficulty", ["easy", "medium", "hard"], index=1)
            task = st.text_area("task (업무 한 줄 요약)", height=80)

            st.markdown("**정답 액션 (최대 8개, package를 비워두면 미사용)**")
            action_rows = []
            for i in range(8):
                acol1, acol2 = st.columns(2)
                pkg = acol1.text_input("package", key=f"wf_action_pkg_{i}")
                act = acol2.text_input("action", key=f"wf_action_act_{i}")
                if pkg.strip() and act.strip():
                    action_rows.append((pkg.strip(), act.strip()))

            submitted = st.form_submit_button("Workflow 케이스 등록", type="primary")

    if submitted:
        packages = sorted({pkg for pkg, _ in action_rows})
        payload = {
            "id": case_id.strip(), "source_bot": source_bot.strip(), "difficulty": difficulty,
            "input": {"task": task.strip()},
            "expected": {
                "packages": packages, "packages_in_catalog": packages,
                "actions": [{"package": pkg, "action": act, "in_catalog": True} for pkg, act in action_rows],
            },
            "scoreable": True,
        }
        ok, msg = _post_json("/eval/workflow/cases", payload)
        if ok:
            st.session_state.pop("/eval/workflow/cases", None)
            st.success("등록했습니다.")
            st.rerun()
        else:
            st.error(f"등록 실패: {msg}")


# ── Workflow 입력 데이터셋 ────────────────────────────────────────────


def _render_workflow_input_tab() -> None:
    data, err = _get("/eval/workflow/input-dataset")
    if err:
        st.warning(f"불러오지 못했습니다: {err}")
        return
    rows = [{"source_bot": k, "text": v} for k, v in data.items()]
    _render_dataset_card(
        card_key="workflow_input",
        title="Workflow 입력 데이터셋",
        description=(
            "source_bot별 상세 업무정의서 원문 — Workflow 라이브 러너가 골드셋 한 줄 요약보다 "
            "우선 사용합니다(RPA-135, 과거 결과와 공정 비교를 위해)."
        ),
        rows=rows,
        id_field="source_bot",
        columns_fn=lambda r: {"source_bot": r["source_bot"], "길이": len(r["text"]), "미리보기": r["text"][:80]},
        empty_columns=["source_bot", "길이", "미리보기"],
        column_widths={"source_bot": "medium", "길이": "small", "미리보기": 600},
        delete_prefix="/eval/workflow/input-dataset",
        list_cache_key="/eval/workflow/input-dataset",
        upload_path="/eval/workflow/input-dataset/upload",
        upload_help='전체 {"source_bot": "원문"} 객체를 통째로 교체합니다.',
        create_dialog=lambda: _workflow_input_create_dialog(list(data.keys())),
    )


@st.dialog("Workflow 입력 데이터셋 등록/수정")
def _workflow_input_create_dialog(existing_bots: list[str]) -> None:
    st.caption("이미 있는 source_bot을 입력하면 원문을 덮어씁니다.")
    options = ["(새로 입력)"] + existing_bots
    select_key = "workflow_input_source_bot_select"
    # 고정 key라 세션에 이전 선택값이 남는데, 그 사이 해당 source_bot이 삭제되면
    # options에 없는 값이 남아 selectbox 생성 시 예외가 난다 — 유효하지 않으면 미리 지운다.
    if st.session_state.get(select_key) not in options:
        st.session_state.pop(select_key, None)
    with st.form("workflow_input_manual_add_form"):
        source_bot = st.selectbox("source_bot", options, key=select_key)
        if source_bot == "(새로 입력)":
            source_bot = st.text_input("새 source_bot 이름")
        text = st.text_area("업무정의서 원문", height=220)
        submitted = st.form_submit_button("등록/수정", type="primary")
    if submitted:
        ok, msg = _post_json(
            "/eval/workflow/input-dataset", {"source_bot": source_bot.strip(), "text": text.strip()},
        )
        if ok:
            st.session_state.pop("/eval/workflow/input-dataset", None)
            st.success("저장했습니다.")
            st.rerun()
        else:
            st.error(f"저장 실패: {msg}")
