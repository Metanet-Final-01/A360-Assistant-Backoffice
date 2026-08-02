from __future__ import annotations

import re


BROWSER_SESSION_PACKAGES_RE = re.compile(r"^(web\s*automation|webautomation|browser|recorder)$", re.IGNORECASE)
SESSION_ACTION_RE = re.compile(r"session", re.IGNORECASE)
CONTROL_FLOW_MARKER_PACKAGES = {"if", "loop", "step", "error handler", "errorhandler"}
CONTROL_FLOW_MARKER_ACTION_RE = re.compile(r"(if|loop|step|break|try|catch|finally|throw|errorhandler)", re.IGNORECASE)
SESSION_LIFECYCLE_PACKAGES = {
    "email",
    "gmail",
    "microsoft 365 outlook",
    "microsoft 365 excel package in automation 360",
}
SESSION_LIFECYCLE_ACTION_RE = re.compile(
    r"(connect|disconnect|closeemail|setsessionvariable)",
    re.IGNORECASE,
)
EVALUATION_EXCLUDED_PACKAGES = {"comment", "logging", "logtofile"}

# 패키지.액션 이름만으로는 핵심업무인지 범용 셋업(로그 폴더 준비, 경로 조립,
# 카운터 등)인지 구별이 안 되는 액션들 - 0089/0098처럼 같은 액션이 케이스
# 안에서 둘 다로 쓰이는 걸 실측 확인함(action_matching.py의 core-business
# 분류 단계에서만 대상으로 삼는다. 이 목록에 없는 액션은 항상 핵심업무로
# 취급 - 애초에 범용 셋업으로 쓰이는 사례를 실측한 적 없다).
AMBIGUOUS_GENERIC_ACTIONS = {
    ("folder", "createfolder"),
    ("folder", "deletefolder"),
    ("file", "createfile"),
    ("string", "assign"),
    ("datetime", "subtract"),
    ("datetime", "tostring"),
    ("datetime", "assign"),
    ("number", "assigntonumber"),
    ("boolean", "assign"),
    ("messagebox", "messagebox"),
}

# AMBIGUOUS_GENERIC_ACTIONS 중 실제 파라미터 값(경로, 조립되는 문자열 등)에 이
# 키워드가 있으면 사람이 검토한 gold_core_actions 9개 파일 전체에서 100%
# 예외 없이 "로그/감사/오류 스냅샷 관련 범용 셋업"이었다(실측 확인, 반대
# 사례 0건). 이 키워드가 없으면 규칙만으로는 판단 못 하고 LLM(judge_core_
# business_relevance)로 넘어간다.
INFRASTRUCTURE_KEYWORD_RE = re.compile(r"(log|audit|error|snapshot|observability)", re.IGNORECASE)


def is_ambiguous_generic_action(package: str | None, action: str | None) -> bool:
    return ((package or "").strip().lower(), (action or "").strip().lower()) in AMBIGUOUS_GENERIC_ACTIONS


def matches_infrastructure_keyword(readable_params: str) -> bool:
    return bool(INFRASTRUCTURE_KEYWORD_RE.search(readable_params or ""))


def action_label(package: str | None, action: str | None) -> str:
    return f"{package or ''}.{action or ''}".strip(".")


def is_disabled_step(step: dict) -> bool:
    return step.get("disabled") is True


def is_browser_session_lifecycle_action(package: str | None, action: str | None) -> bool:
    """Browser/WebAutomation session lifecycle disappeared in the newer Browser model.

    Keep the rule package-scoped so unrelated session actions such as XML.startSession
    still score normally.
    """
    return bool(BROWSER_SESSION_PACKAGES_RE.match(package or "") and SESSION_ACTION_RE.search(action or ""))


def is_control_flow_marker_action(package: str | None, action: str | None) -> bool:
    package_norm = (package or "").strip().lower()
    if package_norm not in CONTROL_FLOW_MARKER_PACKAGES:
        return False
    return bool(CONTROL_FLOW_MARKER_ACTION_RE.search(action or ""))


def is_session_lifecycle_action(package: str | None, action: str | None) -> bool:
    """Evaluation setup/teardown actions that should not count as business work."""
    package_norm = (package or "").strip().lower()
    action_norm = (action or "").strip().lower()
    action_compact = re.sub(r"[\s_-]+", "", action_norm)
    if is_browser_session_lifecycle_action(package, action):
        return True
    if package_norm == "browser" and action_norm == "close":
        return True
    if package_norm in {"task bot", "taskbot"} and "stop" in action_norm:
        return True
    return package_norm in SESSION_LIFECYCLE_PACKAGES and (
        bool(SESSION_LIFECYCLE_ACTION_RE.search(action or "")) or bool(SESSION_LIFECYCLE_ACTION_RE.search(action_compact))
    )


def should_exclude_action(package: str | None, action: str | None) -> bool:
    """Rules that apply symmetrically while converting Gold and predictions.

    Keep this deliberately narrow. MessageBox, Screen, Excel formatting, path
    assembly, and variable assignments can be part of the requested work.
    """
    package_norm = (package or "").strip().lower()
    return (
        package_norm in EVALUATION_EXCLUDED_PACKAGES
        or is_session_lifecycle_action(package, action)
        or is_control_flow_marker_action(package, action)
    )


def normalize_steps_for_evaluation(
    steps: list[dict], *, in_catch: bool = False
) -> list[dict]:
    """Apply the common rule-based conversion policy to a normalized step tree."""
    normalized: list[dict] = []
    for original in steps:
        if is_disabled_step(original):
            continue

        step = dict(original)
        step_type = step.get("type")
        if step_type == "action" and (
            in_catch or should_exclude_action(step.get("package"), step.get("action"))
        ):
            continue

        if "steps" in step:
            step["steps"] = normalize_steps_for_evaluation(
                step.get("steps", []) or [], in_catch=in_catch
            )
        if "branches" in step:
            branches = []
            for original_branch in step.get("branches", []) or []:
                branch = dict(original_branch)
                branch_name = str(branch.get("branch") or "").replace("_", "").lower()
                branch["steps"] = normalize_steps_for_evaluation(
                    branch.get("steps", []) or [],
                    in_catch=in_catch or branch_name in {"catch", "errorhandlercatch"},
                )
                branches.append(branch)
            step["branches"] = branches
        normalized.append(step)
    return normalized
