from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)

TEXT_SUFFIXES = {
    ".json",
    ".xml",
    ".properties",
    ".txt",
    ".md",
    ".csv",
    ".yml",
    ".yaml",
    ".mf",
    ".license",
    ".notice",
}
TEXT_FILENAMES = {
    "manifest.mf",
    "license",
    "notice",
    "dependencies",
    "changes",
    "readme",
}
BINARY_INLINE_SUFFIXES = {
    ".svg",
}


@dataclass
class JarEntry:
    name: str
    is_dir: bool
    size: int
    compressed_size: int
    crc: int
    sha256: str | None
    stored_content_kind: str
    text: str | None = None
    base64: str | None = None


@dataclass
class FullJarMetadata:
    category: str
    bot_dir: str
    jar_name: str
    path: str
    size_bytes: int
    sha256: str
    entry_count: int
    class_count: int
    entries: list[JarEntry]
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def should_store_text(entry_name: str) -> bool:
    path = Path(entry_name)
    suffix = path.suffix.lower()
    stem = path.name.lower()
    if suffix in TEXT_SUFFIXES:
        return True
    if stem in TEXT_FILENAMES:
        return True
    if entry_name.upper() == "META-INF/MANIFEST.MF":
        return True
    return False


def should_store_base64(entry_name: str) -> bool:
    return Path(entry_name).suffix.lower() in BINARY_INLINE_SUFFIXES


def decode_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def export_jar(jar_path: Path, bot_dir: Path, category_dir: Path, root_dir: Path, *, max_text_bytes: int) -> FullJarMetadata:
    entries: list[JarEntry] = []
    class_count = 0
    error: str | None = None
    try:
        with zipfile.ZipFile(jar_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    entries.append(
                        JarEntry(
                            name=info.filename,
                            is_dir=True,
                            size=info.file_size,
                            compressed_size=info.compress_size,
                            crc=info.CRC,
                            sha256=None,
                            stored_content_kind="directory",
                        )
                    )
                    continue

                if info.filename.endswith(".class"):
                    class_count += 1

                data = archive.read(info.filename)
                content_hash = sha256_bytes(data)
                text: str | None = None
                encoded: str | None = None
                stored_kind = "hash_only"
                if should_store_text(info.filename):
                    if len(data) <= max_text_bytes:
                        text = decode_text(data)
                        stored_kind = "text"
                    else:
                        text = decode_text(data[:max_text_bytes])
                        stored_kind = "text_truncated"
                elif should_store_base64(info.filename):
                    encoded = base64.b64encode(data).decode("ascii")
                    stored_kind = "base64"

                entries.append(
                    JarEntry(
                        name=info.filename,
                        is_dir=False,
                        size=info.file_size,
                        compressed_size=info.compress_size,
                        crc=info.CRC,
                        sha256=content_hash,
                        stored_content_kind=stored_kind,
                        text=text,
                        base64=encoded,
                    )
                )
    except Exception as exc:  # noqa: BLE001 - keep a row so cleanup decisions can see failure.
        error = f"{type(exc).__name__}: {exc}"

    return FullJarMetadata(
        category=category_dir.name,
        bot_dir=str(bot_dir.relative_to(root_dir)),
        jar_name=jar_path.name,
        path=str(jar_path.relative_to(root_dir)),
        size_bytes=jar_path.stat().st_size,
        sha256=sha256_file(jar_path),
        entry_count=len(entries),
        class_count=class_count,
        entries=entries,
        error=error,
    )


def collect(root_dir: Path, *, max_text_bytes: int) -> list[FullJarMetadata]:
    rows: list[FullJarMetadata] = []
    for category in CATEGORY_DIRS:
        category_dir = root_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
            for jar_path in sorted(path for path in bot_dir.rglob("*.jar") if is_custom_jar(path)):
                rows.append(export_jar(jar_path, bot_dir, category_dir, root_dir, max_text_bytes=max_text_bytes))
    return rows


def summarize(rows: list[FullJarMetadata]) -> dict:
    return {
        "custom_jar_occurrences": len(rows),
        "unique_custom_jar_names": len({row.jar_name for row in rows}),
        "total_jar_size_mb": round(sum(row.size_bytes for row in rows) / 1024 / 1024, 2),
        "total_entries": sum(row.entry_count for row in rows),
        "total_classes": sum(row.class_count for row in rows),
        "failed_jars": sum(1 for row in rows if row.error),
        "bots_with_custom_jars": len({row.bot_dir for row in rows}),
    }


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# Full Custom JAR Metadata Export",
        "",
        "This export preserves detailed metadata for non-`bot-command-*` JAR files before binary cleanup.",
        "Class files are recorded by entry name, size, CRC, and SHA-256, not decompiled.",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Root: `{payload['root_dir']}`",
        f"- Custom JAR occurrences: `{summary['custom_jar_occurrences']}`",
        f"- Unique custom JAR names: `{summary['unique_custom_jar_names']}`",
        f"- Total JAR size: `{summary['total_jar_size_mb']} MB`",
        f"- Total entries: `{summary['total_entries']}`",
        f"- Total classes: `{summary['total_classes']}`",
        f"- Failed JARs: `{summary['failed_jars']}`",
        "",
        "## JARs",
        "",
        "| JAR | Bot | Entries | Classes | SHA-256 | Error |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| `{row['jar_name']}` | `{row['bot_dir']}` | {row['entry_count']} | "
            f"{row['class_count']} | `{row['sha256']}` | {row['error'] or ''} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive full metadata for custom JARs before deleting binary files.")
    parser.add_argument("--root-dir", type=Path, default=default_unpacked_dir())
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    parser.add_argument(
        "--max-text-bytes",
        type=int,
        default=1024 * 1024,
        help="Maximum bytes of each text entry to inline. Larger text entries are truncated.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = args.root_dir.resolve()
    if not root_dir.exists():
        raise SystemExit(f"Root directory does not exist: {root_dir}")

    rows = collect(root_dir, max_text_bytes=args.max_text_bytes)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "max_text_bytes": args.max_text_bytes,
        "summary": summarize(rows),
        "rows": [asdict(row) for row in rows],
    }
    json_output = args.json_output or (root_dir / "custom_jar_metadata_archive.json")
    md_output = args.md_output or (root_dir / "custom_jar_metadata_archive.md")
    json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_output, payload)
    print(
        json.dumps(
            {
                "json": str(json_output),
                "markdown": str(md_output),
                **payload["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
