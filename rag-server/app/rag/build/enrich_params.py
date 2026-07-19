"""파라미터 LLM 보강(2단) — uicontrol 후보만 있는 행의 파라미터 스키마를 완성한다.

전략 근거: 회의록/2026-07-18-khub-실측-저장전략.md §2.3. 규칙(1단, merge_v2)이 dl 정형만
완성하고 나머지는 후보 필드명 목록으로 남기는데, 이 단계가 그 잔여를 LLM으로 보강한다.

입력 컷 우선순위 — "Settings" 헤딩은 실측상 액션 문서의 ~64%에만 존재하므로(2026-07-18
khub 전수 F6) 고정 규칙이 아니라 체인으로 자른다. 어떤 컷을 썼는지 metadata.enrich_input에 남긴다:
  dl 보유(이미 완성) → 대상 아님
  ① Settings/Overview 섹션  ② ol.steps 절차 블록  ③ 본문 전체(소형 페이지 폴백)

원칙:
- 이름 작명 금지 — name은 후보/본문 표기 그대로. 새 이름을 만들지 않는다.
- DB 접근 없음 — 파일(빌드 산출물)에만 작용. LLM은 app.core.llm(chat_json) 경유,
  usage_context(component="rag_enrich").
- 캐시(data/ingest/enrich_cache.json): (doc id, 입력 해시) 일치 시 LLM 생략 — 재실행 비용 절감.
- 자기 채점(--score-dl): dl 규칙 결과가 있는 행을 같은 경로로 돌려 이름 집합 정합을 측정
  (LLM 추출 품질의 근거 수치).
"""

import contextvars
import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from .. import config
from ..agents.schema import ParsedParameter
from ..agents.structured import chat_json
from ..observability import get_request_id, log_event, new_request_id
from .registry import normalize_pretty_url

logger = logging.getLogger(__name__)

PURPOSE = "enrich_params"
_CUT_LIMIT = 6000

_SYSTEM = (
    "당신은 Automation Anywhere(A360) 액션 문서 여러 개에서 '입력 파라미터 스키마'를 추출하는 파서입니다.\n"
    "각 문서에는 기계 추출된 후보 필드명 목록과 본문 발췌가 주어집니다.\n"
    "규칙:\n"
    "- 본문에 근거가 있는 '사용자 입력 파라미터'만 추출한다. 버튼(Save/Apply/Capture 실행 버튼 등)·메뉴·"
    "액션 이름 자체·출력/반환값은 파라미터가 아니다.\n"
    "- name은 문서 표기를 한 글자도 바꾸지 말고 그대로 쓴다(후보 목록 표기 우선). 새 이름을 지어내지 않는다.\n"
    "- 후보 목록은 힌트일 뿐이며 비어 있거나 불완전할 수 있다 — 본문에 명시된 입력 필드는 "
    "후보에 없어도 반드시 추출한다.\n"
    "- type은 TEXT|NUMBER|BOOLEAN|SELECT|FILE|CREDENTIAL|SESSION|LIST|DICTIONARY|VARIABLE|UNKNOWN 중 "
    "본문 근거로 고른다. 근거가 없으면 UNKNOWN.\n"
    "- required는 본문에 명시가 있을 때만 true/false(예: 'Optional:'이면 false), 없으면 null로 둔다.\n"
    "- 선택지가 열거된 필드는 options에 담는다.\n"
    '반드시 JSON 객체 하나만 출력: {"results": [{"index": int, "parameters": [...]}]}. '
    "results 길이는 입력 문서 수와 같아야 하며, 파라미터가 없으면 빈 배열."
)


class _EnrichParam(ParsedParameter):
    """보강 전용 파라미터 — required를 tri-state(true/false/모름=null)로 허용한다.

    JAR 계약(ParsedParameter)은 required가 bool 고정이지만, 문서 산문에는 필수 여부가
    명시되지 않은 필드가 많다. dl 규칙 추출(merge_v2._dl_params)도 같은 이유로 None을
    쓰므로, LLM 경로만 false로 뭉개면 R3(필수값 누락) 판정에서 '모름'과 '선택'이 섞인다.
    """

    required: bool | None = None


class _Item(BaseModel):
    index: int
    parameters: list[_EnrichParam] = Field(default_factory=list)


class _Batch(BaseModel):
    results: list[_Item] = Field(default_factory=list)


def extract_cut(html: str | None) -> tuple[str, str]:
    """파라미터 서술이 있는 최소 블록을 자른다. (컷 종류, 텍스트)

    우선순위에 이유가 있다 — Overview+Settings를 둘 다 가진 문서(Asana·Snowflake류)에서
    문서 순서대로 자르면 Overview(파라미터 없음)가 잡혀 LLM이 전건 빈 결과를 낸다(2026-07-18
    score-dl 실측). Settings가 없으면 dl이 들어있는 섹션이 파라미터의 실제 위치다.
    """
    soup = BeautifulSoup(html or "", "html.parser")

    def _sec_text(el) -> str:
        sec = el.find_parent("section") or el.parent
        return sec.get_text("\n", strip=True) if sec else ""

    settings, overview = [], []
    for h in soup.select("h2, h3"):
        title = h.get_text(" ", strip=True).lower()
        if title.startswith("settings"):
            settings.append(_sec_text(h))
        elif title.startswith("overview"):
            overview.append(_sec_text(h))
    if settings:
        text = "\n\n".join(settings)
        if len(text) > 80:
            return "settings", text[:_CUT_LIMIT]
    dl = soup.find("dl")
    if dl is not None:
        text = _sec_text(dl)
        if len(text) > 80:
            return "dl_section", text[:_CUT_LIMIT]
    steps = soup.select("ol.steps, ul.steps")
    if steps:
        return "steps", "\n".join(s.get_text("\n", strip=True) for s in steps)[:_CUT_LIMIT]
    if overview:
        text = "\n\n".join(overview)
        if len(text) > 80:
            return "overview", text[:_CUT_LIMIT]
    return "full_body", soup.get_text("\n", strip=True)[:_CUT_LIMIT]


def _input_hash(cut: str, candidates: list[str]) -> str:
    return hashlib.sha1((cut + "|" + ",".join(candidates)).encode("utf-8")).hexdigest()[:16]


def _batch_prompt(items: list[dict]) -> str:
    blocks = []
    for i, it in enumerate(items):
        blocks.append(
            f"===== 문서 index={i} =====\n"
            f"패키지: {it['package']}\n액션: {it['action']}\n"
            f"후보 필드명: {', '.join(it['candidates']) or '(없음)'}\n"
            f"본문 발췌({it['cut_kind']}):\n{it['cut']}"
        )
    return f"아래 {len(items)}개 문서를 각각 추출해 results 배열로 반환하라.\n\n" + "\n\n".join(blocks)


def _rewrite_content(doc: dict, params: list[dict]) -> None:
    head = doc["content"].split("\n파라미터:")[0]
    if params:
        lines = "\n".join(
            f"- {p['name']}"
            + (f" ({p.get('type')})" if p.get("type") else "")
            + (f" [필수]" if p.get("required") else "")
            + (f": {p.get('description', '')[:200]}" if p.get("description") else "")
            for p in params
        )
    else:
        lines = "미상(문서에 필드명이 명시되지 않음)"
    doc["content"] = head + "\n파라미터:\n" + lines


def _apply(doc: dict, params: list[dict], cut_kind: str) -> None:
    # 빈 결과는 '파라미터 없음 확정'([])이 아니라 '미상'(None)으로 기록한다 — 50건 실측
    # (2026-07-19): 문서가 필드를 서술하되 필드명은 UI 스크린샷에만 있어(Google Drive
    # Move file 등) 이름 불명으로 빈 결과가 나오는 경우가 다수였고, 한 줄짜리 문서(REST
    # Delete method)는 '없음'을 주장할 근거 자체가 없다. []로 적재하면 검수 R2가 정당한
    # 파라미터를 전건 위반 처리한다(위험 비대칭: 잘못된 []=오탐, 잘못된 미상=침묵).
    # 캐시의 기존 [] 항목도 이 함수를 다시 지나므로 재호출 없이 소급 정규화된다.
    # label은 빌드 때 metadata에 계산돼 있다(merge_v2.action_label_ko) — 백엔드 edit의
    # 한국어 이름 지목 리졸버(label_candidates)가 스펙 label을 읽으므로 schema에 동봉한다.
    doc["metadata"]["schema"] = {
        "name": doc.get("action_name"),
        "label": doc["metadata"].get("action_label_ko"),
        "parameters": params or None,
    }
    doc["metadata"]["params_source"] = "prose_llm"
    doc["metadata"]["enrich_input"] = cut_kind
    _rewrite_content(doc, params)


def _run_batches(work: list[dict], model: str | None) -> dict[int, list[dict]]:
    """work(전역 인덱스 부여된 항목들)를 배치 LLM 호출로 처리해 {전역 idx: params} 반환."""
    from app.core.llm import usage_context

    batch_size = max(1, config.AGENT_PARSE_BATCH_SIZE)
    out: dict[int, list[dict]] = {}

    def one_batch(chunk: list[tuple[int, dict]]) -> dict[int, list[dict]]:
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _batch_prompt([c for _, c in chunk])},
        ]
        res: dict[int, list[dict]] = {}
        try:
            batch = chat_json(messages, purpose=PURPOSE, model_cls=_Batch, model=model)
        except ValueError as exc:
            logger.warning("보강 배치 실패 (건너뜀): %d개 — %s", len(chunk), exc)
            return res
        for item in batch.results:
            if 0 <= item.index < len(chunk):
                gidx = chunk[item.index][0]
                res[gidx] = [p.to_dict() for p in item.parameters]
        return res

    chunks = []
    indexed = list(enumerate(work))
    for start in range(0, len(indexed), batch_size):
        chunks.append(indexed[start : start + batch_size])

    with usage_context(component="rag_enrich"):
        with ThreadPoolExecutor(max_workers=max(1, config.AGENT_PARSE_WORKERS)) as pool:
            futures = [pool.submit(contextvars.copy_context().run, one_batch, c) for c in chunks]
            done = 0
            for f in as_completed(futures):
                out.update(f.result())
                done += 1
                if done % 20 == 0:
                    print(f"  보강 배치 {done}/{len(chunks)}")
    return out


def enrich_documents(rag_docs: list[dict], dump_dir: str | Path, *, model: str | None = None,
                     limit: int = 0, score_dl: bool = False) -> dict:
    from .merge_v2 import _load_bodies

    model = model or config.AGENT_PARSE_MODEL
    bodies_en = _load_bodies(Path(dump_dir), "en-US")
    by_url = {normalize_pretty_url(d.get("pretty_url", "")): d for d in bodies_en.values() if d.get("pretty_url")}

    cache_path = config.DATA_DIR / "enrich_cache.json"
    cache: dict = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    targets = [
        d for d in rag_docs
        if d["source_type"] in ("action_schema", "trigger_schema")
        and d.get("metadata", {}).get("params_source") == "uicontrol_candidates"
    ]
    if limit > 0:
        targets = targets[:limit]

    stats = {"targets": len(targets), "cached": 0, "enriched": 0, "no_body": 0, "failed": 0}
    work: list[dict] = []
    work_docs: list[dict] = []
    for d in targets:
        body = by_url.get(normalize_pretty_url(d.get("url", "")))
        if not body or not body.get("html"):
            stats["no_body"] += 1
            continue
        kind, cut = extract_cut(body["html"])
        candidates = d["metadata"].get("param_candidates", [])
        h = _input_hash(cut, candidates)
        cached = cache.get(d["id"])
        if cached and cached.get("input_hash") == h:
            _apply(d, cached["parameters"], cached.get("enrich_input", kind))
            stats["cached"] += 1
            continue
        work.append({"package": d["package_name"], "action": d.get("action_name") or "",
                     "candidates": candidates, "cut_kind": kind, "cut": cut, "hash": h})
        work_docs.append(d)

    if get_request_id() is None:
        new_request_id()
    log_event("enrich_params_start", targets=len(targets), llm_items=len(work), model=model)
    print(f"[enrich] 대상 {len(targets)} (캐시 {stats['cached']}, 본문없음 {stats['no_body']}, LLM {len(work)})")

    results = _run_batches(work, model) if work else {}
    for gidx, doc in enumerate(work_docs):
        params = results.get(gidx)
        if params is None:
            stats["failed"] += 1
            continue
        _apply(doc, params, work[gidx]["cut_kind"])
        cache[doc["id"]] = {"input_hash": work[gidx]["hash"], "parameters": params,
                           "enrich_input": work[gidx]["cut_kind"]}
        stats["enriched"] += 1

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    log_event("enrich_params_done", **{k: v for k, v in stats.items()})

    if score_dl:
        stats["dl_score"] = _score_against_dl(rag_docs, by_url, model)
    return stats


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").casefold())


def _score_against_dl(rag_docs: list[dict], by_url: dict, model: str | None) -> dict:
    """dl 규칙 결과를 골드로 LLM 추출 이름 정합을 측정 (행 수정 없음).

    실제 보강 경로와 동일 조건이 되도록 uicontrol 후보를 계산해서 넣는다 — 후보 없이 돌리면
    모델이 보수적으로 전건 빈 결과를 내는 문서가 생겨(2026-07-18 실측, Asana 계열) 측정이 왜곡된다.
    """
    from .merge_v2 import _soup, _uicontrol_candidates

    dl_docs = [d for d in rag_docs if d.get("metadata", {}).get("params_source") == "dl"]
    work, gold = [], []
    for d in dl_docs:
        body = by_url.get(normalize_pretty_url(d.get("url", "")))
        if not body or not body.get("html"):
            continue
        kind, cut = extract_cut(body["html"])
        candidates = _uicontrol_candidates(
            _soup(body["html"]), {(d.get("action_name") or "").casefold(), d["package_name"].casefold()}
        )
        soup_names = [p["name"] for p in d["metadata"]["schema"]["parameters"]]
        work.append({"package": d["package_name"], "action": d.get("action_name") or "",
                     "candidates": candidates, "cut_kind": kind, "cut": cut, "hash": ""})
        gold.append(soup_names)
    results = _run_batches(work, model) if work else {}

    def _match(a: set[str], b: set[str], lenient: bool) -> tuple[int, int, int]:
        if not lenient:
            return len(a & b), len(b - a), len(a - b)
        # 관대 매칭: 한쪽이 다른쪽을 포함하면 같은 필드로 본다 ("session" vs "sessionname" 표면형 차이)
        used_b: set[str] = set()
        tp = 0
        for x in a:
            hit = next((y for y in b - used_b if x in y or y in x), None)
            if hit:
                used_b.add(hit)
                tp += 1
        return tp, len(b - used_b), len(a) - tp
    strict = [0, 0, 0]
    loose = [0, 0, 0]
    scored = 0
    examples = []
    for i, names in enumerate(gold):
        params = results.get(i)
        if params is None:
            continue
        scored += 1
        g = {_norm_name(n) for n in names}
        l = {_norm_name(p["name"]) for p in params}
        for acc, lenient in ((strict, False), (loose, True)):
            tp, fp, fn = _match(g, l, lenient)
            acc[0] += tp
            acc[1] += fp
            acc[2] += fn
        if (l - g or g - l) and len(examples) < 5:
            examples.append({"action": work[i]["action"], "llm_only": sorted(l - g), "rule_only": sorted(g - l)})

    def _pr(acc):
        tp, fp, fn = acc
        return (tp / (tp + fp) if tp + fp else 0.0), (tp / (tp + fn) if tp + fn else 0.0)

    sp, sr = _pr(strict)
    lp, lr = _pr(loose)
    print(f"[enrich/score-dl] {scored}건 채점 — 엄격 P={sp:.2f} R={sr:.2f} / 관대(포함매칭) P={lp:.2f} R={lr:.2f}")
    for ex in examples:
        print(f"    예: {ex['action']} — LLM만: {ex['llm_only']} / 규칙만: {ex['rule_only']}")
    return {"scored": scored, "precision": round(sp, 3), "recall": round(sr, 3),
            "precision_lenient": round(lp, 3), "recall_lenient": round(lr, 3)}
