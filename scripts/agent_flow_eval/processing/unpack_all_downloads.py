"""Test/botstore_deep/downloads/의 원본 470개 Bot Store 다운로드(.zip만, .msi/기타 확장자는
제외) 전체를 flat하게 unpack한다. unpack_selected_zips.py는 98개 제목기반 숏리스트의
3-카테고리 폴더 구조(01_.../02_.../03_...)를 전제로 하지만, 여기서는 그 사전 필터링 없이
470개 원본 전체를 대상으로 전수 감사하기 위한 것이라 카테고리 개념이 없다. zip bomb 방지
로직(safe_extract)은 그대로 재사용한다."""

from __future__ import annotations

import argparse
import json
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from unpack_selected_zips import (
    MAX_ZIP_ENTRIES,
    MAX_ZIP_ENTRY_BYTES,
    MAX_ZIP_TOTAL_BYTES,
    MAX_COMPRESSION_RATIO,
    promote_tree_with_retries,
    remove_tree_with_retries,
    replace_tree_after_extract,
    safe_extract,
)


def safe_extract_skip_oversized(zip_path: Path, output_dir: Path) -> tuple[int, list[str]]:
    """safe_extract와 동일한 zip-bomb 방지 로직이지만, 개별 엔트리가 너무 크면(예:
    bot-command-recorder-*.jar 같은 실제 워크플로우와 무관한 런타임 번들) zip 전체를
    포기하는 대신 그 엔트리만 건너뛴다 -- 워크플로우 JSON은 항상 그런 jar보다 훨씬
    작으니 이 정책으로 잃는 게 없다."""
    output_root = output_dir.resolve()
    file_count = 0
    total_uncompressed = 0
    skipped: list[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_ENTRIES:
            raise ValueError(f"Too many zip entries: {len(members)} > {MAX_ZIP_ENTRIES}")
        for member in members:
            target = (output_dir / member.filename).resolve()
            if output_root != target and output_root not in target.parents:
                raise ValueError(f"Unsafe zip member path: {member.filename}")
            if member.file_size > MAX_ZIP_ENTRY_BYTES:
                skipped.append(f"{member.filename} ({member.file_size} bytes)")
                continue
            if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                skipped.append(f"{member.filename} (compression ratio too high)")
                continue
            total_uncompressed += member.file_size
            if total_uncompressed > MAX_ZIP_TOTAL_BYTES:
                raise ValueError(f"Zip archive too large after decompression: {total_uncompressed} bytes")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                written = 0
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > member.file_size or written > MAX_ZIP_ENTRY_BYTES:
                        raise ValueError(f"Zip member exceeded declared or allowed size: {member.filename}")
                    dst.write(chunk)
            file_count += 1
    return file_count, skipped


@dataclass
class UnpackResult:
    zip_name: str
    output_dir: str
    status: str
    file_count: int = 0
    error: str | None = None


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "Test").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_source_dir() -> Path:
    return default_workspace_root() / "Test" / "botstore_deep" / "downloads"


def default_output_dir() -> Path:
    return default_workspace_root() / "Test" / "botstore_deep" / "full_470_unpacked"


def unpack_all(source_dir: Path, output_dir: Path, *, overwrite: bool, dry_run: bool) -> list[UnpackResult]:
    results: list[UnpackResult] = []
    zip_paths = sorted(source_dir.glob("*.zip"))
    for index, zip_path in enumerate(zip_paths, 1):
        destination = output_dir / zip_path.stem
        temp_destination = output_dir / f"{zip_path.stem}.extracting"
        print(f"[{index}/{len(zip_paths)}] {zip_path.name}", flush=True)

        if dry_run:
            results.append(UnpackResult(zip_path.name, str(destination), "dry-run"))
            continue

        if temp_destination.exists():
            remove_tree_with_retries(temp_destination)
        if destination.exists() and not overwrite:
            print(f"  -> skipped, already exists: {destination}", flush=True)
            results.append(UnpackResult(zip_path.name, str(destination), "skipped-exists"))
            continue

        temp_destination.mkdir(parents=True, exist_ok=True)
        try:
            file_count = safe_extract(zip_path, temp_destination)
            if destination.exists() and overwrite:
                replace_tree_after_extract(temp_destination, destination)
            else:
                promote_tree_with_retries(temp_destination, destination)
            print(f"  -> extracted {file_count} files", flush=True)
            results.append(UnpackResult(zip_path.name, str(destination), "extracted", file_count))
        except Exception as exc:  # noqa: BLE001 - report every bad archive without stopping the whole batch.
            print(f"  -> failed: {type(exc).__name__}: {exc}", flush=True)
            if temp_destination.exists():
                try:
                    remove_tree_with_retries(temp_destination)
                except Exception as cleanup_exc:  # noqa: BLE001
                    print(f"  -> cleanup deferred: {type(cleanup_exc).__name__}: {cleanup_exc}", flush=True)
            results.append(UnpackResult(zip_path.name, str(destination), "failed", error=f"{type(exc).__name__}: {exc}"))
    return results


def write_manifest(output_dir: Path, source_dir: Path, results: list[UnpackResult], *, dry_run: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "dry_run": dry_run,
        "summary": {
            "total": len(results),
            "extracted": sum(1 for r in results if r.status == "extracted"),
            "skipped_exists": sum(1 for r in results if r.status == "skipped-exists"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "dry_run": sum(1 for r in results if r.status == "dry-run"),
        },
        "results": [asdict(r) for r in results],
    }
    manifest_path = output_dir / "unpack_manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unpack all raw Bot Store downloads (flat, no category pre-filter).")
    parser.add_argument("--source-dir", type=Path, default=default_source_dir())
    parser.add_argument("--output-dir", type=Path, default=default_output_dir())
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory does not exist: {source_dir}")

    results = unpack_all(source_dir, output_dir, overwrite=args.overwrite, dry_run=args.dry_run)
    manifest_path = write_manifest(output_dir, source_dir, results, dry_run=args.dry_run)
    summary = json.loads(manifest_path.read_text(encoding="utf-8"))["summary"]
    print(json.dumps({"manifest": str(manifest_path), **summary}, ensure_ascii=False, indent=2))
    if summary.get("failed", 0) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
