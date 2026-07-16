# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs__06_0225_extracttablefromwordtocsv`
- Case: `07_0225_extracttablefromwordtocsv`
- Created at: ``
- Gold actions: `10`
- Prediction actions: `6`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0000`
- Canonical action multiset F1: `0.0000`
- Package multiset F1: `0.0000`
- Package family F1: `0.0000`
- Salient family F1: `0.0000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.1667`
- WorFBench recall: `0.0833`
- WorFBench F1: `0.1111`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-4`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.6667`

## First Mismatches

- `1` gold=`String.assign` prediction=`Word.mswordOpenDocument`
- `2` gold=`String.assign` prediction=`CSV/TXT.cloudOpeningCsvTextFile`
- `3` gold=`Dictionary.put` prediction=`File.filePackageGetPathAction`
- `4` gold=`Dictionary.put` prediction=`Word.mswordReadTable`
- `5` gold=`DLL.Open` prediction=`CSV/TXT.cloudOpeningCsvTextFile`
- `6` gold=`DLL.Open` prediction=`Word.mswordReadText`
- `7` gold=`DLL.Run function` prediction=`None`
- `8` gold=`MessageBox.messageBox` prediction=`None`
- `9` gold=`DLL.Close` prediction=`None`
- `10` gold=`DLL.Close` prediction=`None`
