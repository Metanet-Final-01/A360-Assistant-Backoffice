# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__03_0225_extracttablefromwordtocsv`
- Case: `07_0225_extracttablefromwordtocsv`
- Created at: ``
- Gold actions: `10`
- Prediction actions: `9`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.2105`
- Canonical action multiset F1: `0.2105`
- Package multiset F1: `0.2105`
- Package family F1: `0.2105`
- Salient family F1: `0.2500`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.2105`
- PM4Py precision: `0.0`
- WorFBench precision: `0.2222`
- WorFBench recall: `0.1667`
- WorFBench F1: `0.1905`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-1`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.8571`

## First Mismatches

- `1` gold=`String.assign` prediction=`Dictionary.dictionaryPut`
- `2` gold=`String.assign` prediction=`Dictionary.dictionaryPut`
- `3` gold=`Dictionary.put` prediction=`Dictionary.dictionaryPut`
- `4` gold=`Dictionary.put` prediction=`Python Script.pythonOpenAction`
- `5` gold=`DLL.Open` prediction=`Word.mswordReadTable`
- `6` gold=`DLL.Open` prediction=`Data Table.cloudWriteToFileAction`
- `7` gold=`DLL.Run function` prediction=`Dictionary.dictionaryPut`
- `8` gold=`MessageBox.messageBox` prediction=`Message box.usingMessageBoxAction`
- `9` gold=`DLL.Close` prediction=`Python Script.pythonCloseAction`
- `10` gold=`DLL.Close` prediction=`None`
