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

from bs4 import BeautifulSoup

from .merge import _split_document
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


def _shortdesc(soup: BeautifulSoup, limit: int = 600) -> str:
    p = soup.select_one("p.shortdesc")
    text = p.get_text(" ", strip=True) if p else soup.get_text(" ", strip=True)
    return text[:limit]


def _plain_text(soup: BeautifulSoup, limit: int = 1500) -> str:
    return soup.get_text("\n", strip=True)[:limit]


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


def _overview_action_table(soup: BeautifulSoup) -> list[dict]:
    rows = []
    for tbl in soup.find_all("table"):
        ths = [th.get_text(" ", strip=True).lower() for th in tbl.find_all("th")[:3]]
        if not ths or ths[0] not in ("action", "actions"):
            continue
        for tr in tbl.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) >= 1 and cells[0].get_text(strip=True):
                rows.append(
                    {
                        "action": cells[0].get_text(" ", strip=True)[:80],
                        "description": cells[1].get_text(" ", strip=True)[:300] if len(cells) > 1 else "",
                    }
                )
    return rows


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


def build_documents_v2(dump_dir: str | Path, registry: dict, chunk_size: int, chunk_overlap: int,
                       enricher=None) -> list[dict]:
    dump = Path(dump_dir)
    ov = load_overrides()
    non_action_markers = [m.casefold() for m in ov.get("non_action_leaf_markers", [])]

    toc_en = json.loads((dump / "toc_en-US.json").read_text(encoding="utf-8"))["toc"]
    flat_en = walk_toc(toc_en)
    node_by_cid = {e["content_id"]: e for e in flat_en if e["content_id"]}
    bodies_en = _load_bodies(dump, "en-US")
    bodies_ko = _load_bodies(dump, "ko-KR")
    ko_by_url = {normalize_pretty_url(d.get("pretty_url", "")): d for d in bodies_ko.values()}

    def ko_pair(pretty_url: str) -> dict | None:
        return ko_by_url.get(normalize_pretty_url(pretty_url))

    rag_docs: list[dict] = []
    stats = {"package_overview": 0, "action_schema": 0, "trigger_schema": 0, "package_release": 0, "skipped_no_html": 0}

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
        table_actions = _overview_action_table(root_soup)
        table_keys = {norm_key(r["action"]) for r in table_actions}
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

        for node in nodes:
            if not node["is_leaf"] or not node["content_id"] or node["content_id"] == pkg["subtree_root"]["content_id"]:
                continue
            title = node["title"]
            if any(m in title.casefold() for m in non_action_markers):
                continue
            body = bodies_en.get(node["content_id"])
            if not body or not body.get("html"):
                stats["skipped_no_html"] += 1
                continue
            soup = _soup(body["html"])
            name = action_identity(title, display)
            dl_params = _dl_params(soup)
            candidates = [] if dl_params else _uicontrol_candidates(soup, {name.casefold(), display.casefold()})
            ko = ko_pair(node.get("pretty_url", ""))
            label = action_label_ko(display, pkg.get("label_ko"), ko["title"] if ko else None)
            confidence = "table_confirmed" if norm_key(name) in table_keys else "leaf_unconfirmed"
            category = [p for p in node["path"] if p not in (pkg["subtree_root"]["path"][0] if pkg["subtree_root"]["path"] else "",)][-1:] if node["path"] else []

            param_lines = (
                "\n".join(f"- {p['name']}: {p['description']}" for p in dl_params)
                if dl_params
                else ("후보 필드(uicontrol): " + ", ".join(candidates) if candidates else "없음")
            )
            content = (
                f"패키지: {display}" + (f" / {pkg['label_ko']}" if pkg.get("label_ko") else "") + "\n"
                f"액션: {name}" + (f" / {ko['title']}" if ko else "") + "\n"
                f"설명: {_shortdesc(soup)}\n"
                f"파라미터:\n{param_lines}"
            )
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
                    },
                }
            )
            stats["action_schema"] += 1

    # id 중복 검사 (같은 정규화 이름의 리프 2개 등) — 나중 것에 doc_uid를 붙여 살린다
    seen: dict[str, dict] = {}
    for d in rag_docs:
        if d["id"] in seen:
            d["id"] = _doc_id(d["id"], d["metadata"].get("doc_uid", d["title"]))
        seen[d["id"]] = d

    # LLM 보강(2단)은 반드시 청킹 전에 — 청크는 content 파생물이라 이후 수정하면 어긋난다
    if enricher is not None:
        stats["enrich"] = enricher(rag_docs)

    chunked = [c for doc in rag_docs for c in _split_document(doc, chunk_size, chunk_overlap)]
    return chunked, stats
