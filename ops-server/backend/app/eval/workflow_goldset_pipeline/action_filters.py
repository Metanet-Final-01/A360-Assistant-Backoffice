"""워크플로우 스텝 하나가 채점 대상에서 빠져야 하는지 판단하는 작은 규칙들.

scripts/agent_flow_eval/action_filters.py를 그대로 옮겼다.
"""

import re

BROWSER_SESSION_PACKAGE_PATTERN = re.compile(r"^(web\s*automation|webautomation|browser|recorder)$", re.IGNORECASE)
SESSION_ACTION_PATTERN = re.compile(r"session", re.IGNORECASE)


def is_disabled_step(step: dict) -> bool:
    return step.get("disabled") is True


def is_browser_session_lifecycle_action(package_name: str | None, action_name: str | None) -> bool:
    """브라우저 세션 시작/종료 같은 액션은 채점에서 제외한다.

    최신 Browser 패키지에서는 이 개념 자체가 사라졌기 때문이다. package 이름을
    한정해서 검사하므로, 관계없는 세션 액션(예: XML.startSession)은 정상적으로
    채점 대상에 남는다.
    """
    package_matches = bool(BROWSER_SESSION_PACKAGE_PATTERN.match(package_name or ""))
    action_mentions_session = bool(SESSION_ACTION_PATTERN.search(action_name or ""))
    return package_matches and action_mentions_session
