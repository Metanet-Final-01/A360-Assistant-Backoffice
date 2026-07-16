from __future__ import annotations

import re


BROWSER_SESSION_PACKAGES_RE = re.compile(r"^(web\s*automation|webautomation|browser|recorder)$", re.IGNORECASE)
SESSION_ACTION_RE = re.compile(r"session", re.IGNORECASE)


def action_label(package: str | None, action: str | None) -> str:
    return f"{package or ''}.{action or ''}".strip(".")


def is_browser_session_lifecycle_action(package: str | None, action: str | None) -> bool:
    """Browser/WebAutomation session lifecycle disappeared in the newer Browser model.

    Keep the rule package-scoped so unrelated session actions such as XML.startSession
    still score normally.
    """
    return bool(BROWSER_SESSION_PACKAGES_RE.match(package or "") and SESSION_ACTION_RE.search(action or ""))
