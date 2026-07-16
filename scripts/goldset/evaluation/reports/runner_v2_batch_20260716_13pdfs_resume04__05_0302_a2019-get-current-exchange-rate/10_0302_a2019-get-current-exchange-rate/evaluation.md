# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__05_0302_a2019-get-current-exchange-rate`
- Case: `10_0302_a2019-get-current-exchange-rate`
- Created at: ``
- Gold actions: `18`
- Prediction actions: `13`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.3871`
- Canonical action multiset F1: `0.3871`
- Package multiset F1: `0.5161`
- Package family F1: `0.5161`
- Salient family F1: `0.5000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.3077`
- WorFBench recall: `0.2222`
- WorFBench F1: `0.2581`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-5`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.8387`

## First Mismatches

- `1` gold=`System.systemInformation` prediction=`System.usingTheGetEnvironmentVariableAction`
- `2` gold=`Folder.createFolder` prediction=`Folder.folderCreate`
- `3` gold=`Folder.createFolder` prediction=`Folder.folderCreate`
- `4` gold=`String.assign` prediction=`Dictionary.dictionaryPut`
- `5` gold=`String.assign` prediction=`Dictionary.dictionaryPut`
- `6` gold=`String.assign` prediction=`Dictionary.dictionaryPut`
- `7` gold=`Dictionary.put` prediction=`Dictionary.dictionaryPut`
- `8` gold=`Dictionary.put` prediction=`Dictionary.dictionaryPut`
- `9` gold=`Dictionary.put` prediction=`Application.applicationPackageOpenProgramFileAction`
- `10` gold=`Dictionary.put` prediction=`Message box.usingMessageBoxAction`
- `11` gold=`DLL.Open` prediction=`Number.numberToString`
- `12` gold=`DLL.Open` prediction=`Message box.usingMessageBoxAction`
