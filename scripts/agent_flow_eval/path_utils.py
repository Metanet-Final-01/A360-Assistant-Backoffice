from __future__ import annotations

from pathlib import Path, PurePath


def safe_path_component(value: str, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    candidate = PurePath(text)
    if candidate.is_absolute() or len(candidate.parts) != 1 or text in {".", ".."}:
        raise ValueError(f"{field} must be a single relative path component: {value!r}")
    if any(separator in text for separator in ("/", "\\")) or ".." in candidate.parts:
        raise ValueError(f"{field} must not contain path traversal or separators: {value!r}")
    return text


def ensure_child_path(root: Path, path: Path, *, field: str = "path") -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"{field} escapes {resolved_root}: {resolved_path}")
    return resolved_path
