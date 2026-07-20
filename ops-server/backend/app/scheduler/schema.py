from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class RagIngestScheduleRequest(BaseModel):
    """Ops-level schedule definition for periodic rag-server ingest."""

    schedule_id: str = Field(..., pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    schedule_expression: str = Field(..., examples=["cron(0 3 * * ? *)", "rate(24 hours)"])
    option: int = Field(3, ge=1, le=3)
    clean: bool = False
    enabled: bool = True
    timezone: str = "Asia/Seoul"
    description: str = ""

    # SSM target. Prefer instance tags in production so EC2 replacement does not
    # require editing the schedule.
    instance_ids: list[str] = Field(default_factory=list)
    target_tag_key: str | None = "Role"
    target_tag_value: str | None = "rag-server"

    # SQS target. Useful for local integration tests: EventBridge Scheduler sends
    # a message to SQS, and a local/EC2 consumer polls it then calls localhost
    # rag-server.
    sqs_queue_url: str | None = None
    sqs_queue_arn: str | None = None

    @field_validator("schedule_expression")
    @classmethod
    def validate_schedule_expression(cls, value: str) -> str:
        if not value.startswith(("cron(", "rate(", "at(")):
            raise ValueError("schedule_expression must start with cron(...), rate(...), or at(...)")
        return value

    @model_validator(mode="after")
    def validate_target(self):
        if self.sqs_queue_url:
            return self
        if not self.instance_ids and not (self.target_tag_key and self.target_tag_value):
            raise ValueError("set either instance_ids or target_tag_key/target_tag_value")
        return self


class RagIngestScheduleRecord(RagIngestScheduleRequest):
    provider: Literal["local", "eventbridge-ssm", "eventbridge-sqs"] = "local"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provider_ref: dict = Field(default_factory=dict)


class ScheduleApplyResult(BaseModel):
    status: str
    schedule_id: str
    provider: str
    dry_run: bool = False
    payload: dict | None = None
    response: dict | None = None
