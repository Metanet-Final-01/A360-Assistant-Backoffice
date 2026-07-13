"""문서 파싱 에이전트 — JAR이 없는 패키지의 리프 문서(structured_html)를 액션 스키마로 파싱한다.

배경: 수집 소스 4개 중 JAR 파서만 패키지+액션+파라미터를 온전히 뽑고, 나머지(Fluid 문서·
Control Room 봇)는 구조화 액션을 못 만든다. A360 패키지 대부분은 공개 JAR이 없어 Fluid 문서로만
존재하므로 추천 메뉴(action_schema 조회)에 안 뜬다. 이 에이전트가 그 공백을 메운다 —
export-for-agent가 내보낸 agent_handoff.jsonl의 리프별 structured_html을 LLM으로 읽어,
"이 리프가 진짜 액션인가"를 판정하고 액션이면 파라미터 스키마까지 추출한다.

출력은 JAR 파서와 동일한 packages.json 형태(schema.build_package_dict)라, 기존 build 파이프라인이
그대로 action_schema로 변환한다. 다만 schema_source="llm_agent"로 미검증 신뢰 등급을 표시한다.

관측성: 각 리프/패키지 파싱을 @log_call로 감싸 백엔드와 동일한 JSON Lines 로그(observability.py)를
남기고, LLM 사용량은 core.llm.chat이 usage_context(component="rag_parse", system)로 기록한다.
"""

import contextvars
import json
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, Field

from ..build.doc_action_match import fuzzy_find_name
from ..observability import get_request_id, log_call, log_event, new_request_id
from .schema import ParsedAction, build_package_dict
from .structured import chat_json

logger = logging.getLogger(__name__)

COMPONENT = "rag_parse"
PURPOSE = "parse_actions"

# structured_html이 매우 큰 문서(스크린샷 많은 고급 패키지)의 토큰 폭주를 막는 상한.
# 압축 표현이라 이 정도면 파라미터 표/설명은 거의 담긴다(초과분은 꼬리라 손실 적음).
_MAX_HTML_CHARS = 12000

# 문서가 실제 '액션'인지 판별하고 스키마를 뽑는 공통 규칙(단건·배치 프롬프트가 공유).
_JUDGE_RULES = (
    "판별 기준: 액션은 봇 편집기에서 끌어다 쓰는 실행 단위입니다. 파라미터를 받아 작업을 수행하는 "
    "것이 대표적이지만, 반복(Loop)·조건 분기(If·Else·Else If)·예외 처리(Try·Catch·Finally·Throw)·"
    "단계(Step) 같은 제어 흐름 블록은 파라미터가 없거나 적어도 액션입니다(is_action=true) — 본문을 "
    "감싸는 컨테이너 블록도 액션으로 취급합니다. 다만 개념 소개·튜토리얼·릴리스 노트·목차·예제 나열 "
    "페이지는 액션이 아닙니다(is_action=false).\n"
    "추출 규칙:\n"
    "- name: 문서 제목에서 만든 안정적 식별자(camelCase, 영문). 실제 내부 command명을 모르면 제목 기반으로 추론.\n"
    "- label: 사람이 읽는 액션 이름(문서 제목 그대로).\n"
    "- parameters: 문서의 입력 필드/옵션 표에서 name·type·required(필수 여부)·label·설명을 뽑는다. 없으면 빈 배열.\n"
    "- 문서에 근거가 없는 값은 지어내지 말고 비워둔다(null).\n"
)

# 액션 하나의 JSON 필드(단건 = 이 필드로 이뤄진 객체, 배치 = 여기에 index를 더한 객체의 배열).
_ITEM_FIELDS = (
    '"is_action": bool, "name": str, "label": str|null, "description": str|null, '
    '"return_type": str|null, "return_label": str|null, '
    '"parameters": [{"name": str, "label": str|null, "description": str|null, "type": str|null, '
    '"required": bool, "default": any|null, "options": [str]|null}]'
)

_SYSTEM_PROMPT = (
    "당신은 Automation Anywhere(A360) 공식 문서 페이지 하나를 읽고, 그 페이지가 봇 편집기에서 "
    "실제로 끌어다 쓰는 '액션'을 설명하는지 판별하고, 액션이면 그 스키마를 추출하는 파서입니다.\n"
    + _JUDGE_RULES
    + "반드시 다음 JSON 객체만 출력(감싸는 키 없이 이 객체 자체를): {" + _ITEM_FIELDS + "}\n"
    + '액션이 아니면 {"is_action": false, "name": "", "parameters": []} 를 출력.'
)

_BATCH_SYSTEM_PROMPT = (
    "당신은 Automation Anywhere(A360) 공식 문서 여러 개를 한 번에 읽고, 각 문서가 봇 편집기에서 "
    "실제로 끌어다 쓰는 '액션'을 설명하는지 판별하고, 액션이면 스키마를 추출하는 파서입니다.\n"
    + _JUDGE_RULES
    + '반드시 다음 JSON 객체 하나만 출력하라: {"results": [ ...문서별 결과... ]}. 문서별 결과는 '
    + '입력의 index를 그대로 실은 다음 형태다: {"index": int, ' + _ITEM_FIELDS + "}\n"
    + "results의 길이는 입력 문서 수와 같아야 하며, 각 문서를 정확히 하나씩 판정하라. "
    + '액션이 아니면 is_action=false, name="" 으로 둔다.'
)


class _BatchItem(ParsedAction):
    """배치 결과의 원소 — ParsedAction에 입력 정렬용 index를 더한 형태."""

    index: int


class _BatchResult(BaseModel):
    """배치 파싱 LLM 출력 — 문서별 결과 배열."""

    results: list[_BatchItem] = Field(default_factory=list)


def _leaf_action_name(leaf: dict) -> str:
    """리프에서 결정론적 액션 name을 만든다 — LLM 추론 대신 문서 슬러그를 써서 재실행에도 안정적.

    URL 마지막 경로 조각(예: .../error-handler-throw)을 camelCase로 바꾼다(→ errorHandlerThrow).
    URL이 없으면 menu_id·title 순으로 폴백한다. (실제 내부 command 표기 정합은 별도 후속.)
    """
    url = (leaf.get("url") or "").rstrip("/")
    slug = url.split("/")[-1] if url else (leaf.get("menu_id") or leaf.get("title") or "")
    parts = [p for p in re.split(r"[-_\s/]+", slug.strip()) if p]
    if not parts:
        return ""
    return parts[0][:1].lower() + parts[0][1:] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _leaf_prompt(package_name: str, leaf: dict) -> str:
    path = " > ".join(leaf.get("path_titles", []) or [])
    html = leaf.get("structured_html")
    html_text = json.dumps(html, ensure_ascii=False) if html is not None else "(구조화 HTML 없음)"
    if len(html_text) > _MAX_HTML_CHARS:
        html_text = html_text[:_MAX_HTML_CHARS] + " …(생략)"
    return (
        f"패키지: {package_name}\n"
        f"문서 경로: {path}\n"
        f"문서 제목: {leaf.get('title')}\n"
        f"URL: {leaf.get('url')}\n\n"
        f"아래는 이 문서의 압축 구조(JSON)입니다. 이걸 읽고 액션 여부를 판정하고 스키마를 추출하세요.\n"
        f"{html_text}"
    )


@log_call("parse_leaf", capture_args=("package_name",),
          capture_result=lambda a: {"is_action": a is not None, "name": a.name if a else None})
def parse_leaf(package_name: str, leaf: dict, *, model: str | None = None) -> ParsedAction | None:
    """리프 문서 하나를 LLM으로 파싱한다. 진짜 액션이면 ParsedAction, 아니면 None.

    응답 스키마는 ParsedAction 자체(감싸는 키 없음)라, 모델이 name을 빠뜨린 이상 출력을 내면
    ParsedAction.name(필수)이 없어 ValidationError → chat_json이 1회 교정을 시도한다. 감싸는
    키를 두면(action optional) 규격 위반 출력이 조용히 action=None으로 통과해 유실됐다(RPA 리뷰).
    """
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _leaf_prompt(package_name, leaf)},
    ]
    action = chat_json(messages, purpose=PURPOSE, model_cls=ParsedAction, model=model)
    if not action.is_action:
        return None
    name = _leaf_action_name(leaf)  # name은 LLM 추론이 아니라 leaf 슬러그에서 결정론적으로
    if not name:
        return None
    action.name = name
    return action


def _batch_prompt(package_name: str, chunk: list[dict]) -> str:
    blocks = []
    for i, leaf in enumerate(chunk):
        path = " > ".join(leaf.get("path_titles", []) or [])
        html = leaf.get("structured_html")
        html_text = json.dumps(html, ensure_ascii=False) if html is not None else "(구조화 HTML 없음)"
        if len(html_text) > _MAX_HTML_CHARS:
            html_text = html_text[:_MAX_HTML_CHARS] + " …(생략)"
        blocks.append(
            f"===== 문서 index={i} =====\n"
            f"문서 경로: {path}\n문서 제목: {leaf.get('title')}\nURL: {leaf.get('url')}\n"
            f"압축 구조(JSON):\n{html_text}"
        )
    return (
        f"패키지: {package_name}\n\n"
        f"아래 {len(chunk)}개 문서를 각각 판정·추출해 results 배열로 반환하라 "
        f"(각 결과에 입력 index를 그대로 실을 것).\n\n" + "\n\n".join(blocks)
    )


@log_call("parse_batch", capture_args=("package_name",),
          capture_result=lambda r: {"leaves": len(r), "actions": sum(a is not None for a in r)})
def _parse_batch(
    package_name: str, chunk: list[dict], *, model: str | None = None
) -> list[ParsedAction | None]:
    """리프 묶음(chunk)을 한 번의 LLM 호출로 파싱한다. 입력 순서대로 ParsedAction|None 리스트 반환.

    배치 하나가 교정 후에도 실패하면 그 묶음만 통째로 버리고(전부 None) 다른 배치는 산다.
    모델이 index를 빠뜨리거나 개수가 어긋나도 index로 정렬해 안전하게 매핑한다.
    """
    messages = [
        {"role": "system", "content": _BATCH_SYSTEM_PROMPT},
        {"role": "user", "content": _batch_prompt(package_name, chunk)},
    ]
    out: list[ParsedAction | None] = [None] * len(chunk)
    try:
        batch = chat_json(messages, purpose=PURPOSE, model_cls=_BatchResult, model=model)
    except ValueError as exc:  # 교정 후에도 실패한 배치는 건너뛴다
        logger.warning("배치 파싱 실패 (건너뜀): %s / %d개 리프 — %s", package_name, len(chunk), exc)
        return out
    for item in batch.results:
        if not (0 <= item.index < len(chunk)):
            continue
        if not item.is_action:
            continue
        name = _leaf_action_name(chunk[item.index])  # name은 leaf 슬러그에서 결정론적으로
        if not name:
            continue
        action = ParsedAction.model_validate(item.model_dump(exclude={"index"}))
        action.name = name
        out[item.index] = action
    return out


def _dedupe_actions(actions: list[ParsedAction]) -> list[ParsedAction]:
    """패키지 안에서 name이 겹치면 파라미터가 더 많은(정보가 풍부한) 쪽을 남긴다.

    build_rag_documents는 action id를 (package_name, action.name)으로 만들어 겹치면
    _assert_no_duplicate_ids가 즉시 터진다 — JAR 파서(_dedupe_actions_by_name)와 같은 이유·같은 정책.
    """
    by_name: dict[str, ParsedAction] = {}
    for action in actions:
        prior = by_name.get(action.name)
        if prior is None or len(action.parameters) > len(prior.parameters):
            by_name[action.name] = action
    return list(by_name.values())


@log_call("parse_package", capture_args=("package_name",),
          capture_result=lambda pkg: {"action_count": len(pkg["actions"]) if pkg else 0})
def parse_package(package_name: str, leaves: list[dict], *, model: str | None = None) -> dict | None:
    """한 패키지의 모든 리프를 파싱해 packages.json 항목 dict로 조립한다. 액션이 하나도 없으면 None."""
    actions: list[ParsedAction] = []
    for leaf in leaves:
        try:
            action = parse_leaf(package_name, leaf, model=model)
        except ValueError as exc:  # 교정 후에도 파싱 실패한 리프는 건너뛰고 나머지는 살린다
            logger.warning("리프 파싱 실패 (건너뜀): %s / %s — %s", package_name, leaf.get("title"), exc)
            continue
        if action is not None:
            actions.append(action)
    if not actions:
        return None
    return build_package_dict(package_name, _dedupe_actions(actions))


def load_handoff(handoff_path: Path) -> dict[str, list[dict]]:
    """agent_handoff.jsonl(리프 1줄/개)을 package_name → 리프 리스트로 묶는다."""
    grouped: dict[str, list[dict]] = {}
    with open(handoff_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            leaf = json.loads(line)
            grouped.setdefault(leaf["package_name"], []).append(leaf)
    return grouped


def run(
    handoff_path: Path,
    *,
    jar_package_names: list[str] | None = None,
    model: str | None = None,
    limit: int = 0,
    batch_size: int = 0,
    workers: int = 0,
) -> list[dict]:
    """핸드오프 전체를 파싱해 packages.json 항목 dict 리스트를 반환한다.

    각 패키지의 리프를 batch_size개씩 묶어 한 번의 LLM 호출로 파싱하고(배치), 그 배치들을
    workers개까지 스레드풀로 동시 실행한다(LLM은 I/O 대기라 벽시계 시간이 크게 준다).
    batch_size/workers가 0이면 config 기본값을 쓴다.

    jar_package_names(JAR로 이미 커버된 패키지)와 퍼지 매칭되는 패키지는 파싱하지 않는다 —
    JAR이 항상 우선. 문서 사이트 표기가 JAR 이름과 달라도(예: "Python Script" vs "Python",
    "Data Table" vs "DataTable") 같은 패키지면 건너뛴다.

    limit>0이면 처리하는 리프 총수를 소프트 캡으로 제한하되, **패키지는 절대 중간에 자르지
    않는다** — leaves_seen이 이미 limit 이상이면 다음 패키지부터 시작하지 않는다. 리프 단위로
    잘라 부분 패키지를 만들면 그게 '완성된 것처럼' packages.json에 적재되고, 이후
    export-for-agent가 covered로 취급해 나머지 리프를 영영 못 채운다(RPA 리뷰 확인).

    사용량이 component="rag_parse"(system)로 귀속되도록 usage_context 안에서 호출한다.
    usage_context/request_id는 ContextVar라 배치마다 copy_context()로 워커 스레드에 전파한다.
    """
    from app.core.llm import usage_context

    from ..config import AGENT_PARSE_BATCH_SIZE, AGENT_PARSE_WORKERS

    # max(1, ...): batch_size가 0/음수면 range(0, n, 0)이 ValueError로 run() 전체를 죽인다
    # (as_completed 보호 밖). 잘못된 설정에도 최소 1로 강등해 파이프라인을 살린다.
    batch_size = max(1, batch_size or AGENT_PARSE_BATCH_SIZE)
    workers = workers or AGENT_PARSE_WORKERS
    jar_names = list(jar_package_names or [])
    grouped = load_handoff(handoff_path)
    if get_request_id() is None:
        new_request_id()
    log_event("parse_docs_agent_start", packages=len(grouped), jar_covered=len(jar_names))

    # 작업 목록 구성 — JAR 커버 제외, limit는 패키지 단위 소프트캡(중간 절단 금지), 리프를 배치로 묶음.
    work: list[tuple[str, list[dict]]] = []
    leaves_seen = 0
    for package_name, leaves in grouped.items():
        if fuzzy_find_name(package_name, jar_names):  # JAR 커버(퍼지 매칭) → 건너뜀
            continue
        if limit > 0 and leaves_seen >= limit:
            break  # 소프트 캡: 이미 한도 도달 → 새 패키지 시작 안 함
        leaves_seen += len(leaves)
        for start in range(0, len(leaves), batch_size):
            work.append((package_name, leaves[start : start + batch_size]))

    by_pkg: dict[str, list[ParsedAction]] = defaultdict(list)
    with usage_context(component=COMPONENT):  # actor_type=system, user_id=None
        # 배치마다 별도 copy_context — 하나의 Context를 여러 스레드가 run하면 RuntimeError.
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(contextvars.copy_context().run, _parse_batch, pkg, chunk, model=model): pkg
                for pkg, chunk in work
            }
            for future in as_completed(futures):
                pkg = futures[future]
                try:
                    actions = [a for a in future.result() if a is not None]
                except Exception as exc:  # noqa: BLE001 — 배치 하나 실패가 전체를 죽이지 않게
                    logger.warning("배치 실행 실패 (건너뜀): %s — %s", pkg, exc)
                    continue
                by_pkg[pkg].extend(actions)

    results = [
        build_package_dict(pkg, _dedupe_actions(actions)) for pkg, actions in by_pkg.items() if actions
    ]
    log_event(
        "parse_docs_agent_done",
        packages_parsed=len(results),
        actions=sum(len(p["actions"]) for p in results),
        leaves_seen=leaves_seen,
    )
    return results
