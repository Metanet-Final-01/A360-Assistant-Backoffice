from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)


@dataclass
class UnpackResult:
    category: str
    zip_name: str
    output_dir: str
    status: str
    file_count: int = 0
    error: str | None = None


def remove_tree_with_retries(path: Path, *, retries: int = 8, delay_seconds: float = 0.5) -> None:
    if not path.exists():
        return

    def onexc(func, target, exc_info):  # noqa: ANN001 - shutil callback shape differs by Python version.
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            shutil.rmtree(path, onexc=onexc)
            return
        except TypeError:
            shutil.rmtree(path, onerror=lambda func, target, exc_info: onexc(func, target, exc_info))
            return
        except Exception as exc:  # noqa: BLE001 - Windows file locks are transient and heterogeneous.
            last_error = exc
            if attempt == retries:
                break
            time.sleep(delay_seconds * attempt)
    raise RuntimeError(f"Could not remove {path}: {last_error}")


def promote_tree_with_retries(source: Path, destination: Path, *, retries: int = 8, delay_seconds: float = 0.5) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            source.rename(destination)
            return
        except Exception as exc:  # noqa: BLE001 - os.rename can be blocked briefly on Windows.
            last_error = exc
            if attempt == retries:
                break
            time.sleep(delay_seconds * attempt)
    raise RuntimeError(f"Could not promote {source} to {destination}: {last_error}")


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "Test").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_source_dir() -> Path:
    return (
        default_workspace_root()
        / "Test"
        / "botstore_deep"
        / "selected_task1_candidates"
        / "_by_original_3_categories"
    )


def default_output_dir() -> Path:
    return (
        default_workspace_root()
        / "Test"
        / "botstore_deep"
        / "selected_task1_candidates_unpacked"
        / "_by_original_3_categories"
    )


def safe_extract(zip_path: Path, output_dir: Path) -> int:
    output_root = output_dir.resolve()
    file_count = 0
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (output_dir / member.filename).resolve()
            if output_root != target and output_root not in target.parents:
                raise ValueError(f"Unsafe zip member path: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            file_count += 1
    return file_count


def unpack_category(source_category: Path, output_category: Path, *, overwrite: bool, dry_run: bool) -> list[UnpackResult]:
    results: list[UnpackResult] = []
    zip_paths = sorted(source_category.glob("*.zip"))
    for index, zip_path in enumerate(zip_paths, 1):
        destination = output_category / zip_path.stem
        temp_destination = output_category / f"{zip_path.stem}.extracting"
        print(f"[{source_category.name} {index}/{len(zip_paths)}] {zip_path.name}", flush=True)
        if dry_run:
            results.append(
                UnpackResult(
                    category=source_category.name,
                    zip_name=zip_path.name,
                    output_dir=str(destination),
                    status="dry-run",
                )
            )
            continue

        if temp_destination.exists():
            remove_tree_with_retries(temp_destination)
        if destination.exists() and overwrite:
            remove_tree_with_retries(destination)
        elif destination.exists():
            print(f"  -> skipped, already exists: {destination}", flush=True)
            results.append(
                UnpackResult(
                    category=source_category.name,
                    zip_name=zip_path.name,
                    output_dir=str(destination),
                    status="skipped-exists",
                )
            )
            continue

        temp_destination.mkdir(parents=True, exist_ok=True)
        try:
            file_count = safe_extract(zip_path, temp_destination)
            promote_tree_with_retries(temp_destination, destination)
            print(f"  -> extracted {file_count} files", flush=True)
            results.append(
                UnpackResult(
                    category=source_category.name,
                    zip_name=zip_path.name,
                    output_dir=str(destination),
                    status="extracted",
                    file_count=file_count,
                )
            )
        except Exception as exc:  # noqa: BLE001 - report every bad archive without stopping the whole batch.
            print(f"  -> failed: {type(exc).__name__}: {exc}", flush=True)
            if temp_destination.exists():
                try:
                    remove_tree_with_retries(temp_destination)
                except Exception as cleanup_exc:  # noqa: BLE001 - keep the batch alive and report cleanup failure.
                    print(f"  -> cleanup deferred: {type(cleanup_exc).__name__}: {cleanup_exc}", flush=True)
            results.append(
                UnpackResult(
                    category=source_category.name,
                    zip_name=zip_path.name,
                    output_dir=str(destination),
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return results


def write_manifest(output_dir: Path, source_dir: Path, results: list[UnpackResult], *, dry_run: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "dry_run": dry_run,
        "category_dirs": list(CATEGORY_DIRS),
        "summary": {
            "total": len(results),
            "extracted": sum(1 for r in results if r.status == "extracted"),
            "skipped_exists": sum(1 for r in results if r.status == "skipped-exists"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "dry_run": sum(1 for r in results if r.status == "dry-run"),
        },
        "results": [asdict(result) for result in results],
    }
    manifest_path = output_dir / "unpack_manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unpack selected Bot Store ZIP candidates for goldset preparation.")
    parser.add_argument("--source-dir", type=Path, default=default_source_dir())
    parser.add_argument("--output-dir", type=Path, default=default_output_dir())
    parser.add_argument("--overwrite", action="store_true", help="Delete and re-extract existing unpacked folders.")
    parser.add_argument("--dry-run", action="store_true", help="List ZIPs that would be unpacked without extracting.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory does not exist: {source_dir}")

    all_results: list[UnpackResult] = []
    for category in CATEGORY_DIRS:
        source_category = source_dir / category
        if not source_category.exists():
            all_results.append(
                UnpackResult(
                    category=category,
                    zip_name="",
                    output_dir=str(output_dir / category),
                    status="failed",
                    error=f"Missing source category: {source_category}",
                )
            )
            continue
        output_category = output_dir / category
        all_results.extend(
            unpack_category(source_category, output_category, overwrite=args.overwrite, dry_run=args.dry_run)
        )

    manifest_path = write_manifest(output_dir, source_dir, all_results, dry_run=args.dry_run)
    summary = json.loads(manifest_path.read_text(encoding="utf-8"))["summary"]
    print(json.dumps({"manifest": str(manifest_path), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
