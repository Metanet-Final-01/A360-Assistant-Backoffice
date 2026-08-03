# Evaluation Adapters

Adapters are thin wrappers around external scoring libraries. Keep third-party source
outside this folder.

Expected external paths:

```text
../a360-eval-sandbox/external/WorFBench
```

(PM4Py's adapter and converter were deleted 2026-08-03 - the redesign already excluded
PM4Py from active reporting, and the project confirmed it won't be used at all, so the
`a360-eval-sandbox/external/pm4py` checkout is no longer needed here.)

Adapters should expose small local functions such as:

```python
score_worfbench(gold_json, prediction_json) -> dict
```

They should return plain JSON-serializable dictionaries so `evaluation/` reports stay
stable even if the underlying library changes.
