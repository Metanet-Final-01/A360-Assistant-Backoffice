"""패키지 개요 페이지에서 **공식 액션 목록**을 LLM으로 추출한다 — 구조 규칙을 의미 판단으로 교체.

왜 규칙을 버리는가(실측 2026-07-21):
`_overview_action_table`은 "첫 <th>가 Action/Actions인 <table>"이라는 구조 규칙이다. 그런데
khub 문서는 형식이 일정하지 않아 이 베팅이 여러 번 빗나갔다.
  - Active Directory: 헤더가 'Operation' → 표를 통째로 못 읽음(추출 0행)
  - Microsoft Outlook (macOS): 셀이 <li> 2개인데 평문으로 뭉갬 → 'Connect Disconnect'라는
    실재하지 않는 이름 생성(파싱 아티팩트)
  - Excel advanced: 표 5행이 전부 카테고리명이라 액션 로스터가 아님
  - 규칙 파싱으로 표 0행인 패키지가 17개
헤더 표기·표 개수·셀 중첩은 **우리가 통제하지 않는 외부 문서의 형식**이고, 규칙을 늘리는 건
열거를 늘리는 일이다. 대신 "이 페이지가 이 패키지의 액션 목록을 싣고 있나"를 의미로 판단한다.

환각 방지 두 겹:
  1. 반환된 액션 이름은 **페이지 원문에 그대로 나온 문자열**이어야 한다(축자 검증). 없으면 버린다.
  2. 규칙 파싱 결과와 합집합을 취하고, 어느 쪽에서 왔는지 통계로 남긴다 — 규칙이 잡던 것을
     LLM이 놓치면 그대로 드러난다.
"""

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, Field

from .. import config
from ..agents.structured import chat_json
from ..observability import log_event

logger = logging.getLogger(__name__)

PURPOSE = "extract_action_table"

# 개요 페이지 평문 상한 — 실측 중앙 1,908 / p90 5,531 / 최대 27,404자(Recorder).
# 12,000이면 95% 이상이 온전히 들어가고, 액션 로스터는 보통 페이지 앞부분에 있다.
_PAGE_LIMIT = 12000

_SYSTEM = (
    "너는 Automation Anywhere A360 공식 문서의 **패키지 개요 페이지**를 읽고, 그 페이지가 "
    "그 패키지의 액션(봇 편집기에서 끌어다 쓰는 실행 단위) 목록을 싣고 있는지 판단하고 "
    "목록을 추출하는 분석기다. 반드시 지정된 JSON 스키마로만 답한다.\n\n"
    "판단 기준:\n"
    "- 액션 목록은 대개 표로 실린다. 표의 헤더가 'Action'일 수도 'Operation'일 수도 있고, "
    "표가 여러 개로 나뉘어 있을 수도 있다. **형식이 아니라 내용으로 판단하라.**\n"
    "- 한 셀에 여러 액션이 나열돼 있으면(목록 항목 등) **각각 별도 항목으로** 분리하라.\n"
    "- 표가 액션이 아니라 **카테고리/분류**(예: 'Cell operations', 'Workbook operations')를 "
    "나열하는 것이면 액션이 아니다 — has_action_list=false로 답하라.\n"
    "- 표가 지원 매트릭스·버전 목록·용어 설명이면 액션 목록이 아니다.\n\n"
    "추출 규칙:\n"
    "- action: **페이지에 적힌 그대로**의 액션 이름. 절대 새로 짓거나 다듬지 마라. "
    "원문에 없는 문자열을 쓰면 안 된다.\n"
    "- description: 그 액션의 설명. 페이지에 있는 만큼만 싣고 없으면 빈 문자열.\n"
    "- 액션 목록이 없으면 has_action_list=false, actions=[] 로 답하라.\n"
)


class _TableAction(BaseModel):
    action: str = Field(description="페이지에 적힌 그대로의 액션 이름")
    description: str = ""


class _TableResult(BaseModel):
    has_action_list: bool = Field(description="이 페이지가 액션 목록을 싣고 있으면 true")
    actions: list[_TableAction] = []


_PROMPT_HASH = hashlib.sha256(_SYSTEM.encode("utf-8")).hexdigest()[:12]


def _norm_txt(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def _input_hash(text: str, model: str) -> str:
    return hashlib.sha256(f"{text}|{model}|{_PROMPT_HASH}".encode("utf-8")).hexdigest()[:16]


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def extract_action_tables(items: list[dict], *, model: str | None = None) -> tuple[dict, dict]:
    """items: [{package, text}] → ({package: {has_action_list, actions}}, stats).

    축자 검증을 통과한 항목만 반환한다 — LLM이 페이지에 없는 이름을 지어내면 여기서 걸린다.
    """
    model = model or config.AGENT_PARSE_MODEL
    cache_path = config.DATA_DIR / "table_llm_cache.json"
    cache: dict = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    stats = {"packages": len(items), "cached": 0, "called": 0, "no_action_list": 0,
             "rejected_not_verbatim": 0, "failed": 0}
    out: dict[str, list[dict]] = {}
    todo = []
    for it in items:
        h = _input_hash(it["text"], model)
        hit = cache.get(it["package"])
        if hit and hit.get("input_hash") == h:
            out[it["package"]] = {"has_action_list": hit.get("has_action_list", bool(hit["actions"])),
                                  "actions": hit["actions"]}
            stats["cached"] += 1
            continue
        todo.append({**it, "_hash": h})

    log_event("extract_action_table_start", packages=len(items), cached=stats["cached"],
              llm_items=len(todo), model=model)
    print(f"[table] 개요 페이지 {len(items)} (캐시 {stats['cached']}, LLM {len(todo)})", flush=True)

    # 여러 worker가 공유 stats를 갱신하므로 락으로 직렬화한다 — read-modify-write(+=)와
    # setdefault().append()가 레이스로 유실되면 build_stats.json 근거가 왜곡된다(Qodo 리뷰).
    _stats_lock = threading.Lock()

    def one(it: dict) -> tuple[str, list[dict], str]:
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"패키지: {it['package']}\n\n페이지 본문:\n{it['text']}"},
        ]
        try:
            res = chat_json(messages, purpose=PURPOSE, model_cls=_TableResult, model=model)
        except Exception as exc:  # noqa: BLE001 — 한 페이지 실패가 빌드를 막지 않는다
            logger.warning("액션 표 추출 실패 (건너뜀): %s — %s", it["package"], exc)
            with _stats_lock:
                stats["failed"] += 1
            return it["package"], None, it["_hash"]  # None = 판단 못 함 → 규칙 결과를 그대로 쓴다
        if not res.has_action_list:
            # 액션 목록이 아니라고 판단하면 **규칙 파싱 결과도 무효**가 된다(호출자가 처리).
            # 실측: Excel advanced 표 5행은 전부 카테고리명('Cell operations' 등)인데
            # 규칙은 이를 액션으로 넣고 있었다. 형식이 아니라 내용으로 판단한 결과다.
            with _stats_lock:
                stats["no_action_list"] += 1
                stats.setdefault("no_action_list_packages", []).append(it["package"])
            return it["package"], {"has_action_list": False, "actions": []}, it["_hash"]
        hay = _norm_txt(it["text"])
        kept = []
        for a in res.actions:
            name = re.sub(r"\s+", " ", a.action or "").strip()
            if not name:
                continue
            if _norm_txt(name) not in hay:  # 축자 검증 — 원문에 없는 이름은 버린다
                with _stats_lock:
                    stats["rejected_not_verbatim"] += 1
                    stats.setdefault("rejected_samples", []).append(f"{it['package']}/{name}")
                continue
            kept.append({"action": name[:120], "description": (a.description or "").strip()[:2000]})
        return it["package"], {"has_action_list": True, "actions": kept}, it["_hash"]

    if todo:
        from app.core.llm import usage_context

        workers = max(1, config.AGENT_PARSE_WORKERS)
        with usage_context(component="rag_table", actor_type="system"):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(one, it) for it in todo]
                done = 0
                for fut in as_completed(futures):
                    pkg, res, h = fut.result()
                    if res is None:  # 실패 — 캐시에 남기지 않는다(다음 실행에 재시도)
                        continue
                    out[pkg] = res
                    cache[pkg] = {"input_hash": h, "model": model, "prompt_hash": _PROMPT_HASH,
                                  "has_action_list": res["has_action_list"],
                                  "actions": res["actions"]}
                    stats["called"] += 1
                    done += 1
                    if done % 20 == 0:
                        print(f"  표 추출 {done}/{len(todo)}", flush=True)
        _write_cache(cache_path, cache)

    log_event("extract_action_table_done", **{k: v for k, v in stats.items()
                                              if not isinstance(v, list)})
    return out, stats
