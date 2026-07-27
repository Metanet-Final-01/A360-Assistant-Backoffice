"""UTC 기반 운영 시각을 한국 표준시로 표시하는 공용 도우미."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9), "KST")


def as_kst(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST)


def format_kst(value: object, *, fallback: str = "-") -> str:
    try:
        if pd.isna(value):
            return fallback
    except (TypeError, ValueError):
        pass
    parsed = as_kst(value)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d %H:%M:%S KST")
    if value is None or value == "":
        return fallback
    return str(value)


def to_kst_naive_series(series: pd.Series) -> pd.Series:
    """Altair와 날짜 필터가 재변환하지 않도록 timezone 없는 KST 시각을 반환한다."""
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    return parsed.dt.tz_convert(KST).dt.tz_localize(None)


def format_kst_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    view = df.copy()
    for column in columns:
        if column in view.columns:
            view[column] = view[column].map(format_kst)
    return view
