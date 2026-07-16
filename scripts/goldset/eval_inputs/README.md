# Eval Inputs

Plain-text stand-ins for a business task definition PDF like the reference
`A360 과제1 첨부자료.pdf`. These are the input side of the evaluation: the agent
receives one PDF as if a real user uploaded it, and the result is checked against
the corresponding raw workflow JSON copied from `processing/extract_workflows.py`
output.

- `task_briefs.json` - one entry per finalized eval task, 13 total. Each brief is
  written at business-task abstraction: real business steps and systems, not RPA
  implementation detail such as parameter validation, setup/cleanup, logging, or
  generic try/catch boilerplate.
- `source_file` / `source_files` in `task_briefs.json` keep the historical
  `.goldset.json` stem so existing PDF filenames remain stable. For current eval
  lookup, the same stem maps to the raw extracted workflow JSON:
  `CreateProjectInJIRAusingAPI.goldset.json` -> `CreateProjectInJIRAusingAPI.json`.
- `extracted_workflows_13/` is the canonical backing set for these 13 PDFs. It
  contains one case directory per PDF, each with `workflow_index.json` and
  `workflows/*.json` copied from the curated dataset output produced by
  `processing/extract_workflows.py`.
- `normalized_workflows_13/` contains the regenerated ordered `*.goldset.json`
  scoring artifacts for the same 13 cases. It has 13 case-level files; the `0338`
  case combines `LetterGenerationBot` and `SendPOF_Email` into
  `LetterGenerationBot+SendPOF_Email.goldset.json`.
- `pm4py_13/` contains regenerated PM4Py artifacts for the same 13 cases:
  `*.pnml`, `*.ptml`, and `*.tree.json`.
- `worfbench_13/` contains regenerated WorFBench `*.worfbench.json` records for the
  same 13 cases.
- `comparison_reports/` contains PDF-text vs normalized-workflow comparison reports.
- `0338_lettergenerationbot` is one business task backed by two physical workflow
  files: `LetterGenerationBot.json` and `SendPOF_Email.json`. That is why there are
  13 eval tasks but 14 raw workflow JSON files. Derived scoring artifacts collapse
  those two physical workflows back into one case-level artifact.
- `render_task_briefs.py` renders `task_briefs.json` into one landscape PDF per
  entry under `pdfs/<bot_name>__<source_file_stem(s)>.pdf`.
- `pdfs/` contains the 13 generated eval-input PDFs.

To regenerate PDFs after editing `task_briefs.json`:

```bash
python render_task_briefs.py
```

To regenerate all 13-case artifacts after editing the backing raw workflows:

```bash
python ../processing/build_eval_input_artifacts.py
```
