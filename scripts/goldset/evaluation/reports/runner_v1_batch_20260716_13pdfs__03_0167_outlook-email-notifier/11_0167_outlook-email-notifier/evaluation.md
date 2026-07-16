# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs__03_0167_outlook-email-notifier`
- Case: `11_0167_outlook-email-notifier`
- Created at: ``
- Gold actions: `24`
- Prediction actions: `8`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0000`
- Canonical action multiset F1: `0.0000`
- Package multiset F1: `0.0625`
- Package family F1: `0.2500`
- Salient family F1: `0.4286`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.5714`
- WorFBench recall: `0.16`
- WorFBench F1: `0.25`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-16`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.4848`

## First Mismatches

- `1` gold=`String.assign` prediction=`CSV/TXT.cloudOpeningCsvTextFile`
- `2` gold=`String.assign` prediction=`Logging.cloudInsertingLogToFileCommand`
- `3` gold=`String.assign` prediction=`Excel advanced.excelAdvancedPackageCreateWorkbookAction`
- `4` gold=`String.assign` prediction=`Microsoft Outlook (macOS).macMsOutlookConnectDisconnect`
- `5` gold=`String.assign` prediction=`Databricks.getjobstatusDbricks`
- `6` gold=`Datetime.toString` prediction=`Email.emailDisconnectAction`
- `7` gold=`Folder.createFolder` prediction=`Excel advanced.excelAdvPkgBrkWrbkLinks`
- `8` gold=`Folder.createFolder` prediction=`Error handler.errorHandlerCatch`
- `9` gold=`File.createFile` prediction=`None`
- `10` gold=`LogToFile.logToFile` prediction=`None`
- `11` gold=`Folder.createFolder` prediction=`None`
- `12` gold=`Excel_MS.OpenSpreadsheet` prediction=`None`
