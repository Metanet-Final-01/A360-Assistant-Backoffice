# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs__07_0245_merge-multiple-csv-files`
- Case: `09_0245_merge-multiple-csv-files`
- Created at: ``
- Gold actions: `29`
- Prediction actions: `4`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0606`
- Canonical action multiset F1: `0.0606`
- Package multiset F1: `0.0606`
- Package family F1: `0.1212`
- Salient family F1: `0.0000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0741`
- PM4Py precision: `0.0`
- WorFBench precision: `0.25`
- WorFBench recall: `0.04`
- WorFBench F1: `0.069`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-25`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.2759`

## First Mismatches

- `1` gold=`String.assign` prediction=`Folder.folderCreate`
- `2` gold=`String.assign` prediction=`ServiceNow.servicenowGetAttachment`
- `3` gold=`MessageBox.messageBox` prediction=`Logging.cloudInsertingLogToFileCommand`
- `4` gold=`TaskBot.stopTask` prediction=`Microsoft 365 OneDrive.oneDriveMoveFileOrFolderAction`
- `5` gold=`String.assign` prediction=`None`
- `6` gold=`String.assign` prediction=`None`
- `7` gold=`String.assign` prediction=`None`
- `8` gold=`Folder.createFolder` prediction=`None`
- `9` gold=`Folder.createFolder` prediction=`None`
- `10` gold=`Folder.createFolder` prediction=`None`
- `11` gold=`Folder.createFolder` prediction=`None`
- `12` gold=`Datetime.toString` prediction=`None`
