# Dataset Provenance

This folder (`dataset/`) is the curated goldset export — only what's actually needed
for the workflow-recommendation golden dataset, pulled out of the raw working area
under `Test/botstore_deep/`. Nothing here is hand-edited; everything is written by
`scripts/goldset/processing/normalize_manifests.py` (`--output-root`),
`scripts/goldset/processing/extract_workflows.py`,
`scripts/goldset/processing/convert_to_pm4py.py`,
`scripts/goldset/processing/convert_to_worfbench.py`, and
`scripts/goldset/processing/resolve_subtask_coverage.py`.

`analysis/` scripts are read-only reporting on top of what `processing/` establishes
(they never write into `dataset/<category>/<bot_dir>/`, only corpus-wide report files
directly under `dataset/`) -- `processing/` is meant to eventually be chained as one
batch run producing the whole `dataset/` folder from `Test/`.

## Selection history

- Source pool: `Test/botstore_deep/downloads/` — 470 raw Automation Anywhere Bot Store
  downloads (`.zip`/`.msi`), unprocessed.
- From that pool, 98 bots were shortlisted **by title alone** (no content inspection at
  shortlist time) into `Test/botstore_deep/selected_task1_candidates_unpacked/_by_original_3_categories/`,
  split into three categories:

  | Category | Meaning |
  |---|---|
  | `01_task1_similar_single_15` | Single-sequence bots that looked similar to "task1" by title. |
  | `02_task1_similar_combo_15` | Combos of task1-similar sequences — picked on the hypothesis that a single sequence alone might be too weak a signal for the eval, so bots combining several similar sequences were included too. |
  | `03_broad_sequential_workflow` | Broader sequential workflows — conceptually the same tier/purpose as `01`, just tracked under a separate label. |

- **This is a title-based candidate shortlist, not a verified final set.** The same bot
  can appear under more than one category (e.g. `0031_StockAnalyserAgent` is in both
  `01` and `02`) — that reflects the shortlisting, not a bug. Actual suitability (real
  workflow present, custom JAR dependencies, complexity) was only checked afterward by
  the rest of this toolkit (`analysis/summarize_custom_jars.py`,
  `analysis/archive_custom_jar_metadata.py`, and this `dataset/` export).
- **"98 bots" / "155 workflow files" are category-slot counts, not unique counts.**
  Because the same physical bot can be shortlisted into more than one category folder,
  its files get physically copied into each one — several corpus-wide reports (this
  file included, in earlier revisions) glob across category dirs without deduping and
  end up counting those copies twice. The true unique corpus, confirmed by cross-
  checking `select_eval_candidates.py`'s already-deduped candidate count against a
  fresh dedup pass over the dataset tree, is **70 unique bots / 124 unique workflow
  files** (57 bots contribute exactly 1 workflow file; the largest, a contract-creation
  bot with many `SubTask_*` files, contributes 15). All figures below use the
  deduped 124, not the inflated 155, except where a script's own output is being
  reported as-is (noted inline).

## What's in `dataset/<category>/<bot_dir>/`

- `manifest.normalized.json` — pretty-printed, key-sorted copy of the bot's original
  `manifest.json` (which stays untouched in `Test/`). See its `files[].contentType` for
  what every file in the bot actually is.
- `workflows/<name>.json` — pretty-printed copies of the files whose `contentType` marks
  them as an actual action-sequence workflow (`application/vnd.aa.taskbot`,
  `.headlessbot`, `.workflow` — not `.form`/`.prompt`, which are UI layouts and LLM
  prompt templates, not workflows; not `.aiagent` either, see below).
- `workflows/<name>.goldset.json` — the same file, but walked depth-first into a
  canonical form: `Step`/`Comment` nodes filtered out, `If`/`Loop`/`ErrorHandler`/
  `TriggerLoop` control structure preserved (not flattened) as `if`/`loop`/`try`/
  `trigger_loop` typed steps with nested `steps`/`branches`.
- `workflows/<name>.pnml` / `.ptml` / `.tree.json` — the goldset above converted into
  what pm4py's conformance-checking actually needs: `.pnml` is the Petri net
  (`run_pm4py_conformance.py`-style scripts read this), `.ptml` is pm4py's own process
  tree format, `.tree.json` is the same tree as plain JSON for reading without pm4py
  installed. `if`→XOR over every branch, `loop`→ternary LOOP with silent redo/exit
  (repeat count doesn't matter, only structure), `try`→`SEQUENCE(XOR(try, catch),
  finally)` since pm4py has no native try/catch operator and `finally` must be
  mandatory, not an equal XOR alternative.
- `workflow_index.json` — per bot, lists each extracted workflow file with its
  `contentType` and declared `manualDependencies`/`scannedDependencies` (by filename) —
  e.g. a main taskbot that calls a sub-taskbot that calls a `.py` script shows up as a
  three-node chain here instead of three unrelated files.
- `workflows/<name>.worfbench.json` — the goldset above flattened into WorFBench's
  `Node:`/`Edges:` chain grammar (same wording/format as
  `ops-server/backend/app/eval/workflow/adapters.py`'s `to_worfbench_pred_traj()`, verified
  against WorFBench's own `node_eval.py` parser — see caveat below).

### WorFBench conversion — diagnostic only, not an authoritative score

WorFBench's `f1chain` metric assumes a DAG chain and cannot natively represent
If/Loop/ErrorHandler/TriggerLoop (the paper's own Appendix A.8 acknowledges this).
`processing/convert_to_worfbench.py` handles this by collapsing every branch point in
a `*.goldset.json` to **one representative canonical path** before flattening: `if` →
then-branch only (elseIf/else dropped), `loop` → body executed once (repeat dropped),
`try` → try-body + `finally` only (`catch` dropped), `trigger_loop` → first branch only.
The same rule is applied uniformly, not picked per file.

Each `.worfbench.json` is tagged in `meta` with how much was thrown away to force it
into WorFBench's chain format:
- `worfbench_fidelity: "exact"` — the workflow had **no control-flow node at all**
  (no if/loop/try/trigger_loop), so there was nothing to collapse — the chain is a
  100%-faithful copy of the whole original workflow. **27/124** unique files.
- `worfbench_fidelity: "approximated"` — the workflow **had** at least one
  if/loop/try/trigger_loop, so only one representative path through it was kept and
  everything else (the untaken `elseIf`/`else`, the repeat iterations, the `catch`
  branch) was thrown away — the chain is a partial snapshot of the original, not the
  whole thing; `control_flow_types` lists which construct(s) caused the cut.
  **97/124** unique files (88 of those involve `try`, i.e. exception-handling bots).

**This approximation is intentionally lossy and is scoped to diagnostic use only**: it
exists to (a) let f1chain run at all on workflows it structurally cannot represent, and
(b) measure how much this distortion costs the metric / demonstrate WorFBench's known
DAG limitation empirically on real data — not to produce an authoritative
correctness score. An agent choosing a different, equally-valid branch (e.g. `else`
instead of the recorded `then`) will still be unfairly penalized under this scheme;
that's a structural limitation of WorFBench itself, not a bug in the converter — see
`analysis/worfbench_branch_experiment_findings.md` for a real, measured demonstration
(a valid alternate branch scores *worse* than a genuine missing action).
Verified: 124/124 unique files converted with 0 failures (the underlying script
processes 155 category-folder copies of those 124 physical files — see the "155 vs
124" note above — but every copy of the same file produces identical output, so the
unique count is what matters); the `exact`/`approximated` split (27/97) matches the
corpus's actual control-flow-type distribution; every generated file was independently
re-parsed with WorFBench's real `node_pattern`/`edge_pattern` regexes (copied from
`node_eval.py`, not reimplemented from memory) and produced the expected node count
and edge set.

## Deliberately not carried over

- `.form`/`.prompt` files, `.xlsx`/`.csv` sample data, icons (`.png`), `.dll`/custom JAR
  binaries — not workflows, and not yet decided whether any of them are worth pulling in
  for the golden dataset. Skip until there's a concrete need tied to the eval set itself.
## Eval candidate shortlist (14, out of 124)

Running the full corpus through an LLM with reproducibility verification wasn't
practical, so `analysis/select_eval_candidates.py` filters workflows into
`eval_candidate_shortlist.json`/`.md`. **There is no scoring or ranking anymore.** An
earlier version had a hand-picked score formula (system-diversity weights, fidelity
bonus, control-flow bonus, size penalties, a `Recorder`/`AISense`-"capture ratio"
penalty) and hard cutoffs (3-60 actions, capture ratio ≤50%). On self-audit these were
all judgment calls without real evidence behind the specific numbers, and the
capture-ratio premise itself turned out to be false: `Recorder.capture`/`AISense.capture`
carry a real, structured `<uiType>Action` attribute (`linkAction`, `textboxAction`,
`buttonAction`, `checkboxAction`, `comboboxAction`, `tableAction`, `treeAction`,
`radioAction`, `passwordtextAction`, `menuAction`, `pagetabAction`, `listviewAction`,
`labelAction`, `clientAction` — 14 distinct fields across all 598 capture nodes in the
corpus, closed value sets like CLICK/SETTEXT/SELECTITEMBYTEXT/CHECK) alongside the
opaque `uiObject` blob — captures are *not* indistinguishable, the goldset just wasn't
labeling them by that field yet. Removed entirely rather than patched. A candidate now
passes or fails on 4 filters only, each grounded in a verifiable fact or an explicit
user requirement, not a preference:

A candidate must also not be in the small, explicitly-documented `MANUALLY_EXCLUDED`
list (a specific, reasoned exclusion for a real quality problem found by reading
content -- not a score or numeric threshold; see below) to be eligible.

1. **Main workflow, not a sub-workflow** (`is_main_workflow`): no other workflow file in
   the same bot contains an actual `TaskBot.runTask` node targeting this one. Computed
   from *parsed workflow content* (`processing/resolve_subtask_coverage.py`'s
   `is_real_call_graph_root`), not from `workflow_index.json`'s manifest-derived
   `manualDependencies`/`scannedDependencies` fields — see "Main/sub-workflow
   detection" below for why the manifest fields had to be abandoned.
2. **RAG-transitive-covered**: every action the candidate depends on, including
   everything reachable through `TaskBot.runTask` references resolved recursively
   (`processing/resolve_subtask_coverage.py`), exists in the RAG action catalog — the
   user's explicit requirement (see next section), not a score input. A candidate
   whose own actions all match can still be hiding uncovered work behind an opaque
   delegate call; one missing action anywhere in the resolved set disqualifies the
   whole file.
3. **`action_count >= 3`**: user-specified floor (kept as-is when the other cutoffs
   below were removed — this one is not a self-invented threshold).
4. **`canonical_action_count > 0`**: the WorFBench canonical path (after
   if→then/loop-once/try+finally-only flattening) isn't empty — a logical floor
   (nothing to compare against otherwise), not a size preference.

No scoring, no ranking, no capture-ratio penalty, no upper size band picked by feel.
Every candidate that clears the 4 filters is listed in full, sorted by name — size
(`action_count`) is reported for context, not used to cut further; see
`action_count_stats.md` for the distribution.

**Result: 14 of 124 unique workflows survive all 4 filters plus the manual-exclusion
check.** This went through four revisions:

1. First pass checked only each candidate's own directly-visible actions: 42/124 fully
   covered, 16 survived all filters (still under the old scoring version at that
   point). User's explicit call: keep 16, don't loosen RAG-coverage to hit a larger
   target. (A one-off manually-verified rename-alias table was tried afterward to
   recover a few more — it grew the pool to 21 — but was removed on reflection: even
   careful parameter-list comparison couldn't fully rule out that one entry
   (`cloudWriteToFileAction`) was a different underlying execution path, and the real
   fix is package-version-history-based, not one-off guesses.)
2. **A real gap was found in that 16**: `0188_invoicely-assistant-bot---main`'s own
   actions were all covered, but it does nothing itself except call 3 sub-tasks via
   `TaskBot.runTask` — and none of those 3 sub-tasks are themselves RAG-covered
   (`Wait`/`Window` packages). Checking this systematically
   (`processing/resolve_subtask_coverage.py`) found **6 of the 16** were hiding
   uncovered work this way — `0188`, `0295_sendemailconflatemaster`,
   `0324_oraclepurchaserequisitioncreationmaster`,
   `0346_sappurchaserequisitioncreationmaster`,
   `0359_contract-creation-using-ps-activity-guide-mekkanos`,
   `0389_aaridesktop-createservicenowincident` — all delegate their real work to
   sub-tasks using `Wait`/`Window`/`Forms`/`SAP`/`Database`/`DataTable`/`Keystrokes`/
   `Mouse`/`Credential Manager`, none of which are fully covered. Swapping in the
   substantive sub-task instead was considered and rejected: every one of those
   sub-tasks *also* fails RAG coverage on its own, so no representation of these 6
   bots would pass. Removed; 16 → 10. Also removed at this stage: the old scoring
   formula and capture-ratio filter (self-audit, see above) — the resulting filter set
   was the 4 listed above, still yielding 12 once "entry" was still manifest-based
   (`is_entry_workflow`).
3. **Manifest-based entry detection was replaced with real-call-graph detection**: see
   "Main/sub-workflow detection" below. This surfaced 3 more genuinely-main workflows
   that the manifest had wrongly excluded — `0167_outlook-email-notifier`'s
   `Outlook Email Notifier`, and `0338_lettergenerationbot`'s `LetterGenerationBot` and
   `SendPOF_Email` — bringing the total to **12 → 15**. Rechecked the full list of
   32 RAG-transitive-covered files against the corrected field: exactly these 3 moved
   from "sub-workflow, excluded" to "main, eligible" (32 covered → 15 main / 17 sub,
   was 12 main / 20 sub) — no other file's status changed.
4. **Final manual quality pass, at the user's request** ("정말 가치있는 흐름도를 가지는
   골든데이터셋인지" -- is this really a golden dataset of valuable workflows): all 15
   main-workflow-eligible candidates were read in full. `0410_a2019-invoicely-assistant`
   looked suspicious (17 of 31 actions are `Recorder.capture`) but decoding each
   capture's `<uiType>Action` sub-attribute showed a genuine login + Excel-driven
   invoice-form-fill flow -- kept, confirmed valuable. `0313_sendemailwithimagehtml`
   did not survive: of its 6 actions only 1 (`Email.sendMail`) is real, specifiable
   business content, the other 5 only run in the generic try/catch failure branch --
   a business-task-definition PDF (this project's actual eval input) never specifies
   generic error handling, so ~83% of this file's golden answer is structurally
   unpredictable from any valid business brief. Added to `MANUALLY_EXCLUDED` in
   `select_eval_candidates.py` (a specific, reasoned, documented exclusion, same
   category as the `.aiagent` drop below -- not a score or numeric threshold). 15 → 14.

**Residual quality notes** (found by reading actual action sequences, not caught by any
automated filter): `0224_extracttablesfrompdftocsv` and `0225_extracttablefromwordtocsv`
do the real work inside `DLL.Run function` — the extraction logic itself is opaque to
the action-label level, same underlying issue captures had, just not the same package;
`0137_sendemailusingsmtp-demo` is a 3-step DLL-wrapper demo, not a business task;
`0302_a2019-get-current-exchange-rate` is the same pattern (`DLL.Run function` does the
real exchange-rate lookup) -- kept, since the DLL call itself is the real, specifiable
business action, not error-handling padding. `0313_sendemailwithimagehtml` was excluded
outright (see revision 4 above and `MANUALLY_EXCLUDED` in `select_eval_candidates.py`):
its entire substantive content is a single `Email.sendMail` call, everything else is
generic failure-path boilerplate that no business-task PDF could ever specify.
`0338_lettergenerationbot`'s `SendPOF_Email` has the same one-real-action shape
(`Email.sendMail`, sending the letter `LetterGenerationBot` produced) but was kept
without reservation: unlike `0313`, it's the natural second half of that bot's actual
two-file business process (generate the letter, then send it), and its `Email.sendMail`
attributes (subject "Requested Proof of Funds Letter from Eagle One Financial",
personalized body, the generated letter as attachment) are themselves a fully
specifiable business action, not a byproduct of error handling.
`0410_a2019-invoicely-assistant` looked suspicious at first glance (17 of its 31
actions are `Recorder.capture`), but decoding each capture's `<uiType>Action`
sub-attribute (`linkAction`/`textboxAction`/`comboboxAction`/`clientAction`) showed a
real, legible flow: login (`textboxAction SETTEXT $vInvoicelyUserName$` → password →
submit) followed by filling an invoice form from Excel-sourced values
(`$vExcelRecord{Description}$`, `Invoice No`, `Currency`, `Client Name`, `Quantity`,
`Rate`, ...) — genuinely valuable, not opaque. `0224`/`0225`/`0302`/`0137` (the
DLL-opaque group) are the weakest of the surviving 14 if the eval set gets trimmed
further later, but pass every filter and the manual-exclusion check as-is.

**Remaining 17 sub-workflows, checked for standalone value**: read in full for whether
any is a non-redundant, independently meaningful business task worth manually including
despite being called from a main workflow. Only one candidate stood out —
`0338_lettergenerationbot`'s `LetterGenerationBot` (browser automation → REST API call
→ JSON parsing → Word document generation with bookmark replacement) — and it turned
out to already be one of the 3 promoted above via the real-call-graph fix, not a
special case needed on top. The rest are either redundant with patterns already present
in the 15 (e.g. the `0295`/`0324`/`0346`/`0359` group's Excel/email/login sub-tasks
overlap with system usage already covered by other candidates) or fail RAG coverage
individually anyway. No further manual promotions.

## A second, different delegation mechanism this pipeline didn't check: AARI/HBCWorkflow

The `is_real_call_graph_root` fix (previous section) only taught the pipeline to
recognize `TaskBot.runTask` as a call-graph edge. `0338_lettergenerationbot` exposed a
*different* one: `manifest.normalized.json` shows `LetterGenerationBot.manualDependencies`
listing `ProofOfFunds_Process`, `SendPOF_Email`, `VerifyFunds`, `EnterCustomerID` — and
`ProofOfFunds_Process` (contentType `application/vnd.aa.workflow`) is a real, extracted
goldset file whose *entire* content is `HBCWorkflow.schedule`/`HBCWorkflow.exit` steps
(AARI's own step-by-step progress/branch primitives for its web-triggered human+bot
process UI — `stepId`/`stepTitle`/`stepStatus`, not references to other files by name).
So unlike `TaskBot.runTask`, this delegation mechanism leaves **no parseable call-graph
edge inside any file's content at all** — the actual sequence (`EnterCustomerID` form →
`LetterGenerationBot` → `VerifyFunds` form → `SendPOF_Email`) exists only as manifest
metadata plus each file's own "_input"/"_output"-suffixed variable naming
(`LetterGenerationBot`'s `$sGeneratedLetter_output$` feeding `SendPOF_Email`'s
`$sLetterLocation_input$`), never as an in-content reference our parser can follow.
Because of this, `is_real_call_graph_root` correctly found no `TaskBot.runTask` edge
between `LetterGenerationBot` and `SendPOF_Email` and marked both "main" — technically
correct given what it checks, but wrong about the real picture: they're 2 of 4 steps in
one AARI-orchestrated business task, not 2 independent bots. `ProofOfFunds_Process`
itself (the real orchestrator) fails RAG coverage on its own regardless
(`HBCWorkflow.*` isn't in the catalog — an AARI progress-step primitive, not a business
action, same category of thing as `TaskBot.runTask` itself), and the 2 forms aren't
workflow content at all — which is exactly why only these 2 taskbot files survived
every filter looking independent.

**Fix applied**: rather than teach `resolve_subtask_coverage.py` to parse a second,
content-invisible delegation mechanism (there's nothing in the file content to parse),
`LetterGenerationBot` and `SendPOF_Email` were merged into one `eval_inputs/
task_briefs.json` entry (`source_files`, not `source_file`) — one business-task PDF
covering both, in execution order. `eval_candidate_shortlist.json` still lists both as
2 separate eligible goldset files (14 total) since RAG coverage/action-count filtering
is correctly computed per physical file; the merge is a human-facing curation decision
on top, not a change to the mechanical filter. **Checked whether this recurs**: of the
13 other eligible bots, none has an excluded sibling goldset file in the same bot
directory — this specific splitting issue is isolated to `0338`.

## Main/sub-workflow detection: why manifest dependency fields were abandoned

`workflow_index.json`'s `manualDependencies`/`scannedDependencies` are copied directly
from each bot's own `manifest.json` (Automation Anywhere's own metadata, not derived by
this toolkit) and were originally used to decide "is this the top of the bot's
`TaskBot.runTask` call chain, or a helper sub-task." Direct inspection of the raw
workflow JSON proved this metadata unreliable:

- `0167_outlook-email-notifier`'s single workflow file, "Outlook Email Notifier", has
  **zero** `TaskBot.runTask` nodes in its actual content, yet the manifest's
  `scannedDependencies` listed it as depending on **itself** (a self-reference with no
  basis in the real call structure).
- `0338_lettergenerationbot`'s three files (`LetterGenerationBot`,
  `ProofOfFunds_Process`, `SendPOF_Email`) **all** have zero `TaskBot.runTask` nodes,
  yet the manifest's `manualDependencies` formed a 2-file cycle between
  `LetterGenerationBot` and `ProofOfFunds_Process`.
- `manifest.json` has no explicit "main task" designation field at all — only
  top-level `files`/`packages` keys, with each file entry carrying `contentType`/
  `path`/dependency lists/`metadataForFile` and nothing marking an entry point.

**Why this matters (the self-reference/cycle problem, concretely):** if "main vs
sub-workflow" or a later inlining/merge step is driven by this dependency data, a
self-reference wrongly makes a real, independent workflow look like its own helper
(excluding it from the eligible set for no real reason), and a cycle would make a naive
recursive inliner (see `processing/merge_subworkflows.py`) recurse forever — the merge
would never terminate, or would need an arbitrary depth cap that could truncate a
legitimate deep chain.

**Fix:** compute "main vs sub-workflow" from the *actually-parsed* `TaskBot.runTask`
nodes in each file's real content (already-existing logic,
`collect_direct_pairs_and_refs()` in `processing/resolve_subtask_coverage.py`), not
from the manifest's fields — the manifest is AA's own secondary/derived metadata,
while the parsed `runTask` nodes are the primary source of truth for what the workflow
actually executes. Implemented as `is_real_call_graph_root` on `SubtaskCoverageRow`:
per bot, union every file's own `TaskBot.runTask` targets (`own_refs`, excluding literal
self-references) across all its files, then a file is a "root" (main workflow) iff its
own stem never appears in that union. Rerun confirmed both 0167 and 0338's affected
files now correctly show `is_real_call_graph_root=True`, and two other real edge cases
surfaced (not bugs, just genuine multi-entry bots): `0031_StockAnalyserAgent` (2 roots)
and `0021_EmployeeOnboarding` (5 roots) — plausibly an artifact of the excluded
`.aiagent` orchestrator file (see bottom of this document) no longer being tracked as
a caller once it's dropped from the corpus.

**Cycle safety net, independent of the above:** even with detection now based on real
`runTask` nodes, `processing/merge_subworkflows.py` (see next section) still guards
against a genuine cycle appearing in parsed content: `merge_steps()` tracks a `visited`
set of stems already being expanded on the current recursion path, and if a
`TaskBot.runTask` step's target is already in that set, it leaves the `runTask` step
as-is instead of expanding it — this stops infinite recursion and never silently drops
a call it couldn't resolve (the unresolved reference stays visible in the output).

## Sub-workflow merge script

`processing/merge_subworkflows.py` builds one self-contained goldset for a main
workflow that calls sub-workflows, by replacing every `TaskBot.runTask` step in-place
with the referenced sub-workflow's own `steps` (recursively, at the exact call site —
a sub-workflow reached from two different call sites is inlined separately at each,
not shared/cached as one copy). Built ahead of need: none of the current 15 eligible
candidates actually call any real `TaskBot.runTask` (that's *why* they're main
workflows with no sub-workflow gap), so this has zero applicable candidates today — it
exists for the next batch of goldset additions where a main workflow's sub-workflows
are genuinely RAG-covered and a merged single-file representation becomes useful.

Verified mechanically (not against a real eligible candidate, since none currently
qualify) against `0188_invoicely-assistant-bot---main`'s "Invoicely Assistant Bot -
Main" — chosen only because it's a real multi-sub-workflow case (its 3 sub-tasks fail
RAG coverage individually, so `0188` itself is not eligible; this run tested merge
mechanics only, not eligibility): the source file's own content is boilerplate + 3
`TaskBot.runTask` calls + a log step, but the merged output has **106 actions and zero
remaining `TaskBot.runTask` nodes** — confirming the `Initialize`/`Invoicely Login`/
`Client Creation` sub-workflows' real content was correctly spliced in at each call
site.

## RAG action-catalog coverage — the authoritative custom/unknown-action check

The user's concern: an LLM predicting a workflow can't recommend an action it doesn't
know about, so every goldset action **must** exist in the RAG action catalog.
`analysis/check_actions_in_rag.py` checks this precisely and is now the authoritative
signal (superseding the older, coarser bot-level custom-jar heuristic below) — for each
goldset file it collects every `(package, action)` pair actually invoked (recursing
through every branch, not just the canonical path) and checks it against the RAG
`action_schema` catalog. Output: `rag_action_coverage_report.json/.md`.

**Two critical findings from running this:**

1. **Only 42 of 124 unique goldset files have every action covered** (`fully_covered_files`);
   82 have at least one gap. 25 whole packages used somewhere in the corpus don't
   exist in the catalog *at all* (`SAP`, `Python`, `Forms`, `CsvTxt`, `PDFUtils`,
   `AISense`, `HBCWorkflow`, etc. — full list in the report) — these bots can never be
   fully RAG-covered no matter which of their workflow files you pick.
2. **Some gaps are AA-package version skew, not real absence.** This goldset comes
   from ~2020-era real Bot Store bots; the RAG catalog is built by parsing
   Automation Anywhere's *current* public GitHub package repos
   (`app/rag/sources/github_harvest.py`). AA renamed some command IDs across major
   versions — confirmed directly from both sides (raw workflow JSON vs. RAG's
   `rag_documents.jsonl`): `Prompt.ForValue` (2020 bot) vs. catalog's
   `Prompt.promptForValue`; `Salesforce.Authentication` vs. catalog's
   `Salesforce.authenticate`; `Wait.waitForWindow` vs. catalog's
   `Wait.waitPackageWaitForConditionAction`. Functionally the same action, different
   literal ID — an exact-match check (correctly) still excludes these, since an LLM
   working from today's catalog would never emit the 2020 ID either, but it means the
   "82 files have gaps" number overstates how many actions are *conceptually* unknown
   to the catalog vs. just renamed.

**Separate, more urgent gap, since fixed:** the live Postgres DB behind
`RAG_DATABASE_URL` had **0** `action_schema`/`package_overview` rows at first (only
1821 `doc_page` rows, from an earlier docs-only ingest) — `rag-server/data/ingest/
rag_documents.jsonl` (the pipeline's local build output) had 1616 `action_schema`
entries ready but unpushed. `pipeline.py ingest` has since been run (1830 documents
embedded/upserted, additive only) to close this gap.

**Exact-match limitation, checked (not assumed):** the coverage check above does
literal `(package_name, action_name)` string equality. Verified corpus-wide that
none of the 122 distinct missing pairs are spacing/casing/underscore/punctuation
artifacts (0 recovered after normalizing both sides and stripping all non-
alphanumeric characters) — so it's not a trivial formatting bug. But some of the 25
"packages entirely missing from the catalog" are still real **renames**, not true
absences, e.g.: `Python` (goldset) vs. catalog's `Python Script`; `CsvTxt` vs.
`CSV/TXT`; `DataTable` vs. `Data Table` — and even where the *package* name matches
after normalizing, the *action* naming convention can differ completely (goldset's
`DataTable.writeToFile` vs. the catalog's `cloudWriteToFileAction` under the
matching `Data Table` package — same package, unrelated-looking action name).
Confirming these requires semantic judgment call-by-call, not a string rule, and a
wrong automated guess here is worse than a missed one (a false "covered" verdict
would let a genuinely-unrecognizable action into the eval set undetected). A
one-off manual alias table was tried and removed (see the shortlist section above)
— even careful parameter-list comparison left real doubt on at least one entry, and
the real fix belongs at the package-version-history level, not hand-picked guesses.
Net effect: **the "covered" set (42 unique files) is a reliable lower bound; the "has
gaps" count is a conservative overestimate** of true non-coverage, since it also
counts these unresolved renames as missing.

### Bot-level custom-jar presence check — removed

An earlier, coarser signal (`analysis/report_custom_jar_presence.py`: any `.jar` in
a bot folder not named `bot-command-*`) was used as an advisory-only flag before the
per-action RAG coverage check above existed. It never affected filtering (advisory
only) and conflated genuinely bot-author-private packages with official AA Bot
Store connector packages (`WebAutomation`, `ServiceNow`, `Salesforce`, etc. all
showed up as "custom" under that definition) — coarse enough to be actively
misleading now that the precise, per-action RAG check exists. Removed from
`select_eval_candidates.py` entirely (no `has_custom_jar` field, no scoring
penalty, no shortlist column); `report_custom_jar_presence.py` deleted since the
RAG coverage check was its only reason to exist. `summarize_custom_jars.py`,
`archive_custom_jar_metadata.py`, and `processing/remove_custom_jars.py` serve a
separate, unrelated purpose (physically auditing/cleaning jar files under
`Test/botstore_deep/`) and are untouched.

- **`application/vnd.aa.aiagent` files, entirely** (4 found, e.g. `Stock Analyser Agent`,
  `MainEmployeeOnboardingAiAgent`) — confirmed empirically that 100% of them (4/4) have
  zero top-level nodes, vs. 0% for every other content type. They orchestrate
  other files via `manualDependencies` rather than embedding an action sequence, and an
  AI Agent's actual step order is decided by an LLM at runtime — there is no fixed
  reference sequence for pm4py/WorFBench to check against, so trying to score them is
  conceptually wrong, not just empty. Their referenced sub-workflows (taskbot/
  headlessbot) are extracted and scored normally on their own; only the orchestrator
  layer itself is dropped.
