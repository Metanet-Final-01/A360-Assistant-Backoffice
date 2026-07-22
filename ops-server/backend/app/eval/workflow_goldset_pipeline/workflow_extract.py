"""압축을 푼 A360 봇 폴더에서 실제 "워크플로우(액션 순서)" 파일들을 찾아 읽는다.

A360 봇 폴더에는 manifest.json이 있고, 그 안에 폴더 안 파일들의 목록과 각 파일의
종류(contentType)가 적혀있다. 그중에서 실제로 액션이 순서대로 들어있는 파일만
골라야 한다 — 폼 화면 정의(form)나 LLM 프롬프트 템플릿(prompt) 같은 건 액션
순서가 아니라서 제외한다.

scripts/agent_flow_eval/processing/extract_workflows.py의 로직을 그대로 옮겼다
(단, 원래는 category 배치 구조를 대상으로 여러 봇을 훑었는데, 여기서는 업로드된
봇 폴더 하나만 본다).
"""

import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST_FILENAME = "manifest.json"

# manifest.json의 contentType 중에서 "실제 액션 순서가 들어있는 파일"에 해당하는 것들.
# taskbot/headlessbot/workflow 셋 다 {"triggers": [...], "nodes": [...]} 구조를 쓴다.
# form(화면 레이아웃)과 prompt(LLM 프롬프트 템플릿)는 액션 순서가 아니라서 제외했다.
# aiagent도 제외했다 — AI Agent는 자기 안에 고정된 액션 순서가 없고, 실행할 때마다
# LLM이 그때그때 순서를 정하기 때문에 "정답 순서"라는 게 애초에 없다. AI Agent가
# 참조하는 하위 taskbot/headlessbot들은 각자 따로 정상적으로 채점 대상이 된다.
WORKFLOW_CONTENT_TYPES = frozenset({
    "application/vnd.aa.taskbot",
    "application/vnd.aa.headlessbot",
    "application/vnd.aa.workflow",
})


@dataclass
class ExtractedWorkflowFile:
    manifest_path: str
    content_type: str
    workflow_json: dict


def find_manifest_file(bot_dir: Path) -> Path | None:
    """봇 폴더 바로 밑, 또는 압축을 풀면 흔히 생기는 폴더 하나 더 깊은 곳에서
    manifest.json을 찾는다(zip 안에 봇 이름 폴더가 한 겹 더 있는 경우가 흔하다)."""
    direct_path = bot_dir / MANIFEST_FILENAME
    if direct_path.is_file():
        return direct_path

    nested_matches = list(bot_dir.glob(f"*/{MANIFEST_FILENAME}"))
    if len(nested_matches) == 1:
        return nested_matches[0]

    return None


def extract_workflow_files(bot_dir: Path) -> list[ExtractedWorkflowFile]:
    """봇 폴더 안 manifest.json을 읽고, 실제 워크플로우 파일들을 찾아서 그 내용을 반환한다."""
    manifest_path = find_manifest_file(bot_dir)
    if manifest_path is None:
        raise FileNotFoundError(f"manifest.json을 찾지 못했습니다: {bot_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_root_dir = manifest_path.parent

    workflow_entries = [
        file_entry
        for file_entry in manifest.get("files", [])
        if file_entry.get("contentType") in WORKFLOW_CONTENT_TYPES
    ]

    extracted_files: list[ExtractedWorkflowFile] = []
    for file_entry in workflow_entries:
        manifest_path_value = file_entry.get("path") or ""
        content_type = file_entry.get("contentType", "")

        source_path = _find_file_by_manifest_path(manifest_root_dir, manifest_path_value)
        if source_path is None:
            raise FileNotFoundError(f"manifest에 적힌 파일을 찾지 못했습니다: {manifest_path_value}")

        workflow_json = json.loads(source_path.read_text(encoding="utf-8"))
        extracted_files.append(ExtractedWorkflowFile(
            manifest_path=manifest_path_value,
            content_type=content_type,
            workflow_json=workflow_json,
        ))

    return extracted_files


def _manifest_path_parts(manifest_path_value: str) -> list[str]:
    normalized = manifest_path_value.replace("\\", "/")
    return [part for part in normalized.split("/") if part]


def _find_file_by_manifest_path(bot_dir: Path, manifest_path_value: str) -> Path | None:
    """manifest.json에 적힌 경로(Windows/폴더 구분자가 뒤섞여 있을 수 있다)로
    실제 파일을 찾는다. 정확한 경로에 없으면, 파일 이름이 같고 마지막 몇 단계
    경로가 일치하는 파일을 폴더 전체에서 찾아본다."""
    path_parts = _manifest_path_parts(manifest_path_value)
    if not path_parts:
        return None

    direct_path = bot_dir.joinpath(*path_parts)
    if direct_path.is_file():
        return direct_path

    file_name = path_parts[-1]
    # 액션 파일과 그걸 담은 패키지 폴더가 이름이 같은 경우가 있어서
    # (예: ".../Excel Operation/Excel Operation"), is_file()로 폴더는 걸러낸다.
    suffix_matches = [
        candidate for candidate in bot_dir.rglob(file_name)
        if candidate.is_file()
        and list(candidate.relative_to(bot_dir).parts)[-len(path_parts):] == path_parts
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    name_only_matches = sorted(candidate for candidate in bot_dir.rglob(file_name) if candidate.is_file())
    if len(name_only_matches) == 1:
        return name_only_matches[0]

    return None
