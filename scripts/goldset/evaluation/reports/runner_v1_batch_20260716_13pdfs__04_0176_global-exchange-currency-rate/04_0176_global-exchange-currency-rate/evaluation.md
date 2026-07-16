# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs__04_0176_global-exchange-currency-rate`
- Case: `04_0176_global-exchange-currency-rate`
- Created at: ``
- Gold actions: `24`
- Prediction actions: `9`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0000`
- Canonical action multiset F1: `0.0000`
- Package multiset F1: `0.0606`
- Package family F1: `0.1818`
- Salient family F1: `0.1333`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.2857`
- WorFBench recall: `0.08`
- WorFBench F1: `0.125`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-15`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.5294`

## First Mismatches

- `1` gold=`String.assign` prediction=`CSV/TXT/cloudOpeningCsvTextFile.CSV/TXT 파일에 대한 열기 작업 사용`
- `2` gold=`String.assign` prediction=`Browser.browserPackageDownloadFilesAction`
- `3` gold=`String.assign` prediction=`Browser.browserPackageDownloadFilesAction`
- `4` gold=`String.assign` prediction=`Loop.loop.commands.start`
- `5` gold=`String.assign` prediction=`CSV/TXT/cloudUsingDatatableCreateAction.읽기 작업 사용`
- `6` gold=`Datetime.toString` prediction=`Logging.cloudInsertingLogToFileCommand`
- `7` gold=`Folder.createFolder` prediction=`Error handler.errorHandlerCatch`
- `8` gold=`Folder.createFolder` prediction=`Logging.logToFileLogVariablesToFile`
- `9` gold=`File.createFile` prediction=`Browser.browserPackageCloseAction`
- `10` gold=`LogToFile.logToFile` prediction=`None`
- `11` gold=`Folder.createFolder` prediction=`None`
- `12` gold=`Browser.openbrowser` prediction=`None`
