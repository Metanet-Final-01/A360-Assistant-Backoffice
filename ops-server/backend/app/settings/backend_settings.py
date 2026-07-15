"""A360-Assistant-Backend의 **런타임 설정**을 읽고 바꾼다 (RPA-174).

**observability와 왜 분리했나** (#38 리뷰): `app/observability/**`는 정책상 *"실서비스 백엔드를
관측하는 클라이언트"*이고 *"수집은 읽기 전용 — INSERT/UPDATE/DELETE 추가 금지"*가 명시돼 있다
(.coderabbit.yaml path_instructions). 설정 변경은 **관측이 아니라 조작**이라 그 모듈에 두면
정책 위반이자 의미상 오분류다. 쓰기는 여기 모아 "관측은 보기, 설정은 조작"의 경계를 지킨다.

대상 API 둘 다 백엔드가 무중단 튜닝용으로 만든 것:
- `budget-limits` (RPA-173) — LLM 예산 상한
- `retrieval-params` (RPA-149) — RAG 검색 파라미터

둘 다 백엔드가 **DB 오버라이드 우선 → 없으면 .env 폴백** 구조라, 여기서 바꾸면 재배포/재시작
없이 다음 요청부터 반영된다. append-only라 되돌리기 = 이전 값으로 다시 저장.

인증은 observability 클라이언트의 것을 **재사용**한다(복제 금지 — 인증·재로그인·403 힌트가
두 벌로 갈리면 한쪽만 고치는 사고가 난다). 그쪽이 A안(백엔드 admin API 경유)·자격증명 비노출
원칙을 이미 지키고 있어 그대로 따른다.
"""

import httpx

from app.observability.backend_client import (
    BACKEND_URL,
    BackendAuthError,
    BackendUnavailableError,
    _client,
    _login,
    _raise_for_auth,
)
from app.observability import backend_client as _obs


class BackendValidationError(ValueError):
    """백엔드가 값을 거부했을 때 (400/422) — 백엔드가 준 detail을 그대로 담는다.

    인증 실패·연결 실패와 구분해야 화면이 "내 입력이 틀렸다"와 "시스템이 문제다"를 다르게
    보여줄 수 있다 — 예산 상한의 '월<일 거부' 같은 규칙 위반은 사용자가 고칠 수 있는 것이다.
    """


def _raise_for_validation(resp: httpx.Response) -> None:
    if resp.status_code in (400, 422):
        raise BackendValidationError(f"값이 거부됐습니다({resp.status_code}): {resp.text}")


def _raise_for_backend_5xx(resp: httpx.Response) -> None:
    """백엔드가 5xx면 "백엔드가 죽었다"로 올린다 (#38 리뷰).

    raise_for_status()가 던지는 httpx.HTTPStatusError는 **httpx.RequestError의 서브클래스가
    아니라**(실측 확인) 아래 except에 안 잡힌다. 그러면 ops-server가 502가 아니라 500을 뱉고,
    화면은 "저장 실패(500)"라는 무의미한 메시지를 보여준다 — 502를 만든 이유(백엔드 장애를
    사용자 입력 오류·권한 문제와 구분)가 무너진다.

    4xx는 여기서 안 잡는다: 401/403/400/422는 위에서 각자 의미를 붙여 이미 처리했고, 남은
    4xx(404 등)는 "연결 실패"가 아니라 계약 문제라 HTTPStatusError로 그대로 두는 게 정확하다.
    """
    if resp.status_code >= 500:
        raise BackendUnavailableError(
            f"{BACKEND_URL} 백엔드 오류({resp.status_code}): {resp.text[:200]}")


def _authed_request(method: str, path: str, json: dict | None = None) -> dict:
    """인증된 백엔드 admin 호출 (GET/PUT). 인증 흐름은 observability 클라이언트와 동일 규칙.

    토큰 캐시는 그쪽 모듈 것을 그대로 쓴다(_obs._token_cache) — 로그인을 두 번 하지 않도록.
    """
    with _client() as client:
        try:
            if _obs._OPS_API_KEY:
                # 서비스 키 경로 — 로그인 없이 X-API-Key. 만료/재로그인 개념 없음.
                resp = client.request(method, path, json=json,
                                      headers={"X-API-Key": _obs._OPS_API_KEY})
                if resp.status_code == 401:
                    raise BackendAuthError(f"인증 실패(401): {resp.text}")
                _raise_for_auth(resp)
                _raise_for_validation(resp)
                _raise_for_backend_5xx(resp)
                resp.raise_for_status()
                return resp.json()
            # 관리자 로그인 폴백 경로
            if _obs._token_cache is None:
                _obs._token_cache = _login(client)
            headers = {"Authorization": f"Bearer {_obs._token_cache}"}
            resp = client.request(method, path, json=json, headers=headers)
            if resp.status_code == 401:
                _obs._token_cache = _login(client)
                resp = client.request(method, path, json=json,
                                      headers={"Authorization": f"Bearer {_obs._token_cache}"})
            if resp.status_code == 401:
                raise BackendAuthError(
                    f"인증 실패(401, 재로그인 후에도 동일): {resp.text} — "
                    "A360_BACKEND_ADMIN_EMAIL/PASSWORD가 올바른지 확인하세요."
                )
            _raise_for_auth(resp)
            _raise_for_validation(resp)
            _raise_for_backend_5xx(resp)
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as e:
            raise BackendUnavailableError(f"{BACKEND_URL} 연결 실패: {e}") from e


def fetch_budget_limits() -> dict:
    """현재 활성 LLM 예산 상한 (백엔드 RPA-173).

    반환: {source, subject_daily_usd, subject_monthly_usd, global_daily_usd,
           global_monthly_usd, updated_by, updated_at}. 값이 null이면 그 상한 비활성.
    """
    return _authed_request("GET", "/api/admin/budget-limits")


def update_budget_limits(
    subject_daily_usd: float | None, subject_monthly_usd: float | None,
    global_daily_usd: float | None, global_monthly_usd: float | None,
) -> dict:
    """LLM 예산 상한 갱신 — 재배포 없이 다음 턴부터 반영 (백엔드 RPA-173).

    ⚠️ **서비스를 막는 값이다.** 잘못 낮추면 정상 사용자가 429를 맞는다 — 백엔드 최초 구현의
    예시값($1/일)이 실측 최대($2.02/사용자-일)보다 낮아 켰으면 사고였다. 근거는 백엔드
    scripts/budget_calibration_report.py로 뽑는다.
    4개 전체 스냅샷을 보낸다(부분 갱신 아님 — 감사 이력에 완전한 설정이 남아야 하고, 생략하면
    백엔드의 '월<일 거부' 검증이 우회된다). null = 그 상한 비활성.
    """
    return _authed_request("PUT", "/api/admin/budget-limits", json={
        "subject_daily_usd": subject_daily_usd,
        "subject_monthly_usd": subject_monthly_usd,
        "global_daily_usd": global_daily_usd,
        "global_monthly_usd": global_monthly_usd,
    })


def fetch_retrieval_params() -> dict:
    """현재 활성 RAG 검색 파라미터 (백엔드 RPA-149)."""
    return _authed_request("GET", "/api/admin/retrieval-params")


def update_retrieval_params(
    candidate_pool_size: int, rerank_candidates: int, rrf_k: int,
    vector_weight: float, bm25_weight: float,
) -> dict:
    """RAG 검색 파라미터 갱신 — 재시작 없이 다음 검색부터 반영 (백엔드 RPA-149).

    5개 전체 스냅샷(부분 갱신 아님). 백엔드가 범위(1 이상)·nan/inf를 거부한다.
    """
    return _authed_request("PUT", "/api/admin/retrieval-params", json={
        "candidate_pool_size": candidate_pool_size,
        "rerank_candidates": rerank_candidates,
        "rrf_k": rrf_k,
        "vector_weight": vector_weight,
        "bm25_weight": bm25_weight,
    })
