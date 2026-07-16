# Goldset Processing Pipeline

This folder contains the data-processing steps for the goldset/eval-input corpus.
The key rule is:

- `extract_workflows.py` produces the raw workflow JSON baseline.
- `normalize_extracted_workflows.py` produces a derived ordered-steps artifact for scoring.
- Runner code should orchestrate these scripts, not duplicate their transformation logic.
- Scoring/evaluation code belongs in `../evaluation/`, not here.

## Flow

```text
Bot Store archives / unpacked candidates
  |
  v
unpack_selected_zips.py
  |
  v
normalize_manifests.py
  |
  v
remove_bot_command_jars.py
remove_custom_jars.py
  |
  v
extract_workflows.py
  |
  |  RAW BASELINE
  |  dataset/<category>/<bot>/workflows/*.json
  |  dataset/<category>/<bot>/workflow_index.json
  |
  +----------------------------+
  |                            |
  v                            v
collect_eval_input_extracted_workflows.py
  |                            normalize_extracted_workflows.py
  |                              |
  |                              |  DERIVED SCORING ARTIFACT
  |                              |  dataset/<category>/<bot>/workflows/*.goldset.json
  |                              |
  |                              +--> resolve_subtask_coverage.py
  |                              +--> convert_to_pm4py.py
  |                              +--> convert_to_worfbench.py
  |
  v
eval_inputs/extracted_workflows_13/
  |
  v
build_eval_input_artifacts.py
  |
  +--> eval_inputs/normalized_workflows_13/
  +--> eval_inputs/pm4py_13/
  +--> eval_inputs/worfbench_13/
  +--> eval_inputs/comparison_reports/

runner/logs/<run-id>/run_manifest.json
  |
  v
convert_backend_recommendation.py
  |
  +--> runner/logs/<run-id>/converted_recommendation/normalized/
  +--> runner/logs/<run-id>/converted_recommendation/pm4py/
  +--> runner/logs/<run-id>/converted_recommendation/worfbench/
```

## Stages

### 1. Unpack

`unpack_selected_zips.py`

- Input: selected Bot Store ZIP/MSI candidates under the raw `Test/` working area.
- Output: unpacked candidate folders grouped by category.
- Purpose: create a readable filesystem corpus from downloaded packages.

### 2. Normalize Manifests

`normalize_manifests.py`

- Input: unpacked bot folders containing `manifest.json`.
- Output: `manifest.normalized.json`.
- Purpose: pretty-print and sort manifest data without changing values.

### 3. Remove JAR Binaries

`remove_bot_command_jars.py`

- Purpose: remove standard bot-command JAR binaries after checking manifest references.

`remove_custom_jars.py`

- Purpose: remove custom JAR binaries after their metadata has been exported.

These cleanup steps are about corpus hygiene. They should not change workflow JSON semantics.

### 4. Extract Raw Workflows

`extract_workflows.py`

- Input: unpacked/curated bot folders and each bot `manifest.json`.
- Selects files whose `contentType` is an actual workflow type:
  - `application/vnd.aa.taskbot`
  - `application/vnd.aa.headlessbot`
  - `application/vnd.aa.workflow`
- Output:
  - `dataset/<category>/<bot>/workflows/<workflow>.json`
  - `dataset/<category>/<bot>/workflow_index.json`
  - `dataset/workflow_extraction_report.json`
  - `dataset/workflow_extraction_report.md`

This is the raw baseline. The output keeps Automation Anywhere workflow structure:

```json
{
  "nodes": [],
  "variables": [],
  "packages": [],
  "triggers": []
}
```

It is not byte-identical to the original file because it is read as JSON and written
again with pretty formatting and sorted keys. It should preserve the workflow data
needed for analysis and reconstruction as JSON.

### 5. Collect The 13 Eval-Input Backing Workflows

`collect_eval_input_extracted_workflows.py`

- Input:
  - `eval_inputs/task_briefs.json`
  - raw workflow outputs from `extract_workflows.py`
- Output:
  - `eval_inputs/extracted_workflows_13/<case>/workflows/*.json`
  - `eval_inputs/extracted_workflows_13/<case>/workflow_index.json`
  - `eval_inputs/extracted_workflows_13/manifest.json`

This folder is the backing source set for the 13 generated PDF inputs. It keeps
physical raw workflow files, so it contains 14 raw workflows: the
`0338_lettergenerationbot` PDF is backed by both `LetterGenerationBot.json` and
`SendPOF_Email.json`.

The `source_file` values in `task_briefs.json` still use historical
`*.goldset.json` stems for stable PDF filenames. This collector maps them back to
raw extracted workflow names:

```text
CreateProjectInJIRAusingAPI.goldset.json -> CreateProjectInJIRAusingAPI.json
```

### 5b. Build Clean 13-Case Eval Artifacts

`build_eval_input_artifacts.py`

- Input:
  - `eval_inputs/extracted_workflows_13/`
  - `eval_inputs/task_briefs.json`
  - `eval_inputs/pdfs/`
- Output:
  - `eval_inputs/normalized_workflows_13/`
  - `eval_inputs/pm4py_13/`
  - `eval_inputs/worfbench_13/`
  - `eval_inputs/comparison_reports/`

This script builds a temporary dataset-shaped workspace, runs the normal processing
steps there, then copies only the 13-case artifacts back under `eval_inputs/`.
For multi-source PDF cases, it collapses the component normalized workflows into one
case-level artifact before PM4Py/WorFBench conversion. Today that applies to
`0338_lettergenerationbot`, which becomes
`LetterGenerationBot+SendPOF_Email.goldset.json`.

Use this when the eval input set needs clean, folder-separated artifacts without
polluting `dataset/` with regenerated scoring outputs.

### 6. Normalize Raw Workflows For Scoring

`normalize_extracted_workflows.py`

- Input: raw workflow JSONs from `extract_workflows.py`.
- Output:
  - `dataset/<category>/<bot>/workflows/<workflow>.goldset.json`
  - `dataset/workflow_normalization_report.json`
  - `dataset/workflow_normalization_report.md`

This is a derived artifact, not the raw source. It walks raw `nodes` depth-first and
turns nested Automation Anywhere workflow structure into ordered `steps`.

Main rules:

- `Step` is transparent: its children are spliced into the surrounding order.
- `Comment` is dropped.
- normal action nodes become `{"type": "action", "package": ..., "action": ...}`.
- `If`, `Loop`, `ErrorHandler`, and `TriggerLoop` preserve nested `steps` and
  `branches`.
- unknown nodes with children become `container` steps rather than being discarded.

Output shape:

```json
{
  "source_file": "CreateProjectInJIRAusingAPI.json",
  "triggers": [],
  "steps": []
}
```

The `.goldset.json` extension is kept because downstream converters already consume
that filename pattern. Treat it as "normalized scoring goldset", not raw workflow.

### 7. Resolve Subtask Coverage

`resolve_subtask_coverage.py`

- Input: normalized `*.goldset.json` files.
- Output:
  - `dataset/subtask_transitive_coverage_report.json`
  - `dataset/subtask_transitive_coverage_report.md`
- Purpose: recursively resolve `TaskBot.runTask` references to sibling workflow files
  and report the full action footprint.

This step does not modify or merge workflow files. It only writes a report.

If a workflow has no `TaskBot.runTask` subtask reference, it is not skipped. It still
gets one report row with:

- `own_refs: []`
- `resolved_subtask_files: []`
- `unresolved_subtask_references: []`
- `transitive_action_count` equal to its own reachable action count
- `transitive_fully_covered` determined by its own actions only

So this step is safe to run over the whole normalized corpus.

### 8. Convert To PM4Py

`convert_to_pm4py.py`

- Input: normalized `*.goldset.json` files.
- Output next to each normalized workflow:
  - `*.pnml`
  - `*.ptml`
  - `*.tree.json`
- Purpose: convert ordered steps/control flow into PM4Py process-tree/Petri-net
  formats for scoring experiments.

### 9. Convert To WorFBench

`convert_to_worfbench.py`

- Input: normalized `*.goldset.json` files.
- Output next to each normalized workflow:
  - `*.worfbench.json`
- Purpose: create WorFBench Node/Edges records.

Caveat: WorFBench cannot faithfully represent every branchy workflow. This converter
uses a canonical path approximation and labels outputs with `worfbench_fidelity`.

### 10. Merge Subworkflows

`merge_subworkflows.py`

- Input: normalized `*.goldset.json` files.
- Output: `<name>.merged.goldset.json`.
- Purpose: replace `TaskBot.runTask` steps with referenced sub-workflow steps.

This is optional and manual. It is not called by `resolve_subtask_coverage.py` and is
not part of the default pipeline.

Use it only after `resolve_subtask_coverage.py` shows that a candidate:

- has real resolved subtasks (`resolved_subtask_files` is not empty)
- is transitively covered (`transitive_fully_covered: true`)
- needs one self-contained normalized workflow instead of a parent workflow plus
  sibling sub-workflow files

If there are no subtasks, do not run this step. There is nothing to merge.

### 11. Convert Backend Recommendations

`convert_backend_recommendation.py`

- Input: `runner_v2.py` `run_manifest.json`.
- Default source step: `turnRecommend`.
- Output:
  - `converted_recommendation/normalized/*.goldset.json`
  - optionally `converted_recommendation/pm4py/*`
  - optionally `converted_recommendation/worfbench/*`

This converts backend recommendation JSON into the same normalized shape used by
`normalize_extracted_workflows.py`, while preserving recommendation-specific fields:

- top-level `answer`, `sources`, `variables`, `notes`, `usage_gauge`
- full `original_recommendation`
- per-business-step metadata in container attributes
- per-action labels, parameters, rationale, sources, confidence, and original action JSON

The normalized output uses business-step containers and action leaves. Existing
PM4Py/WorFBench converters treat containers as transparent and score the ordered action
sequence.

Example:

```bash
python processing/convert_backend_recommendation.py \
  runner/logs/runner_v2_repeat_20260715_01/run_manifest.json \
  --with-conversions
```

Recorded baseline run:

- Run: `runner_v2_repeat_20260715_01`
- Step: `turnRecommend`
- HTTP/SSE: `200 OK`, `done`
- Recommendation: 5 top-level steps, 8 actions, 5 variables, 9 sources

## What To Trust

- For original workflow evidence, trust:
  - `dataset/<category>/<bot>/workflows/*.json`
  - `eval_inputs/extracted_workflows_13/<case>/workflows/*.json`
- For ordered evaluation/scoring, trust:
  - `eval_inputs/normalized_workflows_13/<case>/*.goldset.json` for the 13 eval cases
  - `dataset/<category>/<bot>/workflows/*.goldset.json` only when intentionally
    regenerating whole-corpus scoring artifacts
- For the 13 frontend-upload PDF inputs, trust:
  - `eval_inputs/pdfs/`
  - `eval_inputs/task_briefs.json`
  - `eval_inputs/extracted_workflows_13/manifest.json`
  - `eval_inputs/comparison_reports/pdf_vs_normalized.md`

## Common Confusion

`*.json` and `*.goldset.json` are not the same level of artifact.

- `*.json`: raw extracted workflow from Automation Anywhere files.
- `*.goldset.json`: derived ordered-steps representation for scoring adapters.

If the question is "what did the original workflow contain?", start from `*.json`.
If the question is "what ordered steps should a scorer compare?", start from
`*.goldset.json`.

## What Not To Put Here

- live backend calls: use `../runner/`
- PM4Py/WorFBench scoring orchestration: use `../evaluation/`
- full third-party library checkouts: keep using
  `../a360-eval-sandbox/external/pm4py` and
  `../a360-eval-sandbox/external/WorFBench`
