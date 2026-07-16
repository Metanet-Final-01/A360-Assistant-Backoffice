from __future__ import annotations

from typing import Protocol

from .schema import RagIngestScheduleRecord, RagIngestScheduleRequest, ScheduleApplyResult


class SchedulerProvider(Protocol):
    provider_name: str

    def upsert_schedule(self, request: RagIngestScheduleRequest, dry_run: bool = False) -> ScheduleApplyResult:
        ...

    def pause_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        ...

    def resume_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        ...

    def delete_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        ...

    def list_schedules(self) -> list[RagIngestScheduleRecord]:
        ...
