"""docs.automationanywhere.com 공식 문서 khub API 저수준 클라이언트.

이 문서 사이트는 Fluid Topics CMS로 서비스되어 HTML 스크레이핑 없이 /api/khub/* JSON API로
맵/메뉴/본문을 바로 받는다. 이 모듈은 그 API 호출부만 담는다:
- list_maps()          문서 맵 목록
- get_menu(map_id)     맵의 사이드바 목차(ToC) 트리
- fetch_topic_html()   토픽 본문 HTML

v2 덤프 생성기(sources/khub_dump.py, `crawl-khub` 서브커맨드)가 이 헬퍼들로 toc_*.json +
bodies_*.jsonl[html]을 만든다. (원래 여기 있던 v1 크롤러 find_map/flatten_menu/crawl_topics·
html_to_text는 build-llm 일원화로 khub_dump에 대체되어 제거됐다 — refactor/remove-build-v2.)
"""

import time

import httpx

from ..config import DOCS_BASE_URL


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=DOCS_BASE_URL,
        headers={"Accept": "application/json", "User-Agent": "a360-assistant-ingest/0.1"},
        timeout=30.0,
    )


def _get_with_retry(client: httpx.Client, url: str, retries: int = 3) -> httpx.Response:
    for attempt in range(retries):
        try:
            resp = client.get(url)
            if resp.status_code in (429, 502, 503):
                raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def list_maps() -> list[dict]:
    with _client() as client:
        resp = _get_with_retry(client, "/api/khub/maps?page=1&perPage=200")
        return resp.json()


def get_menu(map_id: str) -> list[dict]:
    """문서 사이트 좌측 사이드바 메뉴(목차) 트리를 그대로 받아온다."""
    with _client() as client:
        resp = _get_with_retry(client, f"/api/khub/maps/{map_id}/toc")
        data = resp.json()
        return data if isinstance(data, list) else data.get("toc", [])


def fetch_topic_html(client: httpx.Client, map_id: str, content_id: str) -> str:
    resp = _get_with_retry(client, f"/api/khub/maps/{map_id}/topics/{content_id}/content")
    return resp.text
