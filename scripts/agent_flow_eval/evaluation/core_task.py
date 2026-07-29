from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from action_filters import action_label, is_browser_session_lifecycle_action, is_disabled_step


CORE_PACKAGE_KEYS = {
    "amazonwebservices",
    "awsdynamodb",
    "awss3",
    "browser",
    "csv/txt",
    "database",
    "datatable",
    "docusign",
    "email",
    "excel",
    "exceladvanced",
    "excelms",
    "file",
    "folder",
    "gmail",
    "googledrive",
    "googlesheets",
    "jira",
    "microsoft365outlook",
    "microsoftoutlook",
    "microsoftoutlookmacos",
    "onedrive",
    "pdf",
    "rest",
    "salesforce",
    "sharepoint",
    "slack",
    "teams",
    "trello",
    "webautomation",
    "word",
}

NON_CORE_PACKAGE_KEYS = {
    "clipboard",
    "comment",
    "datetime",
    "delay",
    "dictionary",
    "errorhandler",
    "if",
    "list",
    "logtofile",
    "loop",
    "messagebox",
    "number",
    "screen",
    "string",
    "taskbot",
    "variable",
    "xml",
    "json",
}

NON_CORE_ACTION_RE = re.compile(
    r"(session|connect|disconnect|close|log|message\s*box|messagebox|"
    r"\bwait\b|delay|sleep|loaded|isloaded|assign)",
    re.IGNORECASE,
)


def package_key(package: str | None) -> str:
    return re.sub(r"[^a-z0-9/]+", "", (package or "").lower())


def split_action_label(label: str) -> tuple[str, str]:
    if "." not in label:
        return label, ""
    return label.split(".", 1)


def is_core_action(package: str | None, action: str | None) -> bool:
    if not package or not action:
        return False
    if is_browser_session_lifecycle_action(package, action):
        return False
    key = package_key(package)
    if key in NON_CORE_PACKAGE_KEYS:
        return False
    if NON_CORE_ACTION_RE.search(action):
        return False
    return key in CORE_PACKAGE_KEYS


def is_core_label(label: str) -> bool:
    package, action = split_action_label(label)
    return is_core_action(package, action)


def filter_core_labels(labels: list[str]) -> list[str]:
    return [label for label in labels if is_core_label(label)]


def project_core_steps(steps: list[dict[str, Any]], excluded: list[str] | None = None) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for step in steps:
        converted = project_core_step(step, excluded)
        if converted is not None:
            projected.append(converted)
    return projected


def project_core_step(step: dict[str, Any], excluded: list[str] | None = None) -> dict[str, Any] | None:
    if is_disabled_step(step):
        if excluded is not None:
            excluded.append(action_label(step.get("package"), step.get("action")) or step.get("type", "disabled"))
        return None

    step_type = step.get("type")
    if step_type == "action":
        package = step.get("package")
        action = step.get("action")
        if is_core_action(package, action):
            return deepcopy(step)
        if excluded is not None:
            excluded.append(action_label(package, action))
        return None

    if step_type in {"container", "if", "loop", "trigger_loop", "try"}:
        copied = deepcopy(step)
        copied["steps"] = project_core_steps(step.get("steps", []) or [], excluded)
        if step.get("branches") is not None:
            branches = []
            for branch in step.get("branches", []) or []:
                branch_copy = deepcopy(branch)
                branch_copy["steps"] = project_core_steps(branch.get("steps", []) or [], excluded)
                branches.append(branch_copy)
            copied["branches"] = branches

        has_steps = bool(copied.get("steps"))
        has_branch_steps = any(branch.get("steps") for branch in copied.get("branches", []) or [])
        return copied if has_steps or has_branch_steps else None

    raise ValueError(f"Unknown normalized step type for core projection: {step_type!r}")
