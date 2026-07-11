from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class EvaluationDataset(BaseModel):
    dataset_id: str = Field(min_length=1, description="데이터셋 고유 id")
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str | None = None
    case_ids: list[str] = Field(min_length=1)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("dataset_id", "name", "version")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("값은 비어 있을 수 없습니다")
        return value

    @field_validator("case_ids")
    @classmethod
    def normalize_cases(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("case_id가 최소 한 개 필요합니다")
        return normalized
