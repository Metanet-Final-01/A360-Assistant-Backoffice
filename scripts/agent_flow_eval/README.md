# Agent Flow Evaluation Workspace

This directory is split by responsibility. Keep this boundary simple:

```text
processing/   build and transform A360 workflow gold artifacts
eval_inputs/  the fixed 13 PDF eval inputs and their backing artifacts
runner/       call the live backend like the frontend does
evaluation/   compare backend flowcharts against gold artifacts and write scores
analysis/     read-only corpus investigation and candidate-selection reports
dataset/      curated raw corpus baseline; avoid generated scoring outputs here
```

## Main Flow

```text
processing/extract_workflows.py
  -> dataset/<category>/<bot>/workflows/*.json

processing/collect_eval_input_extracted_workflows.py
  -> eval_inputs/extracted_workflows_13/

processing/build_eval_input_artifacts.py
  -> eval_inputs/normalized_workflows_13/
  -> eval_inputs/pm4py_13/
  -> eval_inputs/worfbench_13/
  -> eval_inputs/comparison_reports/

runner/runner_v2.py
  -> runner/logs/<run-id>/

processing/convert_backend_recommendation.py
  -> runner/logs/<run-id>/converted_recommendation/

evaluation/
  -> compares gold vs converted backend recommendation
```

## Rules Of Thumb

- Need original workflow evidence? Start with `eval_inputs/extracted_workflows_13/`
  or raw `dataset/**/workflows/*.json`.
- Need the 13 gold answers? Use `eval_inputs/normalized_workflows_13/`.
- Need live backend output? Use `runner/logs/<run-id>/`.
- Need scoring? Use `evaluation/`; do not put scoring orchestration in
  `processing/`.
- RAGAS retrieval/answer evaluation belongs in `../ragas_eval/`, not here.
- Do not vendor full third-party libraries into this folder. Use
  `../a360-eval-sandbox/external/pm4py` and
  `../a360-eval-sandbox/external/WorFBench`.
