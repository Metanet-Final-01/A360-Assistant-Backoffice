# Agent Flow Evaluation

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
  core_task.py
  adapters/
    README.md
    pm4py_adapter.py
    worfbench_adapter.py
  reports/
```

Run one converted backend recommendation against one fixed 13-case gold artifact:

```bash
python scripts/agent_flow_eval/evaluation/run_eval_case.py \
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
canonical_action     human-confirmed aliases applied after exact action extraction
core_task            canonical actions projected to business-result actions only
package_multiset     exact package names only
package_family       aliases related package families, e.g. Excel_MS ~= Excel advanced
salient_family       package_family without setup/logging/runtime boilerplate
worfbench            actual WorFBench t_eval_nodes/f1chain over canonicalized Node/Edges
pm4py                actual PM4Py alignment fitness/precision over canonicalized artifacts
core_worfbench       WorFBench over the core_task projection
core_pm4py           PM4Py over the core_task projection
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

The `core_task_*` layers are a separate projection, not a replacement for the
canonical scores. They first apply the confirmed canonical action mapping and then
keep only actions that directly affect external business systems, documents/files,
email/API/browser/spreadsheet work, or user-visible business outputs. Variable
shaping, logging, control markers, exception handlers, delays, debug UI, and session
lifecycle actions are excluded. This answers a narrower question: whether the main
business steps are present and ordered, while PM4Py/WorFBench on the full canonical
artifact still report broader implementation/structure fit.

Do not treat `core_pm4py_*` as the primary core-task score. PM4Py alignment is a
strict conformance check: a prediction can match several important business actions
and still get zero fitness if the trace cannot complete the gold process model. Use
`core_task_action_*` and `core_worfbench_*` first for partial core-task evidence, and
read `core_pm4py_*` as a strict diagnostic.

PM4Py and WorFBench adapters also apply only these human-confirmed equivalence
rules before invoking the external scorer. Candidate files generated under
`action_equivalence_candidates*/` are for human review only and are not treated as
confirmed mappings.

## Rule Governance

Canonical and core-task rules are evaluation policy, not throwaway scoring glue.
They must be updated from observed evaluation results, but not in a way that merely
chases a higher score for one case.

Canonical rules:

- Merge action names only when they represent the same A360 operation with a clear
  basis in the catalog, official package behavior, or reviewed gold/prediction data.
- Prefer stable internal action IDs over UI display names when selecting a canonical
  value.
- Keep ambiguous pairs as candidates until reviewed. Similar names are not enough.

Core-task rules:

- Include actions that directly create or change the business result: API calls,
  email sends, spreadsheet/document operations, browser form work, external-system
  updates, and visible file/document outputs.
- Exclude control markers, variable shaping, logs, debug UI, waits, exception
  handlers, and session lifecycle actions.
- Keep ambiguous packages conservative by default. `File`, `Folder`, `XML`, `JSON`,
  `String`, `Datetime`, and generic browser scripting can be core in a specific
  workflow, but should not be package-wide core without reviewed action-level rules.

Review loop:

1. Run the batch and inspect `missing_core_actions`, `extra_core_actions`, first
   mismatches, and low-scoring cases.
2. Add candidate mappings or core classifications separately from confirmed rules.
3. Promote only reviewed candidates into the rule files.
4. Re-run the same batch and record before/after score changes with the reason for
   each rule update.

Avoid a single weighted total score. PM4Py, WorFBench, canonical action matching,
and core-task projection answer different questions and should be reported in
parallel.

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
