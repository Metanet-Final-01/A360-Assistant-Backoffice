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
}
SESSION_LIFECYCLE_ACTION_RE = re.compile(
    r"(connect|disconnect|closeemail|setsessionvariable)",
    re.IGNORECASE,
)
FORMATTING_ONLY_ACTION_RE = re.compile(r"(autofit|formatcell|setcellcolor)", re.IGNORECASE)


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
    """Scoring-only setup/teardown actions that should not count as business work."""
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


def is_formatting_only_action(package: str | None, action: str | None) -> bool:
    package_norm = (package or "").strip().lower()
    if "excel" not in package_norm:
        return False
    return bool(FORMATTING_ONLY_ACTION_RE.search(action or ""))
