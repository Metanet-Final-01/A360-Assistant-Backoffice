# Manual Exception Review - runner_v2 13 PDFs

## Score Snapshot

- Cases: `13/13` evaluated successfully.
- Current canonical action multiset F1 average: `0.1466`.
- Current canonical action sequence F1 average: `0.1402`.
- Conservative trial with likely equivalences below: canonical multiset `0.2519`, canonical sequence `0.2354`.
- WorfBench edge F1 average: `0.6483`; node-label F1 is near zero because labels/action names differ heavily after conversion.

Generated files in this folder:

- `score_summary.md`
- `score_summary.csv`
- `score_summary.json`
- `canonical_unmatched_gold_actions.csv`
- `canonical_unmatched_prediction_actions.csv`
- `same_family_unmatched_pair_candidates.csv`
- `canonical_mismatch_pairs.csv`

## Recommended Accepted Equivalence Candidates

These are strong candidates to add to `evaluation/action_equivalence_rules.json` after human confirmation.

| priority | gold / legacy | agent / current | rationale | evidence |
|---:|---|---|---|---|
| 1 | `MessageBox.messageBox` | `Message box.usingMessageBoxAction` | Same user message/dialog action under package naming change. | Appears as unmatched pair across 10 cases; gold count 26, pred count 11. |
| 2 | `TaskBot.stopTask` | `Task Bot.taskBotPackageStopAction` | Same stop-current-task/bot control action under package naming change. | Appears across 5 cases; gold count 7, pred count 5. |
| 3 | `LogToFile.logToFile` | `Logging.cloudInsertingLogToFileCommand` | Same log/write-to-log package evolution. | Appears across 3 cases. Need confirm whether `insert log to file` appends exactly like legacy `logToFile`. |
| 4 | `XML.getSingleNode` | `XML.xmlGetSingleNode` | Same XML single-node read action under current action naming. | Same package, same verb/object. |
| 5 | `XML.startSession` | `XML.xmlStartSession` | Same XML session open action under current naming. | Same package, same lifecycle role. |
| 6 | `Number.toString` | `Number.numberToString` | Same number-to-string conversion. | Same package, same function. |
| 7 | `JSONHandler.Query` | `JSON utilities.jsonGetNodeValue` | Likely same JSON path/query value retrieval. | High-impact: gold unmatched 13, pred unmatched 6. Needs manual check because legacy `Query` may support broader JSONPath semantics than get-node-value. |

Suggested update payload shape if accepted later:

```json
{
  "accepted_equivalence_groups": [
    {"canonical": "Message box.usingMessageBoxAction", "members": ["MessageBox.messageBox", "Message box.usingMessageBoxAction"]},
    {"canonical": "Task Bot.taskBotPackageStopAction", "members": ["TaskBot.stopTask", "Task Bot.taskBotPackageStopAction"]},
    {"canonical": "Logging.cloudInsertingLogToFileCommand", "members": ["LogToFile.logToFile", "Logging.cloudInsertingLogToFileCommand"]},
    {"canonical": "XML.xmlGetSingleNode", "members": ["XML.getSingleNode", "XML.xmlGetSingleNode"]},
    {"canonical": "XML.xmlStartSession", "members": ["XML.startSession", "XML.xmlStartSession"]},
    {"canonical": "Number.numberToString", "members": ["Number.toString", "Number.numberToString"]},
    {"canonical": "JSON utilities.jsonGetNodeValue", "members": ["JSONHandler.Query", "JSON utilities.jsonGetNodeValue"]}
  ]
}
```

## Do Not Auto-Accept Yet

These appeared in same-family unmatched candidates but should not be added as equivalence without deeper review.

| gold | prediction | reason |
|---|---|---|
| `Datetime.cloudConvertDatetimeToString` | `Datetime.getActionInDatetime` | Formatting/conversion vs getting current/part of datetime are different operations. |
| `Datetime.usingSubtractAction` | `Datetime.getActionInDatetime` / `Datetime.cloudUsingAddAction` | Subtract/add/get are not equivalent. |
| `Excel advanced.excelAdvancedPackageReadRowAction` | `Excel advanced.excelAdvancedPackageGetSingleCellAction` | Row read vs single-cell read differ in scope and output. |
| `Excel_MS.GetMultipleCells` | `Excel advanced.excelAdvancedPackageReadRowAction` | Range/table read vs row read; previously rejected style. |
| `File.cloudDeletingFile` | `File.filePackageCopyControlRoomFileAction` | Delete vs copy are opposite file operations. |
| `File.copyFiles` / `File.downloadTo` | `File.filePackageCopyControlRoomFileAction` | Related but not same; Control Room file copy has narrower semantics. |
| `Word.mswordFindAndReplaceText` | `Word.mswordInsertText` / `OpenDocument` / `SaveDocumentAs` / `CloseDocument` | Word package neighbors, not equivalent actions. |
| `WebAutomation.*` | `Browser.browserPackageCloseAction` | Browser close is cleanup, not click/get value/send keys/load wait. |
| `Folder.folderCreate` | `Folder.cloudZipFilesAndFolders` | Create folder vs zip files/folders differ. |
| `Screen.screenCaptureDesktop` | `Screen.cloudUsingScreenCaptureWindow` | Desktop capture vs window capture may be partial alternative, not strict equivalence. |
| `System.systemInformation` | `System.usingTheGetEnvironmentVariableAction` | Environment variable lookup is not general system information. |

## Scoring / Preprocessing Candidates

These are not action-equivalence rules, but may need additional score layers or preprocessing.

1. Control-flow markers from agent output:
   - `Error handler.errorHandlerTry`: 8
   - `Error handler.errorHandlerCatch`: 8
   - `Error handler.errorHandlerFinally`: 7
   - `Loop.cloudUsingLoopAction`: 4
   - `If.ifPackageElseIfOptionalAction`: 3

   Recommendation: keep PM4Py-aware conversion for control-flow, but exclude these markers from strict action-equivalence scoring or score them in a separate `control_flow` layer. They are structural markers, not business actions.

2. Gold boilerplate that dominates misses:
   - `String.stringPackageAssignAction`: 65 unmatched
   - `Recorder.capture`: 43 unmatched
   - `Folder.folderCreate`: 29 unmatched
   - `LogToFile.logToFile`: 21 unmatched
   - `Datetime.cloudConvertDatetimeToString`: 20 unmatched

   Recommendation: do not hide these in the main exact score yet. Add a secondary `core_action_score` or `support_excluded_score` layer where setup/logging/string assignment/runtime UI capture can be excluded or down-weighted. This would separate ?agent missed concrete implementation details? from ?agent produced a valid high-level automation plan?.

3. Browser/cleanup/session handling:
   - Existing browser session lifecycle exclusion catches actions containing `session` in Browser/WebAutomation/Recorder packages.
   - Agent also emits cleanup actions like `Browser.browserPackageCloseAction`, which are not always present in gold.

   Recommendation: keep `Browser.close` scoreable for now. Only exclude it if we decide cleanup lifecycle is outside action-score scope globally.

4. Backend/schema quality note:
   - Backend logs repeatedly show `notes` emitted as list and repaired to string.
   - This did not block final runner/converter outputs, but it is a backend output-shape issue worth tracking separately from scoring exceptions.

## Immediate Next Step

If we want a conservative improvement now, apply only the first six equivalence groups. Treat `JSONHandler.Query` <-> `JSON utilities.jsonGetNodeValue` as human-review-needed before adding because it may be broader than a simple node-value read.
