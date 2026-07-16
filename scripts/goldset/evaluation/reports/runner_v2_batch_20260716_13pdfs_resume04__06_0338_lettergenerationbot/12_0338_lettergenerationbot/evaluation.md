# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__06_0338_lettergenerationbot`
- Case: `12_0338_lettergenerationbot`
- Created at: ``
- Gold actions: `98`
- Prediction actions: `16`

## Scores

- Action sequence LCS F1: `0.0175` (precision `0.0625`, recall `0.0102`)
- Action multiset F1: `0.0175`
- Canonical action sequence LCS F1: `0.0702`
- Canonical action multiset F1: `0.0702`
- Package multiset F1: `0.0702`
- Package family F1: `0.0702`
- Salient family F1: `0.1176`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0714`
- PM4Py precision: `0.0`
- WorFBench precision: `0.625`
- WorFBench recall: `0.119`
- WorFBench F1: `0.2`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-82`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0204`
- Diagnostic WorFBench edge F1: `0.3200`

## First Mismatches

- `1` gold=`String.assign` prediction=`Prompt.promptForValue`
- `2` gold=`String.assign` prediction=`Message box.usingMessageBoxAction`
- `3` gold=`MessageBox.messageBox` prediction=`Task Bot.taskBotPackageStopAction`
- `4` gold=`TaskBot.stopTask` prediction=`Folder.folderCreate`
- `5` gold=`String.assign` prediction=`Browser.browserPackageOpenAction`
- `6` gold=`String.assign` prediction=`JSON utilities.jsonStartSession`
- `7` gold=`String.assign` prediction=`JSON utilities.jsonValidate`
- `8` gold=`Folder.createFolder` prediction=`JSON utilities.jsonGetNodeValue`
- `9` gold=`Folder.createFolder` prediction=`JSON utilities.jsonGetNodeValue`
- `10` gold=`Folder.createFolder` prediction=`List.listSize`
- `11` gold=`Folder.createFolder` prediction=`Word.mswordOpenDocument`
- `12` gold=`Datetime.toString` prediction=`Word.mswordInsertText`
