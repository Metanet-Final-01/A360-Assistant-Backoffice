# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__02_0224_extracttablesfrompdftocsv`
- Case: `06_0224_extracttablesfrompdftocsv`
- Created at: ``
- Gold actions: `10`
- Prediction actions: `6`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.2500`
- Canonical action multiset F1: `0.2500`
- Package multiset F1: `0.2500`
- Package family F1: `0.2500`
- Salient family F1: `0.3077`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.25`
- PM4Py precision: `0.0`
- WorFBench precision: `0.3333`
- WorFBench recall: `0.1667`
- WorFBench F1: `0.2222`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-4`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.6667`

## First Mismatches

- `1` gold=`String.assign` prediction=`Dictionary.dictionaryPut`
- `2` gold=`String.assign` prediction=`Dictionary.dictionaryPut`
- `3` gold=`Dictionary.put` prediction=`Dictionary.dictionaryPut`
- `4` gold=`Dictionary.put` prediction=`Application.applicationPackageOpenProgramFileAction`
- `5` gold=`DLL.Open` prediction=`Message box.usingMessageBoxAction`
- `6` gold=`DLL.Open` prediction=`Browser.browserPackageCloseAction`
- `7` gold=`DLL.Run function` prediction=`None`
- `8` gold=`MessageBox.messageBox` prediction=`None`
- `9` gold=`DLL.Close` prediction=`None`
- `10` gold=`DLL.Close` prediction=`None`
