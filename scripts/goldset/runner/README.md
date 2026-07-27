# Goldset Runner

`runner/` is the orchestration layer for goldset evaluation work.

It should not duplicate the transformation logic in `processing/`. Instead, it calls
selected `processing/*.py` steps, records what was run, and writes a durable log folder
for each run.

## Folder Layout

- `runner_plan.md`
  - shared design notes for the runner pipeline.
- `plan_processing_pipeline.py`
  - prints and optionally writes the processing-step plan.
  - does not run backend agent evaluation.
- `run_processing_pipeline.py`
  - executes selected processing scripts in order.
  - every subprocess gets stdout/stderr logs under `logs/<run-id>/`.
- `logs/`
  - run-by-run execution records.
  - generated log files are local working artifacts.

## Boundary

The runner may orchestrate these existing processing steps:

1. `processing/unpack_selected_zips.py`
2. `processing/normalize_manifests.py`
3. `processing/remove_bot_command_jars.py`
4. `processing/archive_custom_jar_metadata.py` or equivalent jar metadata exporter
5. `processing/remove_custom_jars.py`
6. `processing/extract_workflows.py`
7. `processing/normalize_extracted_workflows.py`
8. `processing/resolve_subtask_coverage.py`
9. `processing/convert_to_pm4py.py`
10. `processing/convert_to_worfbench.py`

The runner should only add orchestration, logging, and reproducibility metadata.

Agent v1/v2 live calls and scoring will be separate runner phases after this processing
pipeline is agreed on.

## Live Agent Runner

- `runner_v2.py`
  - reproduces the real frontend flow for one eval-input PDF:
    1. `POST /api/documents`
    2. `POST /api/documents/{id}/parse`
    3. `POST /api/sessions/{session_id}/turn` with `agent_version="v2"` and the frontend analysis message
    4. `POST /api/sessions/{session_id}/turn` with `agent_version="v2"` and the frontend recommendation message
  - records every HTTP response and SSE event under `logs/<run-id>/`.

Example:

```
python runner/runner_v2.py --base-url http://localhost:8000
python runner/runner_v2.py --input eval_inputs/pdfs/0131_currency-rate---oanda__Currency\ Rate\ -\ Oanda.pdf
```
