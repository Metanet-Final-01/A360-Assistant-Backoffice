"""RAG ingest schedule facade.

Ops owns the schedule UI/API, while the execution engine can be swapped:

- local: JSON-backed dry/local development provider
- eventbridge-ssm: EventBridge Scheduler -> SSM SendCommand -> EC2 localhost rag-server
"""

from __future__ import annotations

import os

import httpx

from .eventbridge_sqs import EventBridgeSqsSchedulerProvider
from .eventbridge_ssm import EventBridgeSsmSchedulerProvider
from .local_provider import LocalSchedulerProvider
from .providers import SchedulerProvider
from .schema import RagIngestScheduleRequest, ScheduleApplyResult

RAG_SERVER_URL = (os.getenv("RAG_SERVER_URL") or "http://localhost:8200").rstrip("/")
SCHEDULE_INTERVAL_MINUTES = int(os.getenv("RAG_INGEST_INTERVAL_MINUTES", "1440"))
INGEST_OPTION = int(os.getenv("RAG_INGEST_OPTION", "1"))
SCHEDULER_PROVIDER = (os.getenv("RAG_SCHEDULER_PROVIDER") or "local").strip().lower()


def get_provider(name: str | None = None) -> SchedulerProvider:
    provider = (name or SCHEDULER_PROVIDER).strip().lower()
    if provider in {"local", "dry-run", "dryrun"}:
        return LocalSchedulerProvider()
    if provider in {"eventbridge-ssm", "aws", "eventbridge"}:
        return EventBridgeSsmSchedulerProvider()
    if provider in {"eventbridge-sqs", "sqs"}:
        return EventBridgeSqsSchedulerProvider()
    raise ValueError(f"Unknown RAG scheduler provider: {provider}")


def default_schedule_request() -> RagIngestScheduleRequest:
    return RagIngestScheduleRequest(
        schedule_id="a360-rag-ingest-daily",
        schedule_expression=f"rate({SCHEDULE_INTERVAL_MINUTES} minutes)",
        option=INGEST_OPTION,
        clean=False,
        description="Periodic A360 RAG ingest trigger",
    )


def upsert_schedule(
    request: RagIngestScheduleRequest | None = None,
    *,
    provider_name: str | None = None,
    dry_run: bool = False,
) -> ScheduleApplyResult:
    provider = get_provider(provider_name)
    return provider.upsert_schedule(request or default_schedule_request(), dry_run=dry_run)


def pause_schedule(schedule_id: str, *, provider_name: str | None = None, dry_run: bool = False) -> ScheduleApplyResult:
    return get_provider(provider_name).pause_schedule(schedule_id, dry_run=dry_run)


def resume_schedule(schedule_id: str, *, provider_name: str | None = None, dry_run: bool = False) -> ScheduleApplyResult:
    return get_provider(provider_name).resume_schedule(schedule_id, dry_run=dry_run)


def delete_schedule(schedule_id: str, *, provider_name: str | None = None, dry_run: bool = False) -> ScheduleApplyResult:
    return get_provider(provider_name).delete_schedule(schedule_id, dry_run=dry_run)


def trigger_rag_ingest(option: int = INGEST_OPTION, clean: bool = False) -> dict:
    """Manual trigger path used by Ops UI or one-off checks."""
    resp = httpx.post(
        f"{RAG_SERVER_URL}/rag/ingest",
        params={"option": option, "clean": clean},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def start() -> None:
    """No in-process scheduler by design.

    Production scheduling should be EventBridge Scheduler or another external
    engine. This function remains for backward imports and intentionally does
    not spawn APScheduler inside ops-server.
    """
    return None
