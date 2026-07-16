from __future__ import annotations

import argparse
import json
import os
import stat
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)
FULL_METADATA_FILENAME = "custom_jar_metadata_archive.json"


@dataclass
class RemoveCustomJarResult:
    path: str
    status: str
    error: str | None = None


def default_workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "A360-Assistant-Ops").exists() and (parent / "Test").exists():
            return parent
    raise RuntimeError(f"Could not locate workspace root from {current}")


def default_unpacked_dir() -> Path:
    return (
        default_workspace_root()
        / "Test"
        / "botstore_deep"
        / "selected_task1_candidates_unpacked"
        / "_by_original_3_categories"
    )


def is_custom_jar(path: Path) -> bool:
    return path.suffix.lower() == ".jar" and not path.name.startswith("bot-command-")


def remove_file_with_retries(path: Path, *, retries: int = 8, delay_seconds: float = 0.5) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
            return
        except FileNotFoundError:
            return
        except Exception as exc:  # noqa: BLE001 - Windows file locks can be transient.
            last_error = exc
            if attempt == retries:
                break
            time.sleep(delay_seconds * attempt)
    raise RuntimeError(f"Could not remove {path}: {last_error}")


def collect_custom_jars(root_dir: Path) -> list[Path]:
    jars: list[Path] = []
    for category in CATEGORY_DIRS:
        category_dir = root_dir / category
        if not category_dir.exists():
            continue
        jars.extend(sorted(path for path in category_dir.rglob("*.jar") if is_custom_jar(path)))
    return sorted(jars)


def load_metadata_paths(root_dir: Path) -> set[str]:
    metadata_path = root_dir / FULL_METADATA_FILENAME
    if not metadata_path.exists():
        raise SystemExit(f"Missing {metadata_path}. Run archive_custom_jar_metadata.py before deleting custom JARs.")
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    if payload.get("summary", {}).get("failed_jars"):
        raise SystemExit(f"Full metadata export has failed_jars > 0. Review {metadata_path} before deleting custom JARs.")
    return {row["path"] for row in payload.get("rows", [])}


def write_manifest(root_dir: Path, results: list[RemoveCustomJarResult], *, execute: bool) -> Path:
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "execute": execute,
        "required_metadata_file": str(root_dir / FULL_METADATA_FILENAME),
        "summary": {
            "total": len(results),
            "deleted": sum(1 for result in results if result.status == "deleted"),
            "dry_run": sum(1 for result in results if result.status == "dry-run"),
            "missing_metadata": sum(1 for result in results if result.status == "missing-metadata"),
            "failed": sum(1 for result in results if result.status == "failed"),
        },
        "results": [asdict(result) for result in results],
    }
    manifest_path = root_dir / ("custom_jar_cleanup_manifest.json" if execute else "custom_jar_cleanup_dry_run.json")
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove custom JAR binaries after full metadata export.")
    parser.add_argument("--root-dir", type=Path, default=default_unpacked_dir())
    parser.add_argument("--execute", action="store_true", help="Actually delete custom JAR files.")
    parser.add_argument("--force", action="store_true", help="Delete even if some current JAR paths are missing from metadata.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = args.root_dir.resolve()
    if not root_dir.exists():
        raise SystemExit(f"Root directory does not exist: {root_dir}")

    metadata_paths = load_metadata_paths(root_dir)
    jar_paths = collect_custom_jars(root_dir)
    results: list[RemoveCustomJarResult] = []

    for jar_path in jar_paths:
        relative_path = str(jar_path.relative_to(root_dir))
        if relative_path not in metadata_paths and not args.force:
            results.append(RemoveCustomJarResult(path=relative_path, status="missing-metadata"))
            continue
        if not args.execute:
            results.append(RemoveCustomJarResult(path=relative_path, status="dry-run"))
            continue
        try:
            remove_file_with_retries(jar_path)
            results.append(RemoveCustomJarResult(path=relative_path, status="deleted"))
        except Exception as exc:  # noqa: BLE001 - preserve per-file error and keep going.
            results.append(RemoveCustomJarResult(path=relative_path, status="failed", error=f"{type(exc).__name__}: {exc}"))

    manifest_path = write_manifest(root_dir, results, execute=args.execute)
    summary = json.loads(manifest_path.read_text(encoding="utf-8"))["summary"]
    print(json.dumps({"manifest": str(manifest_path), **summary}, ensure_ascii=False, indent=2))
    if any(result.status == "missing-metadata" for result in results):
        raise SystemExit("Some custom JARs were not present in full metadata. Review manifest or rerun with --force.")
    if not args.execute:
        print("Dry run only. Add --execute to delete custom JAR files.")


if __name__ == "__main__":
    main()
