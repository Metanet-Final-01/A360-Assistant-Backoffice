from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .schema import RagIngestScheduleRecord, RagIngestScheduleRequest, ScheduleApplyResult


DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "scheduler"
SCHEDULES_JSON = DATA_DIR / "rag_ingest_schedules.json"


class LocalSchedulerProvider:
    provider_name = "local"

    def load_schedule_records(self) -> dict[str, dict]:
        if not SCHEDULES_JSON.exists():
            return {}
        return json.loads(SCHEDULES_JSON.read_text(encoding="utf-8"))

    def save_schedule_records(self, records: dict[str, dict]) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SCHEDULES_JSON.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SCHEDULES_JSON)

    def upsert_schedule(self, request: RagIngestScheduleRequest, dry_run: bool = False) -> ScheduleApplyResult:
        record = RagIngestScheduleRecord(**request.model_dump(), provider=self.provider_name)
        payload = record.model_dump()
        if not dry_run:
            records = self.load_schedule_records()
            previous = records.get(request.schedule_id)
            if previous:
                record.created_at = previous.get("created_at", record.created_at)
                record.updated_at = datetime.now(timezone.utc).isoformat()
            records[request.schedule_id] = record.model_dump()
            self.save_schedule_records(records)
        return ScheduleApplyResult(
            status="stored" if not dry_run else "dry_run",
            schedule_id=request.schedule_id,
            provider=self.provider_name,
            dry_run=dry_run,
            payload=payload,
        )

    def pause_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        return self.set_schedule_enabled(schedule_id, False, dry_run)

    def resume_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        return self.set_schedule_enabled(schedule_id, True, dry_run)

    def set_schedule_enabled(self, schedule_id: str, enabled: bool, dry_run: bool) -> ScheduleApplyResult:
        records = self.load_schedule_records()
        if schedule_id not in records:
            return ScheduleApplyResult(status="not_found", schedule_id=schedule_id, provider=self.provider_name, dry_run=dry_run)
        payload = {**records[schedule_id], "enabled": enabled, "updated_at": datetime.now(timezone.utc).isoformat()}
        if not dry_run:
            records[schedule_id] = payload
            self.save_schedule_records(records)
        return ScheduleApplyResult(
            status="updated" if not dry_run else "dry_run",
            schedule_id=schedule_id,
            provider=self.provider_name,
            dry_run=dry_run,
            payload=payload,
        )

    def delete_schedule(self, schedule_id: str, dry_run: bool = False) -> ScheduleApplyResult:
        records = self.load_schedule_records()
        existed = schedule_id in records
        if existed and not dry_run:
            records.pop(schedule_id)
            self.save_schedule_records(records)
        return ScheduleApplyResult(
            status="deleted" if existed and not dry_run else "dry_run" if dry_run else "not_found",
            schedule_id=schedule_id,
            provider=self.provider_name,
            dry_run=dry_run,
        )

    def list_schedules(self) -> list[RagIngestScheduleRecord]:
        return [RagIngestScheduleRecord(**item) for item in self.load_schedule_records().values()]
