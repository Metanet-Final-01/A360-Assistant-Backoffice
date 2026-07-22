"""액션 동치 규칙 저장/조회/승인 처리.

goldset_admin.py의 범용 읽기/쓰기 함수(read_raw/append_case/update_case)를
그대로 쓰고, 여기서는 이 데이터만의 특별한 규칙 세 가지를 추가한다:

1. 충돌 검사 — 같은 액션 이름이 서로 다른 규칙 두 개에 동시에 속하면 안 된다.
2. 승인하려면 근거(evidence)가 최소 1개 있어야 한다.
3. 규칙이 바뀔 때마다 "누가 언제 무엇을 바꿨는지"를 별도 로그 파일에 남기고,
   전체 규칙 세트의 버전 번호를 하나 올린다.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .. import goldset_admin
from .action_rules_schema import ActionEquivalenceRule

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
RULES_PATH = _DATA_DIR / "action_equivalence_rules.json"
RULE_EVENTS_PATH = _DATA_DIR / "action_equivalence_rule_events.jsonl"
RULE_STATE_PATH = _DATA_DIR / "action_equivalence_ruleset_state.json"

_ACTIVE_STATUSES = ("draft", "approved")  # 충돌 검사 대상 — 아직 살아있는 규칙들


class RuleConflictError(goldset_admin.GoldsetWriteError):
    """이미 다른 규칙이 쓰고 있는 액션 이름을 또 등록하려고 할 때."""


def list_rules() -> list[dict]:
    return goldset_admin.read_raw(RULES_PATH)


def get_rule(rule_id: str) -> dict | None:
    return next((rule for rule in list_rules() if rule.get("rule_id") == rule_id), None)


def get_current_ruleset_version() -> int:
    if not RULE_STATE_PATH.exists():
        return 0
    state = json.loads(RULE_STATE_PATH.read_text(encoding="utf-8"))
    return state.get("current_version", 0)


def _bump_ruleset_version() -> int:
    next_version = get_current_ruleset_version() + 1
    RULE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RULE_STATE_PATH.write_text(json.dumps({"current_version": next_version}), encoding="utf-8")
    return next_version


def _append_event(event_type: str, rule_id: str, actor: str, detail: dict, new_version: int) -> None:
    event = {
        "event_type": event_type,  # "created" | "status_changed" | "fields_updated"
        "rule_id": rule_id,
        "actor": actor or "(익명)",
        "detail": detail,
        "ruleset_version": new_version,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    RULE_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RULE_EVENTS_PATH, "a", encoding="utf-8") as events_file:
        events_file.write(json.dumps(event, ensure_ascii=False) + "\n")


def list_events(limit: int = 200) -> list[dict]:
    if not RULE_EVENTS_PATH.exists():
        return []
    lines = RULE_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    return list(reversed(events))[:limit]


def find_conflicting_members(members: list[str], exclude_rule_id: str | None = None) -> list[dict]:
    """members 중에서 이미 다른 규칙(draft 또는 approved)이 쓰고 있는 액션 이름을 찾는다."""
    conflicts: list[dict] = []
    candidate_members = set(members)

    for existing_rule in list_rules():
        if existing_rule.get("rule_id") == exclude_rule_id:
            continue
        if existing_rule.get("status") not in _ACTIVE_STATUSES:
            continue

        overlapping_members = candidate_members & set(existing_rule.get("members", []))
        for member in overlapping_members:
            conflicts.append({
                "member": member,
                "conflicting_rule_id": existing_rule["rule_id"],
                "conflicting_canonical": existing_rule.get("canonical"),
            })

    return conflicts


def create_rule(payload: dict, actor: str) -> ActionEquivalenceRule:
    if not (payload.get("rationale") or "").strip():
        raise goldset_admin.GoldsetWriteError("근거 설명(rationale)을 입력하세요")

    conflicts = find_conflicting_members(payload.get("members", []))
    if conflicts:
        raise RuleConflictError(f"이미 다른 규칙이 쓰고 있는 액션이 있습니다: {conflicts}")

    new_version = _bump_ruleset_version()
    complete_payload = {
        **payload,
        "rule_id": payload.get("rule_id") or uuid4().hex[:12],
        "status": "draft",  # 새 규칙은 항상 draft로 시작 — 승인은 별도 동작
        "created_by": actor or "(익명)",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ruleset_version": new_version,
    }

    created_rule = goldset_admin.append_case(RULES_PATH, ActionEquivalenceRule, complete_payload, "rule_id")
    _append_event("created", created_rule.rule_id, actor, {"canonical": created_rule.canonical}, new_version)
    return created_rule


def update_rule_fields(rule_id: str, patch: dict, actor: str) -> ActionEquivalenceRule:
    """canonical/members/rationale/evidence 수정. status는 여기서 안 바꾼다
    (승인/폐기는 의미가 다른 동작이라 update_rule_status로 따로 뺐다)."""
    if "status" in patch:
        raise goldset_admin.GoldsetWriteError("status 변경은 이 함수가 아니라 승인/폐기 동작으로 처리하세요")

    if "members" in patch:
        conflicts = find_conflicting_members(patch["members"], exclude_rule_id=rule_id)
        if conflicts:
            raise RuleConflictError(f"이미 다른 규칙이 쓰고 있는 액션이 있습니다: {conflicts}")

    new_version = _bump_ruleset_version()
    updated_rule = goldset_admin.update_case(
        RULES_PATH, ActionEquivalenceRule, "rule_id", rule_id, {**patch, "ruleset_version": new_version},
    )
    _append_event("fields_updated", rule_id, actor, {"changed_fields": list(patch.keys())}, new_version)
    return updated_rule


def update_rule_status(rule_id: str, new_status: str, actor: str, note: str = "") -> ActionEquivalenceRule:
    existing_rule = get_rule(rule_id)
    if existing_rule is None:
        raise goldset_admin.GoldsetWriteError(f"rule_id={rule_id!r} 규칙을 찾을 수 없습니다")

    if new_status == "approved":
        has_evidence = len(existing_rule.get("evidence") or []) > 0
        if not has_evidence:
            raise goldset_admin.GoldsetWriteError("승인하려면 근거(evidence)가 최소 1개 있어야 합니다")

    patch = {"status": new_status}
    if new_status == "approved":
        patch["approved_by"] = actor or "(익명)"
        patch["approved_at"] = datetime.now(timezone.utc).isoformat()

    new_version = _bump_ruleset_version()
    patch["ruleset_version"] = new_version
    updated_rule = goldset_admin.update_case(RULES_PATH, ActionEquivalenceRule, "rule_id", rule_id, patch)
    _append_event("status_changed", rule_id, actor, {"new_status": new_status, "note": note}, new_version)
    return updated_rule
