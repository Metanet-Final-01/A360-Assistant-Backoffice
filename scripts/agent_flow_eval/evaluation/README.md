# Agent Flow Evaluation

`evaluation/` is for scoring and comparison. It should not build the dataset, call the
backend, or contain full third-party library checkouts.

## Inputs

Gold/reference side:

```text
eval_inputs/normalized_workflows_13/<case>/*.goldset.json
eval_inputs/worfbench_13/<case>/*
```

Prediction/backend side:

```text
runner/logs/<run-id>/converted_recommendation/normalized/*.goldset.json
runner/logs/<run-id>/converted_recommendation/worfbench/*
```

The backend recommendation must be converted first with:

```bash
python processing/convert_backend_recommendation.py \
  runner/logs/<run-id>/run_manifest.json \
  --with-conversions
```

## External Libraries

Do not copy WorFBench source into this folder. Use adapters that reference the
external checkout:

```text
../a360-eval-sandbox/external/WorFBench
```

Adapter code belongs here:

```text
evaluation/adapters/worfbench_adapter.py
```

(`pm4py_adapter.py`/`processing/convert_to_pm4py.py` were deleted 2026-08-03 - PM4Py
was already excluded from active reporting, and the project confirmed it won't be used
at all, so the code and its `eval_inputs/pm4py_13/`, `.../converted_recommendation/pm4py/`
outputs were removed rather than kept unused. The small label-normalization helpers that
`worfbench_adapter.py` had been importing from `pm4py_adapter.py` (`_canonical_label`,
`_split_action_label`, `load_action_equivalence_map`) now live directly in
`worfbench_adapter.py` - they were never PM4Py-specific.)

## Intended Shape

```text
evaluation/
  README.md
  SCORING_REDESIGN_PLAN.md   design rationale for the action_matching/action_chain redesign (2026-07-30)
  action_matching.py          Rule Match + Judge Match engine, Action P/R/F1 (active primary metric)
  action_chain.py              LIS/LCS-based Action Chain P/R/F1 (active primary metric)
  action_equivalence_rules.json               human-confirmed plain package.action aliases
  action_equivalence_rules_conditional.json   attribute-conditioned aliases (e.g. Recorder.capture only when operation=CLICK)
  gold_core_actions/<case-id>.json  diagnostic-only historical UID annotations; not used by official scoring
  gold_core_actions/_llm_classification_cache.json  shared cache for classify_core_business_actions() (see below) - different system, same directory
  audit_final_goldset.py       actual batch scorer for the 9 confirmed-goldset cases (v2/v3 comparison + audit.json/summary.csv/summary.md); run_eval_batch.py below is legacy-13-only
  export_audit_to_excel.py     turns audit_final_goldset.py's audit.json into a multi-sheet .xlsx (overview, matches, unmatched, judge log, core-business log, branch detail)
  critical_attribute/          Recorder/WebAutomation-style cross-package attribute comparison, feeds action_matching.py
  adapters/
    README.md
    worfbench_adapter.py       "external benchmark reference" only (see below) - also owns the
                               small label-normalization helpers pm4py_adapter.py used to have
  reports/
```

`run_eval_batch.py`/`run_eval_case.py` resolve paths under `eval_inputs/normalized_workflows_13/`
only - they score the original 13-case regression set. The 9 confirmed-goldset cases
(`goldset_expansion/confirmed_goldset/`) are scored by `audit_final_goldset.py`, which reads
Gold from `goldset_expansion/export_main_challenge/deliverable/Main/` (gitignored, regenerable;
verified byte-identical to `confirmed_goldset/gold/` for the cases checked) and predictions from
`runner/logs/<run_id>/converted_recommendation/normalized/`.

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
action_prf1          Rule Match plus optional Judge Match. Final-goldset audits report
                     deterministic Rule-only and nondeterministic Judge-assisted
                     values separately; they must not be presented as one result.
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
`pm4py_artifact_check`. See the "Intended Shape" section above for why - the "sample
one implementation vs compare against an allowed process model" design PM4Py assumes
doesn't fit "compare one human implementation against one agent implementation," and
it scored a real, correct implementation-difference case at fitness 0.089 / precision
0.0 in practice. **PM4Py's adapter/converter code itself was deleted outright on
2026-08-03** (not just excluded from the active report) once the project confirmed
it wouldn't be revisited - do not reintroduce `pm4py_adapter.py`/`convert_to_pm4py.py`
without the same evidence bar this project otherwise holds itself to.

Diagnostic artifact checks are still recorded separately as:

```text
worfbench_diagnostic_artifact_f1 local Node/Edges multiset F1 over converted artifacts
```

Rule-based conversion applied symmetrically to Gold and prediction:

```text
Comment
disabled node and its descendants
Logging / LogToFile
approved Browser / Email session lifecycle
actions inside catch branches
```

The shared implementation is `action_filters.normalize_steps_for_evaluation()` and
is called by both raw-Gold and backend-recommendation converters. Scorers retain the
same checks only for compatibility with older converted artifacts. MessageBox, Screen,
Excel formatting, variable assignment, and path assembly remain scoreable because they
can be requested business work. `XML.startSession` and Excel close actions also remain
scoreable.

After excluded actions are removed, action-equivalence aliases from
`evaluation/action_equivalence_rules.json` are applied to produce the legacy
`canonical_action_*` score layers. The original `action_*` layers remain exact
`package.action` scores without aliasing. These legacy layers are plain label
multiset/LCS only - they don't do 1:1 pairing or attribute comparison, which is
why `action_prf1`/`action_chain` (via `action_matching.py`/`action_chain.py`)
are the confirmed primary layers instead (see above).

`gold_core_actions/<case-id>.json` is not used by official scoring. The files are
retained only as historical analysis because UID exclusions apply to Gold alone and
cannot be reproduced symmetrically on predictions. New exclusions must be deterministic
rules in the shared converter, supported independently of whether they raise a score.
Likewise, a Judge-only match is not promoted to a converter rule without independent
package/action evidence.

### Core-business classification (2026-08-03) - a different, symmetric approach

`classify_core_business_actions()` (`action_matching.py`) is **not** the rejected
`gold_core_actions/` UID-list approach above and does not read or write those files.
It exists because a handful of `(package, action)` pairs are genuinely ambiguous -
the exact same action type is boilerplate setup in one occurrence and real business
work in another (confirmed on real data: `Folder.createFolder` builds a log folder
in one call and the actual archive folder the business definition asks for in
another, in the same case). Package/action name alone cannot resolve this.

The design is deliberately narrow and applied **symmetrically to both Gold and
prediction actions**, only when a case has a matching business definition under
`goldset_expansion/confirmed_goldset/briefs/<case_id>_*.md` (looked up via
`load_business_definition()`; cases without one - e.g. the legacy 13-case set -
skip classification entirely and keep the old all-actions-are-core behavior):

1. Only `(package, action)` pairs in `action_filters.AMBIGUOUS_GENERIC_ACTIONS`
   are ever considered (`Folder.createFolder/deleteFolder`, `File.createFile`,
   `String.assign`, `Datetime.subtract/toString/assign`, `Number.assignToNumber`,
   `Boolean.assign`, `MessageBox.messageBox`). Everything else is always core -
   no observed case needed a broader list.
2. Free keyword rule first: if the action's real parameter text matches
   `log|audit|error|snapshot|observability` (`INFRASTRUCTURE_KEYWORD_RE`), it is
   excluded without any API call. Verified against all 9 confirmed-goldset cases
   with zero false positives/negatives before being adopted.
3. Only the remainder goes to `judge_core_business_relevance()` (gpt-4o-mini,
   temperature=0), given the actual business-definition text, case title, and the
   action's real parameter values - not just the action name. Results are cached
   in `gold_core_actions/_llm_classification_cache.json` keyed by
   `case_id|raw_label|readable_params`, shared across cases and reruns, so a
   reproducibility rerun does not re-spend API calls on unchanged inputs.

Cross-checked against the older human-reviewed `gold_core_actions/0089.json` and
`0098.json` (independent judgment, not consulted by this function): 52 of 52 of
their exclusions were also excluded by this classifier, plus 2 additional
`MessageBox` validation-guard exclusions the human review hadn't flagged - no
case of this classifier keeping something the human review had excluded.

### Branch coverage diagnostic (2026-08-03) - separate metric, does not touch action_prf1/action_chain

`flatten_scored_actions()` still pools every `if`/`elseIf`/`else` branch's actions
into one flat list exactly as before (unchanged, to avoid perturbing the primary
metrics) - which means mutually-exclusive branches (e.g. a real 3-way `if/elseIf/
elseIf` in `0376` where each branch does an equivalent `copyFiles`+`deleteFiles`)
still inflate Gold's action count in `action_prf1`/`action_chain`, understating
recall when a prediction correctly implements only one branch. Fixing this
properly would need a branch-to-branch matching algorithm; per 2026-08-03
decision, that scope was rejected as unnecessary - `score_branch_coverage()`
instead reuses the already-computed Rule/Judge match results with no new
matching logic:

- `flatten_scored_actions(..., _branch_groups=...)` optionally records, for every
  `if` step found at any depth in Gold's tree, which of the already-assigned
  action uids belong to which branch (`if`/`elseIf`/`else`) - a side channel that
  does not change the function's return value.
- `score_branch_coverage(branch_groups, matched_gold_uids, core_gold_uids)`
  reports two diagnostic-only numbers, surfaced in `evaluation.md`/`audit.json`
  alongside but never combined with `action_prf1`: **Branch Coverage** (all-or-
  nothing per branch - no invented percentage threshold - fully-matched branches
  / total scoreable branches) and **Branch Score** (continuous, per-branch
  matched-fraction average). A branch with zero core actions (e.g. a branch that
  was pure logging) is excluded from both denominators rather than forced to 0.

WorFBench's adapter is called only in its unmodified, external-reference form -
it does not apply our `action_equivalence_rules.json` aliases, since the point
of keeping it is to reproduce the published benchmark's own behavior, not to
improve its score with our own rules. Candidate files generated under
`action_equivalence_candidates*/` are for human review only and are not treated
as confirmed mappings until promoted into `action_equivalence_rules.json` /
`action_equivalence_rules_conditional.json`.

## Rule Governance

Equivalence and conversion rules are evaluation policy, not throwaway scoring glue.
They must not be changed merely to raise one case's score.

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

Conversion exclusions:

- Apply the same deterministic rule to Gold and prediction during conversion.
- Require a rationale that is independent of the observed score.
- Do not infer implementation detail from package names such as String, Folder,
  MessageBox, Screen, or Excel formatting.
- Keep case-specific UID annotations diagnostic-only.

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
