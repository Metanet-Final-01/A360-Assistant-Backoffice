# -*- coding: utf-8 -*-
"""P3 골드셋 빌더 — 패키지별 '정답 액션 로스터'를 khub 원문에서 독립 확정한다.

**왜 독립인가:** 빌드 파이프라인은 규칙 + table_llm + judge로 로스터를 만든다. 골드셋을
같은 방법으로 만들면 회귀를 못 잡는다(정의상 서로 일치). 그래서 여기서는 **다른 방법**을
쓴다 — LLM이 개요 페이지 원문을 통째로 읽고 로스터를 뽑고(1회 추출), 개요+리프 제목으로
누락/오탐을 교정한다(1회 검수). 방법이 다르므로 두 결과의 차이가 곧 신호다:
빌드 버그이거나(우리가 깼거나 형식이 바뀜) 골드 오류(사람이 검수).

**결정론 원칙 유지:** 이름은 원문 표기 그대로(축자). 추출/검수 모두 새 이름을 짓지 않는다.

산출(모두 DATA_DIR):
  golden_sources.json  패키지별 원문 — 개요 텍스트 + 리프 문서(제목·본문). 감사·재현용.
  golden.json          {package: {kind, actions:[...], added, removed, notes}} — 검수 후 확정.
  golden_cache.json    (source_hash|model|prompt|pass) 캐시 — 재실행 시 LLM 생략.

사용: INGEST_DATA_DIR=<빌드 산출 디렉터리> \
      .venv/Scripts/python.exe scripts/build_golden.py --dump-dir <khub-dump> [--limit N]
"""
import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 로컬 강제 — usage 기록이 네온으로 새지 않게(run_local과 동일 계약). .env는 안 건드린다.
os.environ["RAG_DATABASE_URL"] = ""
os.environ["OBSERVABILITY_DATABASE_URL"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from pydantic import BaseModel, Field  # noqa: E402

from app.rag import config  # noqa: E402
from app.rag.agents.structured import chat_json  # noqa: E402
from app.rag.build.common import _load_bodies, _plain_text, _soup  # noqa: E402
from app.rag.build.registry import subtree_nodes, walk_toc  # noqa: E402

_OVERVIEW_LIMIT = 15000  # 개요 원문 상한 — 액션 표는 보통 앞부분. 최대 27K(Recorder)라 넉넉히.
_LEAF_TEXT_LIMIT = 4000  # golden_sources 저장용 리프 본문 상한(감사용, LLM엔 안 넣음)


# ── Pass 1: 추출 ────────────────────────────────────────────────────────────
_EXTRACT_SYS = (
    "너는 Automation Anywhere A360 공식 문서의 **패키지 개요 페이지 원문**을 읽고, 그 패키지가 "
    "제공하는 **공식 액션(또는 트리거) 목록**을 그대로 뽑아내는 추출기다. 반드시 지정된 JSON만 답한다.\n\n"
    "규칙:\n"
    "- 액션은 봇 편집기에서 끌어다 쓰는 실행 단위다. 표(헤더가 Action/Actions/Operation 등)나 "
    "목록으로 실린다. 표가 여러 개면 모두 본다.\n"
    "- 이름은 **페이지에 적힌 그대로**(축자). 새로 짓거나 다듬지 마라.\n"
    "- 한 셀/줄에 여러 액션이 나열되면 각각 분리한다.\n"
    "- **아닌 것**: 카테고리/분류(예: 'Cell operations'), 설정·권한·예제·버전·지원 매트릭스·"
    "용어 설명, 패키지 이름 자체. 이런 건 넣지 마라.\n"
    "- 개요에 액션 목록이 없으면 actions=[] 로 답한다(리프 문서가 따로 있을 수 있다 — 그건 검수에서 본다).\n"
)


class _Extract(BaseModel):
    actions: list[str] = Field(default_factory=list, description="개요에 적힌 그대로의 액션 이름들")


# ── Pass 2: 검수 ────────────────────────────────────────────────────────────
_REVIEW_SYS = (
    "너는 위에서 추출한 액션 목록을 **원문과 대조해 검수**하는 심사자다. 반드시 지정된 JSON만 답한다.\n\n"
    "입력: (a) 개요 페이지 원문, (b) 이 패키지 아래 문서 페이지 **제목 목록**(리프), (c) 1차 추출 목록.\n"
    "할 일:\n"
    "- **누락 추가(added)**: 리프 제목 중 실제 액션인데 1차 목록에 없는 것. 리프 제목이 액션 문서면 "
    "그 액션은 실존한다(예: 개요 표엔 없지만 전용 문서가 있는 액션).\n"
    "- **오탐 제거(removed)**: 1차 목록 중 액션이 아닌 것(카테고리·설정·예제·패키지명).\n"
    "- 리프 제목이 예제('Example of…')·설정('Configuring…')·지원표·개념 설명이면 액션이 아니다.\n"
    "- 이름은 원문 표기 그대로. 확신이 없으면 넣지 말고 notes에 남겨라.\n"
    "- confirmed_actions = 최종 확정 목록(1차 - removed + added).\n"
)


class _Review(BaseModel):
    confirmed_actions: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    # 모델이 notes를 문자열/리스트 둘 다로 낸다 — 둘 다 받아 재시도 낭비를 없앤다(_review_one에서 정규화).
    notes: str | list[str] = ""


_EXTRACT_PH = hashlib.sha256(_EXTRACT_SYS.encode()).hexdigest()[:12]
_REVIEW_PH = hashlib.sha256(_REVIEW_SYS.encode()).hexdigest()[:12]


def _src_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def assemble_sources(registry: dict, dump: Path) -> dict:
    """패키지별 원문 조립 — 개요 텍스트 + 리프 문서(제목·본문)."""
    toc_en = json.loads((dump / "toc_en-US.json").read_text(encoding="utf-8"))["toc"]
    node_by_cid = {e["content_id"]: e for e in walk_toc(toc_en) if e["content_id"]}
    bodies = _load_bodies(dump, "en-US")

    out: dict[str, dict] = {}
    for pkg in registry["packages"]:
        sr = pkg.get("subtree_root")
        if not sr:
            continue
        display = pkg["display_en"]
        root = node_by_cid.get(sr["content_id"])
        ov_body = bodies.get(sr["content_id"])
        overview_text = (
            _plain_text(_soup(ov_body["html"]), _OVERVIEW_LIMIT)
            if ov_body and ov_body.get("html") else ""
        )
        leaf_docs = []
        if root:
            for n in subtree_nodes(root, sr["path"]):
                cid = n["content_id"]
                if not cid or cid == sr["content_id"] or not n["is_leaf"]:
                    continue
                b = bodies.get(cid)
                leaf_docs.append({
                    "title": n["title"],
                    "text": _plain_text(_soup(b["html"]), _LEAF_TEXT_LIMIT) if b and b.get("html") else "",
                })
        out[display] = {"kind": pkg.get("kind", "action"), "overview_text": overview_text,
                        "leaf_docs": leaf_docs}
    return out


def _extract_one(display: str, src: dict, model: str) -> list[str]:
    msgs = [
        {"role": "system", "content": _EXTRACT_SYS},
        {"role": "user", "content": f"패키지: {display}\n\n개요 페이지 원문:\n{src['overview_text']}"},
    ]
    res = chat_json(msgs, purpose="golden_extract", model_cls=_Extract, model=model)
    return [a.strip() for a in res.actions if a and a.strip()]


def _review_one(display: str, src: dict, proposed: list[str], model: str) -> dict:
    leaf_titles = [d["title"] for d in src["leaf_docs"]]
    msgs = [
        {"role": "system", "content": _REVIEW_SYS},
        {"role": "user", "content": (
            f"패키지: {display}\n\n개요 페이지 원문:\n{src['overview_text']}\n\n"
            f"이 패키지 아래 문서 제목({len(leaf_titles)}개):\n" + "\n".join(f"- {t}" for t in leaf_titles) + "\n\n"
            f"1차 추출 목록({len(proposed)}개):\n" + "\n".join(f"- {a}" for a in proposed)
        )},
    ]
    res = chat_json(msgs, purpose="golden_review", model_cls=_Review, model=model)
    notes = res.notes if isinstance(res.notes, str) else " / ".join(str(x) for x in res.notes)
    return {"confirmed_actions": [a.strip() for a in res.confirmed_actions if a and a.strip()],
            "added": res.added, "removed": res.removed, "notes": notes}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--limit", type=int, default=0, help="패키지 수 상한(스모크용, 0=전체)")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    model = args.model or config.AGENT_PARSE_MODEL
    dump = Path(args.dump_dir)
    registry = json.loads((config.DATA_DIR / "package_registry.json").read_text(encoding="utf-8"))

    print(f"[golden] 원문 조립 중… (패키지 {len(registry['packages'])})", flush=True)
    sources = assemble_sources(registry, dump)
    _write_json(config.DATA_DIR / "golden_sources.json", sources)
    print(f"[golden] 원문 저장 → golden_sources.json ({len(sources)}패키지)", flush=True)

    items = [(d, s) for d, s in sources.items() if s["overview_text"] or s["leaf_docs"]]
    if args.limit > 0:
        items = items[:args.limit]

    cache_path = config.DATA_DIR / "golden_cache.json"
    cache = _load_cache(cache_path)
    workers = max(1, config.AGENT_PARSE_WORKERS)

    from app.core.llm import usage_context

    # ── Pass 1: 추출 ──
    extracted: dict[str, list[str]] = {}
    todo1 = []
    for d, s in items:
        key = f"x|{_src_hash(s['overview_text'])}|{model}|{_EXTRACT_PH}"
        if d in cache and cache[d].get("extract_key") == key:
            extracted[d] = cache[d]["extract"]
        else:
            todo1.append((d, s, key))
    print(f"[golden] Pass1 추출 — 캐시 {len(extracted)} / LLM {len(todo1)}", flush=True)
    with usage_context(component="rag_golden", actor_type="system"):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_extract_one, d, s, model): (d, s, key) for d, s, key in todo1}
            done = 0
            for fut in as_completed(futs):
                d, s, key = futs[fut]
                try:
                    acts = fut.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"  [추출 실패] {d}: {exc}", flush=True)
                    continue
                extracted[d] = acts
                cache.setdefault(d, {}).update({"extract": acts, "extract_key": key})
                done += 1
                if done % 20 == 0:
                    print(f"  추출 {done}/{len(todo1)}", flush=True)
                    _write_json(cache_path, cache)
    _write_json(cache_path, cache)

    # ── Pass 2: 검수 ──
    golden: dict[str, dict] = {}
    todo2 = []
    for d, s in items:
        proposed = extracted.get(d, [])
        leaf_sig = _src_hash("|".join(sorted(x["title"] for x in s["leaf_docs"])))
        key = f"r|{_src_hash(s['overview_text'])}|{leaf_sig}|{_src_hash('|'.join(proposed))}|{model}|{_REVIEW_PH}"
        if d in cache and cache[d].get("review_key") == key:
            golden[d] = {"kind": s["kind"], **cache[d]["review"]}
        else:
            todo2.append((d, s, proposed, key))
    print(f"[golden] Pass2 검수 — 캐시 {len(golden)} / LLM {len(todo2)}", flush=True)
    with usage_context(component="rag_golden", actor_type="system"):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_review_one, d, s, p, model): (d, s, key) for d, s, p, key in todo2}
            done = 0
            for fut in as_completed(futs):
                d, s, key = futs[fut]
                try:
                    rev = fut.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"  [검수 실패] {d}: {exc}", flush=True)
                    golden[d] = {"kind": s["kind"], "confirmed_actions": extracted.get(d, []),
                                 "added": [], "removed": [], "notes": f"review_failed: {exc}"}
                    continue
                golden[d] = {"kind": s["kind"], **rev}
                cache.setdefault(d, {}).update({"review": rev, "review_key": key})
                done += 1
                if done % 20 == 0:
                    print(f"  검수 {done}/{len(todo2)}", flush=True)
                    _write_json(cache_path, cache)
    _write_json(cache_path, cache)

    _write_json(config.DATA_DIR / "golden.json", golden)
    total_actions = sum(len(g["confirmed_actions"]) for g in golden.values())
    print(f"\n[golden] 완료 → golden.json ({len(golden)}패키지 / 액션 {total_actions})", flush=True)
    print(f"  검수가 추가한 액션 합계: {sum(len(g.get('added', [])) for g in golden.values())}")
    print(f"  검수가 제거한 항목 합계: {sum(len(g.get('removed', [])) for g in golden.values())}")


if __name__ == "__main__":
    main()
