from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATA_DIR = Path(os.getenv("RAG_ARTIFACT_DATA_DIR", "/app/data"))
INGEST_DIR = Path(os.getenv("INGEST_DATA_DIR", DATA_DIR / "ingest"))


def _enabled() -> bool:
    return bool(os.getenv("RAG_ARTIFACT_BUCKET"))


def _prefix() -> str:
    return os.getenv("RAG_ARTIFACT_PREFIX", "rag-ingest-artifacts").strip("/")


def latest_exists() -> bool:
    if not _enabled():
        return False
    import boto3
    from botocore.exceptions import ClientError

    try:
        boto3.client("s3").head_object(
            Bucket=os.environ["RAG_ARTIFACT_BUCKET"],
            Key=f"{_prefix()}/latest/rag-server-data.tar.gz",
        )
        return True
    except ClientError:
        return False


def restore_latest_if_missing() -> dict[str, Any]:
    required = INGEST_DIR / "rag_documents.jsonl"
    if required.is_file():
        return {"ok": True, "restored": False, "reason": "local-present"}
    if not _enabled():
        return {"ok": False, "restored": False, "reason": "artifact-bucket-not-configured"}

    import boto3
    from botocore.exceptions import ClientError

    bucket = os.environ["RAG_ARTIFACT_BUCKET"]
    key = _latest_or_manual_backup_key(boto3.client("s3"), bucket)
    if not key:
        return {"ok": False, "restored": False, "reason": "artifact-not-found"}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        archive_path = tmp_dir / "rag-server-data.tar.gz"
        boto3.client("s3").download_file(bucket, key, str(archive_path))
        with tarfile.open(archive_path, "r:gz") as tar:
            members = tar.getmembers()
            _validate_members(members)
            tar.extractall(tmp_dir, members=members)
        root = _find_restore_root(tmp_dir)
        if not root:
            return {"ok": False, "restored": False, "reason": "unsupported-root-layout", "key": key}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, DATA_DIR, dirs_exist_ok=True)

    return {"ok": True, "restored": True, "bucket": bucket, "key": key}


def _latest_or_manual_backup_key(s3: Any, bucket: str) -> str | None:
    latest_key = f"{_prefix()}/latest/rag-server-data.tar.gz"
    try:
        s3.head_object(Bucket=bucket, Key=latest_key)
        return latest_key
    except ClientError:
        pass

    manual_prefix = f"{_prefix()}/manual-backup/"
    response = s3.list_objects_v2(Bucket=bucket, Prefix=manual_prefix)
    candidates = [
        item
        for item in response.get("Contents", [])
        if item.get("Key", "").endswith(".tar.gz")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["LastModified"])["Key"]


def _validate_members(members: list[tarfile.TarInfo]) -> None:
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not allowed: {member.name}")


def _find_restore_root(tmp_dir: Path) -> Path | None:
    for name in ("rag-server-data", "rag-server-data-backup", "data"):
        root = tmp_dir / name
        if root.is_dir():
            return root
    return None


def _manifest(job_id: str) -> dict[str, Any]:
    files = {}
    for rel in (
        "ingest/khub-dump",
        "ingest/package_registry.json",
        "ingest/extract_llm_cache.json",
        "ingest/rag_documents.jsonl",
        "ingest/build_stats.json",
    ):
        path = DATA_DIR / rel
        if path.is_file():
            files[rel] = {"size": path.stat().st_size}
        elif path.is_dir():
            files[rel] = {"files": sum(1 for item in path.rglob("*") if item.is_file())}
    return {
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunk_size": os.getenv("CHUNK_SIZE"),
        "chunk_overlap": os.getenv("CHUNK_OVERLAP"),
        "agent_parse_model": os.getenv("AGENT_PARSE_MODEL"),
        "files": files,
    }


def upload_latest(job_id: str) -> dict[str, Any] | None:
    if not _enabled():
        return None
    required = INGEST_DIR / "rag_documents.jsonl"
    if not required.exists():
        return {"ok": False, "reason": f"missing {required}"}

    import boto3

    bucket = os.environ["RAG_ARTIFACT_BUCKET"]
    prefix = _prefix()
    manifest = _manifest(job_id)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_key = f"{prefix}/archive/{timestamp}-{job_id}/rag-server-data.tar.gz"
    manifest_key = f"{prefix}/archive/{timestamp}-{job_id}/manifest.json"
    latest_archive_key = f"{prefix}/latest/rag-server-data.tar.gz"
    latest_manifest_key = f"{prefix}/latest/manifest.json"

    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / "rag-server-data.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(DATA_DIR, arcname="rag-server-data")
        s3 = boto3.client("s3")
        s3.upload_file(str(archive_path), bucket, archive_key)
        s3.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": archive_key}, Key=latest_archive_key)
        s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": manifest_key}, Key=latest_manifest_key)

    return {
        "ok": True,
        "bucket": bucket,
        "archive_key": archive_key,
        "latest_archive_key": latest_archive_key,
        "latest_manifest_key": latest_manifest_key,
    }
