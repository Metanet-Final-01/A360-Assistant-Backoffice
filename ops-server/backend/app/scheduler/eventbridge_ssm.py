from __future__ import annotations

import os

from .common import (
    EVENTBRIDGE_RETRY_POLICY,
    aws_region,
    build_state_update_payload,
    compact_json,
    dry_run_result,
    json_safe,
    scheduler_identity,
)
from .schema import RagIngestScheduleRecord, RagIngestScheduleRequest, ScheduleApplyResult


DEFAULT_RAG_INGEST_URL = "http://127.0.0.1:8200/rag/ingest"


class EventBridgeSsmSchedulerProvider:
    """EventBridge Scheduler provider that triggers EC2-local rag-server via SSM.

    Scheduler invokes the AWS SDK target `ssm:sendCommand`; SSM then runs curl on
    the EC2 instance, so rag-server can stay bound to localhost.
    """

    provider_name = "eventbridge-ssm"

    def __init__(
        self,
        *,
        region_name: str | None = None,
        role_arn: str | None = None,
        group_name: str | None = None,
        document_name: str | None = None,
        rag_ingest_url: str | None = None,
    ):
        self.region_name = aws_region(region_name)
        self.role_arn = role_arn or os.getenv("RAG_SCHEDULER_ROLE_ARN", "")
        self.group_name = group_name or os.getenv("RAG_SCHEDULER_GROUP", "default")
        self.document_name = document_name or os.getenv("RAG_SCHEDULER_SSM_DOCUMENT", "AWS-RunShellScript")
        self.rag_ingest_url = (rag_ingest_url or os.getenv("RAG_INGEST_URL") or DEFAULT_RAG_INGEST_URL).rstrip("/")

    def build_ingest_command(self, request: RagIngestScheduleRequest) -> str:
        clean = "true" if request.clean else "false"
        return f"curl -fsS -X POST '{self.rag_ingest_url}?option={request.option}&clean={clean}'"

    def build_ssm_send_command_input(self, request: RagIngestScheduleRequest) -> dict:
        payload: dict = {
            "DocumentName": self.document_name,
            "Parameters": {
                "commands": [self.build_ingest_command(request)],
                "executionTimeout": ["600"],
            },
            "Comment": request.description or f"Trigger A360 RAG ingest schedule={request.schedule_id}",
        }
        if request.instance_ids:
            payload["InstanceIds"] = request.instance_ids
        else:
            payload["Targets"] = [{"Key": f"tag:{request.target_tag_key}", "Values": [request.target_tag_value]}]
        return payload

    def build_schedule_payload(self, request: RagIngestScheduleRequest) -> dict:
        if not self.role_arn:
            raise ValueError("RAG_SCHEDULER_ROLE_ARN is required for EventBridge Scheduler")
        return {
            "Name": request.schedule_id,
            "GroupName": self.group_name,
            "ScheduleExpression": request.schedule_expression,
            "ScheduleExpressionTimezone": request.timezone,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "State": "ENABLED" if request.enabled else "DISABLED",
            "Description": request.description or f"A360 RAG ingest option={request.option} clean={request.clean}",
            "Target": {
                "Arn": "arn:aws:scheduler:::aws-sdk:ssm:sendCommand",
                "RoleArn": self.role_arn,
                "Input": compact_json(self.build_ssm_send_command_input(request)),
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
        # Ops keeps the business definition locally; EventBridge list results are
        # provider state, not enough to reconstruct target option/clean safely.
        return []
