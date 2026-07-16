# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__01_0176_global-exchange-currency-rate`
- Case: `04_0176_global-exchange-currency-rate`
- Created at: ``
- Gold actions: `24`
- Prediction actions: `13`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.2703`
- Canonical action multiset F1: `0.2703`
- Package multiset F1: `0.2703`
- Package family F1: `0.3784`
- Salient family F1: `0.1429`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.1`
- PM4Py precision: `0.0`
- WorFBench precision: `0.4615`
- WorFBench recall: `0.24`
- WorFBench F1: `0.3158`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-11`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.6842`

## First Mismatches

- `1` gold=`String.assign` prediction=`Folder.folderCreate`
- `2` gold=`String.assign` prediction=`File.filePackageCreateAction`
- `3` gold=`String.assign` prediction=`Folder.folderCreate`
- `4` gold=`String.assign` prediction=`Logging.cloudInsertingLogToFileCommand`
- `5` gold=`String.assign` prediction=`Browser.browserPackageOpenAction`
- `6` gold=`Datetime.toString` prediction=`Browser.browserPackageRunJavascriptAction`
- `7` gold=`Folder.createFolder` prediction=`CSV/TXT.cloudOpeningCsvTextFile`
- `8` gold=`Folder.createFolder` prediction=`Data Table.dataTablePackageSetValueSinlgeCellAction`
- `9` gold=`File.createFile` prediction=`Data Table.cloudWriteToFileAction`
- `10` gold=`LogToFile.logToFile` prediction=`Number.numberIncrement`
- `11` gold=`Folder.createFolder` prediction=`Logging.cloudInsertingLogToFileCommand`
- `12` gold=`Browser.openbrowser` prediction=`Logging.cloudInsertingLogToFileCommand`
