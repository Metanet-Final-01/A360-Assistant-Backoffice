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

import json
import logging
from pathlib import Path

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

_SYSTEM_PROMPT = (
    "당신은 Automation Anywhere(A360) 공식 문서 페이지 하나를 읽고, 그 페이지가 봇 편집기에서 "
    "실제로 끌어다 쓰는 '액션'을 설명하는지 판별하고, 액션이면 그 스키마를 추출하는 파서입니다.\n"
    "판별 기준: 액션은 파라미터를 받아 특정 작업을 수행하는 실행 단위입니다. 개념 소개·튜토리얼·"
    "릴리스 노트·목차·예제 나열 페이지는 액션이 아닙니다(is_action=false).\n"
    "추출 규칙:\n"
    "- name: 문서 제목에서 만든 안정적 식별자(camelCase, 영문). 실제 내부 command명을 모르면 제목 기반으로 추론.\n"
    "- label: 사람이 읽는 액션 이름(문서 제목 그대로).\n"
    "- parameters: 문서의 입력 필드/옵션 표에서 name·type·required(필수 여부)·label·설명을 뽑는다. 없으면 빈 배열.\n"
    "- 문서에 근거가 없는 값은 지어내지 말고 비워둔다(null).\n"
    '반드시 다음 JSON 객체만 출력(감싸는 키 없이 이 객체 자체를): {"is_action": bool, "name": str, '
    '"label": str|null, "description": str|null, "return_type": str|null, "return_label": str|null, '
    '"parameters": [{"name": str, "label": str|null, "description": str|null, "type": str|null, '
    '"required": bool, "default": any|null, "options": [str]|null}]}\n'
    '액션이 아니면 {"is_action": false, "name": "", "parameters": []} 를 출력.'
)


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
    if not action.is_action or not action.name.strip():
        return None
    return action


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
) -> list[dict]:
    """핸드오프 전체를 파싱해 packages.json 항목 dict 리스트를 반환한다.

    jar_package_names(JAR로 이미 커버된 패키지)와 퍼지 매칭되는 패키지는 파싱하지 않는다 —
    JAR이 항상 우선. 문서 사이트 표기가 JAR 이름과 달라도(예: "Python Script" vs "Python",
    "Data Table" vs "DataTable") 같은 패키지면 건너뛴다. exact 비교면 이 변형들이 새 패키지로
    잘못 파싱돼 JAR과 중복된 미검증 action_schema가 실서비스에 유입된다(RPA 리뷰 확인).

    limit>0이면 처리하는 리프 총수를 소프트 캡으로 제한하되, **패키지는 절대 중간에 자르지
    않는다** — leaves_seen이 이미 limit 이상이면 다음 패키지부터 시작하지 않는다. 리프 단위로
    잘라 부분 패키지를 만들면 그게 '완성된 것처럼' packages.json에 적재되고, 이후
    export-for-agent가 covered로 취급해 나머지 리프를 영영 못 채운다(RPA 리뷰 확인).

    사용량이 component="rag_parse"(system)로 귀속되도록 usage_context 안에서 호출한다.
    """
    from app.core.llm import usage_context

    jar_names = list(jar_package_names or [])
    grouped = load_handoff(handoff_path)
    if get_request_id() is None:
        new_request_id()
    log_event("parse_docs_agent_start", packages=len(grouped), jar_covered=len(jar_names))

    results: list[dict] = []
    leaves_seen = 0
    with usage_context(component=COMPONENT):  # actor_type=system, user_id=None
        for package_name, leaves in grouped.items():
            if fuzzy_find_name(package_name, jar_names):  # JAR 커버(퍼지 매칭) → 건너뜀
                continue
            if limit > 0 and leaves_seen >= limit:
                break  # 소프트 캡: 이미 한도 도달 → 새 패키지 시작 안 함(패키지 중간 절단 금지)
            leaves_seen += len(leaves)
            package = parse_package(package_name, leaves, model=model)
            if package is not None:
                results.append(package)

    log_event(
        "parse_docs_agent_done",
        packages_parsed=len(results),
        actions=sum(len(p["actions"]) for p in results),
        leaves_seen=leaves_seen,
    )
    return results
