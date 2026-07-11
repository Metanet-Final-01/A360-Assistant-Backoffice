"""A360-Assistant-Backend(실서비스 백엔드)의 모니터링용 읽기 전용 API를 호출한다.

- /api/admin/audit-logs, /api/admin/llm-usage/stats: ADMIN_EMAILS 화이트리스트 관리자
  JWT가 필요 — A360_BACKEND_ADMIN_EMAIL/PASSWORD로 로그인해 토큰을 메모리에만 캐시한다
  (파일에 안 남김 — 로컬 개발 도구라 재로그인 비용이 싸고, 디스크에 토큰을 남기는 것보다
  안전하다). 토큰 만료(401)는 1회 재로그인 후 재시도.
- /api/rag/logs/recent: 인증 불필요(require_debug_enabled 게이트 — APP_ENV=production이면
  403, 로컬 개발이면 열려 있음). 운영 백엔드를 가리키면 403이 뜨는 게 정상 동작이다.
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

_token_cache: str | None = None


class BackendAuthError(RuntimeError):
    """관리자 인증/인가 실패 (401/403) — 메시지에 백엔드가 준 detail을 그대로 담는다."""


class BackendUnavailableError(RuntimeError):
    """A360-Assistant-Backend에 연결 자체가 안 될 때."""


def credentials_configured() -> bool:
    return bool(_ADMIN_EMAIL and _ADMIN_PASSWORD)


def _client() -> httpx.Client:
    return httpx.Client(base_url=BACKEND_URL, timeout=10.0)


def _login(client: httpx.Client) -> str:
    if not credentials_configured():
        raise BackendAuthError(
            "A360_BACKEND_ADMIN_EMAIL/A360_BACKEND_ADMIN_PASSWORD가 설정되지 않았습니다 "
            "(backend/.env)."
        )
    resp = client.post("/api/auth/login", json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD})
    if resp.status_code != 200:
        raise BackendAuthError(f"로그인 실패({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


def _authed_get(path: str, params: dict) -> dict:
    global _token_cache
    with _client() as client:
        try:
            if _token_cache is None:
                _token_cache = _login(client)
            resp = client.get(path, params=params, headers={"Authorization": f"Bearer {_token_cache}"})
            if resp.status_code == 401:
                _token_cache = _login(client)
                resp = client.get(path, params=params, headers={"Authorization": f"Bearer {_token_cache}"})
            if resp.status_code == 401:
                # 재로그인 직후에도 401이면 토큰 만료가 아니라 자격증명 자체가 틀렸다는
                # 뜻 — httpx.HTTPError로 흘려보내면 502(연결 실패)로 오인되니 여기서
                # 바로 403과 같은 인증 오류로 처리한다.
                raise BackendAuthError(
                    f"인증 실패(401, 재로그인 후에도 동일): {resp.text} — "
                    "A360_BACKEND_ADMIN_EMAIL/PASSWORD가 올바른지 확인하세요."
                )
            if resp.status_code == 403:
                raise BackendAuthError(
                    f"권한 없음(403): {resp.text} — A360_BACKEND_ADMIN_EMAIL이 "
                    "Backend의 ADMIN_EMAILS 화이트리스트에 있는지 확인하세요."
                )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise BackendUnavailableError(f"{BACKEND_URL} 연결 실패: {e}") from e


def fetch_audit_logs(limit: int = 500, method: str | None = None, status_code: int | None = None, user_id: str | None = None) -> dict:
    params = {k: v for k, v in {"limit": limit, "method": method, "status_code": status_code, "user_id": user_id}.items() if v is not None}
    return _authed_get("/api/admin/audit-logs", params)


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
