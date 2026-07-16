# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__04_0245_merge-multiple-csv-files`
- Case: `09_0245_merge-multiple-csv-files`
- Created at: ``
- Gold actions: `29`
- Prediction actions: `7`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0556`
- Canonical action multiset F1: `0.0556`
- Package multiset F1: `0.1111`
- Package family F1: `0.1111`
- Salient family F1: `0.0000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0667`
- PM4Py precision: `0.0`
- WorFBench precision: `0.4286`
- WorFBench recall: `0.12`
- WorFBench F1: `0.1875`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-22`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.4375`

## First Mismatches

- `1` gold=`String.assign` prediction=`Prompt.promptForValue`
- `2` gold=`String.assign` prediction=`Prompt.promptForValue`
- `3` gold=`MessageBox.messageBox` prediction=`Message box.usingMessageBoxAction`
- `4` gold=`TaskBot.stopTask` prediction=`Task Bot.taskBotPackageStopAction`
- `5` gold=`String.assign` prediction=`Folder.folderCreate`
- `6` gold=`String.assign` prediction=`Datetime.cloudUsingAddAction`
- `7` gold=`String.assign` prediction=`Prompt.promptForFile`
- `8` gold=`Folder.createFolder` prediction=`None`
- `9` gold=`Folder.createFolder` prediction=`None`
- `10` gold=`Folder.createFolder` prediction=`None`
- `11` gold=`Folder.createFolder` prediction=`None`
- `12` gold=`Datetime.toString` prediction=`None`
