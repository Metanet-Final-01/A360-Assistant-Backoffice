"""BFCL/RAGAS/Workflow 골드셋이 각자 하드코딩된 JSON 파일을 읽기만 했다 —
조회는 이미 각 runner.load_cases()로 있었고, 여기서는 추가(수동 입력)와
업로드(파일 교체) 두 가지 쓰기 동작만 공통으로 구현한다."""

import datetime
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class GoldsetWriteError(RuntimeError):
    """검증 실패 또는 중복 id — 그대로 HTTP 400으로 변환된다."""


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# 파일 하나당 락 하나 — 같은 골드셋 파일에 동시에 두 PATCH/추가/삭제 요청이 들어오면
# read-modify-write(읽기→수정→_write_raw)가 겹쳐서 나중 쓰기가 앞선 변경을 덮어쓸 수
# 있었다(CodeRabbit #42 지적, os.replace는 파일 파손만 막지 갱신 유실은 못 막음).
# threading.Lock으로 전체 구간을 직렬화한다 — FastAPI 동기 엔드포인트는 스레드풀에서
# 도니 이걸로 충분하다(단일 프로세스 배포 전제, 워커 여러 개면 프로세스 간에는 안 먹힘 —
# 이 도구는 JSON 파일을 저장소로 쓰는 소규모 관리용이라 그 정도까진 가정하지 않는다).
_file_locks: dict[Path, threading.Lock] = {}
_file_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    resolved = path.resolve()
    with _file_locks_guard:
        lock = _file_locks.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _file_locks[resolved] = lock
        return lock


def read_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_raw(path: Path, items: list[dict]) -> None:
    """임시 파일에 다 쓰고 fsync 후 원자적으로 교체 — 쓰는 도중 죽어도 기존 파일이
    반쯤 잘린 채로 남는 걸 방지한다(RAGAS 골드셋 승인/반려 UI 붙이면서 쓰기 빈도가
    늘어나 추가함)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(items, ensure_ascii=False, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def append_case(path: Path, model_cls: type[T], new_item: dict, id_field: str) -> T:
    # created_at/updated_at은 RagasCase에만 있는 필드다 — 그 필드가 없는 모델(BFCL/Workflow)
    # 로 넘어가면 pydantic이 조용히 무시한다(extra='forbid' 아님), 그래서 모델별 분기 없이
    # 여기서 공통으로 채워도 안전하다.
    now = _now_iso()
    new_item = {**new_item, "created_at": new_item.get("created_at") or now, "updated_at": now}
    try:
        validated = model_cls.model_validate(new_item)
    except ValidationError as e:
        raise GoldsetWriteError(f"스키마 검증 실패: {e}") from e
    new_id = getattr(validated, id_field)
    with _lock_for(path):
        items = read_raw(path)
        if any(item.get(id_field) == new_id for item in items):
            raise GoldsetWriteError(f"{id_field}={new_id!r}가 이미 있습니다")
        items.append(validated.model_dump())
        _write_raw(path, items)
    return validated


def update_case(path: Path, model_cls: type[T], id_field: str, case_id: str, patch: dict) -> T:
    """기존 케이스 하나를 부분 수정(merge)한다 — RAGAS 승인/반려(status)·수정 UI용.
    patch에 없는 필드는 기존 값을 유지한다. patch에 id_field가 있으면 거부한다 —
    URL의 대상 case_id와 어긋나는 값으로 식별자 자체가 바뀌면(예: 다른 케이스와
    id가 겹치는데도 검사 없이 저장) 골드셋이 조용히 깨진다(CodeRabbit #42 지적)."""
    if id_field in patch:
        raise GoldsetWriteError(f"{id_field}는 patch로 변경할 수 없습니다")
    with _lock_for(path):
        items = read_raw(path)
        idx = next((i for i, item in enumerate(items) if item.get(id_field) == case_id), None)
        if idx is None:
            raise GoldsetWriteError(f"{id_field}={case_id!r} 케이스를 찾을 수 없습니다")
        merged = {**items[idx], **patch, "updated_at": _now_iso()}
        try:
            validated = model_cls.model_validate(merged)
        except ValidationError as e:
            raise GoldsetWriteError(f"스키마 검증 실패: {e}") from e
        items[idx] = validated.model_dump()
        _write_raw(path, items)
    return validated


def delete_case(path: Path, id_field: str, case_id: str) -> bool:
    """id_field == case_id인 케이스 하나를 지운다. 실제로 지워졌으면 True."""
    with _lock_for(path):
        items = read_raw(path)
        remaining = [item for item in items if item.get(id_field) != case_id]
        if len(remaining) == len(items):
            return False
        _write_raw(path, remaining)
        return True


def replace_from_upload(path: Path, model_cls: type[T], raw_bytes: bytes) -> int:
    try:
        items = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise GoldsetWriteError(f"JSON 파싱 실패: {e}") from e
    if not isinstance(items, list):
        raise GoldsetWriteError("최상위가 배열([...])이어야 합니다")
    now = _now_iso()
    for item in items:
        if isinstance(item, dict):
            item.setdefault("created_at", now)
            item.setdefault("updated_at", now)
    try:
        validated = [model_cls.model_validate(item) for item in items]
    except ValidationError as e:
        raise GoldsetWriteError(f"스키마 검증 실패: {e}") from e
    with _lock_for(path):
        _write_raw(path, [v.model_dump() for v in validated])
    return len(validated)


def read_text_map(path: Path) -> dict[str, str]:
    """Workflow 입력 데이터셋(detailed_task_descriptions.json) 전용 — {source_bot: 원문}."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def upsert_text(path: Path, key: str, text: str) -> None:
    with _lock_for(path):
        data = read_text_map(path)
        data[key] = text
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_text_key(path: Path, key: str) -> bool:
    with _lock_for(path):
        data = read_text_map(path)
        if key not in data:
            return False
        del data[key]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True


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
