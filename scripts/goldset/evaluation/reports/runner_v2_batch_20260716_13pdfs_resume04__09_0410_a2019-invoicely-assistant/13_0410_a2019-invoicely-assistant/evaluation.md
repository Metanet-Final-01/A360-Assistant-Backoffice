# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__09_0410_a2019-invoicely-assistant`
- Case: `13_0410_a2019-invoicely-assistant`
- Created at: ``
- Gold actions: `31`
- Prediction actions: `13`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0909`
- Canonical action multiset F1: `0.0909`
- Package multiset F1: `0.0455`
- Package family F1: `0.0909`
- Salient family F1: `0.1000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.1053`
- PM4Py precision: `0.5`
- WorFBench precision: `0.1538`
- WorFBench recall: `0.0667`
- WorFBench F1: `0.093`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-18`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.6047`

## First Mismatches

- `1` gold=`Browser.launchWebsite` prediction=`Browser.browserPackageOpenAction`
- `2` gold=`Recorder.capture` prediction=`Browser.browserPackageRunJavascriptAction`
- `3` gold=`Recorder.capture` prediction=`Wait.waitPackageWaitForConditionAction`
- `4` gold=`Recorder.capture` prediction=`Excel advanced.cloudExcelOpen`
- `5` gold=`Recorder.capture` prediction=`Excel advanced.excelAdvancedGetWorksheetAsDataTable`
- `6` gold=`Recorder.capture` prediction=`Browser.browserPackageRunJavascriptAction`
- `7` gold=`Recorder.capture` prediction=`Browser.browserPackageRunJavascriptAction`
- `8` gold=`Recorder.capture` prediction=`Browser.browserPackageRunJavascriptAction`
- `9` gold=`Folder.createFolder` prediction=`Browser.browserPackageRunJavascriptAction`
- `10` gold=`MessageBox.messageBox` prediction=`Data Table.cloudWriteToFileAction`
- `11` gold=`LogToFile.logToFile` prediction=`Message box.usingMessageBoxAction`
- `12` gold=`LogToFile.logToFile` prediction=`Excel advanced.excelAdvancedPackageCloseAction`
