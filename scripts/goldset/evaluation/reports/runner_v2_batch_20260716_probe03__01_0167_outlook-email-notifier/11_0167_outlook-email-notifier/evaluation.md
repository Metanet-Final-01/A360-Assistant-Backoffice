# Evaluation Report

- Run: `runner_v2_batch_20260716_probe03__01_0167_outlook-email-notifier`
- Case: `11_0167_outlook-email-notifier`
- Created at: ``
- Gold actions: `24`
- Prediction actions: `13`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.1622`
- Canonical action multiset F1: `0.2162`
- Package multiset F1: `0.1081`
- Package family F1: `0.3243`
- Salient family F1: `0.4444`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.5455`
- WorFBench recall: `0.24`
- WorFBench F1: `0.3333`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-11`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.6842`

## First Mismatches

- `1` gold=`String.assign` prediction=`File.filePackageCreateAction`
- `2` gold=`String.assign` prediction=`File.filePackageCreateAction`
- `3` gold=`String.assign` prediction=`Excel advanced.cloudExcelOpen`
- `4` gold=`String.assign` prediction=`Error handler.errorHandlerCatch`
- `5` gold=`String.assign` prediction=`Error handler.errorHandlerFinally`
- `6` gold=`Datetime.toString` prediction=`Excel advanced.excelAdvancedPackageGetSingleCellAction`
- `7` gold=`Folder.createFolder` prediction=`Excel advanced.excelAdvancedPackageGetSingleCellAction`
- `8` gold=`Folder.createFolder` prediction=`Microsoft 365 Outlook.ms365OutlookSaveAllAttachments`
- `9` gold=`File.createFile` prediction=`Microsoft 365 Outlook.ms365OutlookSaveAllAttachments`
- `10` gold=`LogToFile.logToFile` prediction=`Message box.usingMessageBoxAction`
- `11` gold=`Folder.createFolder` prediction=`Data Table.cloudWriteToFileAction`
- `12` gold=`Excel_MS.OpenSpreadsheet` prediction=`Excel advanced.excelAdvancedPackageCloseAction`
