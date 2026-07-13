"""A360-Assistant-Backend(실서비스 백엔드)의 모니터링용 읽기 전용 API를 호출한다.

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

import os
from pathlib import Path

import httpx

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


def _authed_get(path: str, params: dict) -> dict:
    global _token_cache
    with _client() as client:
        try:
            if _OPS_API_KEY:
                # 서비스 키 경로 — 로그인 없이 X-API-Key. 만료/재로그인 개념 없음.
                resp = client.get(path, params=params, headers={"X-API-Key": _OPS_API_KEY})
                if resp.status_code == 401:
                    raise BackendAuthError(f"인증 실패(401): {resp.text}")
                _raise_for_auth(resp)
                resp.raise_for_status()
                return resp.json()
            # 관리자 로그인 폴백 경로
            if _token_cache is None:
                _token_cache = _login(client)
            resp = client.get(path, params=params, headers={"Authorization": f"Bearer {_token_cache}"})
            if resp.status_code == 401:
                _token_cache = _login(client)
                resp = client.get(path, params=params, headers={"Authorization": f"Bearer {_token_cache}"})
            if resp.status_code == 401:
                # 재로그인 직후에도 401이면 토큰 만료가 아니라 자격증명 자체가 틀렸다는
                # 뜻 — httpx.HTTPError로 흘려보내면 502(연결 실패)로 오인되니 여기서
                # 바로 인증 오류로 처리한다.
                raise BackendAuthError(
                    f"인증 실패(401, 재로그인 후에도 동일): {resp.text} — "
                    "A360_BACKEND_ADMIN_EMAIL/PASSWORD가 올바른지 확인하세요."
                )
            _raise_for_auth(resp)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise BackendUnavailableError(f"{BACKEND_URL} 연결 실패: {e}") from e


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
    except httpx.HTTPError as e:
        return {"reachable": False, "status": "unreachable", "checks": {},
                "http_status": None, "error": str(e), "checked_at": checked_at}
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
        except httpx.HTTPError as e:
            raise BackendUnavailableError(f"{BACKEND_URL} 연결 실패: {e}") from e
