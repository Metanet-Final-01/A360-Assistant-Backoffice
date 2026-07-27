"""k6 실행 결과 저장 — eval/log_store.py와 같은 append-only JSONL 방식."""

from pathlib import Path

from .schema import LoadTestRunRecord

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "loadtest_runs.jsonl"


def append_run(record: LoadTestRunRecord) -> LoadTestRunRecord:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")
    return record


def load_runs(label: str | None = None, limit: int = 50) -> list[LoadTestRunRecord]:
    if not LOG_PATH.exists():
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        records = [LoadTestRunRecord.model_validate_json(line) for line in f if line.strip()]
    if label:
        records = [r for r in records if r.label == label]
    records.sort(key=lambda r: r.created_at, reverse=True)
    return records[:limit]
