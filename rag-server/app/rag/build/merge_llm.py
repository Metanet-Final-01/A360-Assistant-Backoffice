"""v2 문서 빌더 — 등기부(package_registry) + khub 덤프에서 rag_documents를 만드는 **정본 빌더**.

action_schema/trigger_schema는 패키지 단위 LLM 구조화 추출(extract_llm)로 생성한다. 규칙 파싱
빌더(build_documents_v2)는 이 경로로 일원화되면서 제거됐다(refactor/remove-build-v2). 공용
헬퍼(_doc_id·_plain_text·_soup·_release_versions·action_label_ko 등)만 common에서 가져온다.

산출 source_type: package_overview / action_schema / trigger_schema / package_release / doc_page.
doc_page는 **ko·en 양 언어를 각각 별도 행으로** 싣는다. 트리거는 'Build automations > Triggers'
트리를 따로 순회해 수집한다. crawl/registry/ingest/embed/opensearch는 무변경 재사용.

흐름은 2단이다: (A) subtree 보유 패키지의 LLM 추출을 스레드풀로 **병렬** 수행(벽시계 단축),
(B) 등기부 순서대로 release/overview/action 행을 **순차** 조립(순서·중복 결정성 확보).
"""

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .. import config
from .extract_llm import _write_cache, extract_package
from .merge import _split_document, chunk_params_for
from .common import (
    _doc_id,
    _doc_uid,
    _load_bodies,
    _plain_text,
    _release_versions,
    _shortdesc,
    _soup,
    action_label_ko,
)
from .registry import load_overrides, norm_key, normalize_pretty_url, walk_toc

logger = logging.getLogger(__name__)

_DOC_PAGE_TEXT_LIMIT = 200_000
_KO_BODY_MIN = 200
_KO_BODY_HEAD = "\n본문(ko):\n"

# 트리거 문서 루트. 트리거 패키지 7종은 등기부에 subtree_root가 없어(릴리스노트/사용법 문서로만
# 잡힌다) 패키지 경로에 안 걸린다 — 문서 트리에서 트리거는 'Build automations > Triggers'
# 아래에 따로 모여 있다. 그래서 이 루트를 패키지처럼 따로 순회해 trigger_schema로 수집한다.
TRIGGER_ROOT_URL = "/r/cloud-build/triggers-concept"

# 트리거 이름 귀속에서 뺄 일반어 — 이게 없으면 'trigger'만 겹쳐도 아무 패키지에나 붙는다.
_TRIG_STOP = {"trigger", "triggers", "the", "a", "an", "for", "in", "on", "to", "of", "and",
              "creating", "configuring", "using", "event", "web", "package"}


def _trigger_tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").casefold())
            if len(w) >= 2 and w not in _TRIG_STOP}


def _attribute_trigger(name: str, trigger_pkgs: list[str]) -> str:
    """추출된 트리거 이름을 등기부의 트리거 패키지에 귀속시킨다.

    "토큰 겹침 최다"는 쓰지 않는다 — 실측에서 'Microsoft Teams event triggers'가 'microsoft'
    한 토큰만 겹쳐 'Microsoft 365 Outlook trigger'로 오귀속됐다. 대신 **그 패키지를 특정하는
    표기가 트리거 이름 안에 통째로 들어 있을 때만** 붙인다:
      ① 공백/기호를 제거한 표기 포함  ('Hotkey trigger' ⊂ 'Hot key trigger' → 매칭)
      ② 패키지의 의미 토큰이 **전부** 트리거 이름에 존재 ('File Folder' ⊆ 'File and Folder')
    둘 다 아니면 'Triggers'로 둔다 — 억지 귀속은 소비처의 잘못된 조회를 만든다.
    """
    if not name:
        return "Triggers"
    hay = norm_key(name)
    want = _trigger_tokens(name)
    # ① 공백 제거 포함관계 — 가장 강한 근거부터
    for p in trigger_pkgs:
        if norm_key(p) and norm_key(p) in hay:
            return p
    # ② 패키지 토큰 전체 포함
    best, score = "Triggers", 0
    for p in trigger_pkgs:
        ptok = _trigger_tokens(p)
        if ptok and ptok <= want and len(ptok) > score:
            best, score = p, len(ptok)
    return best


def _param_dict(p) -> dict:
    """LLMParam → 소비처(백엔드 카탈로그)가 읽는 파라미터 dict(JAR/에이전트 스키마와 동형)."""
    return {
        "name": p.name,
        "label": p.name,
        "type": p.type,
        "required": p.required,
        "description": p.description or None,
    }


def build_documents_llm(dump_dir: str | Path, registry: dict, chunk_size: int, chunk_overlap: int,
                        *, model: str | None = None) -> tuple[list[dict], dict]:
    dump = Path(dump_dir)
    model = model or config.AGENT_PARSE_MODEL
    _ = load_overrides()  # 향후 예외 보정 훅(현재 미사용, 규칙 제거 취지)
    # ko 본문을 붙일 때의 content 상한 — 청킹 입력이라 넘기면 액션 한 건이 여러 청크로
    # 흩어진다. ⚠️ 호출자가 넘긴 chunk_size가 아니라 **action_schema에 실제로 적용될** 폭을
    # 써야 한다(config.CHUNK_PARAMS_BY_SOURCE_TYPE로 타입별 분리 — 현재 1500). 호출자 값
    # (1200)을 그대로 쓰면 예산이 300자 좁아져 ko 본문이 근거 없이 잘린다.
    content_limit = chunk_params_for("action_schema", chunk_size or 1200, chunk_overlap)[0]

    toc_en = json.loads((dump / "toc_en-US.json").read_text(encoding="utf-8"))["toc"]
    flat_en = walk_toc(toc_en)
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

    stats: dict = {"package_overview": 0, "action_schema": 0, "trigger_schema": 0,
                   "package_release": 0, "packages_extracted": 0}
    ex_stats: dict = {"cached": 0, "called": 0, "failed": 0, "map_reduced": 0,
                      "hallucinated_node": 0, "rejected_not_verbatim": 0, "recovered_by_retry": 0,
                      "param_rejected_not_verbatim": 0, "param_recovered_by_retry": 0,
                      "completeness_missing": 0}
    lock = threading.Lock()

    cache_path = config.DATA_DIR / "extract_llm_cache.json"
    cache: dict = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    # ── 단계 A: subtree 보유 패키지 LLM 추출 (병렬) ──────────────────────────────
    targets = []
    failed_packages: dict[str, str] = {}  # display → 실패/스킵 사유 (조용한 누락 방지)
    for pkg in registry["packages"]:
        sr = pkg.get("subtree_root")
        if not sr:
            continue
        root_entry = node_by_cid.get(sr["content_id"])
        if root_entry is None:
            # 등기부는 서브트리를 가리키는데 ToC에서 그 노드를 못 찾았다 — 조용히 빠지면
            # 패키지가 코퍼스에서 통째로 사라지고 원인도 안 남는다.
            failed_packages[pkg["display_en"]] = "root_not_found"
            logger.warning("서브트리 루트를 ToC에서 못 찾음: %s (content_id=%s)",
                           pkg["display_en"], sr.get("content_id"))
            continue
        root_toc = {
            "contentId": root_entry["content_id"], "title": root_entry["title"],
            "prettyUrl": root_entry["pretty_url"], "children": root_entry.get("children", []),
        }
        # 트리거 패키지가 서브트리를 가진 경우 판정 기준이 다르다 — 액션 프롬프트로 보면
        # 트리거 문서를 절차/개념으로 판정해 버린다.
        targets.append((pkg["display_en"], root_toc, root_entry, pkg.get("kind") or "action"))

    extracted: dict[str, tuple] = {}  # display → (verdicts, index, root_entry)

    from app.core.llm import usage_context

    def _run(display: str, root_toc: dict, root_entry: dict, pkg_kind: str):
        verdicts, index = extract_package(
            display, root_toc, bodies_en, model=model, stats=ex_stats, lock=lock, cache=cache,
            kind="trigger" if pkg_kind == "trigger" else "action",
        )
        return display, verdicts, index, root_entry

    workers = max(1, config.AGENT_PARSE_WORKERS)
    print(f"[build-llm] 패키지 {len(targets)}개 LLM 추출 (모델 {model}, 워커 {workers})", flush=True)
    with usage_context(component="rag_parse", actor_type="system"):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run, d, rt, re_, k): d for d, rt, re_, k in targets}
            done = 0
            for fut in as_completed(futures):
                done += 1
                # 패키지 하나의 예외가 빌드 전체를 죽이면 산출물(jsonl·stats)이 아예 안 나온다 —
                # 실패는 그 패키지만 격리하고 사유를 남긴 뒤 나머지를 계속 처리한다.
                try:
                    display, verdicts, index, root_entry = fut.result()
                except Exception as exc:  # noqa: BLE001 — best-effort 격리
                    display = futures[fut]
                    failed_packages[display] = f"{type(exc).__name__}: {exc}"
                    logger.exception("패키지 추출 실패 (건너뜀): %s", display)
                    with lock:
                        ex_stats["worker_failed"] = ex_stats.get("worker_failed", 0) + 1
                        ex_stats.setdefault("worker_failed_packages", []).append(display)
                else:
                    extracted[display] = (verdicts, index, root_entry)
                if done % 10 == 0:
                    print(f"  추출 {done}/{len(targets)}", flush=True)

    # ── 단계 B: 등기부 순서대로 행 조립 (순차 — 순서·중복 결정성) ───────────────
    rag_docs: list[dict] = []
    for pkg in registry["packages"]:
        display = pkg["display_en"]

        # package_release (기존 로직 재사용)
        rel_cid = pkg.get("release_page")
        rel_doc = bodies_en.get(rel_cid) if rel_cid else None
        versions = []
        if rel_doc and rel_doc.get("html"):
            versions = _release_versions(_soup(rel_doc["html"]))
            if versions:
                latest = versions[0]
                rag_docs.append({
                    "id": _doc_id("release", display),
                    "source_type": "package_release",
                    "package_name": display, "action_name": None, "locale": "en-US",
                    "title": f"{display} 패키지 버전 이력",
                    "url": rel_doc.get("pretty_url", ""),
                    "content": (f"패키지: {display}\n최신 버전: {latest['version']} "
                                f"({latest['release_date']}, {latest['release_type']})\n"
                                f"버전 이력: " + ", ".join(v["version"] for v in versions[:10])),
                    "metadata": {"versions": versions, "schema_source": "docs_rule"},
                })
                stats["package_release"] += 1

        if display not in extracted:
            if pkg.get("subtree_root"):
                # 서브트리는 있는데 추출이 없다 = 루트 미발견 또는 워커 실패. 조용히 건너뛰면
                # 패키지가 코퍼스에서 사라진 사실조차 안 남으므로, 사유를 실은 개요 행을 남긴다.
                reason = failed_packages.get(display, "extraction_missing")
                rag_docs.append({
                    "id": _doc_id("pkg2", display),
                    "source_type": "package_overview",
                    "package_name": display, "action_name": None, "locale": "en-US",
                    "title": f"{display} 패키지",
                    "url": (rel_doc or {}).get("pretty_url", ""),
                    "content": (f"패키지: {display}\n액션 추출 실패 — 이 패키지의 액션 목록은 "
                                f"이번 빌드에서 만들어지지 않았습니다(사유: {reason})."),
                    "metadata": {"has_doc_pages": True, "kind": pkg["kind"],
                                 "platform": pkg.get("platform"), "schema_source": "llm_agent",
                                 "extraction_failed": True, "failure_reason": reason},
                })
                stats["package_overview"] += 1
                stats.setdefault("extraction_failed_packages", []).append(f"{display}({reason})")
                continue
            if versions or pkg.get("sources"):
                rag_docs.append({
                    "id": _doc_id("pkg2", display),
                    "source_type": "package_overview",
                    "package_name": display, "action_name": None, "locale": "en-US",
                    "title": f"{display} 패키지",
                    "url": (rel_doc or {}).get("pretty_url", ""),
                    "content": (f"패키지: {display}\n공식 액션 문서 없음(has_doc_pages=false).\n"
                                + (f"버전 이력 존재: 최신 {versions[0]['version']}" if versions else "")),
                    "metadata": {"has_doc_pages": False, "kind": pkg["kind"],
                                 "schema_source": "docs_rule", "platform": pkg.get("platform")},
                })
                stats["package_overview"] += 1
            continue

        verdicts, index, root_entry = extracted[display]
        stats["packages_extracted"] += 1
        source_type = "trigger_schema" if pkg["kind"] == "trigger" else "action_schema"
        sr = pkg["subtree_root"]
        root_soup = _soup((bodies_en.get(sr["content_id"]) or {}).get("html"))
        ko_root = ko_pair(root_entry["pretty_url"])

        # package_overview (신규 추출 액션명 목록으로 구성)
        action_names = [ea.action.name for ea in verdicts]
        rag_docs.append({
            "id": _doc_id("pkg2", display),
            "source_type": "package_overview",
            "package_name": display, "action_name": None,
            "locale": "ko-KR" if ko_root else "en-US",
            "title": f"{display} 패키지" + (f" ({pkg['label_ko']})" if pkg.get("label_ko") else ""),
            "url": root_entry["pretty_url"],
            "content": (f"패키지: {display}" + (f" / {pkg['label_ko']}" if pkg.get("label_ko") else "") + "\n"
                        f"설명: {_shortdesc(root_soup)}\n"
                        f"액션 목록({len(action_names)}개, LLM 추출): " + ", ".join(action_names[:40])),
            "metadata": {"has_doc_pages": True, "kind": pkg["kind"], "platform": pkg.get("platform"),
                         "actions_extracted": action_names, "label_ko": pkg.get("label_ko"),
                         "schema_source": "llm_agent"},
        })
        stats["package_overview"] += 1

        # action_schema / trigger_schema (신규)
        seen_names: dict[str, int] = {}  # norm_key(name) → rag_docs 인덱스 (패키지 내 dedup)
        for ea in verdicts:
            name = ea.action.name
            nid = ea.node_id
            meta_node = index.get(nid, {})
            url = meta_node.get("url", "")
            label_en = meta_node.get("title", name)
            ko = ko_pair(url)
            ko_soup = _soup(ko["html"]) if ko and ko.get("html") else None
            label = action_label_ko(display, pkg.get("label_ko"), ko["title"] if ko else None)
            params = ea.action.parameters
            params_source = "llm_agent" if params else "none"

            nk = norm_key(name)
            if nk in seen_names:
                prev = rag_docs[seen_names[nk]]
                prev_np = len((prev["metadata"].get("schema") or {}).get("parameters") or [])
                if len(params) <= prev_np:
                    continue
                rag_docs[seen_names[nk]] = None  # 파라미터 더 많은 새 행으로 교체(뒤에서 압축)

            param_dicts = [_param_dict(p) for p in params]
            param_lines = ("\n".join(f"- {p['name']}: {p['description'] or ''}" for p in param_dicts)
                           if param_dicts else "없음")
            en_soup = _soup((bodies_en.get(nid) or {}).get("html"))
            desc_ko = _shortdesc(ko_soup, 400) if ko_soup else ""
            content = (
                f"패키지: {display}" + (f" / {pkg['label_ko']}" if pkg.get("label_ko") else "") + "\n"
                f"액션: {name}" + (f" / {ko['title']}" if ko else "") + "\n"
                f"설명: " + (f"{desc_ko} / " if desc_ko else "") + f"{_shortdesc(en_soup)}\n"
                f"파라미터:\n{param_lines}"
            )
            ko_budget = content_limit - len(content) - len(_KO_BODY_HEAD)
            ko_body = _plain_text(ko_soup, ko_budget) if ko_soup and ko_budget >= _KO_BODY_MIN else ""
            if ko_body:
                content += _KO_BODY_HEAD + ko_body

            row = {
                # id 접두는 소스타입과 맞춘다 — 트리거 행이 action2 id를 달면 트리거 트리
                # 방출분(trigger2)과 규칙이 갈려 소비처가 같은 트리거를 둘로 본다.
                "id": _doc_id("trigger2" if source_type == "trigger_schema" else "action2",
                              display, name),
                "source_type": source_type,
                "package_name": display, "action_name": name,
                "locale": "ko-KR" if ko else "en-US",
                "title": f"{display} - {name}",
                "url": url,
                "content": content,
                "metadata": {
                    "doc_uid": _doc_uid(url),
                    "label_en": label_en, "label_ko": ko["title"] if ko else None,
                    "action_label_ko": label,
                    "identity_confidence": "llm_extracted",
                    "params_source": params_source,
                    "schema_source": "llm_agent",
                    "schema": {"name": name, "label": label or name, "parameters": param_dicts},
                    "extraction_reason": ea.reason,
                    "node_id": nid,
                    "ko_body_included": bool(ko_body),
                },
            }
            rag_docs.append(row)
            seen_names[nk] = len(rag_docs) - 1
            stats[source_type] += 1

    # ── 트리거: 'Build automations > Triggers' 서브트리를 따로 순회해 수집 ──────────
    # 패키지 경로와 같은 추출기를 쓰되 트리거 전용 프롬프트(kind="trigger")로 판정한다.
    trig_root = next(
        (e for e in flat_en
         if normalize_pretty_url(e.get("pretty_url", "")) == TRIGGER_ROOT_URL), None
    )
    if trig_root is None:
        logger.warning("트리거 루트(%s)를 ToC에서 찾지 못했습니다 — 트리거 수집 건너뜀", TRIGGER_ROOT_URL)
    else:
        trig_toc = {
            "contentId": trig_root["content_id"], "title": trig_root["title"],
            "prettyUrl": trig_root["pretty_url"], "children": trig_root.get("children", []),
        }
        print(f"[build-llm] 트리거 트리 추출 ({trig_root['title']})", flush=True)
        with usage_context(component="rag_parse", actor_type="system"):
            trig_actions, trig_index = extract_package(
                "Triggers", trig_toc, bodies_en, model=model,
                stats=ex_stats, lock=lock, cache=cache, kind="trigger",
            )
        trigger_pkgs = [p["display_en"] for p in registry["packages"] if p.get("kind") == "trigger"]
        seen_trig: dict[tuple[str, str], int] = {}
        for ea in trig_actions:
            name = ea.action.name
            pkg_name = _attribute_trigger(name, trigger_pkgs)
            meta_node = trig_index.get(ea.node_id, {})
            url = meta_node.get("url", "")
            ko = ko_pair(url)
            ko_soup = _soup(ko["html"]) if ko and ko.get("html") else None
            params = ea.action.parameters
            key = (norm_key(pkg_name), norm_key(name))
            if key in seen_trig:  # 같은 트리거 중복 — 파라미터 많은 쪽 유지
                prev = rag_docs[seen_trig[key]]
                if len(params) <= len((prev["metadata"].get("schema") or {}).get("parameters") or []):
                    continue
                rag_docs[seen_trig[key]] = None
            param_dicts = [_param_dict(p) for p in params]
            param_lines = ("\n".join(f"- {p['name']}: {p['description'] or ''}" for p in param_dicts)
                           if param_dicts else "없음")
            en_soup = _soup((bodies_en.get(ea.node_id) or {}).get("html"))
            desc_ko = _shortdesc(ko_soup, 400) if ko_soup else ""
            content = (
                f"트리거 패키지: {pkg_name}\n"
                f"트리거: {name}" + (f" / {ko['title']}" if ko else "") + "\n"
                f"설명: " + (f"{desc_ko} / " if desc_ko else "") + f"{_shortdesc(en_soup)}\n"
                f"설정 파라미터:\n{param_lines}"
            )
            ko_budget = content_limit - len(content) - len(_KO_BODY_HEAD)
            ko_body = _plain_text(ko_soup, ko_budget) if ko_soup and ko_budget >= _KO_BODY_MIN else ""
            if ko_body:
                content += _KO_BODY_HEAD + ko_body
            rag_docs.append({
                "id": _doc_id("trigger2", pkg_name, name),
                "source_type": "trigger_schema",
                "package_name": pkg_name, "action_name": name,
                "locale": "ko-KR" if ko else "en-US",
                "title": f"{pkg_name} - {name}",
                "url": url,
                "content": content,
                "metadata": {
                    "doc_uid": _doc_uid(url),
                    "label_en": meta_node.get("title", name),
                    "label_ko": ko["title"] if ko else None,
                    "kind": "trigger",
                    "identity_confidence": "llm_extracted",
                    "params_source": "llm_agent" if params else "none",
                    "schema_source": "llm_agent",
                    "schema": {"name": name, "label": name, "parameters": param_dicts},
                    "extraction_reason": ea.reason,
                    "node_id": ea.node_id,
                    "ko_body_included": bool(ko_body),
                },
            })
            seen_trig[key] = len(rag_docs) - 1
        stats["triggers_extracted"] = len(trig_actions)

    rag_docs = [d for d in rag_docs if d is not None]  # dedup으로 비운 자리 압축
    for k in ("action_schema", "trigger_schema", "package_overview", "package_release"):
        stats[k] = sum(1 for d in rag_docs if d["source_type"] == k)

    # ── doc_page: 모든 크롤 문서 원문 전량, ko·en 각 1행 (신규: 양 언어 보관) ──
    catalog_urls = {normalize_pretty_url(d.get("url", "")) for d in rag_docs if d.get("url")}
    doc_stats = {"doc_page": 0, "doc_page_ko": 0, "doc_page_en": 0, "doc_page_no_body": 0,
                 "doc_page_matched_action": 0, "doc_page_extra_maps": 0}
    seen_doc: set[str] = set()
    extra_cids = {n["content_id"] for n in extra_nodes if n.get("content_id")}
    for node in flat_en + extra_nodes:
        cid = node.get("content_id")
        if not cid or cid in seen_doc:
            continue
        seen_doc.add(cid)
        crumbs = [c for c in (node.get("path") or []) if c]
        matched = normalize_pretty_url(node.get("pretty_url", "")) in catalog_urls
        en_body = bodies_en.get(cid)
        ko_body = ko_pair(node.get("pretty_url", ""))
        emitted_any = False
        for locale, body, is_ko in (("en-US", en_body, False), ("ko-KR", ko_body, True)):
            if not body or not body.get("html"):
                continue
            text = _plain_text(_soup(body["html"]), _DOC_PAGE_TEXT_LIMIT)
            if not text:
                continue
            title = node["title"] + (f" / {ko_body['title']}" if is_ko and ko_body.get("title") else "")
            rag_docs.append({
                "id": _doc_id("doc2", cid, locale),  # 언어까지 포함해 ko·en 충돌 방지
                "source_type": "doc_page",
                "package_name": None, "action_name": None, "locale": locale,
                "title": title,
                "url": node.get("pretty_url", ""),
                "content": (" > ".join(crumbs) + "\n\n" if crumbs else "") + text,
                "metadata": {"breadcrumbs": crumbs, "toc_id": node.get("toc_id"),
                             "matched_to_action": matched, "schema_source": "docs_raw"},
            })
            doc_stats["doc_page"] += 1
            doc_stats["doc_page_ko" if is_ko else "doc_page_en"] += 1
            emitted_any = True
        if emitted_any:
            if cid in extra_cids:
                doc_stats["doc_page_extra_maps"] += 1
            if matched:
                doc_stats["doc_page_matched_action"] += 1
        else:
            doc_stats["doc_page_no_body"] += 1
    stats.update(doc_stats)
    stats["extract_llm"] = ex_stats

    _write_cache(cache_path, cache)

    # id 중복 검사(같은 정규화 이름 등) — 나중 것에 salt(doc_uid/title)를 붙여 유일해질 때까지 재해시한다.
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

    chunked = [c for doc in rag_docs for c in _split_document(doc, chunk_size, chunk_overlap)]
    return chunked, stats
