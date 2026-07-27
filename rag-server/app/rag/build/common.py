"""build 계층 **공용 헬퍼** — 등기부(package_registry) + khub 덤프에서 rag_documents를
만들 때 build-llm(merge_llm)·extract_llm·골드셋 스크립트가 함께 쓰는 파싱/정규화 유틸.

이 파일은 원래 `merge_v2.py`(규칙 계층 빌더 `build_documents_v2` + 전용 헬퍼)였다. LLM
구조화 추출(build-llm)로 파이프라인이 일원화되면서(refactor/remove-build-v2) 규칙 빌더와
전용 로직(action_identity·개요표 추출·겸용 제목 분해·세션/플랫폼 주석 등)을 걷어내고,
소스타입과 무관하게 쓰이는 유틸만 남겨 `common.py`로 옮겼다:
- `_doc_id`/`_doc_uid`  : 문서 id·슬러그 산식
- `_soup`/`_clip`/`_shortdesc`/`_plain_text` : HTML → (절단 표시 있는) 평문/요약
- `_release_versions`   : 릴리스노트 버전 이력 파싱
- `_load_bodies`        : bodies_{locale}.jsonl 로더
- `action_label_ko`     : ko 문서 제목 → 한국어 액션 라벨 정제
"""

import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString


def _doc_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _soup(html: str | None) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def _clip(text: str, limit: int) -> str:
    """상한 초과를 '보이게' 잘라낸다 — content는 청킹 입력이라, 무언 절단이면 소비처가
    "이게 전부"라고 읽는다(뒤가 잘린 파라미터 목록을 완전한 목록으로 오해).

    꼬리표 길이까지 limit 안에 넣는다 — 본문을 limit로 자른 뒤 꼬리표를 덧붙이면 결과가
    limit를 ~17자 넘겨(실측: 액션 head 142건이 chunk_size 초과) 액션 한 건이 두 청크로 쪼개진다.
    """
    if len(text) <= limit:
        return text
    suffix = f" …(이하 생략, {limit}자 상한)"
    if limit <= len(suffix):  # 꼬리표조차 안 들어가는 상한이면 무언 절단이 유일한 선택
        return text[:limit]
    return text[: limit - len(suffix)].rstrip() + suffix


def _shortdesc(soup: BeautifulSoup, limit: int = 600) -> str:
    p = soup.select_one("p.shortdesc")
    text = p.get_text(" ", strip=True) if p else soup.get_text(" ", strip=True)
    return _clip(text, limit)


# 개행을 넣을 블록 요소 — 나머지(span/b/code 등 인라인)는 공백으로 이어붙인다.
_BLOCK_TAGS = ["p", "li", "dt", "dd", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "caption"]
_BLOCK_TAG_SET = frozenset(_BLOCK_TAGS)
_SKIP_TAGS = frozenset({"script", "style"})


def _block_owner(node) -> object | None:
    """텍스트 노드를 담고 있는 가장 가까운 블록 조상 — 줄 묶음의 기준."""
    for parent in node.parents:
        if parent.name in _BLOCK_TAG_SET:
            return parent
    return None


def _plain_text(soup: BeautifulSoup, limit: int = 1500) -> str:
    r"""블록 태그 경계에서만 줄을 나눈 평문 — **어떤 텍스트도 버리지 않는다**.

    get_text("\n")은 <span>/<b> 같은 **인라인** 태그마다 개행을 넣어 한국어 문장을 조사
    단위로 박살낸다(실측: "REST 웹 서비스\n패키지\n의\n삭제\n방법\n작업\n을 사용하여…").
    그 상태로는 검색 매칭도 LLM 입력도 문장으로 성립하지 않아, 블록 요소 단위로 뽑는다.

    단, "블록 요소를 순회하며 자손 블록이 있는 것은 건너뛴다"는 방식은 쓸 수 없다 —
    <td>값 설명<p>주석</p></td> 처럼 자기 텍스트를 직접 가진 상위 블록이 통째로 사라져
    본문이 유실된다(실측: 액션 문서 1002건 중 545건이 15자 이상 문장을 잃었다. 예: Active
    Directory/Create group의 옵션 값 '도메인 로컬 / 글로벌 / 유니버설'). 그래서 요소가 아니라
    **텍스트 노드**를 문서 순서로 훑고, 가장 가까운 블록 조상이 바뀔 때만 줄을 나눈다.
    """
    lines: list[str] = []
    buf: list[str] = []
    owner, started = None, False

    def flush() -> None:
        text = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if text:
            lines.append(text)
        buf.clear()

    for node in soup.find_all(string=True):
        if type(node) is not NavigableString:
            continue  # 주석/doctype 등 — 본문이 아니다
        if not node.strip():
            continue
        if node.parent is not None and node.parent.name in _SKIP_TAGS:
            continue
        node_owner = _block_owner(node)
        if started and node_owner is not owner:
            flush()
        owner, started = node_owner, True
        buf.append(str(node))
    flush()
    return _clip("\n".join(lines), limit)


def _doc_uid(pretty_url: str) -> str:
    return (pretty_url or "").rstrip("/").split("/")[-1]


def _release_versions(soup: BeautifulSoup) -> list[dict]:
    versions = []
    for tbl in soup.find_all("table"):
        ths = [th.get_text(" ", strip=True).lower() for th in tbl.find_all("th")[:5]]
        if not ths or "version" not in ths[0]:
            continue
        for tr in tbl.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if cells and cells[0]:
                versions.append(
                    {
                        "version": cells[0][:30],
                        "release_date": cells[1][:30] if len(cells) > 1 else "",
                        "release_type": cells[2][:30] if len(cells) > 2 else "",
                    }
                )
        break  # 첫 Versions summary 테이블만
    return versions[:30]


def _load_bodies(dump: Path, locale: str) -> dict[str, dict]:
    docs = {}
    fp = dump / f"bodies_{locale}.jsonl"
    if fp.exists():
        with open(fp, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                docs[d["content_id"]] = d
    return docs


_KO_LABEL_SUFFIX = re.compile(r"\s*작업(\s*사용)?$")  # "~ 작업" / "~ 작업 사용"(Using the ~ action의 ko형)


def action_label_ko(display: str, pkg_label_ko: str | None, ko_title: str | None) -> str | None:
    """ko 문서 제목에서 한국어 액션 라벨을 뽑는다 — '<패키지>의 <라벨> 작업' 관용 표기 제거.

    백엔드 edit의 이름 지목 리졸버(label_candidates)가 스펙 label을 사용자 문장에 부분
    일치시키므로, 문서 원제("Google Drive의 파일 이동 작업")보다 짧은 라벨("파일 이동")이
    필요하다. 실측 관용형: "<pkg|pkg_ko>의 <라벨> 작업" / "<라벨> 작업" / 접미 없는 원제
    ("삭제 방법"). 정제 결과가 비면 원제를 그대로 쓴다.
    """
    if not ko_title:
        return None
    label = _KO_LABEL_SUFFIX.sub("", ko_title).strip()
    for prefix in filter(None, (display, pkg_label_ko)):
        p = f"{prefix}의 "
        if label.startswith(p):
            label = label[len(p):].strip()
            break
    return label or ko_title
