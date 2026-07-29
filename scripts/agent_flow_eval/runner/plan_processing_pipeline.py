from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessingStep:
    name: str
    script: str
    default_enabled: bool = True
    destructive: bool = False


PROCESSING_STEPS = (
    ProcessingStep("unpack_selected_zips", "processing/unpack_selected_zips.py"),
    ProcessingStep("normalize_manifests", "processing/normalize_manifests.py"),
    ProcessingStep("remove_bot_command_jars", "processing/remove_bot_command_jars.py", default_enabled=False, destructive=True),
    ProcessingStep("archive_custom_jar_metadata", "analysis/archive_custom_jar_metadata.py"),
    ProcessingStep("remove_custom_jars", "processing/remove_custom_jars.py", default_enabled=False, destructive=True),
    ProcessingStep("extract_workflows", "processing/extract_workflows.py"),
    ProcessingStep("normalize_extracted_workflows", "processing/normalize_extracted_workflows.py"),
    ProcessingStep("resolve_subtask_coverage", "processing/resolve_subtask_coverage.py"),
    ProcessingStep("convert_to_pm4py", "processing/convert_to_pm4py.py"),
    ProcessingStep("convert_to_worfbench", "processing/convert_to_worfbench.py"),
)
