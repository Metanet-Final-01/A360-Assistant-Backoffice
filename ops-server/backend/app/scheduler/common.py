from __future__ import annotations

import json
import os
from typing import Any

from .schema import ScheduleApplyResult


DEFAULT_AWS_REGION = "ap-northeast-2"
EVENTBRIDGE_RETRY_POLICY = {
    "MaximumEventAgeInSeconds": 3600,
    "MaximumRetryAttempts": 2,
}


def aws_region(region_name: str | None = None) -> str:
    """Return the AWS region used by scheduler providers."""
    return region_name or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or DEFAULT_AWS_REGION


def compact_json(payload: dict) -> str:
    """Serialize JSON for EventBridge target input."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def json_safe(payload: Any) -> dict:
    """Convert AWS SDK responses into JSON-serializable dictionaries."""
    return json.loads(json.dumps(payload, default=str))


def dry_run_result(provider_name: str, schedule_id: str, payload: dict) -> ScheduleApplyResult:
    return ScheduleApplyResult(
        status="dry_run",
        schedule_id=schedule_id,
        provider=provider_name,
        dry_run=True,
        payload=payload,
    )


def scheduler_identity(schedule_id: str, group_name: str) -> dict:
    return {"Name": schedule_id, "GroupName": group_name}


def build_state_update_payload(current_schedule: dict, state: str) -> dict:
    """Keep the required EventBridge fields while changing only State."""
    required_fields = ("Name", "GroupName", "ScheduleExpression", "FlexibleTimeWindow", "Target")
    optional_fields = ("ScheduleExpressionTimezone", "Description", "StartDate", "EndDate")

    payload = {field: current_schedule[field] for field in required_fields if field in current_schedule}
    for field in optional_fields:
        if field in current_schedule:
            payload[field] = current_schedule[field]
    payload["State"] = state
    return payload
