# WorFBench Branch-Handling: Empirical Verification

Controlled experiment answering item 11: does WorFBench's scorer actually penalize an
agent for choosing a different-but-equally-valid branch, compared to pm4py? Uses REAL
gold artifacts from `0419_sendbulkemailswithtemplate` (one of the finalized 16 eval
candidates) — not a synthetic toy example. Script:
`C:\Users\KDH\AppData\Local\...\scratchpad\worfbench_branch_experiment.py` (uses the
real `.pnml` + real `SendBulkEmailsWithTemplate.worfbench.json`, the actual
`pm4py.fitness_alignments` and WorFBench's own `evaluator.graph_evaluator.t_eval_nodes`
with `all-MiniLM-L6-v2` embeddings — not reimplemented).

## Setup

The real workflow has an `if` inside a loop: `then` branch does 4x `String.replace` +
`Email.sendMail` + `Excel.SetCell` + `Excel.SaveSpreadSheet` (7 actions); `else` branch
does just `Excel.SetCell` (1 action) — both are real, legitimate alternatives baked
into the source bot by its author. Our WorFBench canonical-path gold recorded only the
`then` branch (per the confirmed rule: if -> then-only). Three predictions were scored
against the same gold:

1. `exact_then_branch` — literally identical to the canonical gold (positive control)
2. `alt_else_branch` — identical elsewhere, but takes the `else` branch instead of `then`
3. `wrong_missing_action` — identical to gold minus one action (`Email.sendMail`
   deleted) (negative control — a genuine mistake, not a valid alternative)

## Results (full 48-action trace)

| Variant | pm4py fitness | WorFBench f1 | precision | recall |
|---|---:|---:|---:|---:|
| exact_then_branch | 1.0000 | 0.4286 | 0.4375 | 0.4200 |
| alt_else_branch | **1.0000** | 0.3696 | 0.4048 | 0.3400 |
| wrong_missing_action | 0.9855 | 0.4124 | 0.4255 | 0.4000 |

**pm4py: exactly as predicted.** `alt_else_branch` scores **1.0**, identical to the
exact match — because the gold `.pnml` encodes the `if` as `XOR(then, else)`, both
branches are structurally valid completions of the model. This is real, measured
confirmation (not just the earlier source-code/paper reading) that pm4py's alignment
fitness is unaffected by which valid branch an agent picks.

**WorFBench: confirms the branch-handling problem, and surfaces a second, bigger
problem.** Two findings:

1. **The branch effect is real and in the predicted direction**: `alt_else_branch`
   (0.3696) scores *lower* than `exact_then_branch` (0.4286) — a real penalty for
   picking the equally-valid alternative, exactly the unfairness the canonical-path
   caveat in PROVENANCE.md warned about.
2. **`alt_else_branch` (0.3696) scores *worse* than `wrong_missing_action` (0.4124)** —
   i.e., WorFBench ranks "the agent took a different valid branch" as a *bigger*
   defect than "the agent flat-out forgot to send the email." That ranking is
   indefensible for a metric meant to judge task-completion quality, and it's the
   clearest, most concrete evidence that WorFBench's scoring can misrank predictions
   specifically because of branch structure, not just "score them a bit lower."

**A second, independent structural weakness surfaced along the way** (found while
investigating why even `exact_then_branch` scored only 0.4286, far short of 1.0): the
real trace is **41/48 (85%) duplicate action labels** (`String.assign` x17,
`Folder.createFolder` x5, etc. — completely ordinary for RPA bots, which lean
heavily on generic utility actions). WorFBench's node matching (`match_node`) does
pure Hungarian *maximum-weight* bipartite matching on embedding similarity with no
positional tie-break — when many nodes share the identical label (cosine sim ~1.0
between any two `String.assign` instances), the matching can legally pick any
pairing among them, including one that scrambles the true left-to-right
correspondence. Since the downstream metric is a **longest-increasing-subsequence**
over the resulting index mapping, a scrambled duplicate-label matching directly
lowers the LIS count — so *even a byte-identical prediction* doesn't score 1.0 once
duplicate labels dominate the trace. This is a distinct problem from the DAG/branch
issue (Appendix A.8) and, if anything, more consequential for RPA workflows
specifically, since generic repeated actions (assign/log/delay/folder-create) are
the norm, not the exception, in this domain.

## Local-window follow-up (isolating the duplicate-label confound)

To see the branch effect with less of the above noise mixed in, the same three
variants were re-scored using only the small, mostly-non-repeating local window
directly around the branch (`Number.assignToNumber -> [then/else content] ->
Number.increment`) instead of the full 48-action trace:

| Variant | WorFBench f1 | precision | recall |
|---|---:|---:|---:|
| exact_local | 0.6364 | 0.7000 | 0.5833 |
| alt_branch_local | **0.5000** | 1.0000 | 0.3333 |
| wrong_missing_local | 0.5714 | 0.6667 | 0.5000 |

**The ranking inversion survives isolation from the duplicate-label confound.**
`alt_branch_local` (0.5000) still scores below `wrong_missing_local` (0.5714) — a
genuine miss is still rated *better* than a valid alternate branch, even in a small
window with almost no repeated labels. This rules out "it was just the duplicate-label
noise" as the explanation and confirms the effect is the branch/canonical-path
mismatch itself. (The recall=0.3333 for `alt_branch_local` is mechanically why: the
gold's 4x `String.replace` + `Email.sendMail` + one `Excel.SaveSpreadSheet` simply
aren't in the `else`-branch prediction at all, so 4 of gold's 6 non-trivial nodes go
unmatched — precision looks artificially perfect (1.0) only because the few things the
prediction *did* say all happen to match something, which is itself a sign that
precision/recall here aren't measuring what they're meant to when the prediction and
gold represent two different valid completions of a branch rather than a
better/worse attempt at the same one.)

## Conclusion

Both effects point the same direction: **WorFBench's `f1chain`/node-matching, as
actually implemented and actually run (not just as described in the paper), is not
a reliable absolute or even relative quality signal for RPA-style workflows** —
first because it cannot fairly score a valid alternate branch against a
single-canonical-path gold (confirmed: ranks a valid alternative *below* a genuine
omission), and second because heavy action-label repetition (endemic to RPA) can
suppress its score even for an exact match. This validates treating WorFBench output
as diagnostic-only (per the earlier explicit scoping decision) and, further, suggests
pm4py should be the primary/trusted metric for this dataset, with WorFBench reported
alongside only to demonstrate its known limitations empirically — not as a
co-equal score.
