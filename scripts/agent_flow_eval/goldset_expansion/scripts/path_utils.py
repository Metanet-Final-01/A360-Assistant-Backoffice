"""goldset_expansion 스크립트들이 공유하는 워크스페이스 경로 탐색.

절대경로(C:\\Users\\...)를 하드코딩하지 않고, 이 파일 기준 부모 디렉터리를
올라가며 워크스페이스 루트(= A360-Assistant-Ops 와 Test 가 형제 폴더로 있는 곳)를
찾는다. scripts/agent_flow_eval/processing/run_full_470_normalize_extract.py의
default_workspace_root()와 동일한 규칙."""

from __future__ import annotations

from pathlib import Path


def workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "Test").exists():
            return parent
    raise RuntimeError(f"워크스페이스 루트를 찾지 못함 (기준: {current})")


def full_470_dataset_dir() -> Path:
    return workspace_root() / "Test" / "botstore_deep" / "full_470_dataset" / "full_470"


def goldset_expansion_dir() -> Path:
    """이 스크립트가 속한 repo 안의 goldset_expansion 폴더 (출력 저장 위치)."""
    return Path(__file__).resolve().parent.parent
