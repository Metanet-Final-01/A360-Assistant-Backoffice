"""관측 DB(원격 Postgres)와 로컬 eval_runs.jsonl에서 EDA에 쓸 데이터를 가져온다.

이 스크립트가 읽는 원격 테이블은 A360-Assistant-Backend가 소유·기록하는
관측 DB(OBSERVABILITY_DATABASE_URL)에 있다 — 여기서는 읽기만 한다.
"""

import json
import os
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv

# ops-server/backend/.env에 OBSERVABILITY_DATABASE_URL이 있다.
_ENV_PATH = Path(__file__).resolve().parents[2] / "ops-server" / "backend" / ".env"
_EVAL_RUNS_PATH = Path(__file__).resolve().parents[2] / "ops-server" / "backend" / "data" / "eval_runs.jsonl"


def _observability_dsn() -> str:
    load_dotenv(_ENV_PATH)
    raw_url = os.environ["OBSERVABILITY_DATABASE_URL"]
    return raw_url.replace("postgresql+psycopg://", "postgresql://")


def fetch_table(table_name: str) -> pd.DataFrame:
    """관측 DB 테이블 하나를 통째로 pandas DataFrame으로 가져온다."""
    with psycopg.connect(_observability_dsn()) as connection:
        return pd.read_sql(f"SELECT * FROM {table_name}", connection)  # noqa: S608 - table_name은 코드에서 고정값만 전달


def fetch_llm_usage() -> pd.DataFrame:
    return fetch_table("llm_usage")


def fetch_rag_events() -> pd.DataFrame:
    return fetch_table("rag_events")


def fetch_turn_events() -> pd.DataFrame:
    return fetch_table("turn_events")


def fetch_request_metrics() -> pd.DataFrame:
    return fetch_table("request_metrics")


def fetch_audit_logs() -> pd.DataFrame:
    return fetch_table("audit_logs")


def load_eval_runs() -> pd.DataFrame:
    """로컬 ops-server 평가 결과(RAGAS/pm4py/WorFBench/chunk 실험 등)를 하나의
    평평한 DataFrame으로 만든다. metrics는 [{"name":..,"value":..}] 목록이라
    행마다 컬럼으로 펼친다(pivot)."""
    if not _EVAL_RUNS_PATH.exists():
        return pd.DataFrame()

    records = []
    with open(_EVAL_RUNS_PATH, encoding="utf-8") as eval_runs_file:
        for line in eval_runs_file:
            if line.strip():
                records.append(json.loads(line))

    rows = []
    for record in records:
        row = {
            "run_id": record.get("run_id"),
            "evaluation_id": record.get("evaluation_id"),
            "case_id": record.get("case_id"),
            "source": record.get("source"),
            "agent_label": record.get("agent_label"),
            "logged_at": record.get("logged_at"),
            "score": record.get("score"),
            "passed": record.get("passed"),
        }
        for metric in record.get("metrics", []):
            row[f"metric__{metric['name']}"] = metric["value"]
        rows.append(row)

    return pd.DataFrame(rows)
