"""평가 결과 로그 저장/조회. JSONL append-only — 별도 DB 없이 파일 하나로 충분히
가볍고, grep/cat으로도 직접 들여다볼 수 있다.
"""

import json
import uuid
from pathlib import Path

from .log_schema import EvalRunRecord

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "eval_runs.jsonl"


def append_run(record: EvalRunRecord) -> EvalRunRecord:
    if not record.run_id:
        record = record.model_copy(update={"run_id": uuid.uuid4().hex[:12]})
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
    return record


def load_runs(
    case_id: str | None = None,
    source: str | None = None,
    agent_label: str | None = None,
) -> list[EvalRunRecord]:
    if not LOG_PATH.exists():
        return []
    records = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = EvalRunRecord.model_validate_json(line)
            if case_id and record.case_id != case_id:
                continue
            if source and record.source != source:
                continue
            if agent_label and record.agent_label != agent_label:
                continue
            records.append(record)
    return records


def get_run(run_id: str) -> EvalRunRecord | None:
    for record in load_runs():
        if record.run_id == run_id:
            return record
    return None
