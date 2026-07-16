# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__10_0419_sendbulkemailswithtemplate`
- Case: `02_0419_sendbulkemailswithtemplate`
- Created at: ``
- Gold actions: `55`
- Prediction actions: `13`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0588`
- Canonical action multiset F1: `0.0882`
- Package multiset F1: `0.1765`
- Package family F1: `0.2059`
- Salient family F1: `0.1250`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0571`
- PM4Py precision: `0.0`
- WorFBench precision: `0.4615`
- WorFBench recall: `0.12`
- WorFBench F1: `0.1905`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-42`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.4127`

## First Mismatches

- `1` gold=`String.assign` prediction=`Message box.usingMessageBoxAction`
- `2` gold=`String.assign` prediction=`Task Bot.taskBotPackageStopAction`
- `3` gold=`MessageBox.messageBox` prediction=`File.filePackageCopyControlRoomFileAction`
- `4` gold=`TaskBot.stopTask` prediction=`File.filePackageCreateAction`
- `5` gold=`String.assign` prediction=`Datetime.getActionInDatetime`
- `6` gold=`String.assign` prediction=`Datetime.usingSubtractAction`
- `7` gold=`String.assign` prediction=`Microsoft 365 Excel package in Automation 360.usingOpenWorkbookAction`
- `8` gold=`Folder.createFolder` prediction=`Microsoft 365 Excel package in Automation 360.getWorksheetAsDataTableIn`
- `9` gold=`Folder.createFolder` prediction=`Email.cloudUsingSendAction`
- `10` gold=`Folder.createFolder` prediction=`Microsoft 365 Excel package in Automation 360.writeFromDataTableIn`
- `11` gold=`Folder.createFolder` prediction=`File.filePackageCopyControlRoomFileAction`
- `12` gold=`Datetime.toString` prediction=`Logging.cloudInsertingLogToFileCommand`
