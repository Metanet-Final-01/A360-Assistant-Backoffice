"""BFCL/RAGAS/Workflow 골드셋이 각자 하드코딩된 JSON 파일을 읽기만 했다 —
조회는 이미 각 runner.load_cases()로 있었고, 여기서는 추가(수동 입력)와
업로드(파일 교체) 두 가지 쓰기 동작만 공통으로 구현한다."""

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class GoldsetWriteError(RuntimeError):
    """검증 실패 또는 중복 id — 그대로 HTTP 400으로 변환된다."""


def read_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_raw(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def append_case(path: Path, model_cls: type[T], new_item: dict, id_field: str) -> T:
    try:
        validated = model_cls.model_validate(new_item)
    except ValidationError as e:
        raise GoldsetWriteError(f"스키마 검증 실패: {e}") from e
    new_id = getattr(validated, id_field)
    items = read_raw(path)
    if any(item.get(id_field) == new_id for item in items):
        raise GoldsetWriteError(f"{id_field}={new_id!r}가 이미 있습니다")
    items.append(validated.model_dump())
    _write_raw(path, items)
    return validated


def replace_from_upload(path: Path, model_cls: type[T], raw_bytes: bytes) -> int:
    try:
        items = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise GoldsetWriteError(f"JSON 파싱 실패: {e}") from e
    if not isinstance(items, list):
        raise GoldsetWriteError("최상위가 배열([...])이어야 합니다")
    try:
        validated = [model_cls.model_validate(item) for item in items]
    except ValidationError as e:
        raise GoldsetWriteError(f"스키마 검증 실패: {e}") from e
    _write_raw(path, [v.model_dump() for v in validated])
    return len(validated)


def read_text_map(path: Path) -> dict[str, str]:
    """Workflow 입력 데이터셋(detailed_task_descriptions.json) 전용 — {source_bot: 원문}."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def upsert_text(path: Path, key: str, text: str) -> None:
    data = read_text_map(path)
    data[key] = text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def replace_text_map_from_upload(path: Path, raw_bytes: bytes) -> int:
    try:
        data = json.loads(raw_bytes.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise GoldsetWriteError(f"JSON 파싱 실패: {e}") from e
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise GoldsetWriteError('최상위가 {"source_bot": "업무정의서 원문"} 형태의 객체여야 합니다')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(data)
