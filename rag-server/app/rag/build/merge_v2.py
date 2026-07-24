"""v2 문서 빌더 — 등기부(package_registry) + khub 덤프에서 rag_documents를 만든다.

전략 근거: 회의록/2026-07-18-khub-실측-저장전략.md §2.3~2.4 (규칙 계층).
이 단계는 **LLM 0콜**이다 — 결정론으로 뽑을 수 있는 것만 뽑고, 산문형 파라미터의
name/type/required 보강(LLM 2단)은 후속 단계로 남긴다. 그 미완 상태는 행에
`params_source`(dl|uicontrol_candidates)와 `identity_confidence`(table_confirmed|leaf_unconfirmed)로
정직하게 표기한다.

산출 source_type (trigger_schema 분리 결정 반영, 2026-07-18):
- package_overview: 패키지당 1행 (개요 + Action/Description 테이블 로스터)
- action_schema:    액션 리프당 1행 (identity=en 제목 정규화, doc_uid=슬러그)
- trigger_schema:   트리거 패키지의 설정 문서당 1행 — 추천 메뉴(action_schema 조회)에
                    자동으로 안 들어가도록 소스타입을 분리한다
- package_release:  릴리스노트 버전 이력 (갱신 감지 + 버전 질의 응답용)
"""

import hashlib
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString

from .merge import _split_document, chunk_params_for
from .registry import (
    canonical_name,
    load_overrides,
    norm_key,
    normalize_pretty_url,
    subtree_nodes,
    walk_toc,
)


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


def action_identity(title: str, pkg_display: str) -> str:
    """액션 페이지 제목 → identity 이름. 실측된 제목 문법(F5)을 결정론으로 제거.

    후처리 두 규칙은 백엔드 스모크(2026-07-19)에서 실측된 오염 사례 대응이며, 오탐을 막기 위해
    " action" 문법에 매칭된 제목에만 적용한다:
    - "Open Excel workbook - Open action | Excel advanced" → 파이프 접미 제거 + " - " 마지막 조각 → "Open"
    - "Handle automation errors using the Catch action …" → 마지막 "using the" 뒤 → "Catch"
    """
    t = re.sub(r"\s*\|.*$", "", (title or "").strip())  # "… | Excel advanced" 파이프 변형
    t = re.sub(r"^\s*using\s+(?:the\s+)?", "", t, flags=re.IGNORECASE)
    m = re.search(r"^(.*?)\s+actions?\b", t, flags=re.IGNORECASE)
    if m and m.group(1).strip():
        base = m.group(1)
        low = base.casefold()
        if " using the " in low:
            base = base[low.rindex(" using the ") + len(" using the "):]
        if " - " in base:
            base = base.rsplit(" - ", 1)[1]
    else:
        base = re.sub(
            r"\s+in\s+(?:the\s+)?" + re.escape(pkg_display) + r"(\s+package)?\s*$",
            "",
            t,
            flags=re.IGNORECASE,
        )
    base = re.sub(r"\s+in\s+(?:the\s+)?" + re.escape(pkg_display) + r"(\s+package)?\s*$", "", base, flags=re.IGNORECASE)
    return base.strip() or t


def _doc_uid(pretty_url: str) -> str:
    return (pretty_url or "").rstrip("/").split("/")[-1]


_COMPOUND_SPLIT = re.compile(r"\s+and\s+", re.IGNORECASE)


def _is_compound_action_title(name: str) -> bool:
    """한 문서가 Control Room 액션 여럿을 겸한 제목인지 — 'Move and Move All' 류.

    이런 이름은 Control Room 실명이 아니라(실제로는 Move / Move All 두 액션) 소비처의
    (패키지, 액션) 키 조회가 빗나간다. 분해는 별도 과제라, 여기서는 표시만 한다.
    단순히 ' and ' 포함으로 보면 'Find and replace text'(단일 실명)까지 걸리므로,
    앞 조각의 첫 낱말이 뒤 조각에도 나타날 때만(Move/Move All, Connect/Disconnect) 본다.
    """
    parts = _COMPOUND_SPLIT.split(name or "", maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return False
    head = parts[0].split()[0].casefold()
    return len(head) > 2 and head in parts[1].casefold()


def _compound_parts(name: str) -> list[str]:
    """겸용 제목을 구성 액션으로 쪼갠다 — 'Connect and Disconnect' → ['Connect', 'Disconnect'].

    Control Room 실명은 쪼갠 쪽이다. 세션 여닫기 보정(overrides)이 'Connect'/'Disconnect'를
    선언했을 때 겸용 문서를 근거로 인정하려고 쓴다.
    """
    if not _is_compound_action_title(name):
        return []
    return [p.strip() for p in _COMPOUND_SPLIT.split(name, maxsplit=1) if p.strip()]


_DT_OPTIONAL = re.compile(r"[\s:]*\(?\boptional\b\)?[\s:]*$", re.IGNORECASE)


def _dl_params(soup: BeautifulSoup) -> list[dict]:
    params = []
    for dl in soup.find_all("dl"):
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            desc = dd.get_text(" ", strip=True)[:400] if dd else ""
            name = dt.get_text(" ", strip=True)[:80]
            required = None
            # dt에 "Optional" 마커가 붙어 나오는 문서가 많다(실측: Asana "Due date Optional" 등)
            # — 이름에서 떼어내고 required=False로 반영한다. 안 떼면 이름이 오염되고
            # LLM 채점 골드로도 못 쓴다(2026-07-18 score-dl에서 발견).
            stripped = _DT_OPTIONAL.sub("", name).strip()
            if stripped != name and stripped:
                name = stripped
                required = False
            if desc.lower().startswith("optional"):
                required = False
            params.append({"name": name, "description": desc, "required": required})
    return params


def _uicontrol_candidates(soup: BeautifulSoup, exclude: set[str]) -> list[str]:
    seen, out = set(), []
    for el in soup.select("span.uicontrol"):
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if not text or len(text) > 40:
            continue
        k = text.casefold()
        if k in seen or k in exclude:
            continue
        seen.add(k)
        out.append(text)
    return out[:30]


# 개요 표 description 상한 — 이 설명이 P2에서 파라미터 추출의 유일한 근거다(전용 문서 없는
# overview_table 행). 300이면 실측상 'Validate form' 설명이 두 번째 파라미터 이름 한가운데서
# 잘렸다. 807개 설명 전수(상한 없이): 중앙 34 / p90 98 / 최대 743자 — 2,000이면 실질 무제한이고
# 1,000자 초과가 0건이라 어떤 행도 안 잘린다.
_OVERVIEW_DESC_LIMIT = 2000


def _overview_action_table(soup: BeautifulSoup) -> list[dict]:
    rows = []
    for tbl in soup.find_all("table"):
        ths = [th.get_text(" ", strip=True).lower() for th in tbl.find_all("th")[:3]]
        if not ths or ths[0] not in ("action", "actions"):
            continue
        for tr in tbl.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 1 and cells[0].get_text(strip=True):
                desc = (
                    re.sub(r"\s+", " ", cells[1].get_text(" ", strip=True)).strip()[:_OVERVIEW_DESC_LIMIT]
                    if len(cells) > 1 else ""
                )
                # 셀 안쪽 줄바꿈/탭을 접는다 — 접지 않으면 액션명에 '\n\t\t\t'가 박혀
                # (실측: Apple Mail "Save attachments and Save all\n\t\tattachments") 개요 문장과
                # 테이블 유래 액션명이 그대로 오염된다.
                for name in _table_cell_actions(cells[0]):
                    rows.append({"action": name[:80], "description": desc})
    return rows


def _table_cell_actions(cell) -> list[str]:
    """액션 테이블의 첫 셀 → 액션명 목록. 한 셀이 액션 **여럿**을 담을 수 있다.

    실측(2026-07-21): Microsoft Outlook (macOS) 개요 표의 셀은
    `<li>Connect</li><li>Disconnect</li>` 구조로 **각 액션을 개별 항목으로** 싣는다.
    get_text()로 평탄화하면 'Connect Disconnect'라는 실재하지 않는 이름이 되어,
    리프 제목('Connect and Disconnect')과도 표 자신과도 매칭되지 않는다.
    즉 겸용 표기처럼 보이던 것의 절반은 **우리 파싱 아티팩트**였다.

    반대로 Apple Mail 표는 `<li>` 없이 'Connect and Disconnect'를 평문으로 싣는다 —
    이건 문서가 실제로 겸용 표기한 것이라 여기서 쪼개지 않는다(제목 분해는 별도 경로).
    """
    items = cell.find_all("li")
    if items:
        names = [re.sub(r"\s+", " ", li.get_text(" ", strip=True)).strip() for li in items]
        names = [n for n in names if n]
        if names:
            return names
    text = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
    return [text] if text else []


def _plausible_action_name(name: str) -> bool:
    """표 행이 액션 '이름'다운가 — 문장/설명이 이름으로 잘못 잡힌 것을 거른다.

    실측: table_llm이 Step 개요의 서술("Optional: In the Title field, specify the title.",
    "Configures actions within the Step action.")을 액션명으로 오추출했다. 표를 항상 합집합으로
    방출하게 되면서 이 문장 조각들이 카탈로그를 오염시켰다. 실제 액션명은 짧은 명사/동사구라
    마침표로 끝나지 않고 단어 수가 적다 — 그 신호로 문장을 걷어낸다(실측 최장 실제 이름은
    'Save attachments and Save all attachments' 6단어)."""
    n = (name or "").strip()
    return bool(n) and not n.endswith(".") and len(n.split()) <= 8


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

# 액션 content 뒤에 붙이는 ko 본문 구획. 남는 예산이 이보다 작으면 조각 문장만 실려 무의미하므로 생략한다.
_KO_BODY_HEAD = "\n본문(ko):\n"

# doc_page는 원문 전체를 싣고 길이는 청킹이 감당한다 — 여기서 자르면 문서 꼬리가 영영 사라진다.
# 상한을 아예 없애지 않는 이유는 _plain_text가 limit을 필수로 받기 때문이고, 실측 최대 평문이
# 20,054자라 20만은 사실상 무제한이다(초과 시 _clip이 잘렸다는 표시를 남긴다).
_DOC_PAGE_TEXT_LIMIT = 200_000
_KO_BODY_MIN = 200

# 세션 여닫기 액션명 패턴 — 세션 사용 패키지 안에서만 적용한다(닫기 우선 판정).
_SESSION_OPENER_PAT = re.compile(r"\b(open|connect|launch|start session|log ?in)\b", re.IGNORECASE)
_SESSION_CLOSER_PAT = re.compile(r"\b(close|disconnect|end session|quit|log ?out|sign ?out)\b", re.IGNORECASE)
# ⚠ "terminate"는 제외 — AWS EC2 "Terminate instance"(VM 종료)가 세션 닫기로 오인된다(실측).


def _annotate_session_platform(rag_docs: list[dict], registry: dict, overrides: dict) -> tuple[list[str], list[str]]:
    """액션 스키마에 세션 역할(session_role)·플랫폼(platform)을 후처리로 주석한다.

    세션 — 패키지 단위 유도: 그 패키지 액션들의 파라미터명/uicontrol 후보에 'session'이
    보이면 세션 사용 패키지로 보고, 액션명 패턴으로 opener/closer를 표시한다. 백엔드 검수의
    세션 레지스트리(derive_session_registry)가 JAR 전용 return_type 대신 이 신호를 읽는다 —
    R7/R8(세션 생명주기)이 v2 카탈로그에서 살아남는 배선. 예외는 overrides의
    session_overrides({패키지: {uses_session, openers[], closers[]}})로 보정한다.

    **짝이 맞아야 신는다**: 소비처는 액션 1건당 session_role 하나만 읽으므로(백엔드
    derive_session_registry) 겸용 문서 한 행에 두 역할을 실을 수 없다. 한쪽 역할만 등록된
    패키지는 도움이 되기는커녕 거짓 경보를 만든다 — opener만 있으면 어떤 흐름도 닫을 수 없어
    R8('세션 미종료')이 항상 뜨고, closer만 있으면 열림을 못 봐 아무것도 판정 못 한다.
    그래서 opener/closer가 모두 있을 때만 주석하고, 못 맞춘 패키지는 통계로 드러낸다.
    겸용 문서를 쪼개 만든 행(session_split_into)이 있으면 겸용 행 자체에는 역할을 달지 않는다.

    반환값은 (overrides가 선언했는데 그 이름의 액션 행이 없는 항목, 짝이 안 맞아 역할을 뺀 패키지)다.
    문서도 공식 테이블 근거도 없으면 어떤 주석으로도 균형을 못 맞추므로(예: Active Directory의
    Disconnect 페이지가 덤프에 없고 개요 테이블도 없음) 조용히 넘기지 않고 빌드 통계로 드러낸다.

    플랫폼 — 등기부 로스터 실측(platform={macos,windows})을 각 액션 스키마에 복사한다.
    검수 R16(Windows 미지원 경고)이 DB 추가 조회 없이 카탈로그만으로 판정하게 한다.

    enricher 이후에 호출해야 한다 — uicontrol 문서의 파라미터명은 보강 뒤에야 존재한다.
    """
    pkg_by_display = {p["display_en"]: p for p in registry.get("packages", [])}
    sess_over = overrides.get("session_overrides", {})
    missing_declared: list[str] = []
    unbalanced: list[str] = []
    by_pkg: dict[str, list[dict]] = {}
    for d in rag_docs:
        if d.get("source_type") == "action_schema":
            by_pkg.setdefault(d["package_name"], []).append(d)

    for pkg_name, docs in by_pkg.items():
        uses_session = any(
            "session" in (p.get("name") or "").lower()
            for doc in docs
            for p in ((doc["metadata"].get("schema") or {}).get("parameters") or [])
        ) or any(
            "session" in c.lower()
            for doc in docs
            for c in doc["metadata"].get("param_candidates") or []
        )
        over = sess_over.get(pkg_name, {})
        if over.get("uses_session") is not None:
            uses_session = bool(over["uses_session"])
        platform = (pkg_by_display.get(pkg_name) or {}).get("platform")

        if uses_session:
            names = {doc.get("action_name") or "" for doc in docs}
            for key in ("openers", "closers"):
                # 메시지는 ASCII 기호만 — 이 문자열은 stats로 stdout에 찍히고, cp949 콘솔에선
                # em dash 같은 문자가 UnicodeEncodeError로 빌드를 끝에서 죽인다.
                missing_declared += [
                    f"{pkg_name}/{n}({key[:-1]}): 액션 문서 없음"
                    for n in (over.get(key) or []) if n not in names
                ]

        # 1단계: 역할 후보를 모은다(아직 안 싣는다 — 짝이 맞는지 먼저 본다).
        role_of: dict[int, str] = {}
        for doc in docs:
            name = doc.get("action_name") or ""
            if not uses_session or doc["metadata"].get("session_split_into"):
                continue  # 겸용 행은 쪼갠 행에 역할을 넘긴다
            role = None
            if name in (over.get("openers") or []):
                role = "opener"
            elif name in (over.get("closers") or []):
                role = "closer"
            elif _SESSION_CLOSER_PAT.search(name):
                role = "closer"
            elif _SESSION_OPENER_PAT.search(name):
                role = "opener"
            if role:
                role_of[id(doc)] = role

        # 2단계: opener/closer 짝이 맞을 때만 싣는다.
        found = set(role_of.values())
        if len(found) == 1:
            only = next(iter(found))
            unbalanced.append(
                f"{pkg_name}: {only}만 {len(role_of)}건 — 짝 없음, session_role 미부여"
            )
            role_of = {}

        for doc in docs:
            name = doc.get("action_name") or ""
            role = role_of.get(id(doc))
            if role is None and platform is None:
                continue
            schema = doc["metadata"].get("schema")
            if not isinstance(schema, dict):
                # schema 없는 행(파라미터 미상)에도 세션·플랫폼 신호는 실어야 한다 —
                # parameters=None은 백엔드가 params_unknown으로 정규화한다.
                schema = {"name": name, "label": doc["metadata"].get("action_label_ko"), "parameters": None}
                doc["metadata"]["schema"] = schema
            if role:
                # 소비처(백엔드 derive_session_registry)는 session_role 한 값만 읽는다 —
                # 한 행에 두 역할을 담지 않고, 겸용은 행 자체를 쪼개서 짝을 맞춘다.
                schema["session_role"] = role
            if platform is not None:
                schema["platform"] = platform

    return missing_declared, unbalanced


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


# 개요 페이지 평문 상한(LLM 표 추출 입력) — 실측 중앙 1,908 / p90 5,531 / 최대 27,404자.
_TABLE_PAGE_LIMIT = 12000


def _merge_table_sources(rule_rows: list[dict], llm_res: dict | None,
                         display: str, stats: dict) -> list[dict]:
    """공식 액션 목록을 확정한다 — **LLM의 의미 판단이 규칙보다 우선**한다.

    세 갈래다:
      (a) LLM이 "액션 목록 아님"(has_action_list=false) → 규칙 결과도 **버린다.**
          그게 교체의 핵심이다 — Excel advanced 표 5행은 전부 카테고리명('Cell operations')인데
          규칙은 헤더가 'Action'이라는 이유로 액션으로 넣고 있었다.
      (b) LLM이 목록을 냈다 → LLM ∪ 규칙. 어느 쪽도 상대의 상위집합이 아니라서 합집합이다
          (규칙은 잘 만들어진 표에 정확하고, LLM은 헤더 표기가 달라도 읽는다).
      (c) LLM을 안 돌렸거나 실패(None) → 규칙 결과를 그대로 쓴다.
    차이는 전부 통계로 남긴다 — 어느 쪽이 무엇을 놓치는지 계측 없이는 신뢰할 수 없다.
    """
    rule_by_key = {norm_key(r["action"]): r for r in rule_rows if r.get("action")}
    if llm_res is None:
        return list(rule_by_key.values())
    if not llm_res.get("has_action_list"):
        if rule_by_key:
            stats["table_llm_suppressed"] = stats.get("table_llm_suppressed", 0) + len(rule_by_key)
            sample = ", ".join(r["action"] for r in list(rule_by_key.values())[:3])
            stats.setdefault("table_llm_suppressed_packages", []).append(
                f"{display}: 규칙 {len(rule_by_key)}행 폐기 ({sample})"
            )
        return []

    llm_rows = llm_res.get("actions") or []
    llm_keys = {norm_key(r["action"]) for r in llm_rows if r.get("action")}
    out = dict(rule_by_key)
    n_llm_only = 0
    for r in llm_rows:
        k = norm_key(r["action"])
        if not k:
            continue
        if k in out:
            # 같은 액션이면 설명이 더 긴 쪽을 쓴다(규칙은 셀 텍스트, LLM은 문맥 반영).
            if len(r.get("description") or "") > len(out[k].get("description") or ""):
                out[k] = {"action": out[k]["action"], "description": r["description"]}
            continue
        out[k] = r
        n_llm_only += 1
    n_rule_only = len(set(rule_by_key) - llm_keys)
    if n_llm_only:
        stats["table_llm_only"] = stats.get("table_llm_only", 0) + n_llm_only
        stats.setdefault("table_llm_only_packages", []).append(f"{display}: {n_llm_only}건")
    if n_rule_only:
        stats["table_rule_only"] = stats.get("table_rule_only", 0) + n_rule_only
        stats.setdefault("table_rule_only_packages", []).append(f"{display}: {n_rule_only}건")
    return list(out.values())


def build_documents_v2(dump_dir: str | Path, registry: dict, chunk_size: int, chunk_overlap: int,
                       enricher=None, judger=None, table_extractor=None) -> list[dict]:
    dump = Path(dump_dir)
    ov = load_overrides()
    non_action_markers = [m.casefold() for m in ov.get("non_action_leaf_markers", [])]
    # 예제/설정 문서 마커지만, 정규화 이름이 공식 액션 테이블에 있으면 실존 액션 문서다
    # (Datetime "Example of using the Assign action" → Assign). 무조건 차단하면 그 액션이 사라진다.
    soft_non_action_markers = [m.casefold() for m in ov.get("non_action_leaf_markers_unless_table_confirmed", [])]
    # ko 본문을 실을 때의 content 상한 — 청킹 입력이므로 상한을 넘기면 액션 한 건이 여러
    # 청크로 흩어져 검색 품질이 떨어진다. 상한에 걸린 사실은 _clip이 본문에 남긴다.
    # ⚠️ 여기서 쓸 값은 호출자가 넘긴 chunk_size가 아니라 **action_schema에 실제로 적용될**
    #    청크 폭이다(config.CHUNK_PARAMS_BY_SOURCE_TYPE로 타입별 분리됨 — 현재 1500).
    #    호출자 값(1200)을 그대로 쓰면 예산이 300자 좁아져 ko 본문이 근거 없이 잘린다.
    content_limit = chunk_params_for("action_schema", chunk_size or 1200, chunk_overlap)[0]

    toc_en = json.loads((dump / "toc_en-US.json").read_text(encoding="utf-8"))["toc"]
    flat_en = walk_toc(toc_en)
    # 보조 맵(toc_{locale}__*.json)은 **패키지 등기와 무관**하다 — 패키지는 'Automation 360'
    # 맵에만 있다. 그래서 flat_en(등기·액션 방출용)에는 넣지 않고, doc_page 방출용으로만
    # 따로 모은다. 실측: Control Room APIs 34토픽 등 55건이 여기서 들어온다.
    extra_nodes: list[dict] = []
    for locale in ("en-US", "ko-KR"):
        for fp in sorted(dump.glob(f"toc_{locale}__*.json")):
            try:
                extra_nodes += walk_toc(json.loads(fp.read_text(encoding="utf-8"))["toc"])
            except (json.JSONDecodeError, KeyError, OSError):
                continue
    node_by_cid = {e["content_id"]: e for e in flat_en if e["content_id"]}
    bodies_en = _load_bodies(dump, "en-US")
    bodies_ko = _load_bodies(dump, "ko-KR")
    ko_by_url = {normalize_pretty_url(d.get("pretty_url", "")): d for d in bodies_ko.values()}

    def ko_pair(pretty_url: str) -> dict | None:
        return ko_by_url.get(normalize_pretty_url(pretty_url))

    rag_docs: list[dict] = []
    stats = {"package_overview": 0, "action_schema": 0, "trigger_schema": 0, "package_release": 0, "skipped_no_html": 0}

    # 공식 액션 목록을 LLM으로 추출한다(구조 규칙 → 의미 판단). 규칙 파싱 결과와 **합집합**을
    # 취하고 어느 쪽에서 왔는지 통계로 남긴다 — 규칙이 잡던 것을 LLM이 놓치면 그대로 드러난다.
    llm_tables: dict[str, list[dict]] = {}
    if table_extractor is not None:
        items = []
        for pkg in registry["packages"]:
            sr = pkg.get("subtree_root")
            if not sr:
                continue
            b = bodies_en.get(sr["content_id"])
            if not b or not b.get("html"):
                continue
            items.append({"package": pkg["display_en"],
                          "text": _plain_text(_soup(b["html"]), _TABLE_PAGE_LIMIT)})
        llm_tables, stats["table_llm"] = table_extractor(items)

    for pkg in registry["packages"]:
        display = pkg["display_en"]
        # ── package_release ──
        rel_cid = pkg.get("release_page")
        rel_doc = bodies_en.get(rel_cid) if rel_cid else None
        versions = []
        if rel_doc and rel_doc.get("html"):
            versions = _release_versions(_soup(rel_doc["html"]))
            if versions:
                latest = versions[0]
                content = (
                    f"패키지: {display}\n"
                    f"최신 버전: {latest['version']} ({latest['release_date']}, {latest['release_type']})\n"
                    f"버전 이력: " + ", ".join(v["version"] for v in versions[:10])
                )
                rag_docs.append(
                    {
                        "id": _doc_id("release", display),
                        "source_type": "package_release",
                        "package_name": display,
                        "action_name": None,
                        "locale": "en-US",
                        "title": f"{display} 패키지 버전 이력",
                        "url": rel_doc.get("pretty_url", ""),
                        "content": content,
                        "metadata": {"versions": versions, "schema_source": "docs_rule"},
                    }
                )
                stats["package_release"] += 1

        # ── trigger_schema (분리 소스타입) ──
        if pkg["kind"] == "trigger":
            trigger_docs = []
            if pkg.get("subtree_root"):
                root = node_by_cid.get(pkg["subtree_root"]["content_id"])
                if root:
                    trigger_docs = [n for n in subtree_nodes(root, pkg["subtree_root"]["path"]) if n["content_id"]]
            for t in pkg.get("trigger_usage", []):
                found = next((d for d in bodies_en.values() if d["title"] == t), None)
                if found:
                    trigger_docs.append(
                        {"title": found["title"], "content_id": found["content_id"],
                         "pretty_url": found.get("pretty_url", ""), "path": found.get("breadcrumbs", []), "is_leaf": True}
                    )
            for node in trigger_docs:
                body = bodies_en.get(node["content_id"])
                if not body or not body.get("html"):
                    stats["skipped_no_html"] += 1
                    continue
                soup = _soup(body["html"])
                ko = ko_pair(node["pretty_url"])
                candidates = _uicontrol_candidates(soup, {display.casefold()})
                content = (
                    f"트리거 패키지: {display}\n"
                    f"문서: {node['title']}" + (f" / {ko['title']}" if ko else "") + "\n"
                    f"설명: {_shortdesc(soup)}\n"
                    f"설정 필드 후보(uicontrol): {', '.join(candidates) or '없음'}\n\n"
                    + (_plain_text(_soup(ko["html"]), 1200) if ko and ko.get("html") else _plain_text(soup, 1200))
                )
                rag_docs.append(
                    {
                        "id": _doc_id("trigger", display, node["title"]),
                        "source_type": "trigger_schema",
                        "package_name": display,
                        "action_name": action_identity(node["title"], display),
                        "locale": "ko-KR" if ko else "en-US",
                        "title": f"{display} - {node['title']}",
                        "url": node.get("pretty_url", ""),
                        "content": content,
                        "metadata": {
                            "doc_uid": _doc_uid(node.get("pretty_url", "")),
                            "label_ko": ko["title"] if ko else None,
                            "action_label_ko": action_label_ko(display, pkg.get("label_ko"), ko["title"] if ko else None),
                            "kind": "trigger",
                            "params_source": "uicontrol_candidates",
                            "param_candidates": candidates,
                            "schema_source": "docs_rule",
                        },
                    }
                )
                stats["trigger_schema"] += 1
            continue  # 트리거 패키지는 action_schema 경로를 타지 않는다

        # ── package_overview + action_schema ──
        if not pkg.get("subtree_root"):
            # 문서 페이지 없는 패키지(F3 ~12개): identity만 — 릴리스노트 요약으로 개요 생성
            if versions or pkg["sources"]:
                rag_docs.append(
                    {
                        "id": _doc_id("pkg2", display),
                        "source_type": "package_overview",
                        "package_name": display,
                        "action_name": None,
                        "locale": "en-US",
                        "title": f"{display} 패키지",
                        "url": (rel_doc or {}).get("pretty_url", ""),
                        "content": (
                            f"패키지: {display}\n공식 액션 문서 없음(has_doc_pages=false).\n"
                            + (f"버전 이력 존재: 최신 {versions[0]['version']}" if versions else "")
                        ),
                        "metadata": {"has_doc_pages": False, "kind": pkg["kind"], "schema_source": "docs_rule",
                                     "platform": pkg.get("platform")},
                    }
                )
                stats["package_overview"] += 1
            continue

        root = node_by_cid.get(pkg["subtree_root"]["content_id"])
        if root is None:
            continue
        nodes = subtree_nodes(root, pkg["subtree_root"]["path"])
        root_body = bodies_en.get(pkg["subtree_root"]["content_id"])
        root_soup = _soup(root_body.get("html") if root_body else "")
        table_actions = _merge_table_sources(
            _overview_action_table(root_soup), llm_tables.get(display), display, stats
        )
        # 테이블 셀은 링크 제목이라 "Connect action in GitHub package" 꼴로 온다 — 리프 제목에
        # 쓰는 정규화를 여기에도 걸어야 대조가 성립한다. 안 걸면 실존 확인된 액션이
        # leaf_unconfirmed로 오분류돼 판별 LLM을 불필요하게 태운다(실측 불일치 116건 중 ~90건).
        table_keys = {norm_key(action_identity(r["action"], display)) for r in table_actions}
        table_keys |= {norm_key(r["action"]) for r in table_actions}
        ko_root = ko_pair(root.get("pretty_url", "")) if root_body else None

        rag_docs.append(
            {
                "id": _doc_id("pkg2", display),
                "source_type": "package_overview",
                "package_name": display,
                "action_name": None,
                "locale": "ko-KR" if ko_root else "en-US",
                "title": f"{display} 패키지" + (f" ({pkg['label_ko']})" if pkg.get("label_ko") else ""),
                "url": root.get("pretty_url", ""),
                "content": (
                    f"패키지: {display}" + (f" / {pkg['label_ko']}" if pkg.get("label_ko") else "") + "\n"
                    f"설명: {_shortdesc(root_soup)}\n"
                    f"액션 목록({len(table_actions)}개, 공식 테이블): "
                    + ", ".join(r["action"] for r in table_actions[:40])
                ),
                "metadata": {
                    "has_doc_pages": True,
                    "kind": pkg["kind"],
                    "platform": pkg.get("platform"),
                    "actions_from_table": table_actions,
                    "label_ko": pkg.get("label_ko"),
                    "schema_source": "docs_rule",
                },
            }
        )
        stats["package_overview"] += 1

        emitted: list[dict] = []  # 이 패키지에서 방출된 액션 행 — 테이블/겸용 보강의 중복 방지용

        def _skip_non_action(title: str, kind: str) -> None:
            """마커로 차단된 제목을 통계에 남긴다 — 조용히 return하면 12행이 흔적 없이 사라지고,
            그 결과가 '액션 0건 패키지'로 둔갑해도(테이블 승격 발동) 원인을 못 찾는다."""
            stats["skipped_non_action"] = stats.get("skipped_non_action", 0) + 1
            stats.setdefault("skipped_non_action_titles", []).append(f"{display}/{title} [{kind}]")

        def emit_action(node: dict, promoted: bool = False) -> None:
            title = node["title"]
            if promoted:
                # 루트 제목은 "<패키지> package" 꼴이라 그대로 두면 액션명이 "Analyze package"가 된다
                title = canonical_name(title) or title
                if norm_key(title) == norm_key(display):
                    title = display  # 표기 흔들림 방지 — 등기부 표기(display_en)로 맞춘다
            if any(m in title.casefold() for m in non_action_markers):
                _skip_non_action(title, "hard")
                return
            name = action_identity(title, display)
            if norm_key(name) not in table_keys and any(m in title.casefold() for m in soft_non_action_markers):
                _skip_non_action(title, "soft")
                return
            body = bodies_en.get(node["content_id"])
            if not body or not body.get("html"):
                stats["skipped_no_html"] += 1
                return
            soup = _soup(body["html"])
            dl_params = _dl_params(soup)
            candidates = [] if dl_params else _uicontrol_candidates(soup, {name.casefold(), display.casefold()})
            ko = ko_pair(node.get("pretty_url", ""))
            ko_soup = _soup(ko["html"]) if ko and ko.get("html") else None
            label = action_label_ko(display, pkg.get("label_ko"), ko["title"] if ko else None)
            confidence = "table_confirmed" if norm_key(name) in table_keys else "leaf_unconfirmed"
            category = [p for p in node["path"] if p not in (pkg["subtree_root"]["path"][0] if pkg["subtree_root"]["path"] else "",)][-1:] if node["path"] else []

            param_lines = (
                "\n".join(f"- {p['name']}: {p['description']}" for p in dl_params)
                if dl_params
                else ("후보 필드(uicontrol): " + ", ".join(candidates) if candidates else "없음")
            )
            # locale=ko-KR로 표기하면서 본문은 전량 영문이던 문제(M6) — ko 요약을 앞세우고 en을
            # 병기한다. ko만 실으면 영문 액션명·용어 질의가 죽고, en만 실으면 한국어 질의가 안 걸린다.
            desc_ko = _shortdesc(ko_soup, 400) if ko_soup else ""
            content = (
                f"패키지: {display}" + (f" / {pkg['label_ko']}" if pkg.get("label_ko") else "") + "\n"
                f"액션: {name}" + (f" / {ko['title']}" if ko else "") + "\n"
                f"설명: " + (f"{desc_ko} / " if desc_ko else "") + f"{_shortdesc(soup)}\n"
                f"파라미터:\n{param_lines}"
            )
            ko_budget = content_limit - len(content) - len(_KO_BODY_HEAD)
            ko_body = _plain_text(ko_soup, ko_budget) if ko_soup and ko_budget >= _KO_BODY_MIN else ""
            if ko_body:
                content += _KO_BODY_HEAD + ko_body
            rag_docs.append(
                {
                    "id": _doc_id("action2", display, name),
                    "source_type": "action_schema",
                    "package_name": display,
                    "action_name": name,
                    "locale": "ko-KR" if ko else "en-US",
                    "title": f"{display} - {name}",
                    "url": node.get("pretty_url", ""),
                    "content": content,
                    "metadata": {
                        "doc_uid": _doc_uid(node.get("pretty_url", "")),
                        "label_en": title,
                        "label_ko": ko["title"] if ko else None,
                        "action_label_ko": label,
                        "category_path": category,
                        "identity_confidence": confidence,
                        "params_source": "dl" if dl_params else "uicontrol_candidates",
                        "schema": {"name": name, "label": label, "parameters": dl_params} if dl_params else None,
                        "param_candidates": candidates,
                        "schema_source": "docs_rule",
                        # locale이 ko-KR인데 한국어 본문이 실제로 실렸는지 — M6 재발 감시용 신호
                        "ko_body_included": bool(ko_body),
                        **({"promoted_from_root": True} if promoted else {}),
                        # Control Room 실명이 아님을 소비처가 알 수 있게 표시(분해는 별도 과제)
                        **({"compound_action_title": True} if _is_compound_action_title(name) else {}),
                    },
                }
            )
            emitted.append(rag_docs[-1])
            stats["action_schema"] += 1
            if _is_compound_action_title(name):
                stats["compound_action_title"] = stats.get("compound_action_title", 0) + 1

        def emit_known_action(name: str, description: str, origin: str, url: str, source_doc: dict | None = None) -> bool:
            """파라미터 미상 액션 행 — "존재는 확실하고 스펙만 모른다"를 정직하게 싣는다.

            근거는 두 가지뿐이다: 패키지 개요의 **공식 액션 테이블**(origin=overview_table)과
            겸용 문서를 쪼갠 **구성 액션**(origin=compound_split). 문서가 없다는 이유로 안 실으면
            소비처(검수 R1)가 실존 액션을 환각으로 판정하거나, 세션 짝이 영영 안 맞는다.
            """
            name = re.sub(r"\s+", " ", name or "").strip()
            if not name or norm_key(name) == norm_key(display):
                return False  # 패키지명 자체는 액션이 아니다(테이블 첫 행이 패키지명인 문서 대비)
            if any(norm_key(d["action_name"]) == norm_key(name) for d in emitted):
                return False
            rag_docs.append(
                {
                    "id": _doc_id("action2", display, name),
                    "source_type": "action_schema",
                    "package_name": display,
                    "action_name": name,
                    "locale": "en-US",
                    "title": f"{display} - {name}",
                    "url": url or "",
                    "content": (
                        f"패키지: {display}" + (f" / {pkg['label_ko']}" if pkg.get("label_ko") else "") + "\n"
                        f"액션: {name}\n"
                        f"설명: {description or '설명 없음(전용 문서 없음)'}\n"
                        f"파라미터: 미상 — 이 액션은 전용 문서가 없고 "
                        + ("패키지 개요의 공식 액션 테이블" if origin == "overview_table"
                           else f"겸용 문서 '{(source_doc or {}).get('action_name', '')}'")
                        + "로 존재만 확인했다."
                    ),
                    "metadata": {
                        "doc_uid": _doc_uid(url or ""),
                        "label_en": name,
                        "label_ko": None,
                        "action_label_ko": None,
                        "category_path": [],
                        "identity_confidence": "table_confirmed" if origin == "overview_table" else "compound_split",
                        "params_source": "unknown",
                        "schema": None,
                        "param_candidates": [],
                        "schema_source": "docs_rule",
                        "ko_body_included": False,
                        "action_source": origin,
                        # overview_table 행의 파라미터 추출 근거(P2). 이 설명이 유일한 소스라
                        # enrich가 재파싱 없이 바로 쓰도록 원문을 실어둔다.
                        **({"raw_description": description} if origin == "overview_table" and description else {}),
                        **({"split_from_action": source_doc["action_name"]} if source_doc else {}),
                    },
                }
            )
            emitted.append(rag_docs[-1])
            stats["action_schema"] += 1
            stats[f"action_from_{origin}"] = stats.get(f"action_from_{origin}", 0) + 1
            return True

        actions_before = stats["action_schema"]
        for node in nodes:
            if not node["is_leaf"] or not node["content_id"] or node["content_id"] == pkg["subtree_root"]["content_id"]:
                continue
            emit_action(node)

        # 공식 액션 표의 행을 **리프 유무와 무관하게 항상 합집합**으로 방출한다 — 표엔 있지만
        # 전용 리프 문서가 없는 액션(예: Database/Connect)을 세운다. 예전엔 '리프 0건'일 때만
        # 보충해, 리프가 이미 있는 다액션 패키지의 표-전용 액션이 통째로 누락됐다(golden이
        # Database/Connect로 포착). table_actions는 table_llm이 카테고리(Excel advanced의
        # 'Cell operations' 등)를 이미 제거한 로스터라 쓰레기가 딸려오지 않고, emit_known_action의
        # 중복 방지가 이미 방출된 이름을 거른다. 표만 있는 액션은 파라미터 미상 행으로 서고,
        # 설명이 있으면 P2 enrich가 채운다.
        before_table = stats["action_schema"]
        for row in table_actions:
            if not _plausible_action_name(row["action"]):
                stats["table_action_rejected_junk"] = stats.get("table_action_rejected_junk", 0) + 1
                stats.setdefault("table_action_rejected_samples", []).append(f"{display}/{row['action'][:60]}")
                continue
            emit_known_action(row["action"], row["description"], "overview_table", root.get("pretty_url", ""))
        if stats["action_schema"] > before_table:
            stats.setdefault("action_table_union_packages", []).append(
                f"{display}: {stats['action_schema'] - before_table}개"
            )

        if stats["action_schema"] == actions_before:
            # 리프도 공식 표도 0건 — 루트를 승격한다(단일 페이지 패키지: Goto/SOAP Web Service).
            # 루트 승격은 액션명이 패키지명이 되지만, 여기서 안 세우면 액션 0건이 된다. nodes의
            # 루트 항목은 walk_toc 표기 때문에 content_id가 비어 있어 등기부로 직접 조립한다.
            emit_action(
                {"title": pkg["subtree_root"]["title"], "content_id": pkg["subtree_root"]["content_id"],
                 "pretty_url": root.get("pretty_url", ""), "path": pkg["subtree_root"]["path"], "is_leaf": True},
                promoted=True,
            )
            if stats["action_schema"] > actions_before:
                stats["action_promoted_root"] = stats.get("action_promoted_root", 0) + 1
                stats.setdefault("action_promoted_root_packages", []).append(display)

        # ── 겸용 제목 분해: "Delete and Delete all" → Delete / Delete all ──
        # Control Room 실명은 쪼갠 쪽이다(같은 계열 Microsoft 365 Outlook의 공식 표가
        # Delete/Delete all/Move/Move all로 분리 표기하는 것이 근거). 겸용 이름만 실으면
        # 에이전트가 "Apple Mail/Reply"를 낼 때 카탈로그 조회가 빗나가고, 반대로 겸용 이름을
        # 그대로 내면 사용자가 봇 편집기에서 못 찾는 이름을 보게 된다.
        # 겸용 원본 행은 남긴다 — "Move and Move All 액션" 같은 질의에 그 문서가 걸려야 한다.
        # 표기 근거 우선순위: 공식 액션 테이블 행 > 리프 제목 분해(표가 깨진 패키지 대비).
        for parent in list(emitted):
            parts = _compound_parts(parent.get("action_name") or "")
            for part in parts:
                row = next((r for r in table_actions if norm_key(r["action"]) == norm_key(part)), None)
                emit_known_action(
                    row["action"] if row else part,
                    (row["description"] if row
                     else f"{parent['action_name']} 문서에 함께 기술된 액션."),
                    "compound_split",
                    parent.get("url", ""),
                    source_doc=parent,
                )

        # ── 세션 여닫기 보정: overrides가 선언한 opener/closer의 액션 행 확보 ──
        # 백엔드 derive_session_registry는 액션 1건당 session_role 하나만 읽는다. 그래서 겸용
        # 문서("Connect and Disconnect") 한 행으로는 opener/closer 짝을 만들 수 없다 —
        # 근거(공식 테이블 / 겸용 문서 분해)가 있는 이름은 별도 행으로 세워야 균형이 맞는다.
        sess_over = (ov.get("session_overrides") or {}).get(display) or {}
        for role_key in ("openers", "closers"):
            for declared in sess_over.get(role_key) or []:
                if any(norm_key(d["action_name"]) == norm_key(declared) for d in emitted):
                    continue
                row = next((r for r in table_actions if norm_key(r["action"]) == norm_key(declared)), None)
                if row:
                    emit_known_action(declared, row["description"], "overview_table", root.get("pretty_url", ""))
                    continue
                parent = next(
                    (d for d in emitted
                     if any(norm_key(p) == norm_key(declared) for p in _compound_parts(d["action_name"]))),
                    None,
                )
                if parent:
                    emit_known_action(
                        declared,
                        f"{parent['action_name']} 문서에 함께 기술된 액션.",
                        "compound_split",
                        parent.get("url", ""),
                        source_doc=parent,
                    )
                    # 겸용 문서는 Control Room 실명이 아니다 — 쪼갠 행이 역할을 갖도록 표시해두고
                    # 주석 단계에서 겸용 행에는 session_role을 달지 않는다(중복 역할 방지).
                    parent["metadata"].setdefault("session_split_into", []).append(declared)

    # ── doc_page: 공식 문서 원문 전량 ────────────────────────────────────────────
    # 카탈로그(action_schema 등)는 "라벨: 값" 정형 요약이라, 설치·관리·라이선스·개념처럼
    # 액션이 아닌 질문에 답할 근거가 없다. v1은 doc_page 6,468청크(코퍼스의 77%)를 실었는데
    # v2는 0건이었다 — 에이전트가 A360 전반을 답하려면 이 원문이 있어야 한다.
    #
    # 액션 문서도 doc_page로 **함께** 싣는다(matched_to_action=true로 표시). 같은 문서라도
    # action_schema는 파라미터 정형 요약이고 doc_page는 예제·주의사항·절차가 든 원문이라
    # 역할이 다르다 — 검색이 질문 성격에 따라 고른다(v1도 동일 방식).
    catalog_urls = {normalize_pretty_url(d.get("url", "")) for d in rag_docs if d.get("url")}
    doc_stats = {"doc_page": 0, "doc_page_ko": 0, "doc_page_en_fallback": 0,
                 "doc_page_no_body": 0, "doc_page_matched_action": 0, "doc_page_extra_maps": 0}
    seen_doc_cids: set[str] = set()
    extra_cids = {n["content_id"] for n in extra_nodes if n.get("content_id")}
    for node in flat_en + extra_nodes:
        cid = node.get("content_id")
        if not cid or cid in seen_doc_cids:
            continue  # 맵 간 중복 방지(실측상 충돌 0건이나 방어)
        seen_doc_cids.add(cid)
        if cid in extra_cids:
            doc_stats["doc_page_extra_maps"] += 1
        en_body = bodies_en.get(cid)
        ko_body = ko_pair(node.get("pretty_url", ""))
        # ko 우선, ko 본문이 없으면(한국어판 미번역 — 실측 82건 404) en으로 폴백한다.
        body = ko_body if (ko_body and ko_body.get("html")) else en_body
        if not body or not body.get("html"):
            doc_stats["doc_page_no_body"] += 1
            continue
        is_ko = body is ko_body
        text = _plain_text(_soup(body["html"]), _DOC_PAGE_TEXT_LIMIT)
        if not text:
            doc_stats["doc_page_no_body"] += 1
            continue
        crumbs = [c for c in (node.get("path") or []) if c]
        matched = normalize_pretty_url(node.get("pretty_url", "")) in catalog_urls
        if matched:
            doc_stats["doc_page_matched_action"] += 1
        doc_stats["doc_page_ko" if is_ko else "doc_page_en_fallback"] += 1
        rag_docs.append(
            {
                "id": _doc_id("doc2", cid),
                "source_type": "doc_page",
                "package_name": None,
                "action_name": None,
                "locale": "ko-KR" if is_ko else "en-US",
                # 제목은 ko/en 병기 — 본문이 ko여도 영문 용어 질의가 걸려야 한다.
                "title": node["title"] + (f" / {ko_body['title']}" if is_ko and ko_body.get("title") else ""),
                "url": node.get("pretty_url", ""),
                "content": (" > ".join(crumbs) + "\n\n" if crumbs else "") + text,
                "metadata": {
                    "breadcrumbs": crumbs,
                    "toc_id": node.get("toc_id"),
                    # 이 문서가 카탈로그(action_schema 등)로도 실렸는지 — 소비처가 원하면 거를 수 있다
                    "matched_to_action": matched,
                    "schema_source": "docs_raw",
                },
            }
        )
        doc_stats["doc_page"] += 1
    stats.update(doc_stats)

    # id 중복 검사 (같은 정규화 이름의 리프 2개 등) — 나중 것에 doc_uid를 붙여 살린다.
    # doc_uid가 빈 문자열일 수도 있어(pretty_url 없음) or로 title 폴백한다 — .get(key, default)는
    # 키가 있으면 빈 값이어도 폴백하지 않아 죽은 코드였다. 재해시가 또 충돌하면 유일해질 때까지 돈다.
    seen: dict[str, dict] = {}
    for d in rag_docs:
        if d["id"] in seen:
            salt = (d["metadata"].get("doc_uid") or "").strip() or d["title"]
            new_id, attempt = _doc_id(d["id"], salt), 1
            while new_id in seen:
                attempt += 1
                new_id = _doc_id(d["id"], salt, str(attempt))
            d["id"] = new_id
        seen[d["id"]] = d

    # 액션 여부 판별(LLM)은 보강보다 **먼저** — 비-액션으로 판정돼 버릴 문서에 보강 비용을
    # 쓰지 않기 위해서다. 대상은 공식 액션 테이블에 없는 리프뿐이고, 판정 결과 채워진
    # 파라미터는 params_source=judge_llm이 되어 뒤의 보강 대상에서 자연히 빠진다.
    if judger is not None:
        judge_stats, drop_ids = judger(rag_docs)
        if drop_ids:
            rag_docs[:] = [d for d in rag_docs if d["id"] not in drop_ids]
            for key in ("action_schema", "trigger_schema"):
                stats[key] = sum(1 for d in rag_docs if d["source_type"] == key)
        stats["judge"] = judge_stats

    # LLM 보강(2단)은 반드시 청킹 전에 — 청크는 content 파생물이라 이후 수정하면 어긋난다
    if enricher is not None:
        stats["enrich"] = enricher(rag_docs)

    # (source_type, package_name, action_name) 중복 — id가 달라도 소비처(Backend 카탈로그)의
    # 인덱스 키가 같아 뒤 행이 조용히 버려진다(M4 실측 2건). 두 행의 파라미터 집합이 실제로
    # 다르므로 아무거나 고를 수 없다 → 신뢰도 높은 행이 원래 이름을 갖고, 나머지는 doc_uid를
    # 붙여 결정론적으로 분리하고 빌드 통계에 경고로 남긴다. 보강 뒤에 도는 이유는 둘이다 —
    # 보강 프롬프트에 doc_uid 접미가 붙은 이름이 흘러가지 않게, 그리고 보강으로 확정된
    # params_source까지 보고 대표 행을 고르려고.
    by_identity: dict[tuple, list[dict]] = {}
    for d in rag_docs:
        if d.get("action_name"):
            by_identity.setdefault((d["source_type"], d["package_name"], d["action_name"]), []).append(d)
    conflicts: list[str] = []
    for (_stype, pkg_name, act), group in by_identity.items():
        if len(group) < 2:
            continue
        # 공식 액션 테이블 확인 → dl 파라미터 보유 → doc_uid 사전순. 입력이 같으면 결과도 같다.
        ranked = sorted(group, key=lambda d: (
            d["metadata"].get("identity_confidence") != "table_confirmed",
            d["metadata"].get("params_source") != "dl",
            d["metadata"].get("doc_uid") or "",
        ))
        for dup in ranked[1:]:
            uid = (dup["metadata"].get("doc_uid") or "").strip() or dup["id"]
            dup["action_name"] = f"{act} ({uid})"
            dup["metadata"]["identity_conflict_with"] = act
        conflicts.append(
            f"{pkg_name}/{act}: " + ", ".join((d["metadata"].get("doc_uid") or d["id"]) for d in ranked)
        )
    if conflicts:
        stats["identity_conflicts"] = conflicts

    # 세션·플랫폼 주석은 보강 뒤(파라미터명 확보), 청킹 전(metadata가 청크로 복제됨)에.
    session_gaps, session_unbalanced = _annotate_session_platform(rag_docs, registry, ov)
    if session_gaps:
        # overrides가 선언한 여닫기 액션의 문서도 공식 테이블 근거도 없는 경우 — 못 메우니 드러낸다
        stats["session_declared_missing"] = session_gaps
    if session_unbalanced:
        # 짝이 안 맞아 역할을 빼버린 패키지 — 거짓 R8을 피한 대가로 그 패키지의 세션 검사는 꺼진다
        stats["session_role_dropped_unbalanced"] = session_unbalanced

    chunked = [c for doc in rag_docs for c in _split_document(doc, chunk_size, chunk_overlap)]
    return chunked, stats
