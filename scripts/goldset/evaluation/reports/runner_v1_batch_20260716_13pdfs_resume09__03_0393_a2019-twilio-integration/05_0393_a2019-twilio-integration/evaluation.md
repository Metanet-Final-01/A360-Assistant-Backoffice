# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs_resume09__03_0393_a2019-twilio-integration`
- Case: `05_0393_a2019-twilio-integration`
- Created at: ``
- Gold actions: `47`
- Prediction actions: `7`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0370`
- Canonical action multiset F1: `0.0370`
- Package multiset F1: `0.0741`
- Package family F1: `0.1481`
- Salient family F1: `0.0000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0741`
- PM4Py precision: `0.0`
- WorFBench precision: `0.2`
- WorFBench recall: `0.0238`
- WorFBench F1: `0.0426`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-40`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.2857`

## First Mismatches

- `1` gold=`String.assign` prediction=`If.if`
- `2` gold=`String.assign` prediction=`Folder.folderCreate`
- `3` gold=`MessageBox.messageBox` prediction=`Process Composer.processComposerCreateARequest`
- `4` gold=`TaskBot.stopTask` prediction=`If.if`
- `5` gold=`String.assign` prediction=`Logging.logToFileLogVariablesToFile`
- `6` gold=`String.assign` prediction=`Logging.logToFileLogVariablesToFile`
- `7` gold=`String.assign` prediction=`Screen.cloudUsingScreenCaptureArea`
- `8` gold=`Folder.createFolder` prediction=`None`
- `9` gold=`Folder.createFolder` prediction=`None`
- `10` gold=`Folder.createFolder` prediction=`None`
- `11` gold=`Folder.createFolder` prediction=`None`
- `12` gold=`Datetime.toString` prediction=`None`
