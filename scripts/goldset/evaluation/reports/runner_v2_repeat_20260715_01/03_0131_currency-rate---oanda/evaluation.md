# Evaluation Report

- Run: `runner_v2_repeat_20260715_01`
- Case: `03_0131_currency-rate---oanda`
- Created at: `2026-07-15T15:50:33.657229+00:00`
- Gold actions: `45`
- Prediction actions: `8`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0000`
- Canonical action multiset F1: `0.0000`
- Package multiset F1: `0.0000`
- Package family F1: `0.2642`
- Salient family F1: `0.4828`
- Adjacent edge F1: `0.0000`
- WorFBench node-label F1: `0.0000`
- WorFBench edge F1: `0.3137`

## PM4Py Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-39`
- PNML hash equal: `False`

## First Mismatches

- `1` gold=`String.assign` prediction=`Excel advanced.cloudExcelOpen`
- `2` gold=`String.assign` prediction=`Excel advanced.excelAdvancedPackageReadRowAction`
- `3` gold=`MessageBox.messageBox` prediction=`Browser.browserPackageOpenAction`
- `4` gold=`TaskBot.stopTask` prediction=`Browser.browserPackageRunJavascriptAction`
- `5` gold=`String.assign` prediction=`Error handler.errorHandlerCatch`
- `6` gold=`String.assign` prediction=`Excel advanced.excelAdvancedPackageSetCellAction`
- `7` gold=`String.assign` prediction=`Excel advanced.excelAdvancedPackageCloseAction`
- `8` gold=`Folder.createFolder` prediction=`Browser.browserPackageCloseAction`
- `9` gold=`Folder.createFolder` prediction=`None`
- `10` gold=`Folder.createFolder` prediction=`None`
- `11` gold=`Folder.createFolder` prediction=`None`
- `12` gold=`Datetime.toString` prediction=`None`
