"""런타임 설정 뷰 — LLM 예산 상한 + RAG 검색 파라미터 (RPA-174).

**백오피스 최초의 "조작하는 화면"이다.** 그동안 백오피스는 읽기(관측 조회)만 연결돼 있어
"보는 화면"이었고, 백엔드가 만들어둔 무중단 튜닝 API는 슬라이더를 붙일 곳이 없어 놀고 있었다
(retrieval-params는 RPA-149 이후 연결 0건).

두 설정 모두 백엔드가 **DB 오버라이드 우선 → 없으면 .env 폴백** 구조라, 여기서 바꾸면
재배포/재시작 없이 다음 요청부터 반영된다. append-only라 되돌리기 = 이전 값으로 다시 저장.

⚠️ **예산 상한은 서비스를 막는 값이다.** 잘못 낮추면 정상 사용자가 429를 맞는다 — 실제로
백엔드 최초 구현의 예시값($1/일)이 실측 최대($2.02/사용자-일)보다 낮아, 켰다면 사고였다.
그래서 이 화면은 슬라이더만 주지 않고 **실측 근거를 어디서 뽑는지 함께 안내**한다.
"""

import requests
import streamlit as st

from components.layout import card, page_header, section_header
from config import OPS_BACKEND_URL

_TIMEOUT = 15

# 위젯 key 목록 — 저장 성공·새로고침 시 지워서 DB 최신값으로 재초기화한다.
_BUDGET_KEYS = ("b_sd", "b_sm", "b_gd", "b_gm")
_RETRIEVAL_KEYS = ("r_pool", "r_rerank", "r_k", "r_vw", "r_bw")


def _reset_widgets(keys: tuple[str, ...]) -> None:
    """위젯 session_state를 비워 다음 렌더가 GET 값으로 다시 초기화되게 한다 (#38 리뷰).

    **왜 필요한가**: Streamlit은 `key`가 있으면 위젯 값을 session_state에 고정하고 `value=`를
    첫 렌더에만 쓴다("a key stabilizes the widget's identity and preserves its value"). 그래서
    다른 관리자가 값을 바꿔도 이 화면의 입력칸은 **옛 값을 계속 들고 있고**, 그대로 저장하면
    남의 변경을 조용히 되돌린다(lost update). 저장 성공·명시적 새로고침 때 지워야 한다.
    """
    for k in keys:
        st.session_state.pop(k, None)


def _get(path: str) -> dict | None:
    """설정 조회. 실패는 화면에 사유별로 보여준다(권한/연결/값) — 조용히 비면 안 된다."""
    try:
        r = requests.get(f"{OPS_BACKEND_URL}{path}", timeout=_TIMEOUT)
    except requests.RequestException as e:
        st.error(f"ops-server에 연결하지 못했습니다: {e}")
        return None
    if r.status_code == 403:
        st.error(f"권한 없음 — A360_BACKEND_OPS_API_KEY 설정을 확인하세요.\n\n{r.text}")
        return None
    if r.status_code == 502:
        st.error(f"A360-Assistant-Backend에 연결하지 못했습니다.\n\n{r.text}")
        return None
    if r.status_code != 200:
        st.error(f"조회 실패({r.status_code}): {r.text}")
        return None
    return r.json()


def _put(path: str, body: dict) -> dict | None:
    """설정 저장. 422(값 거부)는 사용자가 고칠 수 있으므로 백엔드 메시지를 그대로 보여준다."""
    try:
        r = requests.put(f"{OPS_BACKEND_URL}{path}", json=body, timeout=_TIMEOUT)
    except requests.RequestException as e:
        st.error(f"ops-server에 연결하지 못했습니다: {e}")
        return None
    if r.status_code == 422:
        st.error(f"값이 거부됐습니다 — 백엔드 검증 실패:\n\n{r.text}")
        return None
    if r.status_code == 403:
        st.error(f"권한 없음: {r.text}")
        return None
    if r.status_code != 200:
        st.error(f"저장 실패({r.status_code}): {r.text}")
        return None
    return r.json()


def _source_badge(data: dict, keys: tuple[str, ...], refresh_key: str) -> None:
    """지금 적용 중인 값이 어디서 왔는지 + 최신값 다시 불러오기.

    새로고침이 필요한 이유: 입력칸은 session_state에 고정돼 있어(위 _reset_widgets 참고) 다른
    관리자가 바꾼 값이 자동으로 안 들어온다. 저장 전에 최신 상태를 확인할 수단이 있어야 한다.
    """
    col1, col2 = st.columns([5, 1])
    with col1:
        if data.get("source") == "db":
            st.caption(
                f"🟢 **적용 중: 여기서 바꾼 값** · 마지막 변경 `{data.get('updated_by') or '?'}` "
                f"· {data.get('updated_at') or '시각 미상'}"
            )
        else:
            st.caption("⚪ **적용 중: 백엔드 .env 기본값** — 아직 여기서 바꾼 적이 없습니다.")
    with col2:
        if st.button("↻ 최신값", key=refresh_key,
                     help="다른 관리자가 바꿨을 수 있습니다 — 입력칸을 현재 적용값으로 되돌립니다"):
            _reset_widgets(keys)
            st.rerun()


def _num_or_none(label: str, value, key: str, help: str) -> float | None:
    """빈칸 = 비활성(null). 0을 '끔'으로 쓰면 백엔드가 거부하므로 빈칸으로 끄게 한다."""
    raw = st.text_input(label, value="" if value is None else str(value), key=key, help=help)
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        st.warning(f"{label}: 숫자가 아닙니다 — 무시하고 비활성으로 둡니다.")
        return None


def _render_budget() -> None:
    section_header(
        "LLM 예산 상한",
        "초과하면 해당 주체의 턴이 429로 차단됩니다. 빈칸 = 그 상한 비활성.",
    )
    data = _get("/settings/budget-limits")
    if data is None:
        return
    _source_badge(data, _BUDGET_KEYS, "b_refresh")

    st.info(
        "⚠️ **서비스를 막는 값입니다.** 너무 낮으면 정상 사용자가 429를 맞고, 너무 높으면 방어가 "
        "안 됩니다. 감으로 정하지 마세요 — 실측 권장값은 백엔드에서 뽑습니다:\n\n"
        "```\npython scripts/budget_calibration_report.py\n```\n"
        "주체 = 로그인 사용자면 user, 익명이면 session 단위입니다."
    )

    with card("budget_form"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**주체별** — 누가 얼마 썼나 (chargeback)")
            sd = _num_or_none("일 상한 (USD)", data.get("subject_daily_usd"), "b_sd",
                              "한 사용자(익명이면 한 세션)가 하루에 쓸 수 있는 상한")
            sm = _num_or_none("월 상한 (USD)", data.get("subject_monthly_usd"), "b_sm",
                              "월 상한은 일 상한보다 커야 합니다 (작으면 백엔드가 거부)")
        with c2:
            st.markdown("**전역** — OpenAI 청구서 자체 보호")
            gd = _num_or_none("일 상한 (USD)", data.get("global_daily_usd"), "b_gd",
                              "서비스 전체가 하루에 쓸 수 있는 상한")
            gm = _num_or_none("월 상한 (USD)", data.get("global_monthly_usd"), "b_gm",
                              "월 상한은 일 상한보다 커야 합니다")

        if st.button("예산 상한 저장", key="b_save", type="primary"):
            saved = _put("/settings/budget-limits", {
                "subject_daily_usd": sd, "subject_monthly_usd": sm,
                "global_daily_usd": gd, "global_monthly_usd": gm,
            })
            if saved:
                st.success("저장했습니다 — 재배포 없이 다음 턴부터 반영됩니다.")
                # 위젯을 비워 다음 렌더가 **DB에서 다시 읽은 값**으로 초기화되게 한다 —
                # 저장된 게 정말 뭔지 확인시키고, 이후 남의 변경도 제때 반영된다.
                _reset_widgets(_BUDGET_KEYS)
                st.rerun()


def _render_retrieval() -> None:
    section_header(
        "RAG 검색 파라미터",
        "하이브리드 검색(RRF)·리랭커 튜닝. 재시작 없이 다음 검색부터 반영됩니다.",
    )
    data = _get("/settings/retrieval-params")
    if data is None:
        return
    _source_badge(data, _RETRIEVAL_KEYS, "r_refresh")

    with card("retrieval_form"):
        c1, c2 = st.columns(2)
        with c1:
            pool = st.number_input(
                "후보 풀 크기", min_value=1, max_value=500,
                value=int(data.get("candidate_pool_size", 50)), key="r_pool",
                help="벡터·BM25 각 branch에서 뽑는 후보 수 (RRF 입력 폭)")
            rerank = st.number_input(
                "리랭크 후보 수", min_value=1, max_value=200,
                value=int(data.get("rerank_candidates", 20)), key="r_rerank",
                help="RRF 융합 후 리랭커에 넘길 상한 — 키우면 정확도↑ 비용↑")
            k = st.number_input(
                "RRF k", min_value=1, max_value=1000,
                value=int(data.get("rrf_k", 60)), key="r_k",
                help="클수록 상위 순위 가중이 완만해집니다")
        with c2:
            vw = st.number_input(
                "벡터 가중치", min_value=0.0, max_value=10.0, step=0.1,
                value=float(data.get("vector_weight", 1.0)), key="r_vw",
                help="키우면 의미 검색 비중↑")
            bw = st.number_input(
                "BM25 가중치", min_value=0.0, max_value=10.0, step=0.1,
                value=float(data.get("bm25_weight", 1.0)), key="r_bw",
                help="키우면 키워드 매칭 비중↑")

        if st.button("검색 파라미터 저장", key="r_save", type="primary"):
            saved = _put("/settings/retrieval-params", {
                "candidate_pool_size": int(pool), "rerank_candidates": int(rerank),
                "rrf_k": int(k), "vector_weight": float(vw), "bm25_weight": float(bw),
            })
            if saved:
                st.success("저장했습니다 — 재시작 없이 다음 검색부터 반영됩니다.")
                _reset_widgets(_RETRIEVAL_KEYS)
                st.rerun()


def render() -> None:
    page_header("런타임 설정")
    st.caption(
        "A360-Assistant-Backend의 동작을 재배포 없이 조정합니다. 변경은 이력으로 남고"
        "(누가·언제), 되돌리려면 이전 값으로 다시 저장하면 됩니다."
    )
    budget_tab, retrieval_tab = st.tabs(["LLM 예산 상한", "RAG 검색 파라미터"])
    with budget_tab:
        _render_budget()
    with retrieval_tab:
        _render_retrieval()
