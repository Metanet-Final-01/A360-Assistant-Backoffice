# Agent Flow Evaluation Workspace

> **처음 보는 사람은 [`docs/`](docs/README.md)부터 읽으세요.** 봇 zip에서
> 시작해 에이전트 점수가 나오기까지의 전 과정과, 각 규칙을 왜 그렇게 정했고
> 어떻게 검증했는지를 사전 지식 없이 읽을 수 있게 정리해 뒀습니다.
> 이 README는 폴더 배치와 스크립트 실행 순서만 다룹니다.

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
- **Need the 9 confirmed gold answers (the official set)?** Use
  `goldset_expansion/confirmed_goldset/gold/`, and score them with
  `evaluation/audit_final_goldset.py` — **not** `run_eval_batch.py`, which
  only handles the legacy 13.
- Need live backend output? Use `runner/logs/<run-id>/`.
- Need scoring? Use `evaluation/`; do not put scoring orchestration in
  `processing/`.
- RAGAS retrieval/answer evaluation belongs in `../ragas_eval/`, not here.
- Do not vendor full third-party libraries into this folder. Use
  `../a360-eval-sandbox/external/WorFBench`.
- **PM4Py is gone** (deleted 2026-08-03, adapter and converter both). It scored
  fitness 0.089 / precision 0.0 on real data because conformance checking treats
  normal implementation differences as deviations — see
  [`docs/appendix-b-pm4py-rejected.md`](docs/appendix-b-pm4py-rejected.md)
  before considering it again.
