"""A360-Assistant-Backend(실서비스 백엔드)의 모니터링용 admin API를 호출한다.

**읽기 + 런타임 설정 쓰기** (RPA-174). 오랫동안 읽기 전용이었으나, 백엔드가 만들어둔 무중단
튜닝 API(retrieval-params RPA-149 / budget-limits RPA-173)를 쓸 화면이 없어 놀고 있어서
쓰기 경로를 연다. 쓰기는 GET과 **같은 인증·에러 처리**를 재사용한다(_authed_request).

인증 두 방식 (A360_BACKEND_OPS_API_KEY 설정 여부로 자동 선택):
- **서비스 API 키(권장)**: A360_BACKEND_OPS_API_KEY를 X-API-Key 헤더로 보낸다. 머신(M2M)
  신원이라 사람 로그인을 재사용하지 않는다(백엔드 RPA-118). 백엔드 OPS_API_KEY와 일치해야 함.
- **관리자 로그인(폴백)**: 키가 없으면 A360_BACKEND_ADMIN_EMAIL/PASSWORD로 로그인해 JWT를
  메모리에만 캐시한다(디스크 미기록). 토큰 만료(401)는 1회 재로그인 후 재시도.
  → 백엔드 RPA-118 머지 전에도 동작하도록 남겨둔 하위호환 경로.

- /api/rag/logs/recent: 인증 불필요(require_debug_enabled 게이트 — APP_ENV=production이면
  403, 로컬 개발이면 열려 있음). 운영 백엔드를 가리키면 403이 뜨는 게 정상 동작이다.
- /health: 인증 불필요 — 생존 감시(probe)용. 503(degraded/unhealthy)도 '도달함'으로 본다.
"""

import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

BACKEND_URL = (os.getenv("A360_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
_ADMIN_EMAIL = os.getenv("A360_BACKEND_ADMIN_EMAIL", "")
_ADMIN_PASSWORD = os.getenv("A360_BACKEND_ADMIN_PASSWORD", "")
_OPS_API_KEY = os.getenv("A360_BACKEND_OPS_API_KEY", "").strip()

_token_cache: str | None = None


class BackendAuthError(RuntimeError):
    """관리자 인증/인가 실패 (401/403) — 메시지에 백엔드가 준 detail을 그대로 담는다."""


class BackendUnavailableError(RuntimeError):
    """A360-Assistant-Backend에 연결 자체가 안 될 때."""


class BackendValidationError(ValueError):
    """백엔드가 값을 거부했을 때 (400/422) — 백엔드가 준 detail을 그대로 담는다 (RPA-174).

    쓰기 경로에서만 의미가 있다. 인증 실패(BackendAuthError)·연결 실패(BackendUnavailableError)와
    구분해야 화면이 "내 입력이 틀렸다"와 "시스템이 문제다"를 다르게 보여줄 수 있다 — 예산 상한의
    '월<일 거부' 같은 규칙 위반은 사용자가 고칠 수 있는 것이므로 원문을 보여줘야 한다.
    """


def credentials_configured() -> bool:
    """API 키 또는 관리자 로그인 자격 중 하나라도 있으면 True."""
    return bool(_OPS_API_KEY) or bool(_ADMIN_EMAIL and _ADMIN_PASSWORD)


def _client() -> httpx.Client:
    return httpx.Client(base_url=BACKEND_URL, timeout=10.0)


def _login(client: httpx.Client) -> str:
    if not (_ADMIN_EMAIL and _ADMIN_PASSWORD):
        raise BackendAuthError(
            "인증 자격이 없습니다 — A360_BACKEND_OPS_API_KEY(권장) 또는 "
            "A360_BACKEND_ADMIN_EMAIL/PASSWORD를 backend/.env에 설정하세요."
        )
    resp = client.post("/api/auth/login", json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD})
    if resp.status_code != 200:
        raise BackendAuthError(f"로그인 실패({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def _raise_for_auth(resp: httpx.Response) -> None:
    if resp.status_code == 403:
        hint = (
            "A360_BACKEND_OPS_API_KEY가 Backend OPS_API_KEY와 일치하는지"
            if _OPS_API_KEY
            else "A360_BACKEND_ADMIN_EMAIL이 Backend의 ADMIN_EMAILS 시드에 있는지(is_admin)"
        )
        raise BackendAuthError(f"권한 없음(403): {resp.text} — {hint} 확인하세요.")


def _raise_for_validation(resp: httpx.Response) -> None:
    """400/422는 사용자가 고칠 수 있는 값 오류 — 백엔드 detail을 그대로 올린다 (RPA-174).

    raise_for_status에 맡기면 HTTPStatusError가 되어 화면이 "시스템 오류"로 오인시킨다.
    """
    if resp.status_code in (400, 422):
        raise BackendValidationError(f"값이 거부됐습니다({resp.status_code}): {resp.text}")


def _authed_request(method: str, path: str, params: dict | None = None,
                    json: dict | None = None) -> dict:
    """인증된 백엔드 admin 호출. GET/PUT 공용 — 인증·재로그인·에러 분류를 한 곳에 둔다.

    쓰기를 위해 메서드만 파라미터화했다(RPA-174). 이 로직을 PUT용으로 복제하면 토큰 재로그인·
    403 힌트·연결 실패 구분이 두 벌로 갈려 한쪽만 고치는 사고가 난다.
    """
    global _token_cache
    with _client() as client:
        try:
            if _OPS_API_KEY:
                # 서비스 키 경로 — 로그인 없이 X-API-Key. 만료/재로그인 개념 없음.
                resp = client.request(method, path, params=params, json=json,
                                      headers={"X-API-Key": _OPS_API_KEY})
                if resp.status_code == 401:
                    raise BackendAuthError(f"인증 실패(401): {resp.text}")
                _raise_for_auth(resp)
                _raise_for_validation(resp)
                resp.raise_for_status()
                return resp.json()
            # 관리자 로그인 폴백 경로
            if _token_cache is None:
                _token_cache = _login(client)
            headers = {"Authorization": f"Bearer {_token_cache}"}
            resp = client.request(method, path, params=params, json=json, headers=headers)
            if resp.status_code == 401:
                _token_cache = _login(client)
                headers = {"Authorization": f"Bearer {_token_cache}"}
                resp = client.request(method, path, params=params, json=json, headers=headers)
            if resp.status_code == 401:
                # 재로그인 직후에도 401이면 토큰 만료가 아니라 자격증명 자체가 틀렸다는
                # 뜻 — httpx.HTTPError로 흘려보내면 502(연결 실패)로 오인되니 여기서
                # 바로 인증 오류로 처리한다.
                raise BackendAuthError(
                    f"인증 실패(401, 재로그인 후에도 동일): {resp.text} — "
                    "A360_BACKEND_ADMIN_EMAIL/PASSWORD가 올바른지 확인하세요."
                )
            _raise_for_auth(resp)
            _raise_for_validation(resp)
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as e:
            # 전송 실패(네트워크·타임아웃)만 '연결 실패'로. raise_for_status가 던지는
            # HTTPStatusError(4xx/5xx)는 그대로 전파 — 응답 상태를 '연결 실패'로 오인시키지 않는다.
            raise BackendUnavailableError(f"{BACKEND_URL} 연결 실패: {e}") from e


def _authed_get(path: str, params: dict) -> dict:
    return _authed_request("GET", path, params=params)


def _authed_put(path: str, body: dict) -> dict:
    return _authed_request("PUT", path, json=body)


def probe_health() -> dict:
    """백엔드 /health 생존 감시(무인증). 네트워크 실패=도달 못함, 503도 도달함으로 본다.

    데이터 조회(admin API)와 분리된 경량 프로브 — 백엔드가 죽었다는 사실 자체를 감지한다.
    시크릿/예외 원문은 담지 않는다(백엔드 /health가 status·checks만 반환).
    """
    from datetime import datetime, timezone

    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        with httpx.Client(base_url=BACKEND_URL, timeout=3.0) as client:
            resp = client.get("/health")
    except httpx.HTTPError:
        # 원문 예외(str(e))는 내부 호스트·포트·프록시 등 배포 세부를 노출하므로 상태 응답에
        # 넣지 않는다 — 고정 코드만(CodeRabbit #9). 상세는 서버 로그로만.
        logger.warning("백엔드 health 프로브 실패", exc_info=True)
        return {"reachable": False, "status": "unreachable", "checks": {},
                "http_status": None, "error": "connection_failed", "checked_at": checked_at}
    body = {}
    try:
        body = resp.json()
    except ValueError:
        pass
    return {
        "reachable": True,
        "status": body.get("status", "healthy" if resp.status_code == 200 else "unhealthy"),
        "checks": body.get("checks", {}),
        "http_status": resp.status_code,
        "error": None,
        "checked_at": checked_at,
    }


def fetch_audit_logs(limit: int = 500, method: str | None = None, status_code: int | None = None, user_id: str | None = None, since: str | None = None) -> dict:
    params = {k: v for k, v in {"limit": limit, "method": method, "status_code": status_code, "user_id": user_id, "since": since}.items() if v is not None}
    return _authed_get("/api/admin/audit-logs", params)


def fetch_rag_events(request_id: str | None = None, limit: int = 500) -> dict:
    """RAG 파이프라인 단계 로그(RPA-128) — 관측 DB의 rag_events 테이블, 인증 필요.
    기존 /api/rag/logs/recent(비인증·프로덕션 비활성)와 달리 운영 환경에서도 조회 가능."""
    params = {k: v for k, v in {"request_id": request_id, "limit": limit}.items() if v is not None}
    return _authed_get("/api/admin/rag-events", params)


def fetch_request_metrics(since: str | None = None, limit: int = 500, method: str | None = None, path: str | None = None) -> dict:
    params = {k: v for k, v in {"since": since, "limit": limit, "method": method, "path": path}.items() if v is not None}
    return _authed_get("/api/admin/request-metrics", params)


def fetch_llm_usage_stats(days: int = 30, group_by: str = "component") -> dict:
    return _authed_get("/api/admin/llm-usage/stats", {"days": days, "group_by": group_by})


def fetch_metrics_daily(days: int = 7, method: str | None = None, path: str | None = None) -> dict:
    params = {k: v for k, v in {"days": days, "method": method, "path": path}.items() if v is not None}
    return _authed_get("/api/admin/metrics-daily", params)


def fetch_usage_daily(days: int = 30, component: str | None = None, model: str | None = None) -> dict:
    params = {k: v for k, v in {"days": days, "component": component, "model": model}.items() if v is not None}
    return _authed_get("/api/admin/usage-daily", params)


def fetch_turn_events(session_id: str | None = None, limit: int = 200) -> dict:
    params = {k: v for k, v in {"session_id": session_id, "limit": limit}.items() if v is not None}
    return _authed_get("/api/admin/turn-events", params)


# ── 런타임 설정 (읽기+쓰기) — 백엔드가 무중단 튜닝용으로 만든 API (RPA-174) ──────────
# 두 API 모두 DB 오버라이드가 있으면 그 값(source="db"), 없으면 백엔드 .env(source="config")를
# 준다. PUT은 append-only라 되돌리기 = 이전 값으로 다시 PUT (이력은 updated_by/updated_at).


def fetch_budget_limits() -> dict:
    """현재 활성 LLM 예산 상한 (백엔드 RPA-173).

    반환: {source, subject_daily_usd, subject_monthly_usd, global_daily_usd,
           global_monthly_usd, updated_by, updated_at}. 값이 null이면 그 상한 비활성.
    """
    return _authed_get("/api/admin/budget-limits", {})


def update_budget_limits(
    subject_daily_usd: float | None, subject_monthly_usd: float | None,
    global_daily_usd: float | None, global_monthly_usd: float | None,
) -> dict:
    """LLM 예산 상한 갱신 — 재배포 없이 다음 턴부터 반영 (백엔드 RPA-173).

    ⚠️ **서비스를 막는 값이다.** 잘못 낮추면 정상 사용자가 429를 맞는다 — 백엔드 최초 구현의
    예시값($1/일)이 실측 최대($2.02/사용자-일)보다 낮아 켰으면 사고였다. 근거는 백엔드
    scripts/budget_calibration_report.py로 뽑는다.
    4개 값 전체 스냅샷을 보낸다(부분 갱신 아님 — 감사 이력에 완전한 설정이 남아야 한다).
    null = 그 상한 비활성. 백엔드가 0·음수와 '월<일'을 거부한다(BackendValidationError).
    """
    return _authed_put("/api/admin/budget-limits", {
        "subject_daily_usd": subject_daily_usd,
        "subject_monthly_usd": subject_monthly_usd,
        "global_daily_usd": global_daily_usd,
        "global_monthly_usd": global_monthly_usd,
    })


def fetch_retrieval_params() -> dict:
    """현재 활성 RAG 검색 파라미터 (백엔드 RPA-149).

    반환: {source, candidate_pool_size, rerank_candidates, rrf_k, vector_weight,
           bm25_weight, updated_by, updated_at}.
    """
    return _authed_get("/api/admin/retrieval-params", {})


def update_retrieval_params(
    candidate_pool_size: int, rerank_candidates: int, rrf_k: int,
    vector_weight: float, bm25_weight: float,
) -> dict:
    """RAG 검색 파라미터 갱신 — 재시작 없이 다음 검색부터 반영 (백엔드 RPA-149).

    5개 값 전체 스냅샷(부분 갱신 아님). 백엔드가 범위(1 이상)·nan/inf를 거부한다.
    """
    return _authed_put("/api/admin/retrieval-params", {
        "candidate_pool_size": candidate_pool_size,
        "rerank_candidates": rerank_candidates,
        "rrf_k": rrf_k,
        "vector_weight": vector_weight,
        "bm25_weight": bm25_weight,
    })


def fetch_rag_logs_recent(limit: int = 100) -> dict:
    with _client() as client:
        try:
            resp = client.get("/api/rag/logs/recent", params={"limit": limit})
            if resp.status_code == 403:
                raise BackendAuthError(
                    f"디버그 엔드포인트 비활성화(403): {resp.text} — 이 백엔드는 "
                    "APP_ENV=production이라 /api/rag/logs/recent가 막혀 있습니다(정상 동작)."
                )
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as e:
            # 전송 실패(네트워크·타임아웃)만 '연결 실패'로. raise_for_status가 던지는
            # HTTPStatusError(4xx/5xx)는 그대로 전파 — 응답 상태를 '연결 실패'로 오인시키지 않는다.
            raise BackendUnavailableError(f"{BACKEND_URL} 연결 실패: {e}") from e
