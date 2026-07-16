# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs__01_0131_currency-rate---oanda`
- Case: `03_0131_currency-rate---oanda`
- Created at: ``
- Gold actions: `45`
- Prediction actions: `16`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0656`
- Canonical action multiset F1: `0.0656`
- Package multiset F1: `0.0656`
- Package family F1: `0.1639`
- Salient family F1: `0.1176`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0588`
- PM4Py precision: `0.0`
- WorFBench precision: `0.5`
- WorFBench recall: `0.1463`
- WorFBench F1: `0.2264`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-31`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.5424`

## First Mismatches

- `1` gold=`String.assign` prediction=`If.ifPackageElseIfOptionalAction`
- `2` gold=`String.assign` prediction=`Prompt.promptForValue`
- `3` gold=`MessageBox.messageBox` prediction=`Task Bot.taskBotPackageStopAction`
- `4` gold=`TaskBot.stopTask` prediction=`Folder.folderCreate`
- `5` gold=`String.assign` prediction=`Datetime.getActionInDatetime`
- `6` gold=`String.assign` prediction=`Microsoft 365 Excel package in Automation 360.usingOpenWorkbookAction`
- `7` gold=`String.assign` prediction=`Microsoft 365 Excel package in Automation 360.getWorksheetAsDataTableIn`
- `8` gold=`Folder.createFolder` prediction=`Error handler.errorHandlerCatch`
- `9` gold=`Folder.createFolder` prediction=`Error handler.errorHandlerFinally`
- `10` gold=`Folder.createFolder` prediction=`Browser.browserPackageOpenAction`
- `11` gold=`Folder.createFolder` prediction=`Prompt.promptForValue`
- `12` gold=`Datetime.toString` prediction=`Microsoft 365 Excel package in Automation 360.office365ExcelSetCell`
