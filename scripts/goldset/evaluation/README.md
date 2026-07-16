# Goldset Evaluation

`evaluation/` is for scoring and comparison. It should not build the dataset, call the
backend, or contain full third-party library checkouts.

## Inputs

Gold/reference side:

```text
eval_inputs/normalized_workflows_13/<case>/*.goldset.json
eval_inputs/pm4py_13/<case>/*
eval_inputs/worfbench_13/<case>/*
```

Prediction/backend side:

```text
runner/logs/<run-id>/converted_recommendation/normalized/*.goldset.json
runner/logs/<run-id>/converted_recommendation/pm4py/*
runner/logs/<run-id>/converted_recommendation/worfbench/*
```

The backend recommendation must be converted first with:

```bash
python processing/convert_backend_recommendation.py \
  runner/logs/<run-id>/run_manifest.json \
  --with-conversions
```

## External Libraries

Do not copy PM4Py or WorFBench source into this folder. Use adapters that reference the
external checkouts:

```text
../a360-eval-sandbox/external/pm4py
../a360-eval-sandbox/external/WorFBench
```

Adapter code belongs here:

```text
evaluation/adapters/pm4py_adapter.py
evaluation/adapters/worfbench_adapter.py
```

## Intended Shape

```text
evaluation/
  README.md
  adapters/
    README.md
    pm4py_adapter.py
    worfbench_adapter.py
  reports/
```

Run one converted backend recommendation against one fixed 13-case gold artifact:

```bash
python scripts/goldset/evaluation/run_eval_case.py \
  --case-id 03_0131_currency-rate---oanda \
  --run-id runner_v2_repeat_20260715_01
```

It writes:

```text
evaluation/reports/<run-id>/<case-id>/evaluation.json
evaluation/reports/<run-id>/<case-id>/evaluation.md
```

Current score layers:

```text
action_sequence      exact package.action order, strictest
action_multiset      exact package.action bag
package_multiset     exact package names only
package_family       aliases related package families, e.g. Excel_MS ~= Excel advanced
salient_family       package_family without setup/logging/runtime boilerplate
worfbench            actual WorFBench t_eval_nodes/f1chain over canonicalized Node/Edges
pm4py                actual PM4Py alignment fitness/precision over canonicalized artifacts
```

Diagnostic artifact checks are still recorded separately as:

```text
pm4py_artifact_check             PNML readability/hash/tree-size checks
worfbench_diagnostic_artifact_f1 local Node/Edges multiset F1 over converted artifacts
```

Preprocessing before scoring:

```text
browser_session_lifecycle_action
  package regex: ^(web\s*automation|webautomation|browser|recorder)$
  action regex:  session
```

These actions are excluded from gold and prediction scoring because the newer Browser
model no longer exposes the legacy WebAutomation session lifecycle as a normal action.
The rule is package-scoped so unrelated session actions such as `XML.startSession`
remain scoreable.

After excluded actions are removed, action-equivalence aliases from
`evaluation/action_equivalence_rules.json` are applied to produce the
`canonical_action_*` score layers. The original `action_*` layers remain exact
`package.action` scores without aliasing.

PM4Py and WorFBench adapters also apply only these human-confirmed equivalence
rules before invoking the external scorer. Candidate files generated under
`action_equivalence_candidates*/` are for human review only and are not treated as
confirmed mappings.

Example:

```text
--case-id 03_0131_currency-rate---oanda
--run-id runner_v2_repeat_20260715_01
```

## Boundary

- `processing/`: creates artifacts and format conversions.
- `runner/`: executes the frontend-equivalent backend flow.
- `evaluation/`: compares gold artifacts to backend artifacts and records scores.
- `analysis/`: corpus exploration and candidate-selection reports.
