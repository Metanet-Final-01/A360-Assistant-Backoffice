"""패키지 서브트리 원문 → LLM 구조화 추출 (규칙 파싱 제거 재설계).

배경(계획서 v3-llm-package-extract-plan): 기존 build-v2는 액션·파라미터를 구조 규칙
(_overview_action_table 헤더 표기, is_leaf 필터, _dl_params 등)으로 뽑고 LLM은 보조로만
썼다. 규칙은 외부 문서 형식에 베팅이라 여러 번 빗나갔고(헤더 'Operation', <li> 셀, 카테고리
표, is_leaf 오판으로 본문 미수집 → 파라미터 공백), 규칙을 늘리는 건 열거를 늘리는 일이었다.

이 모듈은 그 발상을 뒤집는다 — **한 패키지의 서브트리 전체(루트+하위 노드의 제목·원문·계층)를
통째로 LLM에 넣어** 노드별로 "이게 액션인가"를 판정하고, 액션이면 파라미터까지 구조화 추출한다.
is_leaf·헤더 표기 같은 구조 규칙에 의존하지 않는다.

표기 결정성(발견은 LLM, 표기는 결정론):
  - **액션명**: LLM이 그 노드의 **제목(title)에 나온 짧은 액션 이름**을 돌려주고, 우리는 그것이
    제목/본문에 글자 그대로(정규화 후) 존재하는지 verbatim 대조한다. 문장 전체·의역은 형태 가드
    (≤8단어·마침표로 안 끝남, 'The/Use the' 접두 제거)로 걸러 짧은 이름만 남긴다. 불일치·부적격이면
    그 항목만 표적 재질의(1회), 그래도 실패면 폐기. 원문(제목)에 없는 표기는 존재하지 않는 것으로 취급.
    → 제목 유래라 안정 id(_doc_id("action2", pkg, name))가 규칙 베이스라인과 정렬돼 비교가 쉬워진다.
  - **파라미터**: 제품 문서가 파라미터를 산문 지시("Enter a name for the computer.")로만 적는 경우가
    많아 '짧은 라벨'은 원문에 축자로 없다(기존 params_source=prose_llm도 같은 이유로 요약했다).
    그래서 파라미터 name은 verbatim 강제하지 않고, LLM이 그 노드 content만 보고 요약한 라벨을 쓰되,
    description에 근거 문장을 실어 그라운딩한다. 빈 이름·문장형(과장) 이름만 형태로 걸러낸다.

완결 회계 — 입력의 모든 노드가 출력에 node_id로 등장해야 한다. 조용히 빠진 노드는 is_action=false로
간주하고 통계에 남긴다(LLM이 큰 서브트리에서 노드를 흘리는 것 감지).

결정성은 현 파이프라인 철학 그대로: temperature 미설정 + 내용 해시 캐시(같은 입력=같은 출력
재사용) + JSON mode/pydantic/1회 교정 + 안정 id. 캐시는 원자적 쓰기(tmp→replace).
"""

import hashlib
import json
import logging
import re
import threading
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from .. import config
from ..agents.structured import chat_json
from .merge_v2 import _clip, _plain_text, _soup

logger = logging.getLogger(__name__)

PURPOSE = "extract_package_actions"
COMPONENT = "rag_parse"

# 패키지 입력 평문 상한(문자). gpt-5-mini 입력 한계(272K 토큰)의 ~18%인 50K 토큰을 EN 기준
# ~5자/토큰으로 환산한 값 ≈ 250,000자. 모델 한계가 아니라 품질·안전 상한이다(계획서 §8).
# 실측 최대 패키지가 Recorder ~22K 토큰(≈110K자)이라 현재 코퍼스는 이 상한에 걸리지 않는다.
_MAX_INPUT_CHARS = 250_000
_NODE_TEXT_LIMIT = 40_000  # 노드 하나의 content 평문 상한(초대형 단일 노드 방어).

_TYPE_ENUM = "TEXT|NUMBER|BOOLEAN|SELECT|FILE|CREDENTIAL|SESSION|LIST|DICTIONARY|VARIABLE|UNKNOWN"

# 액션명 정제 — 제목 문법(F5)의 접두·접미를 결정론으로 제거(merge_v2.action_identity 축약판).
_NAME_PREFIX = re.compile(r"^(?:the|use the|use|using the|using)\s+", re.IGNORECASE)
_NAME_SUFFIX = re.compile(r"\s+actions?$", re.IGNORECASE)

_SYSTEM = (
    "너는 Automation Anywhere(A360) 공식 문서에서 한 **패키지의 문서 서브트리 전체**를 읽고, "
    "각 문서 노드가 봇 편집기에서 끌어다 쓰는 '액션'을 설명하는지 판정하고, 액션이면 그 "
    "파라미터 스키마까지 추출하는 분석기다. 반드시 지정된 JSON 스키마로만 답한다.\n\n"
    "입력은 다음 형태의 JSON이다: {\"package\": 이름, \"nodes\": [{\"node_id\", \"title\", "
    "\"content\", \"nodes\": [...재귀...]}]}. content는 그 노드 본문의 평문이다.\n\n"
    "판정 기준:\n"
    "- 액션(is_action=true): 파라미터를 받아 작업을 수행하는 실행 단위. 반복(Loop)·조건(If)·예외"
    "처리(Try/Catch) 같은 제어 흐름 컨테이너도 액션이다.\n"
    "- 액션 아님(is_action=false): 개념·튜토리얼·릴리스노트·목차·예제 나열·카테고리 분류"
    "(예: 'Cell operations')·설치/관리 가이드. reason에 example|procedure|concept|category|overview 중 하나.\n\n"
    "추출 규칙(엄수):\n"
    "- **입력의 모든 노드**를 그 node_id 그대로 출력에 실어라(빠짐없이). 액션이 아니면 "
    "is_action=false, actions=[] 만.\n"
    "- **한 노드가 액션 여럿을 기술할 수 있다.** 그 경우 actions 배열에 **전부** 담아라. "
    "특히 (a) 패키지 문서가 한 페이지뿐이고 그 안에 액션들이 나열된 경우, (b) 한 문서가 "
    "'Connect and Disconnect'처럼 액션 둘을 겸해 기술하는 경우. 액션이 하나면 원소 1개.\n"
    "- action.name = 그 액션의 **짧은 이름**. 반드시 그 노드의 **title 또는 본문에 나온 이름**을 "
    "쓰되, 'action'·'in the ... package' 같은 수식어는 떼고 **핵심 이름만** 남겨라. "
    "예: title 'Create computer' → 'Create computer'. 절대 **문장 전체**나 설명을 name에 넣지 마라. "
    "번역·의역·새 작명 금지(원문 표기 그대로).\n"
    "- action.label = 사람이 읽는 이름(보통 name과 같음).\n"
    "- parameters: 그 노드 content에서 **그 액션이** 받는 입력 필드/옵션을 뽑는다. "
    "parameter.name = **짧은 필드 라벨**(예: 'Session name', 'Computer name'). "
    "parameter.description = 그 필드를 설명하는 문서 문장(근거). "
    f"parameter.type = {_TYPE_ENUM} 중 추정(모르면 UNKNOWN). required = true/false/null.\n"
    "- 문서에 파라미터 근거가 없으면 parameters=[]. 절대 지어내지 마라.\n"
)

# 트리거 전용 프롬프트 — 트리거는 '실행 단위'가 아니라 '봇을 자동 실행시키는 이벤트'다.
# 액션 프롬프트를 그대로 쓰면 트리거 설정 문서가 전부 procedure/concept으로 판정된다.
_SYSTEM_TRIGGER = (
    "너는 Automation Anywhere(A360) 공식 문서의 **트리거(Trigger) 문서 트리**를 읽고, 각 문서 "
    "노드가 어떤 **트리거**를 기술하는지 판정하고 그 설정 파라미터를 추출하는 분석기다. "
    "반드시 지정된 JSON 스키마로만 답한다.\n\n"
    "입력 형태: {\"package\": 이름, \"nodes\": [{\"node_id\", \"title\", \"content\", \"nodes\": [...]}]}.\n\n"
    "판정 기준:\n"
    "- 트리거(is_action=true): 봇/오토메이션을 **자동으로 실행시키는 이벤트**와 그 설정을 기술하는 문서. "
    "예: 이메일 수신, 파일/폴더 변경, 핫키, 창 열림, 웹훅/웹 트리거, ServiceNow 레코드 생성 등. "
    "'Creating a X trigger'/'Configuring a X trigger'처럼 **설정 절차 문서라도 그 트리거를 정의하면 트리거로 본다.**\n"
    "- 트리거 아님(is_action=false): 트리거 개념 개요·목록·가용성 표·워크플로 설명 등. "
    "reason에 concept|overview|availability|workflow 중 하나.\n\n"
    "추출 규칙(엄수):\n"
    "- **입력의 모든 노드**를 그 node_id 그대로 출력에 실어라(빠짐없이).\n"
    "- **한 노드가 트리거 여럿을 기술하면 actions 배열에 전부** 담아라.\n"
    "- action.name = 그 트리거의 **짧은 이름**(예: 'Email trigger', 'Window trigger', "
    "'GitHub Repository event trigger'). title/본문에 나온 표기를 쓰고, 'Creating a'/'Configuring a' "
    "같은 동사구 수식어와 'in an automation' 같은 꼬리는 떼라. 문장 전체를 name에 넣지 마라. 새 작명 금지.\n"
    "- **제품/서비스 이름은 반드시 남겨라.** 'Configuring a Jira web trigger in an automation'은 "
    "'Jira web trigger'이지 'Web trigger'가 아니다. 'Web trigger'/'Trigger'처럼 일반명으로 "
    "뭉뚱그리면 서로 다른 트리거가 한 이름으로 뭉개진다.\n"
    "- parameters: 그 트리거의 설정 필드(예: 'Email account', 'Folder path', 'Hot key', 'Listener URL'). "
    "parameter.name = 짧은 필드 라벨, description = 근거 문장. "
    f"type = {_TYPE_ENUM} 중 추정. 근거 없으면 parameters=[].\n"
)

_RETRY_SYSTEM = (
    "너는 A360 문서에서 뽑은 액션 이름을 교정하는 도구다. 아래 각 대상에 대해, 그 노드의 title과 "
    "원문을 보고 **그 액션의 짧은 이름**('action'·'in the ... package' 수식어를 뗀 핵심 이름)을 "
    "돌려줘라. 문장이 아니라 이름만. 원문/제목에 그런 액션이 없으면 빈 문자열(\"\"). 새 작명 금지. "
    '반드시 JSON으로만: {"items": [{"key": 입력키, "verbatim": 짧은이름_또는_빈문자열}]}'
)


class LLMParam(BaseModel):
    name: str
    type: str | None = None
    required: bool | None = None
    description: str | None = None


class LLMAction(BaseModel):
    name: str = ""
    label: str | None = None
    parameters: list[LLMParam] = Field(default_factory=list)


class NodeVerdict(BaseModel):
    """노드 하나의 판정 — 한 문서가 액션 **여럿**을 기술할 수 있으므로 actions는 리스트다.

    단일 페이지 패키지(Interactive Forms·Credential·Goto 등, 실측 노드 1개)는 액션들이 그
    한 페이지 안에 전부 적혀 있다. 노드당 액션 1개로 두면 그런 패키지에서 최대 1개만
    나오거나(대개 개요로 판정돼) 0개가 된다 — 실측 6패키지 0액션의 원인이었다.
    겸용 문서('Connect and Disconnect')도 규칙 분해 없이 여기서 자연히 2개로 나온다.
    """

    # 모델이 입력 키 'id'를 그대로 echo하는 경우가 많아 alias로 받아 교정 재시도를 없앤다.
    model_config = ConfigDict(populate_by_name=True)
    node_id: str = Field(validation_alias=AliasChoices("node_id", "id"))
    is_action: bool = False
    actions: list[LLMAction] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_single_action(cls, v):
        """모델이 단수 'action' 키로 답해도 받아준다(형식 이탈 흡수 — 교정 호출 절약)."""
        if isinstance(v, dict) and not v.get("actions") and isinstance(v.get("action"), dict):
            return {**v, "actions": [v["action"]]}
        return v


class PackageExtraction(BaseModel):
    package: str = ""
    nodes: list[NodeVerdict] = Field(default_factory=list)


class ExtractedAction(BaseModel):
    """검증을 통과해 채택된 액션 1건 — (출처 노드, 액션). 노드당 여러 건 나올 수 있다."""

    node_id: str
    action: LLMAction
    reason: str | None = None


class _RetryItem(BaseModel):
    key: str
    verbatim: str = ""


class _RetryResult(BaseModel):
    items: list[_RetryItem] = Field(default_factory=list)


_PROMPT_HASH = hashlib.sha256(
    (_SYSTEM + "||" + _SYSTEM_TRIGGER + "||" + _RETRY_SYSTEM).encode("utf-8")
).hexdigest()[:12]


def _norm(s: str) -> str:
    """verbatim 대조용 정규화 — 공백 1칸 축약 + 소문자화(table_llm._norm_txt와 동일 규칙)."""
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def _clean_name(name: str) -> str:
    """액션명 정제 — 'The '/'Use the ' 접두, ' action' 접미 제거(제목 문법 결정론 정리)."""
    n = re.sub(r"\s+", " ", name or "").strip()
    n = _NAME_PREFIX.sub("", n)
    n = _NAME_SUFFIX.sub("", n).strip()
    return n


def _plausible(name: str) -> bool:
    """액션 '이름'다운가 — 문장/설명이 이름으로 잘못 온 것을 거른다(merge_v2._plausible_action_name).

    실제 액션명은 짧은 명사/동사구라 마침표로 안 끝나고 단어 수가 적다(실측 최장 6단어).
    """
    n = (name or "").strip()
    return bool(n) and not n.endswith(".") and len(n.split()) <= 8


def _input_hash(payload: str, model: str) -> str:
    return hashlib.sha256(f"{payload}|{model}|{_PROMPT_HASH}".encode("utf-8")).hexdigest()[:16]


def _write_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def build_package_input(root_toc: dict, bodies_en: dict) -> tuple[dict, dict]:
    """패키지 서브트리(raw ToC 노드) → (입력 JSON, 노드 인덱스).

    입력 JSON은 계층을 그대로 재귀(nodes). 각 노드에 node_id(content_id)·title·url·content(태그
    제거 평문)를 담는다 — 비-leaf 노드도 자기 content를 담아 is_leaf 누락(액션 본문 미수집)을 없앤다.

    노드 인덱스 = {content_id: {title, url, content, hay}} — verbatim 대조·행 조립·완결 회계에 쓴다.
    hay = 정규화(title + 본문) — 액션명은 제목/본문 어디에 있든 대조되게 한다.
    """
    index: dict[str, dict] = {}

    def rec(toc_node: dict) -> dict:
        cid = toc_node.get("contentId")
        title = toc_node.get("title", "")
        url = toc_node.get("prettyUrl", "")
        body = bodies_en.get(cid) if cid else None
        content = _plain_text(_soup(body.get("html")), _NODE_TEXT_LIMIT) if body and body.get("html") else ""
        children = [rec(ch) for ch in toc_node.get("children", []) or []]
        node = {"node_id": cid or "", "title": title, "url": url, "content": content, "nodes": children}
        if cid:
            index[cid] = {"title": title, "url": url, "content": content,
                          "hay": _norm(title + " \n " + content)}
        return node

    root = rec(root_toc)
    return root, index


def _pack_payload(node: dict) -> str:
    return json.dumps(node, ensure_ascii=False)


def _fit_node(node: dict, budget: int) -> dict:
    """노드(서브트리 포함)를 예산 안에 들어오게 만든다 — 긴 content부터 보이게 절단(_clip).

    자기 content만 줄여선 안 된다 — 덩치가 자손에 있는 트리(부모는 짧고 손자가 거대)에서는
    예산을 영영 못 맞춘다. 그래서 서브트리 전체의 content를 **긴 것부터** 깎아 초과분을 흡수한다.
    구조(노드·제목·URL)는 건드리지 않으므로, 본문을 다 비워도 남는 구조 오버헤드가 예산을
    넘으면 그대로 돌려준다(그 이상은 트리를 깨뜨린다).
    """
    node = json.loads(_pack_payload(node))  # 원본 불변 — 깊은 복사
    for _ in range(8):  # 절단→재측정을 몇 번 반복(JSON 이스케이프로 감소폭이 정확하지 않다)
        over = len(_pack_payload(node)) - budget
        if over <= 0:
            return node
        refs: list[dict] = []

        def _collect(n: dict) -> None:
            if n.get("content"):
                refs.append(n)
            for child in n.get("nodes") or []:
                _collect(child)

        _collect(node)
        if not refs:
            return node  # 더 깎을 본문이 없다 — 구조만으로 초과
        refs.sort(key=lambda n: len(n["content"]), reverse=True)
        remaining = over + 64  # 꼬리표·이스케이프 여유
        for ref in refs:
            if remaining <= 0:
                break
            cur = len(ref["content"])
            cut = min(cur, remaining)
            ref["content"] = _clip(ref["content"], max(0, cur - cut))
            remaining -= cut
    return node


def _split_children(root: dict, max_chars: int, stats: dict | None = None) -> list[dict]:
    """입력이 예산 초과면 루트의 직계 자식을 예산 이하로 그리디 패킹해 N개 청크로 나눈다(맵-리듀스 map).

    노드 하나를 중간에서 자르지 않는다(파라미터 표 절단 방지). 현재 코퍼스는 이 경로를 안 탄다(폴백).

    ⚠️ 예산 초과는 자식 개수만으로 생기지 않는다 — 루트 자체(제목+본문)가 이미 예산을 넘거나
    자식 하나가 예산보다 클 수 있다. 그 경우 그리디 패킹만으로는 여전히 초과 청크가 나와
    보호 장치가 무력해지므로, 각각 content를 절단해 맞추고 통계로 드러낸다.
    """

    def _bump(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    kids = root.get("nodes") or []
    # 루트 단독(자식 제외)이 예산을 넘으면 루트 본문부터 줄인다.
    bare = {**root, "nodes": []}
    if len(_pack_payload(bare)) > max_chars:
        _bump("oversized_root")
        root = {**_fit_node(bare, max_chars), "nodes": kids}
        bare = {**root, "nodes": []}
    if not kids:
        return [root]

    base = len(_pack_payload(bare))
    chunks: list[list[dict]] = [[]]
    cur = base
    for kid in kids:
        # 자식 하나가 남은 예산보다 크면 그 자식의 본문을 절단해 맞춘다.
        if base + len(_pack_payload(kid)) > max_chars:
            _bump("oversized_child")
            kid = _fit_node(kid, max(0, max_chars - base))
        ksz = len(_pack_payload(kid)) + 1  # +1 = 배열 구분자(,) — 안 세면 자식이 많을수록 누적 드리프트
        if chunks[-1] and cur + ksz > max_chars:
            chunks.append([])
            cur = base
        chunks[-1].append(kid)
        cur += ksz

    # 최종 보장 — 루트를 줄이고 자식을 절단해도, 자식의 구조적 오버헤드(id·제목·URL)까지는
    # 없앨 수 없어 청크가 예산을 몇십 자 넘길 수 있다. 남은 초과분은 청크 루트 본문에서 깎아
    # "예산 이하" 불변식을 실제로 지킨다(안 지키면 이 함수의 존재 이유가 사라진다).
    out = []
    for ch in chunks:
        chunk = {**root, "nodes": ch}
        if len(_pack_payload(chunk)) > max_chars:
            _bump("oversized_chunk")
            chunk = _fit_node(chunk, max_chars)
        out.append(chunk)
    return out


def _name_fix(rejected: dict[str, tuple[str, str]], index: dict, model: str) -> dict[str, str]:
    """액션명 표적 재시도(계획서 §6-2) — 형태 부적격/불일치 이름만 한 번에 재질의한다.

    rejected: {key: (node_id, 거부된_원본이름)}. key는 노드당 여러 액션을 구분하는 "nid#i".
    그 노드의 title+원문을 근거로 '짧은 이름'을 받아온다. 패키지 전체를 다시 돌리지 않고
    실패 항목만 1회. 반환: {key: 교정된_짧은이름(verbatim 통과분만)}.
    """
    if not rejected:
        return {}
    blocks = []
    for key, (nid, name) in rejected.items():
        meta = index.get(nid, {})
        blocks.append(
            f'[key="{key}"] title="{meta.get("title", "")}" 거부된이름="{name}"\n'
            f"원문:\n{_clip(meta.get('content', ''), 4000)}"
        )
    messages = [
        {"role": "system", "content": _RETRY_SYSTEM},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]
    try:
        res = chat_json(messages, purpose=PURPOSE + "_retry", model_cls=_RetryResult, model=model)
    except (ValueError, RuntimeError) as exc:
        logger.warning("액션명 표적 재시도 실패 (건너뜀): %s", exc)
        return {}
    out: dict[str, str] = {}
    for it in res.items:
        name = _clean_name(it.verbatim)
        entry = rejected.get(it.key)
        if not entry:
            continue
        nid = entry[0]
        if name and _plausible(name) and _norm(name) in index.get(nid, {}).get("hay", ""):
            out[it.key] = name
    return out


def _clean_params(params: list[LLMParam]) -> list[LLMParam]:
    """파라미터 정리 — 빈 이름·문장형(과장) 이름 제거. name은 verbatim 강제 안 함(산문 라벨)."""
    out = []
    for p in params:
        pn = re.sub(r"\s+", " ", p.name or "").strip()
        if not pn or len(pn.split()) > 12:  # 문장이 name으로 온 경우 제거
            continue
        p.name = pn
        out.append(p)
    return out


def _wrap_payload(package: str, root_node: dict) -> dict:
    """프롬프트가 명시한 입력 계약 그대로 감싼다 — {"package": 이름, "nodes": [루트]}.

    루트 노드 dict를 그대로 보내면 프롬프트가 설명한 형태(package/nodes 래퍼)와 실제 입력이
    어긋나, 모델이 잘못된 파싱 전제를 따를 수 있다(계약 불일치).
    """
    return {"package": package, "nodes": [root_node]}


def _extract_one(payload_node: dict, index: dict, package: str, model: str, stats: dict,
                 lock: threading.Lock, *, kind: str = "action") -> tuple[list[ExtractedAction], set[str]]:
    """단일 입력(패키지/트리거 트리 또는 청크) → (채택된 ExtractedAction 리스트, LLM이 응답한 node_id 집합).

    노드 하나가 액션 여럿을 낼 수 있으므로 (node_id, action) 쌍의 평탄한 리스트를 돌려준다.
    두 번째 값은 **완결 회계용**이다 — 채택 여부와 무관하게 "LLM이 verdict를 돌려준 노드"를
    모아야 '조용히 흘린 노드'와 '정상 비-액션 판정'을 구분할 수 있다.
    kind="trigger"면 트리거 전용 프롬프트를 쓴다(판정 기준이 다르다).
    """
    messages = [
        {"role": "system", "content": _SYSTEM_TRIGGER if kind == "trigger" else _SYSTEM},
        {"role": "user", "content": _pack_payload(_wrap_payload(package, payload_node))},
    ]
    try:
        result = chat_json(messages, purpose=PURPOSE, model_cls=PackageExtraction, model=model)
    except (ValueError, RuntimeError) as exc:
        logger.warning("추출 실패 (건너뜀): %s — %s", package, exc)
        with lock:
            stats["failed"] += 1
        return [], set()

    # 채택 전에 먼저 모은다 — is_action=false도 '응답했다'는 사실은 완결 회계에 필요하다.
    returned_ids = {v.node_id for v in result.nodes if v.node_id}

    accepted: list[ExtractedAction] = []
    pending: dict[str, tuple[str, str]] = {}          # key → (node_id, 거부된 원본이름)
    pending_obj: dict[str, tuple[NodeVerdict, LLMAction]] = {}
    for v in result.nodes:
        if not v.is_action or not v.node_id or not v.actions:
            continue
        meta = index.get(v.node_id)
        if meta is None:  # 입력에 없던 node_id 환각 → 폐기
            with lock:
                stats["hallucinated_node"] += 1
            continue
        for i, act in enumerate(v.actions):
            name = _clean_name(act.name)
            if name and _plausible(name) and _norm(name) in meta["hay"]:
                act.name = name
                act.parameters = _clean_params(act.parameters)
                accepted.append(ExtractedAction(node_id=v.node_id, action=act, reason=v.reason))
            else:
                key = f"{v.node_id}#{i}"
                pending[key] = (v.node_id, act.name or "")
                pending_obj[key] = (v, act)

    if pending:  # 액션명 표적 재시도
        fixes = _name_fix(pending, index, model)
        for key, (nid, old) in pending.items():
            v, act = pending_obj[key]
            fixed = fixes.get(key)
            if fixed:
                act.name = fixed
                act.parameters = _clean_params(act.parameters)
                accepted.append(ExtractedAction(node_id=nid, action=act, reason=v.reason))
                with lock:
                    stats["recovered_by_retry"] += 1
            else:
                with lock:
                    stats["rejected_not_verbatim"] += 1
                    stats.setdefault("rejected_samples", []).append(f"{package}/{old[:60]}")
    return accepted, returned_ids


def extract_package(pkg_display: str, root_toc: dict, bodies_en: dict, *, model: str,
                    stats: dict, lock: threading.Lock, cache: dict,
                    kind: str = "action") -> tuple[list[ExtractedAction], dict]:
    """한 패키지(또는 트리거 트리)의 서브트리를 추출한다.

    반환: (채택 ExtractedAction 리스트, 노드 인덱스). 노드 하나가 액션 여럿을 낼 수 있다.
    kind="trigger"면 트리거 전용 프롬프트로 판정한다.
    캐시: 입력 JSON + model + prompt_hash 해시가 저장된 것과 같으면 LLM 재호출 없이 재사용.
    """
    payload, index = build_package_input(root_toc, bodies_en)
    # 해시는 **실제로 모델에 보내는 것**(래퍼 포함)으로 잡는다 — 보내는 것과 캐시 키가 어긋나면
    # 계약이 바뀌어도 옛 결과를 재사용한다.
    payload_str = _pack_payload(_wrap_payload(pkg_display, payload))
    h = _input_hash(payload_str + f"|kind={kind}", model)

    hit = cache.get(pkg_display)
    if hit and hit.get("input_hash") == h:
        with lock:
            stats["cached"] += 1
        return [ExtractedAction.model_validate(d) for d in hit["verdicts"]], index

    content_nodes = {nid for nid, m in index.items() if m["content"]}
    returned_ids: set[str] = set()
    if len(payload_str) > _MAX_INPUT_CHARS:  # 예산 초과 → 맵-리듀스(현재 미발동)
        with lock:
            stats["map_reduced"] += 1
            stats.setdefault("map_reduced_packages", []).append(f"{pkg_display}({len(payload_str)}자)")
        merged: dict[tuple[str, str], ExtractedAction] = {}
        for ch in _split_children(payload, _MAX_INPUT_CHARS, stats):
            chunk_accepted, chunk_ids = _extract_one(ch, index, pkg_display, model, stats, lock, kind=kind)
            returned_ids |= chunk_ids  # 완결 회계는 청크 합집합 기준
            for ea in chunk_accepted:
                key = (ea.node_id, _norm(ea.action.name))
                prev = merged.get(key)
                if prev is None or len(ea.action.parameters) > len(prev.action.parameters):
                    merged[key] = ea
        accepted = list(merged.values())
    else:
        with lock:
            stats["called"] += 1
        accepted, returned_ids = _extract_one(payload, index, pkg_display, model, stats, lock, kind=kind)
        # 빈 결과 자기치유: content 노드가 있는데 0건이면 재호출(최대 2회).
        # 동시 호출 부하/출력 절단으로 빈 응답이 오고 교정이 그걸 빈 nodes로 통과시키는 경우가
        # 실측됐다(워커>1). 진짜 비-액션 패키지(UI Agents 등)는 재시도해도 0이면 그대로 수용한다.
        attempt = 0
        while not accepted and content_nodes and attempt < 2:
            attempt += 1
            with lock:
                stats["empty_retry"] = stats.get("empty_retry", 0) + 1
            accepted, returned_ids = _extract_one(payload, index, pkg_display, model, stats, lock, kind=kind)

    # 완결 회계 — 기준은 "채택된 액션"이 아니라 **LLM이 verdict를 돌려준 노드**다.
    # 채택 기준으로 세면 정상적인 비-액션 판정까지 전부 결손으로 잡혀, 정작 잡으려던
    # '조용히 흘린 노드'와 구분이 안 돼 통계가 무의미해진다.
    missing = content_nodes - returned_ids
    if missing:
        with lock:
            stats["completeness_missing"] += len(missing)

    cache[pkg_display] = {
        "input_hash": h, "model": model, "prompt_hash": _PROMPT_HASH, "kind": kind,
        "verdicts": [ea.model_dump() for ea in accepted],
    }
    return accepted, index
