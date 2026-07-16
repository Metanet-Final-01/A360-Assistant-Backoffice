# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs__08_0302_a2019-get-current-exchange-rate`
- Case: `10_0302_a2019-get-current-exchange-rate`
- Created at: ``
- Gold actions: `18`
- Prediction actions: `6`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0833`
- Canonical action multiset F1: `0.0833`
- Package multiset F1: `0.1667`
- Package family F1: `0.2500`
- Salient family F1: `0.1429`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.2`
- WorFBench recall: `0.0556`
- WorFBench F1: `0.087`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-12`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.5000`

## First Mismatches

- `1` gold=`System.systemInformation` prediction=`Box.boxCrtFldr`
- `2` gold=`Folder.createFolder` prediction=`Box.boxCrtFldr`
- `3` gold=`Folder.createFolder` prediction=`Logging.logToFileLogVariablesToFile`
- `4` gold=`String.assign` prediction=`DLL.dllPackageCloseAction`
- `5` gold=`String.assign` prediction=`Error handler.errorHandlerCatch`
- `6` gold=`String.assign` prediction=`Number.numberToString`
- `7` gold=`Dictionary.put` prediction=`None`
- `8` gold=`Dictionary.put` prediction=`None`
- `9` gold=`Dictionary.put` prediction=`None`
- `10` gold=`Dictionary.put` prediction=`None`
- `11` gold=`DLL.Open` prediction=`None`
- `12` gold=`DLL.Open` prediction=`None`
