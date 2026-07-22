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
- 캐시(data/ingest/enrich_cache.json): (doc id, 입력+모델+프롬프트 해시) 일치 시 LLM 생략 —
  재실행 비용은 줄이되, 모델/프롬프트를 바꾸면 자동으로 miss 되어 개선이 반영된다.
- 자기 채점(--score-dl): dl 규칙 결과가 있는 행을 같은 경로로 돌려 이름 집합 정합을 측정
  (LLM 추출 품질의 근거 수치).
"""

import contextvars
import hashlib
import inspect
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


def _input_hash(cut: str, candidates: list[str], model: str) -> str:
    """캐시 키 — 문서 입력뿐 아니라 '무엇으로 뽑았는지'(모델·시스템 프롬프트)도 포함한다.

    입력만 해싱하면 AGENT_PARSE_MODEL을 바꾸거나 _SYSTEM을 고쳐 재빌드해도 전건 캐시
    히트가 나서 옛 결과가 그대로 재적재된다 — 품질 개선이 조용히 무효가 된다.
    기존 캐시(enrich_cache.json)는 키가 달라져 자연히 miss 되고 다음 빌드에서 갱신된다.
    """
    key = f"{cut}|{','.join(candidates)}|{model}|{_PROMPT_HASH}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


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


def _compute_prompt_hash() -> str:
    """프롬프트 지문 — _SYSTEM만이 아니라 _batch_prompt 템플릿까지 포함해야 한다.

    출력 형태(블록 구성·index 부여·지시문)를 실제로 좌우하는 건 사용자 메시지 템플릿인데
    _SYSTEM만 해싱하면 그쪽을 고쳐도 캐시가 전건 히트해서 옛 결과가 그대로 재적재된다 —
    캐시 키에 모델·프롬프트를 넣어 막으려던 바로 그 상황이 재현된다.
    """
    try:
        template = inspect.getsource(_batch_prompt)
    except OSError:  # 소스 없이 배포된 경우(zip/frozen) — 시스템 프롬프트만으로 폴백
        template = "<source-unavailable>"
    return hashlib.sha1(f"{_SYSTEM}\n{template}".encode("utf-8")).hexdigest()[:12]


_PROMPT_HASH = _compute_prompt_hash()


def _cache_entry(item: dict, params: list[dict], model: str) -> dict:
    """캐시 엔트리 — model/prompt_hash는 키(input_hash)에 이미 녹아 있지만, 어떤 조합으로
    뽑힌 값인지 나중에 추적할 수 있도록 엔트리에도 남긴다(해시만으로는 역추적이 안 된다)."""
    return {"input_hash": item["hash"], "parameters": params,
            "enrich_input": item["cut_kind"], "model": model, "prompt_hash": _PROMPT_HASH}


def _write_cache(path: Path, data: dict) -> None:
    """원자적 교체로 캐시를 쓴다 — 중간 flush로 쓰기 횟수가 늘었으므로, 쓰는 도중 중단되면
    잘린 JSON이 남아 다음 빌드가 캐시 로드 단계에서 통째로 실패한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


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


def _apply_result(doc: dict, params: list[dict], cut_kind: str) -> bool:
    """추출 결과를 행에 반영한다. 반환: 실제로 반영했으면 True.

    params_source='unknown' 행(P2 대상: overview_table·compound_split — 전용 문서가 없어
    파라미터를 '미상'으로 실어둔 행)은 **빈 결과일 때 내리지 않는다.** '근거는 봤지만 뽑을
    이름이 없음'을 prose_llm 빈 행으로 바꾸면 (a) '전용 문서 없음' 출처 설명을 잃고 (b) 검수
    R2가 정당한 미상을 위반으로 뒤집는다. 이름이 실제로 나온 경우에만 승격한다.
    uicontrol 행은 기존대로 빈 결과도 prose_llm으로 확정한다(_apply 주석의 위험 비대칭 근거)."""
    if doc["metadata"].get("params_source") == "unknown" and not params:
        return False
    _apply(doc, params, cut_kind)
    return True


def _enrich_source(d: dict, by_url: dict) -> tuple:
    """행 종류별 파라미터 추출 근거를 정한다. 반환 (cut_kind, cut, candidates) 또는 ('skip', 사유).

    - uicontrol_candidates: 액션 전용 문서 본문에서 컷(기존 경로).
    - unknown/overview_table: 근거는 표 설명(raw_description)뿐이다 — 전용 문서가 없다.
    - unknown/compound_split: 겸용 원본 문서에서 해당 액션 몫을 뽑는다(url=겸용 문서).
    """
    md = d.get("metadata", {})
    src = md.get("params_source")
    if src == "uicontrol_candidates":
        body = by_url.get(normalize_pretty_url(d.get("url", "")))
        if not body or not body.get("html"):
            return ("skip", "no_body")
        kind, cut = extract_cut(body["html"])
        return (kind, cut, md.get("param_candidates", []))
    if src == "unknown":
        origin = md.get("action_source")
        if origin == "overview_table":
            desc = (md.get("raw_description") or "").strip()
            if len(desc) < 30:  # 표 설명이 너무 짧으면 파라미터를 서술할 여지가 없다 — 호출 낭비
                return ("skip", "no_source")
            return ("overview_desc", desc[:_CUT_LIMIT], [])
        if origin == "compound_split":
            body = by_url.get(normalize_pretty_url(d.get("url", "")))  # 겸용 원본 문서
            if not body or not body.get("html"):
                return ("skip", "no_body")
            kind, cut = extract_cut(body["html"])
            return (f"compound:{kind}", cut, [])
    return ("skip", "no_source")


def _verbatim_filter(params: list[dict], source: str) -> tuple[list[dict], int]:
    """P2 미상 채움 전용 축자 필터 — param 이름이 근거 원문(설명/겸용 문서)에 없으면 버린다.

    원칙(발견은 LLM·표기는 결정론): enrich 프롬프트가 '문서 표기 그대로'를 요구하지만 강제가
    아니라, 짧은 설명에서 모델이 형제 액션의 표준 필드(실측: Interactive Forms Disable/
    Unhighlight에 'Form name'·'Form element')를 추론해 넣을 수 있다. 근거 원문에 그 이름이
    없으면 우리가 확인한 사실이 아니므로 버린다 — table_llm 축자 검증과 같은 계약이다.
    uicontrol 경로(기존)는 후보 목록이 별도 근거라 이 필터를 걸지 않는다."""
    hay = _norm_name(source)
    kept = [p for p in params if _norm_name(p.get("name", "")) and _norm_name(p["name"]) in hay]
    return kept, len(params) - len(kept)


_FLUSH_EVERY = 20  # 배치 N개마다 캐시 중간 저장(진행 출력 주기와 동일)


def _run_batches(work: list[dict], model: str | None, stats: dict | None = None,
                 on_flush=None) -> dict[int, list[dict]]:
    """work(전역 인덱스 부여된 항목들)를 배치 LLM 호출로 처리해 {전역 idx: params} 반환.

    on_flush(부분결과)가 주어지면 주기적으로/중단 시에 호출한다 — 콜드 캐시 빌드가
    중간에 끊겨도 그 회차의 LLM 비용이 통째로 증발하지 않게 하기 위함이다.
    """
    from app.core.llm import usage_context

    batch_size = max(1, config.AGENT_PARSE_BATCH_SIZE)
    out: dict[int, list[dict]] = {}

    def one_batch(chunk: list[tuple[int, dict]]) -> tuple[dict[int, list[dict]], int, int]:
        """(채택한 결과, 정합성 위반으로 폐기한 항목 수, 응답에서 누락된 항목 수)"""
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _batch_prompt([c for _, c in chunk])},
        ]
        res: dict[int, list[dict]] = {}
        try:
            batch = chat_json(messages, purpose=PURPOSE, model_cls=_Batch, model=model)
        except ValueError as exc:
            logger.warning("보강 배치 실패 (건너뜀): %d개 — %s", len(chunk), exc)
            return res, 0, 0
        # index를 그대로 믿으면 안 된다 — index가 겹치거나 범위를 벗어나면 어느 문서의 결과인지
        # 특정할 수 없어 A액션 파라미터가 B액션 행에 붙고, 그게 B의 '정상' input_hash로 캐시에
        # 적혀 영구 고착된다. stats상으론 enriched 성공으로 보여 탐지도 안 되므로 그때만
        # 배치 전체를 폐기한다(폐기분은 캐시에 남지 않아 재실행 시 재시도된다).
        idxs = [item.index for item in batch.results]
        if len(set(idxs)) != len(idxs) or any(not 0 <= i < len(chunk) for i in idxs):
            logger.warning(
                "보강 배치 index 정합성 위반 (배치 폐기): 요청 %d개 / 응답 %d개, index=%s",
                len(chunk), len(idxs), idxs,
            )
            return res, len(chunk), 0
        for item in batch.results:
            gidx = chunk[item.index][0]
            res[gidx] = [p.to_dict() for p in item.parameters]
        # 개수 부족은 오정렬 신호가 아니다 — index가 모두 유일하고 범위 안이면 chunk[index]
        # 매핑에 모호함이 없다. _SYSTEM은 '파라미터 없으면 빈 배열'을 요구하지만 모델이 빈
        # 항목을 아예 생략하는 건 흔한 실패 양상이라, 이걸 폐기 사유로 두면 그런 문서 1건이
        # 섞인 배치마다 정상 추출분(배치 크기-1건)이 함께 버려진다. 반환분은 채택하고
        # 누락 index만 경고로 남긴다(누락분은 캐시 미기록 → 다음 실행에서 재시도).
        missing = sorted(set(range(len(chunk))) - set(idxs))
        if missing:
            logger.warning(
                "보강 배치 응답 누락 (반환분 %d건은 채택): 요청 %d개, 누락 index=%s",
                len(idxs), len(chunk), missing,
            )
        return res, 0, len(missing)

    chunks = []
    indexed = list(enumerate(work))
    for start in range(0, len(indexed), batch_size):
        chunks.append(indexed[start : start + batch_size])

    dropped, missing, aborted = 0, 0, ""
    with usage_context(component="rag_enrich"):
        with ThreadPoolExecutor(max_workers=max(1, config.AGENT_PARSE_WORKERS)) as pool:
            futures = [pool.submit(contextvars.copy_context().run, one_batch, c) for c in chunks]
            done = 0
            for f in as_completed(futures):
                if aborted:  # 중단 확정 후 남은 future는 결과를 보지 않는다(취소분은 예외를 낸다)
                    continue
                try:
                    res, n_dropped, n_missing = f.result()
                except Exception as exc:  # noqa: BLE001
                    # ValueError만 잡으면 부족하다 — app/core/llm.py가 인증 실패·사용량 한도
                    # 초과를 RuntimeError로 올리는데, 이게 여기서 되던져지면 _run_batches가
                    # 통째로 중단되고 호출자의 캐시 쓰기까지 건너뛰어 그 회차에 성공한 보강분이
                    # 전부 증발한다. 배치 재시도로 회복되지 않는 종류이므로 남은 배치는
                    # 포기하되, 이미 성공한 분은 반환·flush해서 디스크에 남긴다.
                    aborted = str(exc) or exc.__class__.__name__
                    logger.error("보강 중단 — 성공분만 반영: %s", aborted)
                    for other in futures:
                        other.cancel()
                    continue
                out.update(res)
                dropped += n_dropped
                missing += n_missing
                done += 1
                if done % _FLUSH_EVERY == 0:
                    print(f"  보강 배치 {done}/{len(chunks)}")
                    if on_flush is not None:
                        on_flush(out)  # 주기적 중간 저장 — 강제 종료돼도 여기까지는 남는다
    if aborted and on_flush is not None:
        on_flush(out)
    # 폐기·누락은 무음이면 안 된다 — 호출자 stats로 올려 로그·집계에 남긴다.
    # 둘 다 결과가 없는 항목이라 호출자에서 failed로도 세어진다. 운영자가 2배로 읽지 않도록
    # 겹침을 로그에 명시한다.
    if dropped or missing:
        print(f"  [enrich] index 정합성 위반 폐기 {dropped}건 / 응답 누락 {missing}건 "
              f"(둘 다 failed에 포함, 캐시 미기록 → 재실행 시 재시도)")
    if aborted:
        print(f"  [enrich] 중단됨 — 완료 배치 {done}/{len(chunks)}만 반영: {aborted}")
    if stats is not None:
        # 대입이 아니라 누산 — _run_batches가 한 실행에서 두 번(보강 + --score-dl) 호출될 수
        # 있어 대입이면 앞 집계가 묻힌다.
        stats["dropped_misaligned"] = stats.get("dropped_misaligned", 0) + dropped
        stats["missing_in_response"] = stats.get("missing_in_response", 0) + missing
        if aborted:
            stats["aborted"] = aborted
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
        and d.get("metadata", {}).get("params_source") in ("uicontrol_candidates", "unknown")
    ]
    if limit > 0:
        targets = targets[:limit]

    # unknown(P2: 전용 문서 없는 overview_table·compound_split 행)은 따로 집계한다 —
    # 채웠는지 / 근거는 봤지만 못 채웠는지를 uicontrol 보강과 섞으면 신호가 안 보인다.
    stats = {"targets": len(targets), "cached": 0, "enriched": 0, "no_body": 0, "no_source": 0,
             "failed": 0, "unknown_targets": 0, "unknown_filled": 0, "unknown_kept": 0,
             "unknown_param_rejected": 0, "dropped_misaligned": 0, "missing_in_response": 0}
    work: list[dict] = []
    work_docs: list[dict] = []
    for d in targets:
        was_unknown = d["metadata"].get("params_source") == "unknown"
        if was_unknown:
            stats["unknown_targets"] += 1
        src = _enrich_source(d, by_url)
        if src[0] == "skip":
            stats[src[1]] += 1
            continue
        kind, cut, candidates = src
        h = _input_hash(cut, candidates, model)
        cached = cache.get(d["id"])
        if cached and cached.get("input_hash") == h:
            cparams = cached["parameters"]
            if was_unknown:  # 캐시엔 무필터 원본이 있으므로 적용 직전 다시 축자 필터
                cparams, rej = _verbatim_filter(cparams, cut)
                stats["unknown_param_rejected"] += rej
            applied = _apply_result(d, cparams, cached.get("enrich_input", kind))
            stats["cached"] += 1
            if was_unknown:
                stats["unknown_filled" if applied else "unknown_kept"] += 1
            continue
        work.append({"package": d["package_name"], "action": d.get("action_name") or "",
                     "candidates": candidates, "cut_kind": kind, "cut": cut, "hash": h,
                     "was_unknown": was_unknown})
        work_docs.append(d)

    if get_request_id() is None:
        new_request_id()
    log_event("enrich_params_start", targets=len(targets), llm_items=len(work), model=model)
    print(f"[enrich] 대상 {len(targets)} (캐시 {stats['cached']}, 본문없음 {stats['no_body']}, "
          f"근거없음 {stats['no_source']}, LLM {len(work)}) — 그중 미상 {stats['unknown_targets']}건")

    def _flush(partial: dict[int, list[dict]]) -> None:
        """진행 중 성공분을 캐시에 중간 저장한다(_run_batches의 메인 스레드에서만 호출).

        캐시 키에 모델·프롬프트가 들어가면서 기존 enrich_cache.json은 전건 miss라,
        콜드 캐시 빌드 한 번의 LLM 비용이 크다. 최종 write가 _run_batches '뒤'에만 있으면
        중간에 한도 초과·강제 종료가 나는 순간 그 회차 비용이 통째로 증발한다.
        """
        snapshot = dict(cache)
        for gidx, params in partial.items():
            snapshot[work_docs[gidx]["id"]] = _cache_entry(work[gidx], params, model)
        _write_cache(cache_path, snapshot)

    results = _run_batches(work, model, stats, on_flush=_flush) if work else {}
    for gidx, doc in enumerate(work_docs):
        params = results.get(gidx)
        if params is None:
            stats["failed"] += 1
            continue
        # 빈 결과라도 캐시엔 원본(무필터)을 남긴다 — 다음 실행 재호출(재비용) 방지. 축자
        # 필터는 cut에서 결정론적이라 적용 직전 다시 건다(캐시 히트 경로와 동일).
        cache[doc["id"]] = _cache_entry(work[gidx], params, model)
        if work[gidx].get("was_unknown"):
            params, rej = _verbatim_filter(params, work[gidx]["cut"])
            stats["unknown_param_rejected"] += rej
        applied = _apply_result(doc, params, work[gidx]["cut_kind"])
        if applied:
            stats["enriched"] += 1
        if work[gidx].get("was_unknown"):
            stats["unknown_filled" if applied else "unknown_kept"] += 1

    _write_cache(cache_path, cache)
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
