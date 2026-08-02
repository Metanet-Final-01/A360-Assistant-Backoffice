"""package.action만으로는 Recorder/Loop 같은 액션의 실제 의미를 못 잡는 문제를 풀기
위한 "critical attribute" 추출기. GPT 재설계안(2026-07-30)의 핵심 주장을 그대로
구현한다: A360 원본(raw.json)의 attributes에 이미 정보가 다 있고, 지금까지 우리
pipeline(normalize_extracted_workflows.py)이 그걸 goldset.json에 그대로 복사만 해두고
채점에는 안 썼다는 것. 이 모듈은 그 attributes를 실제로 파싱해서 의미 있는 필드로
꺼낸다.

실증 확인(0020_HelpDesk_Copilot__Change_Status_Of_Query.raw.json 등 실제 데이터로
직접 검증함, 추측 아님):
- Recorder.capture의 commandName은 항상 "capture"라 무의미하고, 진짜 동작 종류는
  attributes 안에 위젯별로 다른 이름의 액션 속성으로 들어있다
  (예: listviewAction=GETTOTALITEMS/SELECTITEMBYINDEX, comboboxAction=SELECTITEMBYTEXT).
- 클릭/입력 대상은 attributes의 "uiObject" 안에 있는데, 이게 바로 못 읽는 base64 blob이다.
  디코딩하면 CSS Selector/DOMXPath/HTML Tag/HTML Class/HTML FrameSrc(=실제 웹사이트 URL)/
  HTML InnerText가 다 들어있다.
- Loop는 loopType(WHILE/ITERATOR - 우리 39개 후보 안에서 실제로 나온 값은 이 둘뿐이고
  고정횟수형 TIMES 타입은 없었음)과 그에 딸린 condition(Boolean 변수명) 또는
  iterator(반복 대상 변수명)를 attributes에 갖고 있다.

다른 패키지(StructuredDataExtraction 등)는 이번 39개 Main/Challenge 후보 안에 실제
사례가 없어서 여기서는 구현하지 않았다 - 나중에 실제 사례가 나오면 같은 패턴으로
추가하면 된다. 없는 걸 추측해서 미리 만들지 않는다."""

from __future__ import annotations

import base64
import json
import re
from typing import Any


def _unwrap(value: dict | None) -> Any:
    """{"string": "...", "type": "STRING"} 같은 A360 속성값 래퍼에서 실제 스칼라를 꺼낸다."""
    if not isinstance(value, dict):
        return value
    for key in ("string", "number", "boolean", "expression"):
        if key in value:
            return value[key]
    return value


def _find_attribute(attributes: list[dict], name: str) -> dict | None:
    for attr in attributes or []:
        if attr.get("name") == name:
            return attr.get("value")
    return None


def readable_parameters(step: dict, *, max_chars: int = 300) -> str:
    """이 스텝의 모든 attribute를 "이름=값" 텍스트로 펼친다 - Recorder/WebAutomation
    전용인 common_signature()와 달리 어떤 패키지든 그대로 쓸 수 있다.

    judge_core_business_relevance()에 실제 파라미터 값(예: folderPath가
    $pStrLogsFolder$인지 $pStrWTemp$인지)을 보여주려고 만들었다 - 액션 이름만
    보면 "이 Folder.deleteFolder가 로그 정리용인지 진짜 업무 폴더 삭제인지"
    구별이 안 되는데, 실제 대상 경로를 보면 구별된다(0098에서 실제로 확인함).
    uiObject 같은 base64 blob은 통째로 보여주면 의미가 없어서 건너뛴다."""
    parts: list[str] = []
    for attr in step.get("attributes", []) or []:
        name = attr.get("name")
        if not name or name == "uiObject":
            continue
        value = _unwrap(attr.get("value"))
        if not isinstance(value, (str, int, float, bool)) or value == "":
            continue
        text = str(value)
        if len(text) > max_chars:
            text = text[:max_chars] + "…"
        parts.append(f"{name}={text}")
    return ", ".join(parts) if parts else "(파라미터 없음)"


def decode_ui_object(attributes: list[dict]) -> dict | None:
    """Recorder 스텝의 "uiObject" 속성(base64 blob) -> 실제 대상 객체 정보.

    반환 필드: css_selector, dom_xpath, html_tag, html_id, html_class,
    frame_src(실제 웹사이트 URL), inner_text_snippet(앞 80자, 참고용).
    blob이 없거나 파싱 실패하면 None(추측하지 않음)."""
    ui_object = _find_attribute(attributes, "uiObject")
    if not ui_object or not isinstance(ui_object, dict):
        return None
    blob_b64 = (ui_object.get("uiObject") or {}).get("blob")
    if not blob_b64:
        return None
    try:
        decoded = json.loads(base64.b64decode(blob_b64))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    obj_node = decoded.get("objNode", {})
    props = {p.get("name"): p.get("value") for p in obj_node.get("properties", []) or []}
    return {
        "css_selector": props.get("CSS Selector") or None,
        "dom_xpath": props.get("DOMXPath") or None,
        "html_tag": props.get("HTML Tag") or None,
        "html_id": props.get("HTML ID") or None,
        "html_class": props.get("HTML Class") or None,
        "frame_src": props.get("HTML FrameSrc") or None,
        "inner_text_snippet": (props.get("HTML InnerText") or "")[:80] or None,
    }


def recorder_signature(step: dict) -> dict:
    """Recorder.capture 스텝 -> {widget_action, target(uiObject 디코딩), bound_value}.

    widget_action은 attributes 중 이름이 "Action"으로 끝나는 항목을 찾아
    "<속성이름>:<값>" 형태로 만든다(예: "listviewAction:GETTOTALITEMS"). 어떤 위젯
    타입이든(listview/combobox/click/textbox 등) 이 규칙으로 다 잡힌다 - 위젯마다
    속성 이름은 다르지만 전부 "...Action"으로 끝나는 패턴을 실제 데이터에서 확인함."""
    attributes = step.get("attributes", []) or []
    widget_action = None
    for attr in attributes:
        name = attr.get("name", "")
        if name.endswith("Action"):
            widget_action = f"{name}:{_unwrap(attr.get('value'))}"
            break

    bound_value = _unwrap(_find_attribute(attributes, "value"))

    return {
        "widget_action": widget_action,
        "target": decode_ui_object(attributes),
        "bound_value": bound_value,
    }


def loop_signature(step: dict) -> dict:
    """Loop 스텝 -> {loop_type, iterator_target 또는 condition_target, check_at_end}.

    loop_type: WHILE(조건 반복) / ITERATOR(컬렉션 반복) / 그 외(실제 관측 안 됨, 있는
    그대로 문자열 보존). "반복 횟수로 IF 평탄화 가능"은 TIMES류 고정횟수 loop에만
    해당하는데, 우리 39개 후보 안에는 그런 사례가 없었음 - WHILE/ITERATOR는 둘 다
    "언제 끝날지 정적으로 알 수 없는" 반복이라 IF로 평탄화할 수 없다."""
    attributes = step.get("attributes", []) or []
    loop_type = _unwrap(_find_attribute(attributes, "loopType"))

    iterator = _find_attribute(attributes, "iterator")
    condition = _find_attribute(attributes, "condition")
    check_at_end = _unwrap(_find_attribute(attributes, "checkConditionAtEnd"))

    target = None
    if iterator:
        target = {"kind": "iterator", "name": iterator.get("iteratorName"), "package": iterator.get("packageName")}
    elif condition:
        target = {"kind": "condition", "name": condition.get("conditionalName"), "package": condition.get("packageName")}

    return {"loop_type": loop_type, "target": target, "check_at_end": check_at_end}


# Recorder의 위젯별 ...Action 값 -> 패키지 간 비교를 위한 추상 operation 상수.
# 실제 데이터(금 시세 조회 봇 raw.json)로 확인된 값만 매핑한다 - 추측 금지.
_RECORDER_OPERATION_MAP = {
    "CLICK": "CLICK",
    "EXTRACTTOCSV": "EXTRACT_TABLE",
    "GETTOTALITEMS": "COUNT",
    "SELECTITEMBYINDEX": "SELECT",
    "SELECTITEMBYTEXT": "SELECT",
}

# WebAutomation 액션명 -> 추상 operation 상수. 실제 예측 데이터
# (gpt-5.6-luna, 금 시세 조회 봇 예측)에서 확인된 clickelement/gettablecontent만.
_WEBAUTOMATION_OPERATION_MAP = {
    "clickelement": "CLICK",
    "gettablecontent": "EXTRACT_TABLE",
}

_XPATH_CONTAINS_TEXT_RE = re.compile(r"contains\(\s*\.\s*,\s*['\"]([^'\"]+)['\"]\s*\)")


def _recorder_common_signature(step: dict) -> dict:
    attributes = step.get("attributes", []) or []
    operation = None
    for attr in attributes:
        name = attr.get("name", "")
        if name.endswith("Action"):
            raw_value = _unwrap(attr.get("value"))
            operation = _RECORDER_OPERATION_MAP.get(raw_value)
            break
    target = decode_ui_object(attributes)
    target_text = target.get("inner_text_snippet") if target else None
    return {"operation": operation, "target_text": target_text}


def _webautomation_common_signature(step: dict) -> dict:
    attributes = step.get("attributes", []) or []
    action = step.get("action") or ""
    operation = _WEBAUTOMATION_OPERATION_MAP.get(action)

    search_value = _unwrap(_find_attribute(attributes, "search"))
    target_text = None
    if isinstance(search_value, str):
        m = _XPATH_CONTAINS_TEXT_RE.search(search_value)
        if m:
            target_text = m.group(1)
    return {"operation": operation, "target_text": target_text}


_COMMON_SIGNATURE_BUILDERS = {
    "Recorder": _recorder_common_signature,
    "WebAutomation": _webautomation_common_signature,
}


def common_signature(step: dict) -> dict:
    """서로 다른 패키지(Recorder vs WebAutomation 등) 간에도 비교 가능하도록
    "operation"(추상 동작 상수)과 "target_text"(대상 텍스트) 두 필드만 뽑는다.
    이번 금 시세 조회 봇 실제 사례(Recorder.capture ↔ WebAutomation.clickelement/
    gettablecontent)에 필요한 만큼만 구현 - 다른 패키지는 필요해지면 추가한다.
    지원 안 하는 패키지는 {"operation": None, "target_text": None}."""
    if step.get("type") != "action":
        return {"operation": None, "target_text": None}
    builder = _COMMON_SIGNATURE_BUILDERS.get(step.get("package"))
    if builder is None:
        return {"operation": None, "target_text": None}
    return builder(step)


# 이 필드들은 "우리 39개 후보로 실증 확인된" 패키지/구조만 다룬다. 다른 패키지가
# 새로 들어오면 여기 dispatch에 추가하되, 반드시 실제 raw.json으로 attribute 구조를
# 먼저 확인한 뒤에 추가할 것 (추측 금지).
#
# 주의: normalize_extracted_workflows.py의 convert_node()는 "loop"/"if"/"try" 같은
# 제어구조 스텝에는 package 필드를 아예 안 넣는다(action/container 타입만 package를
# 가짐) - Loop는 type=="loop"로, Recorder 같은 leaf 액션은 package=="Recorder"로
# 구분해야 한다. 이 둘을 같은 방식(package 기준)으로 찾으려다 실제로 Loop가 0/56으로
# 하나도 안 잡히는 버그가 났었음 - type과 package를 섞어 쓰면 안 된다.
_ACTION_PACKAGE_BUILDERS = {
    "Recorder": recorder_signature,
}
_STEP_TYPE_BUILDERS = {
    "loop": loop_signature,
}


def critical_signature(step: dict) -> dict:
    """스텝에 맞는 critical attribute를 뽑는다. 모르는 package/type이면 빈 dict
    (=package.action 매칭만으로 채점, 지금까지 하던 대로)."""
    step_type = step.get("type")
    builder = _STEP_TYPE_BUILDERS.get(step_type)
    if builder is not None:
        return builder(step)
    if step_type == "action":
        builder = _ACTION_PACKAGE_BUILDERS.get(step.get("package"))
        if builder is not None:
            return builder(step)
    return {}
