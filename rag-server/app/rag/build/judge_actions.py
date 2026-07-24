"""리프 문서가 **진짜 액션인지** LLM으로 판별한다 — 규칙으로는 안 풀리는 부분만.

왜 필요한가(실측 2026-07-21):
- 비-액션 문서(가이드·예제·설정 절차)가 액션으로 적재된다. 현재 방어는 제목 문자열 마커
  33종인데, 그 표본틀이 "enrich 결과 params=[]인 50건"이었다. 절차형 비-액션 문서는 화면
  필드명이 뽑혀 params>0이 되므로 **이 표본에 원리적으로 들어오지 않는다.** 실제로 신규
  마커 23종을 전 TOC에 재적용하면 params>0 리프는 0건이 걸린다 — 마커를 늘려도 안 잡힌다.
- HTML 구조 신호(DITA taskbody/steps)도 부적합하다. 실제 액션 115건에 붙고, 오염 8건 중
  4건에는 없다 — 양방향으로 틀린다.
- 남는 해는 LLM 판별뿐이고, 이는 저장전략 F5("제목만으로 액션 여부 판별 불가")가 지목한
  설계 원안이다. 판별기 자체는 agents/package_parser.py에 v1부터 있었고 연결만 안 돼 있었다.

v1과 다른 점 셋 — v1이 버린 것을 도로 들여오지 않기 위해:
1. **액션 이름을 뽑지 않는다.** 이름은 문서 제목(merge_v2.action_identity)이 정본이다.
   v1은 LLM에 camelCase 작명을 시켜 코퍼스가 getWorksheetNames 같은 화면에 없는 이름으로
   오염됐다(실측: v1 액션 1,157종 중 75%).
2. **입력이 structured_html(12,000자 상한)이 아니라 extract_cut**(중앙값 953자)이다.
   v1 parse_actions가 $4.63을 쓴 이유의 상당 부분이 그 상한이다.
3. **대상이 공식 액션 테이블에 없는 리프(leaf_unconfirmed)뿐**이다. 표에 등재된 액션은
   존재가 이미 확정이라 판별이 불필요하다.

부수 소득: v2는 파라미터 description이 7%뿐이고(v1은 99%) return_type은 0건인데, 판별과
같은 호출에서 함께 받아 채운다 — 문서를 두 번 태우지 않는다.
"""

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, Field

from .. import config
from ..agents.schema import ParsedParameter
from ..agents.structured import chat_json
from ..agents.package_parser import _JUDGE_RULES  # 판정 규칙은 한 벌만 유지한다
from ..observability import log_event
from .enrich_params import extract_cut

logger = logging.getLogger(__name__)

PURPOSE = "judge_actions"

_SYSTEM = (
    "너는 Automation Anywhere A360 공식 문서를 읽고, 각 문서가 봇 편집기에서 끌어다 쓰는 "
    "**액션**을 설명하는 문서인지 판정하는 분석기다. 반드시 지정된 JSON 스키마로만 답한다.\n\n"
    + _JUDGE_RULES
    + "\n중요: **액션 이름은 뽑지 마라.** 이름은 문서 제목이 정본이며 이 단계의 산출물이 아니다.\n"
    "파라미터는 문서에 실제로 적힌 입력 필드/옵션만 싣는다. 근거가 없으면 빈 배열로 두고 지어내지 않는다.\n"
)


def _compute_prompt_hash() -> str:
    """프롬프트가 바뀌면 캐시를 무효화한다 — 안 그러면 판정 기준을 고쳐도 옛 결과가 되살아난다."""
    return hashlib.sha256(_SYSTEM.encode("utf-8")).hexdigest()[:12]


def _input_hash(cut: str, model: str) -> str:
    """캐시 키에 model과 프롬프트 해시를 함께 녹인다(enrich_params M8과 같은 이유)."""
    key = f"{cut}|{model}|{_PROMPT_HASH}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # 원자적 교체 — 쓰다 죽어도 기존 캐시가 안 깨진다


class _JudgedItem(BaseModel):
    index: int = Field(description="입력으로 준 문서 index를 그대로")
    is_action: bool = Field(description="봇 편집기에서 쓰는 실행 단위면 true, 개념·예제·설정 가이드면 false")
    description: str | None = None
    return_type: str | None = None
    return_label: str | None = None
    parameters: list[ParsedParameter] = []


class _JudgedBatch(BaseModel):
    results: list[_JudgedItem]


_PROMPT_HASH = _compute_prompt_hash()


def _batch_prompt(chunk: list[dict]) -> str:
    blocks = []
    for i, it in enumerate(chunk):
        blocks.append(
            f"===== 문서 index={i} =====\n"
            f"패키지: {it['package']}\n문서 제목: {it['title']}\n"
            f"문서 본문 발췌({it['cut_kind']}):\n{it['cut']}"
        )
    return (
        f"아래 {len(chunk)}개 문서를 각각 판정하고, 액션이면 파라미터도 함께 추출해 "
        f"results 배열로 반환하라(각 결과에 입력 index를 그대로 실을 것).\n\n"
        + "\n\n".join(blocks)
    )


def _run_batches(work: list[dict], model: str | None, stats: dict) -> dict[int, _JudgedItem]:
    """work를 배치 LLM 호출로 처리해 {전역 idx: 판정} 반환. 실패한 배치는 통째로 건너뛴다."""
    from app.core.llm import usage_context

    batch_size = max(1, config.AGENT_PARSE_BATCH_SIZE)
    out: dict[int, _JudgedItem] = {}
    # worker가 공유 stats를 갱신하므로 락으로 직렬화한다(table_llm과 동일, Qodo 리뷰).
    _stats_lock = threading.Lock()

    def one_batch(chunk: list[tuple[int, dict]]) -> dict[int, _JudgedItem]:
        items = [it for _, it in chunk]
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _batch_prompt(items)},
        ]
        got: dict[int, _JudgedItem] = {}
        try:
            batch = chat_json(messages, purpose=PURPOSE, model_cls=_JudgedBatch, model=model)
        except Exception as exc:  # noqa: BLE001 — 한 배치 실패가 전체 빌드를 막지 않는다
            logger.warning("판별 배치 실패 (건너뜀): %d개 — %s", len(chunk), exc)
            with _stats_lock:
                stats["failed"] = stats.get("failed", 0) + len(chunk)
            return got
        seen = set()
        for item in batch.results:
            if not (0 <= item.index < len(chunk)) or item.index in seen:
                continue
            seen.add(item.index)
            got[chunk[item.index][0]] = item
        missing = len(chunk) - len(seen)
        if missing:
            # 응답이 짧으면 반환분만 채택한다 — 배치 전체를 버리면 멀쩡한 판정까지 잃는다.
            with _stats_lock:
                stats["missing_in_response"] = stats.get("missing_in_response", 0) + missing
        return got

    chunks = [
        list(enumerate(work))[i:i + batch_size]
        for i in range(0, len(work), batch_size)
    ]
    workers = max(1, config.AGENT_PARSE_WORKERS)
    with usage_context(component="rag_judge", actor_type="system"):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(one_batch, c): i for i, c in enumerate(chunks)}
            done = 0
            for fut in as_completed(futures):
                out.update(fut.result())
                done += 1
                if done % 20 == 0:
                    print(f"  판별 배치 {done}/{len(chunks)}", flush=True)
    return out


def judge_documents(rag_docs: list[dict], dump_dir: str | Path, *, model: str | None = None,
                    limit: int = 0) -> tuple[dict, set[str]]:
    """leaf_unconfirmed 액션 문서를 판별한다. (stats, 버릴 doc id 집합) 반환.

    호출자(merge_v2)가 반환된 id를 rag_docs에서 제거한다 — 여기서 리스트를 직접 변형하지
    않는 이유는, 판별이 실패했을 때 '아무것도 안 버림'이 기본값이 되게 하기 위해서다.
    """
    from .merge_v2 import _load_bodies, _soup  # 순환 임포트 회피 — 호출 시점에 가져온다
    from .registry import normalize_pretty_url

    dump = Path(dump_dir)
    bodies_en = _load_bodies(dump, "en-US")
    by_url = {normalize_pretty_url(d.get("pretty_url", "")): d
              for d in bodies_en.values() if d.get("pretty_url")}

    model = model or config.AGENT_PARSE_MODEL
    targets = [
        d for d in rag_docs
        if d["source_type"] in ("action_schema", "trigger_schema")
        and d.get("metadata", {}).get("identity_confidence") == "leaf_unconfirmed"
        and d.get("chunk_index", 0) == 0
        # 루트 승격 행은 제외한다. 그건 리프 문서가 아니라 **패키지 개요 페이지**이고,
        # 존재 근거는 "이 패키지에 액션이 있다"는 것이지 그 페이지 자체가 액션 문서라는 게
        # 아니다. 판별기에 넣으면 당연히 is_action=false가 나오고(실측: Goto/SOAP Web Service),
        # 액션 0건 패키지를 막으려고 세운 행이 도로 사라져 M5가 재발한다.
        and not d.get("metadata", {}).get("promoted_from_root")
    ]
    if limit:
        targets = targets[:limit]

    cache_path = config.DATA_DIR / "judge_cache.json"
    cache: dict = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    work, work_docs = [], []
    no_body = cached_n = 0
    resolved: dict[str, _JudgedItem] = {}
    for d in targets:
        body = by_url.get(normalize_pretty_url(d.get("url", "")))
        if not body or not body.get("html"):
            no_body += 1
            continue
        kind, cut = extract_cut(body["html"])
        if not cut:
            no_body += 1
            continue
        h = _input_hash(cut, model)
        hit = cache.get(d["id"])
        if hit and hit.get("input_hash") == h:
            resolved[d["id"]] = _JudgedItem.model_validate(hit["judged"])
            cached_n += 1
            continue
        work.append({"package": d["package_name"], "title": d.get("action_name") or d.get("title"),
                     "cut_kind": kind, "cut": cut, "_id": d["id"], "_hash": h})
        work_docs.append(d)

    stats = {"targets": len(targets), "cached": cached_n, "judged": 0, "dropped_not_action": 0,
             "params_filled": 0, "no_body": no_body, "failed": 0, "missing_in_response": 0}
    log_event("judge_actions_start", targets=len(targets), cached=cached_n,
              llm_items=len(work), model=model)
    print(f"[judge] 대상 {len(targets)} (캐시 {cached_n}, 본문없음 {no_body}, LLM {len(work)})", flush=True)

    if work:
        results = _run_batches(work, model, stats)
        for gidx, item in results.items():
            doc = work_docs[gidx]
            resolved[doc["id"]] = item
            cache[doc["id"]] = {"input_hash": work[gidx]["_hash"], "model": model,
                                "prompt_hash": _PROMPT_HASH, "judged": item.model_dump()}
        _write_cache(cache_path, cache)

    by_id = {d["id"]: d for d in targets}
    drop: set[str] = set()
    for doc_id, item in resolved.items():
        doc = by_id.get(doc_id)
        if doc is None:
            continue
        stats["judged"] += 1
        if not item.is_action:
            drop.add(doc_id)
            stats["dropped_not_action"] += 1
            stats.setdefault("dropped_titles", []).append(
                f"{doc['package_name']}/{doc.get('action_name')}"
            )
            continue
        _adopt(doc, item, stats)
    print(f"  [judge] 판정 {stats['judged']} / 비-액션 폐기 {stats['dropped_not_action']} "
          f"/ 파라미터 채움 {stats['params_filled']}", flush=True)
    log_event("judge_actions_done", **{k: v for k, v in stats.items() if not isinstance(v, list)})
    return stats, drop


def _adopt(doc: dict, item: _JudgedItem, stats: dict) -> None:
    """액션으로 판정된 문서에 파라미터·설명·반환값을 반영한다. **이름은 건드리지 않는다.**"""
    meta = doc.setdefault("metadata", {})
    schema = meta.get("schema")
    params = [p.model_dump(exclude_none=False) for p in item.parameters]
    # 규칙(dl)로 이미 뽑힌 파라미터가 있으면 LLM 결과로 덮지 않는다 — dl이 더 확실하다.
    if params and meta.get("params_source") != "dl":
        if schema is None:
            schema = {"name": doc.get("action_name"), "label": meta.get("action_label_ko"),
                      "parameters": params}
            meta["schema"] = schema
        else:
            schema["parameters"] = params
        meta["params_source"] = "judge_llm"
        stats["params_filled"] += 1
    if item.description and schema is not None:
        schema.setdefault("description", item.description)
    if item.return_type and schema is not None:
        schema.setdefault("return_type", item.return_type)
        if item.return_label:
            schema.setdefault("return_label", item.return_label)
    meta["is_action_judged"] = True
