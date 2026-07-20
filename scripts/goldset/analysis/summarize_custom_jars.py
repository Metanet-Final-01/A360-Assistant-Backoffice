from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


CATEGORY_DIRS = (
    "01_task1_similar_single_15",
    "02_task1_similar_combo_15",
    "03_broad_sequential_workflow",
)


@dataclass
class ParsedJar:
    category: str
    bot_dir: str
    jar_name: str
    path: str
    size_bytes: int
    sha256: str
    class_count: int | None
    package_prefixes: list[str]
    manifest: dict[str, str]
    package_json: dict | None
    locale_keys: list[str]
    icon_paths: list[str]
    has_a360_command_classes: bool
    likely_role: str
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_jar(jar_name: str, package_json: dict | None) -> str:
    name = jar_name.lower()
    package_text = json.dumps(package_json or {}, ensure_ascii=False).lower()
    haystack = f"{name} {package_text}"
    if any(token in haystack for token in ("excel", "csv", "htmltable")):
        return "data_table_excel"
    if any(token in haystack for token in ("browser", "webautomation", "web automation")):
        return "web_browser"
    if any(token in haystack for token in ("pdf", "word", "document")):
        return "document"
    if any(token in haystack for token in ("teams", "twilio", "servicenow", "credential", "salesforce")):
        return "external_service"
    if any(token in haystack for token in ("json", "system", "variables", "information")):
        return "utility"
    return "unknown"


def read_text_from_archive(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        return archive.read(name).decode("utf-8", errors="ignore")
    except KeyError:
        return None


def parse_manifest(text: str | None) -> dict[str, str]:
    if not text:
        return {}
    values: dict[str, str] = {}
    current_key: str | None = None
    for line in text.splitlines():
        if line.startswith(" ") and current_key:
            values[current_key] += line[1:]
            continue
        if ": " not in line:
            current_key = None
            continue
        key, value = line.split(": ", 1)
        values[key] = value
        current_key = key
    return values


def parse_package_json(archive: zipfile.ZipFile) -> dict | None:
    for candidate in ("package.json", "META-INF/package.json"):
        text = read_text_from_archive(archive, candidate)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_parse_error": f"Invalid JSON in {candidate}", "_raw_prefix": text[:500]}
    return None


def parse_locale_keys(archive: zipfile.ZipFile, names: list[str]) -> list[str]:
    keys: set[str] = set()
    for name in names:
        if not name.startswith("locales/") or not name.endswith(".json"):
            continue
        text = read_text_from_archive(archive, name)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            keys.update(str(key) for key in payload.keys())
    return sorted(keys)


def package_prefixes_from_classes(names: list[str]) -> list[str]:
    counter: Counter[str] = Counter()
    for name in names:
        if not name.endswith(".class") or "/" not in name:
            continue
        parts = name.split("/")
        if len(parts) < 3:
            continue
        if parts[0] == "META-INF":
            continue
        counter[".".join(parts[:3])] += 1
    return [prefix for prefix, _ in counter.most_common(20)]


def parse_jar(jar_path: Path, bot_dir: Path, category_dir: Path, root_dir: Path) -> ParsedJar:
    size_bytes = jar_path.stat().st_size
    sha256 = sha256_file(jar_path)
    class_count: int | None = None
    package_prefixes: list[str] = []
    manifest: dict[str, str] = {}
    package_json: dict | None = None
    locale_keys: list[str] = []
    icon_paths: list[str] = []
    has_a360_command_classes = False
    error: str | None = None
    likely_role = "unknown"

    try:
        with zipfile.ZipFile(jar_path) as archive:
            names = archive.namelist()
            class_count = sum(1 for name in names if name.endswith(".class"))
            package_prefixes = package_prefixes_from_classes(names)
            has_a360_command_classes = any(name.startswith("com/automationanywhere/botcommand/") for name in names)
            manifest = parse_manifest(read_text_from_archive(archive, "META-INF/MANIFEST.MF"))
            package_json = parse_package_json(archive)
            locale_keys = parse_locale_keys(archive, names)
            icon_paths = sorted(name for name in names if name.startswith("icons/") and not name.endswith("/"))
            likely_role = classify_jar(jar_path.name, package_json)
    except Exception as exc:  # noqa: BLE001 - keep a structured row for bad archives.
        error = f"{type(exc).__name__}: {exc}"

    return ParsedJar(
        category=category_dir.name,
        bot_dir=str(bot_dir.relative_to(root_dir)),
        jar_name=jar_path.name,
        path=str(jar_path.relative_to(root_dir)),
        size_bytes=size_bytes,
        sha256=sha256,
        class_count=class_count,
        package_prefixes=package_prefixes,
        manifest=manifest,
        package_json=package_json,
        locale_keys=locale_keys,
        icon_paths=icon_paths,
        has_a360_command_classes=has_a360_command_classes,
        likely_role=likely_role,
        error=error,
    )


def collect(root_dir: Path) -> list[ParsedJar]:
    rows: list[ParsedJar] = []
    for category in CATEGORY_DIRS:
        category_dir = root_dir / category
        if not category_dir.exists():
            continue
        for bot_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
            for jar_path in sorted(path for path in bot_dir.rglob("*.jar") if is_custom_jar(path)):
                rows.append(parse_jar(jar_path, bot_dir, category_dir, root_dir))
    return rows


def summarize(rows: list[ParsedJar]) -> dict:
    by_name: dict[str, dict] = defaultdict(
        lambda: {
            "count": 0,
            "total_size_bytes": 0,
            "sha256": set(),
            "bots": set(),
            "likely_roles": set(),
            "has_a360_command_classes": False,
        }
    )
    for row in rows:
        item = by_name[row.jar_name]
        item["count"] += 1
        item["total_size_bytes"] += row.size_bytes
        item["sha256"].add(row.sha256)
        item["bots"].add(row.bot_dir)
        item["likely_roles"].add(row.likely_role)
        item["has_a360_command_classes"] = item["has_a360_command_classes"] or row.has_a360_command_classes

    unique = []
    for jar_name, item in sorted(by_name.items(), key=lambda kv: (-kv[1]["count"], kv[0].lower())):
        unique.append(
            {
                "jar_name": jar_name,
                "count": item["count"],
                "total_size_mb": round(item["total_size_bytes"] / 1024 / 1024, 2),
                "unique_hashes": len(item["sha256"]),
                "likely_roles": sorted(item["likely_roles"]),
                "has_a360_command_classes": item["has_a360_command_classes"],
                "bots": sorted(item["bots"]),
            }
        )

    return {
        "custom_jar_occurrences": len(rows),
        "unique_custom_jars": len(unique),
        "total_size_mb": round(sum(row.size_bytes for row in rows) / 1024 / 1024, 2),
        "bots_with_custom_jars": len({row.bot_dir for row in rows}),
        "unique_custom_jar_summary": unique,
    }


def write_markdown(path: Path, payload: dict) -> None:
    summary = payload["summary"]
    lines = [
        "# Parsed Custom JAR Metadata",
        "",
        "This report parses non-`bot-command-*` JAR files and preserves metadata that can survive binary cleanup.",
        "",
        f"- Created at: `{payload['created_at']}`",
        f"- Root: `{payload['root_dir']}`",
        f"- Custom JAR occurrences: `{summary['custom_jar_occurrences']}`",
        f"- Unique custom JAR names: `{summary['unique_custom_jars']}`",
        f"- Total size: `{summary['total_size_mb']} MB`",
        f"- Bots with custom JARs: `{summary['bots_with_custom_jars']}`",
        "",
        "## Unique Custom JARs",
        "",
        "| JAR | Count | Size MB | Hashes | Role | A360 Command Classes | Sample Bots |",
        "| --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for item in summary["unique_custom_jar_summary"]:
        sample_bots = "<br>".join(item["bots"][:4])
        roles = ", ".join(f"`{role}`" for role in item["likely_roles"])
        lines.append(
            f"| `{item['jar_name']}` | {item['count']} | {item['total_size_mb']} | "
            f"{item['unique_hashes']} | {roles} | `{item['has_a360_command_classes']}` | {sample_bots} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse custom JAR metadata for selected goldset candidates.")
    parser.add_argument("--root-dir", type=Path, default=default_unpacked_dir())
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = args.root_dir.resolve()
    if not root_dir.exists():
        raise SystemExit(f"Root directory does not exist: {root_dir}")

    rows = collect(root_dir)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "root_dir": str(root_dir),
        "summary": summarize(rows),
        "rows": [asdict(row) for row in rows],
    }
    json_output = args.json_output or (root_dir / "custom_jar_summary.json")
    md_output = args.md_output or (root_dir / "custom_jar_summary.md")
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
