# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs__01_0131_currency-rate---oanda`
- Case: `03_0131_currency-rate---oanda`
- Created at: `2026-07-15T22:17:34.150974+00:00`
- Gold actions: `45`
- Prediction actions: `9`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0000`
- Canonical action multiset F1: `0.0000`
- Package multiset F1: `0.0000`
- Package family F1: `0.1481`
- Salient family F1: `0.2069`
- Adjacent edge F1: `0.0000`
- WorFBench node-label F1: `0.0000`
- WorFBench edge F1: `0.3462`

## PM4Py Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-38`
- PNML hash equal: `False`

## First Mismatches

- `1` gold=`String.assign` prediction=`Box.boxCrtFldr`
- `2` gold=`String.assign` prediction=`Box.boxCrtFldr`
- `3` gold=`MessageBox.messageBox` prediction=`Box.boxCrtFldr`
- `4` gold=`TaskBot.stopTask` prediction=`Box.boxCrtFldr`
- `5` gold=`String.assign` prediction=`Excel advanced.excelAdvancedPackageCreateWorkbookAction`
- `6` gold=`String.assign` prediction=`Excel advanced.excelAdvancedPackageReadRowAction`
- `7` gold=`String.assign` prediction=`Loop.loop.commands.start`
- `8` gold=`Folder.createFolder` prediction=`Excel advanced.excelAdvancedPackageCreateWorkbookAction`
- `9` gold=`Folder.createFolder` prediction=`Logging.cloudInsertingLogToFileCommand`
- `10` gold=`Folder.createFolder` prediction=`None`
- `11` gold=`Folder.createFolder` prediction=`None`
- `12` gold=`Datetime.toString` prediction=`None`
