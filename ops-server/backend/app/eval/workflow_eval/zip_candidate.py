"""Bot Store zip 업로드 -> Workflow 골드셋 후보 분석.

기존 오프라인 파이프라인(A360-Assistant-Ops/scripts/goldset/processing/)이 봇 zip
더미 전체를 배치로 처리하는 CLI였다면, 이건 관리자가 zip 하나를 웹에서 올렸을 때
그 자리에서 분석 리포트를 보여주는 온라인 버전이다. 핵심 알고리즘(정규화·runTask
탐지)은 그 파이프라인의 검증된 로직을 그대로 옮겼다(재구현하지 않음):

- 정규화(Step/Comment 필터, If/Loop/ErrorHandler/TriggerLoop 보존): 원본
  processing/normalize_extracted_workflows.py의 convert_node/convert_nodes/
  convert_branch/extra_binding_fields.
- TaskBot.runTask 참조 추출: 원본 processing/resolve_subtask_coverage.py의
  extract_taskbot_filename/collect_direct_pairs_and_refs.

디스크에 압축 해제하지 않고 zip 바이트를 메모리에서만 읽는다(zipfile.ZipFile +
BytesIO, ZipInfo.filename만 참조하고 절대 실제 경로에 쓰지 않음) — zip-slip이
구조적으로 불가능하다. 압축폭탄 방지로 항목 수/개별 파일 크기 상한만 둔다.

메인/서브워크플로우 판정은 이 zip 안에 실제로 들어있는 파일들 사이에서만 계산된다
(PROVENANCE.md의 콘퍼스 전체 교차 판정과 달리, 업로드 시점엔 다른 봇과의 관계를
알 수 없다는 명시적 한계) — 그래서 "same-zip 기준"이라고 리포트에 표시한다.

RAG 카탈로그 커버리지는 app/eval/workflow/catalog_actions.json이 있을 때만
계산한다(README에 명시된 크로스서버 의존 — build_catalog.py 참고). 없으면 각
액션의 in_catalog을 None으로 두고 "카탈로그 없음"으로 리포트한다 — 조용한
오탐(모두 covered로 보이는 거짓 안심)보다 명시적으로 "모른다"가 낫다.
"""

from __future__ import annotations

import io
import json
import urllib.parse
import zipfile
from pathlib import Path

# extract_workflows.py의 WORKFLOW_CONTENT_TYPES와 동일 — form/prompt/aiagent는
# 실제 액션 시퀀스가 아니라서 제외(그 파일 docstring의 근거 그대로).
WORKFLOW_CONTENT_TYPES = frozenset({
    "application/vnd.aa.taskbot",
    "application/vnd.aa.headlessbot",
    "application/vnd.aa.workflow",
})

# PROVENANCE.md가 실측으로 확인한 "액션 라벨만으로는 내부 로직이 불투명한" 패키지들
# (0224/0225/0137/0302의 DLL.Run function, Recorder/AISense.capture) — 새 후보에도
# 같은 잣대를 자동으로 들이대 관리자가 등록 전에 바로 알 수 있게 한다.
OPAQUE_PACKAGES = frozenset({"DLL", "Recorder", "AISense"})

_MAX_ENTRIES = 2000
_MAX_ENTRY_BYTES = 20 * 1024 * 1024  # 20MB — 봇 워크플로우 JSON 치고는 넉넉한 상한


class ZipCandidateError(RuntimeError):
    """zip 파싱/구조 문제 — 그대로 HTTP 400으로 변환된다."""


# --- 정규화 (processing/normalize_extracted_workflows.py에서 그대로 옮김) ---

_TRANSPARENT_PACKAGES = frozenset({"Step"})
_SKIPPED_PACKAGES = frozenset({"Comment"})
_IF_PACKAGES = frozenset({"If"})
_TRY_PACKAGES = frozenset({"ErrorHandler"})
_LOOP_PACKAGES = frozenset({"Loop"})
_BRANCH_ONLY_LOOP_PACKAGES = frozenset({"TriggerLoop"})


def _extra_binding_fields(node: dict) -> dict:
    extra: dict = {}
    if "returnTo" in node:
        extra["return_to"] = node["returnTo"]
    if "returns" in node:
        extra["returns"] = node["returns"]
    return extra


def _convert_branch(branch: dict) -> dict:
    return {
        "branch": branch.get("commandName"),
        "attributes": branch.get("attributes", []),
        **_extra_binding_fields(branch),
        "steps": _convert_nodes(branch.get("children", []) or []),
    }


def _convert_node(node: dict) -> list[dict]:
    package = node.get("packageName")

    if package in _SKIPPED_PACKAGES:
        return []
    if package in _TRANSPARENT_PACKAGES:
        return _convert_nodes(node.get("children", []) or [])

    common = {
        "uid": node.get("uid"),
        "disabled": bool(node.get("disabled", False)),
        "attributes": node.get("attributes", []),
        **_extra_binding_fields(node),
    }

    if package in _IF_PACKAGES:
        return [{
            "type": "if", **common,
            "steps": _convert_nodes(node.get("children", []) or []),
            "branches": [_convert_branch(b) for b in node.get("branches", []) or []],
        }]
    if package in _TRY_PACKAGES:
        return [{
            "type": "try", **common,
            "steps": _convert_nodes(node.get("children", []) or []),
            "branches": [_convert_branch(b) for b in node.get("branches", []) or []],
        }]
    if package in _LOOP_PACKAGES:
        return [{
            "type": "loop", **common,
            "steps": _convert_nodes(node.get("children", []) or []),
        }]
    if package in _BRANCH_ONLY_LOOP_PACKAGES:
        return [{
            "type": "trigger_loop", **common,
            "branches": [_convert_branch(b) for b in node.get("branches", []) or []],
        }]
    if node.get("children"):
        return [{
            "type": "container", "package": package, "action": node.get("commandName"),
            **common, "steps": _convert_nodes(node.get("children", []) or []),
        }]

    return [{"type": "action", "package": package, "action": node.get("commandName"), **common}]


def _convert_nodes(nodes: list[dict]) -> list[dict]:
    steps: list[dict] = []
    for node in nodes:
        steps.extend(_convert_node(node))
    return steps


def normalize_workflow(raw: dict) -> dict:
    """원본 workflow JSON({"triggers": [...], "nodes": [...]}) -> 정규화된
    {"triggers": [...], "steps": [...]} (goldset.json과 같은 형태)."""
    return {"triggers": raw.get("triggers", []), "steps": _convert_nodes(raw.get("nodes", []) or [])}


# --- TaskBot.runTask 참조 추출 (processing/resolve_subtask_coverage.py에서 옮김) ---

def _extract_taskbot_filename(step: dict) -> str | None:
    for attr in step.get("attributes", []) or []:
        if attr.get("name") != "taskbot":
            continue
        file_str = (attr.get("value") or {}).get("taskbotFile", {}).get("string")
        if not file_str:
            continue
        decoded = urllib.parse.unquote(file_str)
        return decoded.rstrip("/").split("/")[-1]
    return None


def collect_pairs_refs_and_opaque(
    steps: list[dict], pairs: list[tuple[str, str]], refs: set[str], opaque_packages: set[str],
) -> None:
    """actions/container 노드의 (package, action)을 등장 순서대로 pairs에 모으고,
    TaskBot.runTask 참조 파일명은 refs에, OPAQUE_PACKAGES에 속한 패키지 이름은
    opaque_packages에 모은다 — 하나의 순회로 세 가지를 다 뽑아 트리를 두 번 걷지
    않는다."""
    for step in steps:
        step_type = step.get("type")
        if step_type in ("action", "container"):
            pkg, act = step.get("package"), step.get("action")
            if pkg and act:
                pairs.append((pkg, act))
                if pkg in OPAQUE_PACKAGES:
                    opaque_packages.add(pkg)
                if pkg == "TaskBot" and act == "runTask":
                    filename = _extract_taskbot_filename(step)
                    if filename:
                        refs.add(filename)
        collect_pairs_refs_and_opaque(step.get("steps", []) or [], pairs, refs, opaque_packages)
        for branch in step.get("branches", []) or []:
            collect_pairs_refs_and_opaque(branch.get("steps", []) or [], pairs, refs, opaque_packages)


# --- RAG 카탈로그 (있으면 참조, 없으면 None 반환 — 조용한 오탐 방지) ---

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "workflow" / "catalog_actions.json"


def _load_catalog() -> set[tuple[str, str]] | None:
    if not _CATALOG_PATH.exists():
        return None
    catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    return {
        (pkg_name, action_name)
        for pkg_name, pkg in catalog.items()
        for action_name in pkg.get("actions", {})
    }


# --- zip 파싱 (메모리에서만, 디스크에 안 씀 — zip-slip 구조적으로 불가능) ---

def _find_manifest_name(names: list[str]) -> str:
    candidates = sorted((n for n in names if n.lower().endswith("manifest.json")), key=len)
    if not candidates:
        raise ZipCandidateError("zip 안에서 manifest.json을 찾을 수 없습니다")
    return candidates[0]


def _find_entry_by_basename(names: list[str], basename: str) -> str | None:
    matches = [n for n in names if n.rsplit("/", 1)[-1] == basename]
    return matches[0] if matches else None


def analyze_zip(raw_bytes: bytes) -> dict:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile as e:
        raise ZipCandidateError(f"zip 파일이 아니거나 손상되었습니다: {e}") from e

    infos = zf.infolist()
    if len(infos) > _MAX_ENTRIES:
        raise ZipCandidateError(f"zip 항목이 너무 많습니다({len(infos)} > {_MAX_ENTRIES}) — 압축폭탄 의심")

    def _read_guarded(name: str) -> bytes:
        # 실제로 읽어서 압축 해제하는 항목에만 크기 상한을 건다 — bot zip은 종종
        # 우리가 전혀 안 읽는 무관한 대용량 커스텀 jar(bot-command-* 등)를 같이
        # 담고 있어서(실측: 700MB대 jar 포함 사례 확인), 모든 항목에 상한을 걸면
        # 정상적인 봇 zip까지 거부된다. manifest.json/워크플로우 JSON은 원래 작은
        # 파일이라 이 상한 안에서 자연스럽게 걸러진다.
        size = zf.getinfo(name).file_size
        if size > _MAX_ENTRY_BYTES:
            raise ZipCandidateError(f"{name} 항목이 너무 큽니다({size} bytes)")
        return zf.read(name)

    names = zf.namelist()
    manifest_name = _find_manifest_name(names)
    try:
        manifest = json.loads(_read_guarded(manifest_name).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ZipCandidateError(f"manifest.json 파싱 실패: {e}") from e

    workflow_entries = [f for f in manifest.get("files", []) if f.get("contentType") in WORKFLOW_CONTENT_TYPES]
    if not workflow_entries:
        raise ZipCandidateError(
            "manifest.json에 워크플로우 타입(taskbot/headlessbot/workflow) 파일이 없습니다 "
            "— form/prompt/aiagent만 있으면 채점 대상이 아닙니다"
        )

    catalog = _load_catalog()

    # 1단계: 이 zip 안의 모든 워크플로우 파일을 먼저 정규화해서, filename(확장자 없는
    # stem 아니라 manifest가 쓰는 실제 파일명) -> steps 매핑을 만든다 — same-zip 기준
    # main/sub 판정(어떤 파일이 다른 파일의 runTask 대상인지)에 전부 필요하기 때문.
    parsed: dict[str, dict] = {}
    parse_errors: list[dict] = []
    for entry in workflow_entries:
        manifest_path_value = entry.get("path") or ""
        filename = manifest_path_value.split("\\")[-1] if manifest_path_value else None
        if not filename:
            parse_errors.append({"filename": None, "error": "manifest 항목에 path가 없습니다"})
            continue
        zip_entry_name = _find_entry_by_basename(names, filename)
        if zip_entry_name is None:
            parse_errors.append({"filename": filename, "error": "zip 안에서 해당 파일을 찾을 수 없습니다"})
            continue
        try:
            raw = json.loads(_read_guarded(zip_entry_name).decode("utf-8"))
        except ZipCandidateError as e:
            parse_errors.append({"filename": filename, "error": str(e)})
            continue
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            parse_errors.append({"filename": filename, "error": f"JSON 파싱 실패: {e}"})
            continue
        parsed[filename] = {
            "steps": normalize_workflow(raw)["steps"],
            "content_type": entry.get("contentType"),
        }

    # 2단계: 파일별로 액션 쌍/참조/opaque 패키지를 뽑고, 서로의 runTask 참조 집합으로
    # main/sub를 판정한다(다른 파일이 나를 참조하면 나는 sub).
    referenced_by_others: set[str] = set()
    per_file: dict[str, dict] = {}
    for filename, data in parsed.items():
        pairs: list[tuple[str, str]] = []
        refs: set[str] = set()
        opaque: set[str] = set()
        collect_pairs_refs_and_opaque(data["steps"], pairs, refs, opaque)
        per_file[filename] = {"pairs": pairs, "refs": refs, "opaque": opaque, "content_type": data["content_type"]}
        referenced_by_others |= refs

    workflows_report = []
    for filename, info in per_file.items():
        pairs = info["pairs"]
        actions = [{"package": pkg, "action": act} for pkg, act in pairs]
        if catalog is not None:
            missing = sorted({f"{pkg}.{act}" for pkg, act in pairs if (pkg, act) not in catalog})
            for a in actions:
                a["in_catalog"] = (a["package"], a["action"]) in catalog
            coverage_pct = round(100 * (len(pairs) - len(missing)) / len(pairs), 1) if pairs else 0.0
        else:
            missing = None
            for a in actions:
                a["in_catalog"] = None
            coverage_pct = None

        workflows_report.append({
            "filename": filename,
            "content_type": info["content_type"],
            "action_count": len(pairs),
            "is_main_workflow_same_zip": filename not in referenced_by_others,
            "referenced_sub_workflows": sorted(info["refs"]),
            "opaque_packages": sorted(info["opaque"]),
            "rag_catalog_available": catalog is not None,
            "rag_coverage_pct": coverage_pct,
            "rag_missing_actions": missing,
            "actions": actions,
        })

    return {
        "manifest_name": manifest_name,
        "workflows": sorted(workflows_report, key=lambda w: w["filename"]),
        "parse_errors": parse_errors,
    }
