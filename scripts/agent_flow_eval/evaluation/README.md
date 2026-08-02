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
  SCORING_REDESIGN_PLAN.md   design rationale for the action_matching/action_chain redesign (2026-07-30)
  action_matching.py          Rule Match + Judge Match engine, Action P/R/F1 (active primary metric)
  action_chain.py              LIS/LCS-based Action Chain P/R/F1 (active primary metric)
  action_equivalence_rules.json               human-confirmed plain package.action aliases
  action_equivalence_rules_conditional.json   attribute-conditioned aliases (e.g. Recorder.capture only when operation=CLICK)
  gold_core_actions/<case-id>.json  per-case uid include/exclude (manual "implementation trick" exclusion, gold side only)
  critical_attribute/          Recorder/WebAutomation-style cross-package attribute comparison, feeds action_matching.py
  adapters/
    README.md
    pm4py_adapter.py           kept, no longer called from the active report (see below)
    worfbench_adapter.py       kept, "external benchmark reference" only (see below)
  reports/
```

**`core_task.py` was deleted (2026-07-30)** along with this file's old `package_family()`/
`salient_families()` — they were an unjustified hardcoded package classification
(confirmed via `git log` that the introducing commit had no documented rationale, and it
directly contradicted itself on File/Folder/XML/JSONHandler/Dictionary/List). Do not
reintroduce a "core package" classification without the same evidence bar this project
otherwise holds itself to.

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

Current score layers (as of the 2026-07-30 redesign, see `SCORING_REDESIGN_PLAN.md`
for the full rationale):

```text
action_prf1          PRIMARY. action_matching.py: Rule Match (canonical label +
                     attribute-conditioned equivalence) + Judge Match (embedding
                     mutual-Top-1 candidate -> gpt-4o-mini verdict) -> explicit 1:1
                     (gold_id, pred_id) pairs -> Precision/Recall/F1.
action_chain         PRIMARY. action_chain.py: LIS over the action_prf1 match pairs
                     sorted by predicted order (cross-checked against an LCS-DP over
                     the same match relation - they must be numerically equal since
                     the matching is already a fixed 1:1 assignment; a mismatch means
                     a bug in the matching, not a real scoring signal). NOT a pure
                     order-accuracy metric - missing/extra actions affect it too.
action_sequence      legacy. exact package.action order (plain LCS), no aliasing.
action_multiset      legacy. exact package.action bag, no aliasing.
canonical_action     legacy. action_equivalence_rules.json aliases applied, still
                     plain-label multiset/LCS (no 1:1 pairing, no attribute matching).
package_multiset     legacy. exact package names only.
worfbench            EXTERNAL REFERENCE ONLY. actual vendored WorFEval t_eval_nodes
                     (all-mpnet-base-v2 embedding, match_threshold=0.6, real
                     networkx max-weight-matching + LIS chain-F1) - kept unmodified
                     as a comparison point against the published benchmark, not used
                     to judge our agents.
```

**Removed (2026-07-30), not just deprioritized:** `core_task`, `package_family`,
`salient_family`, `core_worfbench`, `core_pm4py`, and active use of `pm4py`/
`pm4py_artifact_check`. See the "Intended Shape" section above for why. PM4Py's
adapter code is kept on disk but is no longer called by `run_eval_case.py`'s active
report - the "sample one implementation vs compare against an allowed process model"
design PM4Py assumes doesn't fit "compare one human implementation against one agent
implementation," and it scored a real, correct implementation-difference case at
fitness 0.089 / precision 0.0 in practice.

Diagnostic artifact checks are still recorded separately as:

```text
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
`evaluation/action_equivalence_rules.json` are applied to produce the legacy
`canonical_action_*` score layers. The original `action_*` layers remain exact
`package.action` scores without aliasing. These legacy layers are plain label
multiset/LCS only - they don't do 1:1 pairing or attribute comparison, which is
why `action_prf1`/`action_chain` (via `action_matching.py`/`action_chain.py`)
are the confirmed primary layers instead (see above).

Instead of a separate "core task" package/label projection, the current design
excludes implementation-detail actions on a per-case, per-uid basis via
`gold_core_actions/<case-id>.json` (§7 of `SCORING_REDESIGN_PLAN.md`) - a human
reviews the gold workflow once and marks specific uids `include: false` with a
reason (e.g. a repeated manual-cell-formatting block), rather than relying on a
package-wide or label-wide rule. This is gold-side only; there is currently no
equivalent exemption for predicted actions that happen to implement the same
excluded detail a different way (tracked as an open gap, see
`HANDOFF_CODEX.md`).

WorFBench's adapter is called only in its unmodified, external-reference form -
it does not apply our `action_equivalence_rules.json` aliases, since the point
of keeping it is to reproduce the published benchmark's own behavior, not to
improve its score with our own rules. Candidate files generated under
`action_equivalence_candidates*/` are for human review only and are not treated
as confirmed mappings until promoted into `action_equivalence_rules.json` /
`action_equivalence_rules_conditional.json`.

## Rule Governance

Equivalence rules and gold core-action exclusions are evaluation policy, not
throwaway scoring glue. They must be updated from observed evaluation results,
but not in a way that merely chases a higher score for one case.

Canonical/equivalence rules (`action_equivalence_rules.json`,
`action_equivalence_rules_conditional.json`):

- Merge action names only when they represent the same A360 operation with a clear
  basis in the catalog, official package behavior, or reviewed gold/prediction data.
  Do not merge just because two actions serve a similar higher-level business
  purpose - `judge_action_equivalence()` is explicitly instructed to check atomic
  substitutability, not shared intent, and the same standard applies to plain
  rule entries.
- Conditional rules must be verified against real attribute values from actual
  gold/prediction data before being added (e.g. confirming `Recorder.capture`'s
  `operation` value actually matches the `WebAutomation` action it's paired with) -
  not added on the assumption that two actions with similar names probably behave
  the same (see `Excel_MS.CreateSpreadsheet` vs `OpenSpreadsheet`, which looked
  like aliases but were confirmed NOT equivalent once real parameter values were
  checked - the prediction's `filePath` was null).
- Prefer stable internal action IDs over UI display names when selecting a canonical
  value.
- Keep ambiguous pairs as candidates until reviewed. Similar names are not enough.

Gold core-action exclusions (`gold_core_actions/<case-id>.json`):

- Written once per case by a human reviewing the gold workflow, not derived from
  a package- or label-wide rule (there is no `core_task`-style package
  classification anymore - see above for why that was removed).
- Exclude only actions that are a genuine implementation detail of one specific
  gold recording (e.g. a repeated manual cell-formatting block), not actions that
  are merely low-frequency or unfamiliar.
- Record a `reason` string per excluded uid so a later reviewer can see why
  without re-deriving it from the raw workflow.

Review loop:

1. Run the batch and inspect `unmatched_gold`/`unmatched_pred`, the `judge_log`,
   and low-scoring cases.
2. Add candidate equivalence mappings separately from confirmed rules (under
   `action_equivalence_candidates*/`).
3. Promote only reviewed candidates into the rule files.
4. Re-run the same batch and record before/after score changes with the reason for
   each rule update.

Avoid a single weighted total score. `action_prf1`/`action_chain` (primary),
the legacy plain-label layers, and WorFEval (external reference) answer different
questions and should be reported in parallel.

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
