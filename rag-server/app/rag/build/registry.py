"""패키지 등기부(package_registry) 빌더 — khub 덤프(ToC+본문)에서 공식 패키지 전수를 등기한다.

전략 근거: final-etc-files/회의록/2026-07-18-khub-실측-저장전략.md §2.2.
어떤 단일 소스도 완전하지 않다는 실측(F2)에 따라 3소스 합집합으로 구성한다:
  ① 릴리스노트 로스터("Package updates overview" 자식) — 가장 넓음, 버전이력 페이지 연결
  ② 로스터 페이지("Packages available in Automation 360") 테이블 — 플랫폼(macOS/Windows) 메타
  ③ 본문 트리에서 발견되는 패키지 서브트리 — 액션 문서의 실제 위치(6개 브랜치 분산, F3)

이름 정규화는 실측된 접미어 변형(F1)을 전부 규칙화하고, 규칙으로 안 풀리는 예외는
`app/rag/data/registry_overrides.json`에 명시 등록한다(휴리스틱 확장 금지 원칙).
identity 키는 en 제목 유래 표기(display_en), ko 제목은 라벨로만 쓴다(F8).
"""

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

# 실측된 접미어 변형 전부 (순서 중요 — 구체적인 것부터)
_SUFFIX_PATTERNS = [
    r"\s+package\s+(?:updates|releases)$",
    r"\s+package\s+in\s+Automation\s+360$",
    r"\s+package\s*\([^)]*\)$",        # "... package (formerly known as Apigee package)"
    r"\s+package\s*-\s*.+$",           # "SAP package - Automate SAP applications"
    r"\s+packages$",                   # 롤업 "Google packages"
    r"\s+package$",
    # 'package'라는 낱말이 빠진 변형. 로스터 표의 트리거 7종 중 'Interface trigger updates'만
    # 이 꼴이라(나머지 6종은 'X trigger package updates') 위 규칙이 하나도 안 걸렸고,
    # 미정규화 원문이 그대로 패키지명으로 등기됐다(실측). 'trigger'/'package' 같은 패키지형
    # 낱말이 앞에 있을 때만 떼어낸다 — 'Completed feature deprecations' 류 오탐 방지.
    r"(?<=\btrigger)\s+(?:updates|releases)$",
]


def canonical_name(title: str) -> str:
    """패키지 제목에서 접미어 변형을 제거해 순수 이름을 얻는다. 매칭 없으면 원문 그대로."""
    t = (title or "").strip()
    for pat in _SUFFIX_PATTERNS:
        new = re.sub(pat, "", t, flags=re.IGNORECASE)
        if new != t:
            return new.strip()
    return t


def norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").casefold())


def normalize_pretty_url(pretty_url: str) -> str:
    m = re.match(r"^(/r)/[a-z]{2}-[a-z]{2}(/.*)$", pretty_url or "")
    return m.group(1) + m.group(2) if m else (pretty_url or "")


def walk_toc(nodes, path=None, parent=None, out=None):
    if out is None:
        out = []
    path = path or []
    for n in nodes:
        entry = {
            "title": n.get("title", ""),
            "content_id": n.get("contentId"),
            "toc_id": n.get("tocId"),
            "pretty_url": n.get("prettyUrl", ""),
            "path": list(path),
            "children": n.get("children", []),
        }
        out.append(entry)
        walk_toc(n.get("children", []), path + [entry["title"]], n, out)
    return out


def subtree_nodes(node: dict, base_path: list[str]) -> list[dict]:
    """루트 포함 모든 하위 노드를 (경로 포함) 평탄화."""
    out = []

    def rec(n, path):
        out.append(
            {
                "title": n.get("title", ""),
                "content_id": n.get("contentId"),
                "pretty_url": n.get("prettyUrl", ""),
                "path": list(path),
                "is_leaf": not n.get("children"),
            }
        )
        for c in n.get("children", []):
            rec(c, path + [n.get("title", "")])

    rec(node, base_path)
    return out


def load_overrides() -> dict:
    p = Path(__file__).resolve().parent.parent / "data" / "registry_overrides.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _load_bodies(dump: Path, locale: str) -> dict[str, dict]:
    docs = {}
    fp = dump / f"bodies_{locale}.jsonl"
    if fp.exists():
        with open(fp, encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                docs[d["content_id"]] = d
    return docs


def _parse_roster_tables(html: str) -> dict[str, dict]:
    """로스터 페이지의 **플랫폼 매트릭스** 테이블 → {norm_key: {name, macos, windows}}.

    주의(실측 2026-07-19): 이 페이지에는 첫 열이 Name인 테이블이 둘 있다 —
    ① 플랫폼 호환성 매트릭스(Name/macOS/Windows), ② 최근 업데이트 목록(Name/Updated in
    v.xx/Version/…). 첫 th만 보고 잡으면 ②를 집어 "Updated 여부"가 macos로 오염된다
    (CodeRabbit 리뷰 계기로 발견). 헤더에 macOS·Windows 열이 실제로 있는 테이블만 파싱한다.
    """
    out: dict[str, dict] = {}
    soup = BeautifulSoup(html or "", "html.parser")
    for tbl in soup.find_all("table"):
        ths = [th.get_text(" ", strip=True).lower() for th in tbl.find_all("th")[:4]]
        if not ths or "name" not in ths[0]:
            continue
        if len(ths) < 3 or "macos" not in ths[1] or "windows" not in ths[2]:
            continue  # 플랫폼 매트릭스가 아닌 Name 테이블(업데이트 목록 등)은 건너뜀
        for tr in tbl.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            name = cells[0].get_text(" ", strip=True)
            if not name:
                continue
            plat = [c.get_text(" ", strip=True) for c in cells[1:3]]

            def _supported(cell: str) -> bool:
                return bool(cell and cell not in ("-", "No"))

            out[norm_key(name)] = {
                "name": name,
                "macos": _supported(plat[0]) if len(plat) > 0 else False,
                # Windows 열도 동일 규칙으로 파싱 — Apple 계열처럼 Windows 미지원 패키지가 있다
                "windows": _supported(plat[1]) if len(plat) > 1 else True,
            }
    return out


# 액션 페이지 제목도 "~ package(s)"로 끝난다("Connect action for Google packages",
# "Activate user action in the Okta package"). 링크 후보에서 이런 것을 걸러내지 않으면
# 패키지 루트 대신 액션 한 건을 서브트리 루트로 잡아 그 패키지의 액션이 전부 사라진다
# (실측 2026-07-21: Google Calendar/Drive/Sheets 액션 59건 유실, 루트 승격으로 둔갑).
_ACTION_PAGE_TITLE = re.compile(r"\baction(s)?\b", re.IGNORECASE)

# 이름 토큰 판정 불용어 — 패키지형 접미어/관사. 링크 후보가 패키지명과 실제로 관련 있는지 볼 때 뺀다.
_NAME_STOP = {"package", "the", "a", "an", "of", "for", "and", "to", "in", "on"}


def _name_tokens(name: str) -> set[str]:
    """이름에서 의미 토큰 집합. 불용어·1글자를 뺀다 — 링크 노드가 패키지명과 관련 있나 판정용."""
    return {
        w for w in re.findall(r"[a-z0-9]+", (name or "").casefold())
        if len(w) >= 2 and w not in _NAME_STOP
    }


def _linked_doc_root(body: dict | None, by_tocid: dict, expect_name: str) -> dict | None:
    """릴리스노트 페이지 본문의 <span data-tocid> 중 그 패키지의 **문서 트리 루트**를 고른다.

    한 페이지가 여러 노드를 링크한다(실측: Bridge 릴리스노트는 4개 — 버전 번호, 패키지 문서,
    관련 가이드 2건). 후보를 다음 순서로 좁힌다:
      (a) Release Notes 브랜치 밖 + 제목이 패키지형(canonical_name이 접미어를 실제로 떼어냄)
      (b) 제목에 'action(s)'가 없다 — 액션 페이지 배제(위 상수 주석 참고)
      (c) 이름이 일치하면 그것을 택한다 — 링크와 이름이 서로를 확인해주는 가장 강한 근거
      (d) 이름이 안 맞으면(링크의 존재 이유: 'IQ Bot - DA Bridge' vs 'IQ Bot - Document
          Automation Bridge') 자식 보유 → 얕은 경로 순으로 택한다

    자식 보유를 **요구**하지는 않는다 — 단일 페이지 패키지(Goto/SOAP Web Service)도 잡아야 한다.
    """
    html = (body or {}).get("html")
    if not html:
        return None
    cands = []
    for span in BeautifulSoup(html, "html.parser").find_all(attrs={"data-tocid": True}):
        node = by_tocid.get(span["data-tocid"])
        if not node or not node.get("content_id"):
            continue
        if node["path"] and node["path"][0] == "Release Notes":
            continue
        title = node["title"]
        if canonical_name(title) == title:  # 패키지형 접미어가 없다 → 패키지 문서 아님
            continue
        if _ACTION_PAGE_TITLE.search(title):
            continue
        cands.append(node)
    if not cands:
        return None
    want = norm_key(expect_name)
    exact = [n for n in cands if norm_key(canonical_name(n["title"])) == want]
    if exact:
        cands = exact
    else:
        # 정확 매칭이 없으면 이름 토큰이 겹치는 후보만 남긴다. 링크가 완전히 무관한 노드를
        # 가리키는 경우(실측: 'Gmail' 릴리스노트가 stray로 'If package'를 링크 → Gmail 액션
        # 15건이 If의 3건으로 대체됨)를 거른다. 걸러지면 None을 반환해 ③ 트리 이름매칭이
        # 'Gmail package' 노드를 올바로 잡게 넘긴다. naming-variant('IQ Bot - DA Bridge' ↔
        # 'IQ Bot - Document Automation Bridge')는 공통 토큰(iq/bot/bridge)이 있어 통과한다.
        want_tokens = _name_tokens(expect_name)
        cands = [n for n in cands if _name_tokens(canonical_name(n["title"])) & want_tokens]
        if not cands:
            return None
    return sorted(cands, key=lambda n: (not n.get("children"), len(n["path"]), n["title"]))[0]


def build_registry(dump_dir: str | Path) -> dict:
    dump = Path(dump_dir)
    ov = load_overrides()
    exclude = {norm_key(t) for t in ov.get("exclude_titles", [])}
    alias_map = {norm_key(k): v for k, v in ov.get("canonical_aliases", {}).items()}

    toc_en = json.loads((dump / "toc_en-US.json").read_text(encoding="utf-8"))["toc"]
    toc_ko = json.loads((dump / "toc_ko-KR.json").read_text(encoding="utf-8"))["toc"]
    flat_en = walk_toc(toc_en)
    bodies_en = _load_bodies(dump, "en-US")

    # ko 페어링: pretty_url(로케일 접두 제거) → ko 노드
    ko_by_url = {
        normalize_pretty_url(e["pretty_url"]): e
        for e in walk_toc(toc_ko)
        if e.get("pretty_url")
    }

    # toc_id → 노드. 문서 본문의 <span data-tocid>가 이 키로 다른 문서를 확정 지목한다.
    by_tocid = {e["toc_id"]: e for e in flat_en if e.get("toc_id")}

    registry: dict[str, dict] = {}  # norm_key → entry
    linked_by_cid: dict[str, str] = {}  # 링크-서브트리 content_id → 패키지 키 (변형 브리지 보호용)

    def ensure(name: str, source: str) -> dict:
        key = norm_key(name)
        key = norm_key(alias_map.get(key, name))
        display = alias_map.get(norm_key(name), name)
        entry = registry.setdefault(
            key,
            {
                "display_en": display,
                "label_ko": None,
                "kind": "trigger" if display.casefold().endswith("trigger") else "action",
                "sources": [],
                "aliases": [],
                "platform": None,
                "has_doc_pages": False,
                "subtree_root": None,      # {content_id, title, path}
                "release_page": None,      # content_id
                "trigger_usage": [],       # 사용법 문서 제목들 (kind=trigger 전용)
            },
        )
        if source not in entry["sources"]:
            entry["sources"].append(source)
        if name != entry["display_en"] and name not in entry["aliases"]:
            entry["aliases"].append(name)
        return entry

    # ① 릴리스노트 로스터
    #
    # 여기서 링크 그래프도 같이 탄다 — 릴리스노트 페이지 본문의 <span data-tocid>가 그 패키지의
    # **문서 트리 노드를 확정적으로 지목**한다(실측 2026-07-21: 자식 135건 중 133건 98%).
    # 이름 매칭(norm_key)은 표기가 갈리면 조용히 실패한다 — 릴리스노트 'IQ Bot - Document
    # Automation Bridge' vs 문서트리 'IQ Bot - DA Bridge'가 실제로 그랬고, 액션 2개가 통째로
    # 유실됐다. 링크는 그 실패가 원리적으로 없다(doc_structure.py:11-16이 이미 실측해둔 사실).
    rel = next(e for e in flat_en if e["title"] == "Package updates overview")
    for child in rel["children"]:
        t = child.get("title", "")
        if norm_key(t) in exclude:
            continue
        name = canonical_name(t)
        if name == t:  # 접미어 규칙 미매칭 → 패키지 아님으로 간주하지 않고 경고 대상
            continue
        entry = ensure(name, "release_notes")
        entry["release_page"] = child.get("contentId")
        linked = _linked_doc_root(bodies_en.get(child.get("contentId")), by_tocid, name)
        if linked:
            entry["has_doc_pages"] = True
            entry["subtree_root"] = {
                "content_id": linked["content_id"],
                "title": linked["title"],
                "path": linked["path"],
            }
            entry["subtree_source"] = "release_link"  # 이름 매칭이 아니라 링크로 잡혔음
            if "doc_tree" not in entry["sources"]:
                entry["sources"].append("doc_tree")
            linked_by_cid[linked["content_id"]] = norm_key(alias_map.get(norm_key(name), name))

    # ② 로스터 페이지 테이블 (플랫폼)
    #
    # 문서 특정 주의(실측 2026-07-19): 덤프에는 "Packages available in v.40/39/38/37"
    # (버전별 업데이트 표)도 함께 있어, 부분 일치 next()로 고르면 엉뚱한 문서를 집는다.
    # 플랫폼 매트릭스는 "Packages available in Automation 360" 본체에만 있다.
    roster_doc = next(
        (
            d for d in bodies_en.values()
            if "packages available in automation 360" in d.get("title", "").lower()
        ),
        None,
    )
    if roster_doc:
        for row in _parse_roster_tables(roster_doc.get("html") or "").values():
            # 매트릭스 Name 셀은 링크 제목이라 "Apple Mail package"/"~ package updates"
            # 접미어가 붙어 온다 — 정규화 없이 등기하면 중복 항목이 생긴다(실측 251개 폭증).
            name = canonical_name(row["name"])
            if not name:
                continue
            entry = ensure(name, "roster_page")
            entry["platform"] = {"macos": row["macos"], "windows": row["windows"]}

    # ③ 본문 트리 서브트리 발견 (Release Notes 브랜치 제외, 얕은 매치 우선)
    #
    # 주의(과탐 방지): "Activate user action in the Okta package"처럼 액션 페이지 제목도
    # " package"로 끝난다. 그래서 패키지형 접미어만으로는 부족하고,
    #   (a) ①·②에서 이미 등기된 이름(seed)과 일치하거나
    #   (b) 컨테이너(자식 보유)이면서 부모가 알려진 패키지 섹션(액션 섹션 직계·롤업 하위)일 때
    # 만 서브트리 루트로 인정한다. — 실측상 doc-only 신규 패키지(AWS Comprehend NLP 등)는
    # 전부 액션 섹션 직계 컨테이너였다(2026-07-18 khub 실측 F2).
    seed_keys = set(registry.keys())
    rollups = {norm_key(t) for t in ov.get("rollup_titles", [])}
    _PKG_PARENTS = {"Actions to build automations"} | set(ov.get("rollup_titles", []))
    # 트리 우선: 이름이 매칭되는 'X package' 트리 노드가 있으면 그걸 서브트리 루트로 **확정**하고
    # ①의 릴리스-링크를 덮어쓴다 — 링크는 stray 링크에 취약(Gmail←If 오귀속)하나, 트리 노드는
    # 위치(액션 섹션 직계)와 이름이 스스로를 증명한다. 단, 링크가 **다른(변형명)** 패키지의 서브트리로
    # 지목한 노드('IQ Bot - DA Bridge' ← 'IQ Bot - Document Automation Bridge')는 건드리지 않는다 —
    # 그 브리지는 링크만이 이어줄 수 있고, 트리가 덮으면 변형 패키지가 중복/유실된다.
    claimed: set[str] = set()
    for e in sorted(flat_en, key=lambda x: len(x["path"])):
        if e["path"] and e["path"][0] == "Release Notes":
            continue
        if norm_key(e["title"]) in exclude:
            continue
        name = canonical_name(e["title"])
        if name == e["title"]:
            continue  # 패키지형 제목 아님
        if norm_key(e["title"]) in rollups:
            continue  # 롤업은 자식이 개별 패키지로 잡힌다
        key = norm_key(alias_map.get(norm_key(name), name))
        is_seed = key in seed_keys
        is_section_container = bool(e["children"]) and bool(e["path"]) and e["path"][-1] in _PKG_PARENTS
        if not (is_seed or is_section_container):
            continue
        if linked_by_cid.get(e["content_id"], key) != key:
            continue  # 이 노드는 다른 변형명 패키지의 링크-서브트리다 — 덮어쓰기/중복 금지
        if key in claimed:
            continue  # 같은 패키지의 더 깊은/중복 노드
        claimed.add(key)
        entry = ensure(name, "doc_tree")
        entry["has_doc_pages"] = True
        entry["subtree_root"] = {
            "content_id": e["content_id"],
            "title": e["title"],
            "path": e["path"],
        }
        entry["subtree_source"] = "doc_tree"  # 트리 'X package' 노드 이름매칭으로 잡음
        ko = ko_by_url.get(normalize_pretty_url(e["pretty_url"]))
        if ko:
            entry["label_ko"] = canonical_name_ko(ko["title"])

    # 트리거 사용법 문서 매핑 (패키지 페이지가 없는 트리거 5종 — F3)
    for pkg, titles in ov.get("trigger_usage", {}).items():
        entry = ensure(pkg, "trigger_usage_docs")
        entry["kind"] = "trigger"
        entry["trigger_usage"] = titles

    # 커버리지 리포트
    release_names = {k for k, v in registry.items() if "release_notes" in v["sources"]}
    doc_names = {k for k, v in registry.items() if v["has_doc_pages"]}
    report = {
        "total": len(registry),
        "with_doc_pages": len(doc_names),
        "release_only(no_doc_pages)": sorted(
            registry[k]["display_en"]
            for k in release_names - doc_names
            if not registry[k]["trigger_usage"]
        ),
        "doc_only(not_in_release_notes)": sorted(
            registry[k]["display_en"] for k in doc_names - release_names
        ),
    }
    return {"packages": sorted(registry.values(), key=lambda p: p["display_en"].casefold()), "report": report}


_KO_SUFFIX = re.compile(r"\s*패키지(\s*업데이트)?\s*$")


def canonical_name_ko(title: str) -> str:
    return _KO_SUFFIX.sub("", (title or "").strip()).strip()
