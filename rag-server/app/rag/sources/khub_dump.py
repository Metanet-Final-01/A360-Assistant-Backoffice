"""khub(Fluid Topics) v2 덤프 생성 — build-llm이 소비하는 toc_*.json + bodies_*.jsonl[html].

기존 docs_crawler(v1 docs.jsonl)와 **별개**다: 이건 registry/build-llm 계층이 쓰는 원문 덤프를
만든다 — raw html 포함, 주 맵('Automation 360') **전수** + 보조 맵(Control Room APIs 등) 전부.
세션 스크래치패드에만 있던 크롤러 2종(주 맵 전수 + 보조 맵)을 하나로 합쳐 리포에 편입한 것(P5).

산출(dump_dir):
  toc_{locale}.json            주 맵 목차 {map_id, title, toc}
  toc_{locale}__{slug}.json    보조 맵 목차 (build-llm이 toc_{locale}__*.json 글롭으로 읽음)
  bodies_{locale}.jsonl        모든 토픽 본문 — {content_id, toc_id, title, breadcrumbs,
                               pretty_url, map_id, map_title, html}. _load_bodies가 content_id로 인덱싱.

이어받기: bodies_{locale}.jsonl에 html이 이미 있는 content_id는 다시 안 받는다 — 재실행이
빠르고 중단에 강하다. (증분 판정은 LLM 캐시가 내용 해시로 하므로 여기선 전수 재순회해도 된다.
크롤 시간 자체는 못 줄인다 — 해시하려면 본문을 받아야 하니까.)

⚠️ append라 실행 전 백업 권장. 보조 맵 content_id가 주 맵과 겹치면 done 집합이 재수집을
막아준다(중복 레코드는 _load_bodies가 마지막 것으로 덮어쓰므로 무해).
"""

import json
import re
import time
from pathlib import Path

from . import docs_crawler as dc

PRIMARY_TITLE = "Automation 360"
DEFAULT_LOCALES = ("ko-KR", "en-US")


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")


def _flatten_toc(nodes: list[dict], path: list[str] | None = None, out: list[dict] | None = None) -> list[dict]:
    """TOC 전체를 평탄화 — contentId 있는 모든 노드(서브트리 필터 없음)."""
    if out is None:
        out = []
    path = path or []
    for n in nodes:
        title = n.get("title", "")
        if n.get("contentId"):
            out.append({
                "content_id": n["contentId"],
                "toc_id": n.get("tocId"),
                "title": title,
                "breadcrumbs": list(path),
                "pretty_url": n.get("prettyUrl", ""),
            })
        _flatten_toc(n.get("children", []), path + [title], out)
    return out


def _dedup(topics: list[dict]) -> list[dict]:
    seen: set[str] = set()
    uniq = []
    for t in topics:
        if t["content_id"] not in seen:
            seen.add(t["content_id"])
            uniq.append(t)
    return uniq


def _load_done(path: Path) -> set[str]:
    """html까지 받은 content_id 집합 — 이어받기/중복방지용."""
    done: set[str] = set()
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("html"):
                    done.add(d["content_id"])
    return done


def _maps_for_locale(maps: list[dict], locale: str) -> list[dict]:
    out = []
    for m in maps:
        md = {x["key"]: x["values"] for x in m.get("metadata", [])}
        if md.get("ft:locale") == [locale]:
            out.append(m)
    return out


def _fetch_bodies(client, map_id: str, map_title: str, topics: list[dict], out_path: Path,
                  done: set[str], delay: float, on_progress) -> tuple[int, int]:
    ok = fail = 0
    with out_path.open("a", encoding="utf-8") as f:
        for i, t in enumerate(topics):
            if t["content_id"] in done:
                continue
            rec = {**t, "map_id": map_id, "map_title": map_title}
            try:
                rec["html"] = dc.fetch_topic_html(client, map_id, t["content_id"])
                ok += 1
            except Exception as exc:  # noqa: BLE001 — 개별 실패가 전체 크롤을 막지 않게
                rec["html"] = None
                rec["error"] = str(exc)[:200]
                fail += 1
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done.add(t["content_id"])
            if on_progress and (i + 1) % 100 == 0:
                f.flush()
                on_progress(map_title, i + 1, len(topics), ok, fail)
            time.sleep(delay)
    return ok, fail


def crawl_khub_dump(dump_dir, locales=DEFAULT_LOCALES, primary_title=PRIMARY_TITLE,
                    delay: float = 0.12, limit: int = 0, on_progress=None) -> dict:
    """khub 전 맵을 순회해 v2 덤프를 만든다. limit>0이면 맵마다 앞 N개만(스모크용).

    반환: {locale: {primary_topics, aux_maps, fetched_ok, failed}}.
    """
    dump = Path(dump_dir)
    dump.mkdir(parents=True, exist_ok=True)
    maps = dc.list_maps()
    stats: dict[str, dict] = {}

    for locale in locales:
        loc_maps = _maps_for_locale(maps, locale)
        primary = next((m for m in loc_maps if m.get("title") == primary_title), None)
        if not primary:
            raise ValueError(f"주 맵을 찾지 못했습니다: {primary_title!r} ({locale})")
        aux = [m for m in loc_maps if m.get("title") != primary_title]

        out_path = dump / f"bodies_{locale}.jsonl"
        done = _load_done(out_path)

        def _save_toc(map_id: str, title: str, toc: list, fname: str) -> None:
            (dump / fname).write_text(
                json.dumps({"map_id": map_id, "title": title, "toc": toc}, ensure_ascii=False),
                encoding="utf-8",
            )

        with dc._client() as client:
            # ── 주 맵 ──
            toc = dc.get_menu(primary["id"])
            _save_toc(primary["id"], primary_title, toc, f"toc_{locale}.json")
            topics = _dedup(_flatten_toc(toc))
            if limit > 0:
                topics = topics[:limit]
            ok, fail = _fetch_bodies(client, primary["id"], primary_title, topics, out_path, done, delay, on_progress)

            # ── 보조 맵 ──
            aux_ok = 0
            for m in aux:
                atoc = dc.get_menu(m["id"])
                # 한글 등 비ASCII 제목은 슬러그가 빈값이 된다 — map_id로 폴백해 파일명 충돌을 막는다
                # (build-llm이 toc_{locale}__*.json을 글롭으로 읽으므로 파일명이 유일해야 함).
                slug = _slugify(m["title"]) or m["id"][:12]
                _save_toc(m["id"], m["title"], atoc, f"toc_{locale}__{slug}.json")
                atopics = _dedup(_flatten_toc(atoc))
                if limit > 0:
                    atopics = atopics[:limit]
                a_ok, a_fail = _fetch_bodies(client, m["id"], m["title"], atopics, out_path, done, delay, on_progress)
                aux_ok += a_ok
                fail += a_fail

        stats[locale] = {"primary_topics": len(topics), "aux_maps": len(aux),
                         "fetched_ok": ok + aux_ok, "failed": fail}
    return stats
