"""업로드된 zip 파일 하나를 안전하게 압축 해제한다.

zip 안에 있는 파일 경로를 그대로 믿으면 위험하다 — 예를 들어 "../../어딘가"처럼
상위 폴더로 빠져나가는 경로나, 압축 해제하면 디스크를 꽉 채우는 "zip bomb"이
들어있을 수 있다. 그래서 압축을 풀기 전에 안전한지부터 확인한다.

scripts/agent_flow_eval/processing/unpack_selected_zips.py의 safe_extract() 로직을
그대로 옮겼다 — 파일 개수/크기/압축률 제한값도 그대로다.
"""

import zipfile
from pathlib import Path

MAX_ENTRY_COUNT = 20_000
MAX_SINGLE_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class UnsafeZipError(ValueError):
    """업로드된 zip이 안전 기준을 벗어나서 압축을 풀지 않았을 때 발생시킨다."""


def extract_zip_safely(zip_path: Path, output_dir: Path) -> int:
    """zip_path를 output_dir 아래에 안전하게 압축 해제하고, 풀린 파일 개수를 반환한다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_root = output_dir.resolve()

    extracted_file_count = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRY_COUNT:
            raise UnsafeZipError(f"zip 안에 파일이 너무 많습니다: {len(entries)}개 (최대 {MAX_ENTRY_COUNT}개)")

        for entry in entries:
            target_path = (output_dir / entry.filename).resolve()
            _check_path_stays_inside_output_dir(entry.filename, target_path, output_root)

            total_uncompressed_bytes += entry.file_size
            _check_entry_size_is_safe(entry, total_uncompressed_bytes)

            if entry.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            _write_entry_to_disk(archive, entry, target_path)
            extracted_file_count += 1

    return extracted_file_count


def _check_path_stays_inside_output_dir(entry_name: str, target_path: Path, output_root: Path) -> None:
    is_output_root_itself = target_path == output_root
    is_inside_output_root = output_root in target_path.parents
    if not is_output_root_itself and not is_inside_output_root:
        raise UnsafeZipError(f"zip 안의 파일 경로가 압축 해제 폴더 바깥을 가리킵니다: {entry_name}")


def _check_entry_size_is_safe(entry: zipfile.ZipInfo, total_uncompressed_bytes_so_far: int) -> None:
    if entry.file_size > MAX_SINGLE_FILE_BYTES:
        raise UnsafeZipError(f"파일 하나가 너무 큽니다: {entry.filename} ({entry.file_size} bytes)")
    if total_uncompressed_bytes_so_far > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise UnsafeZipError(f"압축을 풀었을 때 전체 크기가 너무 큽니다: {total_uncompressed_bytes_so_far} bytes")
    if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
        raise UnsafeZipError(f"압축률이 비정상적으로 높습니다(zip bomb 의심): {entry.filename}")


def _write_entry_to_disk(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, target_path: Path) -> None:
    with archive.open(entry) as source_file, target_path.open("wb") as destination_file:
        bytes_written = 0
        while True:
            chunk = source_file.read(1024 * 1024)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > entry.file_size or bytes_written > MAX_SINGLE_FILE_BYTES:
                raise UnsafeZipError(f"압축 해제된 크기가 zip에 적힌 크기보다 큽니다: {entry.filename}")
            destination_file.write(chunk)
