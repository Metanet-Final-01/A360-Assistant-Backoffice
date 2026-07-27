from __future__ import annotations

import json
import os

from .common import (
    EVENTBRIDGE_RETRY_POLICY,
    aws_region,
    build_state_update_payload,
    dry_run_result,
    json_safe,
    scheduler_identity,
)
from .schema import RagIngestScheduleRecord, RagIngestScheduleRequest, ScheduleApplyResult


class EventBridgeSqsSchedulerProvider:
    """EventBridge Scheduler provider that enqueues RAG ingest requests to SQS.

    This path is mainly useful for local/EC2 integration tests where rag-server
    stays bound to localhost. EventBridge can reach SQS; a consumer running next
    to rag-server polls SQS and calls the local HTTP endpoint.
    """

    provider_name = "eventbridge-sqs"

    def __init__(
        self,
        *,
        region_name: str | None = None,
        role_arn: str | None = None,
        group_name: str | None = None,
        queue_arn: str | None = None,
        queue_url: str | None = None,
    ):
        self.region_name = aws_region(region_name)
        self.role_arn = role_arn or os.getenv("RAG_SCHEDULER_ROLE_ARN", "")
        self.group_name = group_name or os.getenv("RAG_SCHEDULER_GROUP", "default")
        self.queue_arn = queue_arn or os.getenv("RAG_INGEST_SQS_QUEUE_ARN", "")
        self.queue_url = queue_url or os.getenv("RAG_INGEST_SQS_QUEUE_URL", "")

    def build_message_body(self, request: RagIngestScheduleRequest) -> dict:
        return {
            "type": "rag_ingest",
            "schedule_id": request.schedule_id,
            "option": request.option,
            "clean": request.clean,
        }

    def build_schedule_payload(self, request: RagIngestScheduleRequest) -> dict:
        queue_arn = request.sqs_queue_arn or self.queue_arn
        if not queue_arn:
            raise ValueError("RAG_INGEST_SQS_QUEUE_ARN or request.sqs_queue_arn is required")
        if not self.role_arn:
            raise ValueError("RAG_SCHEDULER_ROLE_ARN is required for EventBridge Scheduler")
        return {
            "Name": request.schedule_id,
            "GroupName": self.group_name,
            "ScheduleExpression": request.schedule_expression,
            "ScheduleExpressionTimezone": request.timezone,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "State": "ENABLED" if request.enabled else "DISABLED",
            "Description": request.description or f"A360 RAG ingest via SQS option={request.option} clean={request.clean}",
            "Target": {
                "Arn": queue_arn,
                "RoleArn": self.role_arn,
                "Input": json.dumps(self.build_message_body(request), ensure_ascii=False, separators=(",", ":")),
                "RetryPolicy": EVENTBRIDGE_RETRY_POLICY,
            },
        }

    def scheduler_client(self):
        import boto3

        return boto3.client("scheduler", region_name=self.region_name)

    def upsert_schedule(self, request: RagIngestScheduleRequest, dry_run: bool = False) -> ScheduleApplyResult:
        payload = self.build_schedule_payload(request)
        if dry_run:
            return dry_run_result(self.provider_name, request.schedule_id, payload)
        client = self.scheduler_client()
        try:
            response = client.update_schedule(**payload)
            status = "updated"
        except client.exceptions.ResourceNotFoundException:
            response = client.create_schedule(**payload)
            status = "created"
        return ScheduleApplyResult(
            status=status,
            schedule_id=request.schedule_id,
            provider=self.provider_name,
            response=json_safe(response),
        )

    def pause_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        return self.set_schedule_state(schedule_id, "DISABLED", dry_run)

    def resume_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        return self.set_schedule_state(schedule_id, "ENABLED", dry_run)

    def set_schedule_state(self, schedule_id: str, state: str, dry_run: bool) -> ScheduleApplyResult:
        payload = {**scheduler_identity(schedule_id, self.group_name), "State": state}
        if dry_run:
            return dry_run_result(self.provider_name, schedule_id, payload)
        client = self.scheduler_client()
        current = client.get_schedule(Name=schedule_id, GroupName=self.group_name)
        update_payload = build_state_update_payload(current, state)
        response = client.update_schedule(**update_payload)
        return ScheduleApplyResult(
            status="updated", schedule_id=schedule_id, provider=self.provider_name, response=json_safe(response)
        )

    def delete_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        payload = scheduler_identity(schedule_id, self.group_name)
        if dry_run:
            return dry_run_result(self.provider_name, schedule_id, payload)
        response = self.scheduler_client().delete_schedule(**payload)
        return ScheduleApplyResult(
            status="deleted", schedule_id=schedule_id, provider=self.provider_name, response=json_safe(response)
        )

    def list_schedules(self) -> list[RagIngestScheduleRecord]:
        return []
