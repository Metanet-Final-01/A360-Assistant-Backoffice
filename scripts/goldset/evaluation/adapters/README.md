# Evaluation Adapters

Adapters are thin wrappers around external scoring libraries. Keep third-party source
outside this folder.

Expected external paths:

```text
../a360-eval-sandbox/external/pm4py
../a360-eval-sandbox/external/WorFBench
```

Adapters should expose small local functions such as:

```python
score_pm4py(gold_pnml, prediction_tree_json) -> dict
score_worfbench(gold_json, prediction_json) -> dict
```

They should return plain JSON-serializable dictionaries so `evaluation/` reports stay
stable even if the underlying library changes.
