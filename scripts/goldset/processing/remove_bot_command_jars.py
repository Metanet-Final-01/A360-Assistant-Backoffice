from __future__ import annotations

import argparse
import json
import os
import re
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

BOT_COMMAND_JAR_PATTERN = "bot-command-*.jar"
BOT_COMMAND_JAR_RE = re.compile(r"bot-command-[A-Za-z0-9_.-]+\.jar")


@dataclass
class BotJarCheck:
    category: str
    bot_dir: str
    actual_jars: int
    manifest_refs: int
    deleted_jars: int = 0
    status: str = "pending"
    missing_from_manifest: list[str] | None = None
    extra_manifest_refs: list[str] | None = None
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


def read_manifest_jar_refs(bot_dir: Path) -> set[str]:
    refs: set[str] = set()
    for manifest_path in bot_dir.rglob("manifest.json"):
        try:
            text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        refs.update(BOT_COMMAND_JAR_RE.findall(text))
    return refs


def scan_bot(bot_dir: Path, category_dir: Path, root_dir: Path) -> tuple[BotJarCheck, list[Path]]:
    jar_paths = sorted(bot_dir.rglob(BOT_COMMAND_JAR_PATTERN))
    actual_names = {path.name for path in jar_paths}
    manifest_refs = read_manifest_jar_refs(bot_dir)
    missing_from_manifest = sorted(actual_names - manifest_refs)
    extra_manifest_refs = sorted(manifest_refs - actual_names)
    if not actual_names and manifest_refs:
        status = "already-removed"
    elif not missing_from_manifest and not extra_manifest_refs:
        status = "ok"
    else:
        status = "mismatch"
    return (
        BotJarCheck(
            category=category_dir.name,
            bot_dir=str(bot_dir.relative_to(root_dir)),
            actual_jars=len(actual_names),
            manifest_refs=len(manifest_refs),
            status=status,
            missing_from_manifest=missing_from_manifest,
            extra_manifest_refs=extra_manifest_refs,
        ),
        jar_paths,
    )


def write_cleanup_manifest(root_dir: Path, checks: list[BotJarCheck], *, execute: bool) -> Path:
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "execute": execute,
        "category_dirs": list(CATEGORY_DIRS),
        "summary": {
            "bots": len(checks),
            "actual_bot_command_jars": sum(check.actual_jars for check in checks),
            "manifest_refs": sum(check.manifest_refs for check in checks),
            "deleted_jars": sum(check.deleted_jars for check in checks),
            "mismatched_bots": sum(1 for check in checks if check.status == "mismatch"),
            "already_removed_bots": sum(1 for check in checks if check.status == "already-removed"),
            "failed_bots": sum(1 for check in checks if check.status == "failed"),
        },
        "results": [asdict(check) for check in checks],
    }
    manifest_path = root_dir / ("bot_command_jar_cleanup_manifest.json" if execute else "bot_command_jar_cleanup_dry_run.json")
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove Bot Store bot-command JAR binaries after manifest cross-check.")
    parser.add_argument("--root-dir", type=Path, default=default_unpacked_dir())
    parser.add_argument("--execute", action="store_true", help="Actually delete bot-command-*.jar files.")
    parser.add_argument("--force", action="store_true", help="Delete even if manifest and filesystem JAR lists differ.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = args.root_dir.resolve()
    if not root_dir.exists():
        raise SystemExit(f"Root directory does not exist: {root_dir}")

    checks: list[BotJarCheck] = []
    jars_by_bot: list[tuple[BotJarCheck, list[Path]]] = []
    for category in CATEGORY_DIRS:
        category_dir = root_dir / category
        if not category_dir.exists():
            checks.append(
                BotJarCheck(
                    category=category,
                    bot_dir=str(category_dir),
                    actual_jars=0,
                    manifest_refs=0,
                    status="failed",
                    error=f"Missing category directory: {category_dir}",
                )
            )
            continue
        for bot_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
            check, jar_paths = scan_bot(bot_dir, category_dir, root_dir)
            checks.append(check)
            jars_by_bot.append((check, jar_paths))

    has_mismatch = any(check.status == "mismatch" for check in checks)
    has_failure = any(check.status == "failed" for check in checks)
    if (has_mismatch or has_failure) and args.execute and not args.force:
        manifest_path = write_cleanup_manifest(root_dir, checks, execute=False)
        raise SystemExit(
            "Aborted: manifest/filesystem mismatch or missing category detected. "
            f"Review {manifest_path} or rerun with --force."
        )

    if args.execute:
        for check, jar_paths in jars_by_bot:
            if check.status == "already-removed":
                continue
            deleted = 0
            for jar_path in jar_paths:
                try:
                    remove_file_with_retries(jar_path)
                    deleted += 1
                except Exception as exc:  # noqa: BLE001 - report per bot and continue.
                    check.status = "failed"
                    check.error = f"{type(exc).__name__}: {exc}"
                    break
            check.deleted_jars = deleted
            if check.status == "ok":
                check.status = "deleted"
    else:
        for check in checks:
            if check.status == "ok":
                check.status = "dry-run"

    manifest_path = write_cleanup_manifest(root_dir, checks, execute=args.execute)
    summary = json.loads(manifest_path.read_text(encoding="utf-8"))["summary"]
    print(json.dumps({"manifest": str(manifest_path), **summary}, ensure_ascii=False, indent=2))
    if not args.execute:
        print("Dry run only. Add --execute to delete bot-command-*.jar files.")


if __name__ == "__main__":
    main()
