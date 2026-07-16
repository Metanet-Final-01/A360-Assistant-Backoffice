# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__08_0393_a2019-twilio-integration`
- Case: `05_0393_a2019-twilio-integration`
- Created at: ``
- Gold actions: `47`
- Prediction actions: `16`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0635`
- Canonical action multiset F1: `0.0635`
- Package multiset F1: `0.1587`
- Package family F1: `0.1587`
- Salient family F1: `0.0000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.1081`
- PM4Py precision: `0.0`
- WorFBench precision: `0.2667`
- WorFBench recall: `0.0952`
- WorFBench F1: `0.1404`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-31`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.5517`

## First Mismatches

- `1` gold=`String.assign` prediction=`Message box.usingMessageBoxAction`
- `2` gold=`String.assign` prediction=`Task Bot.taskBotPackageStopAction`
- `3` gold=`MessageBox.messageBox` prediction=`Datetime.getActionInDatetime`
- `4` gold=`TaskBot.stopTask` prediction=`Folder.cloudZipFilesAndFolders`
- `5` gold=`String.assign` prediction=`String.stringPackageAssignAction`
- `6` gold=`String.assign` prediction=`REST Web Services.cloudUsingGetAction`
- `7` gold=`String.assign` prediction=`JSON utilities.jsonValidate`
- `8` gold=`Folder.createFolder` prediction=`JSON utilities.jsonGetNodeValue`
- `9` gold=`Folder.createFolder` prediction=`JSON utilities.jsonGetNodeValue`
- `10` gold=`Folder.createFolder` prediction=`JSON utilities.jsonGetNodeValue`
- `11` gold=`Folder.createFolder` prediction=`JSON utilities.jsonGetNodeValue`
- `12` gold=`Datetime.toString` prediction=`String.stringPackageAssignAction`
