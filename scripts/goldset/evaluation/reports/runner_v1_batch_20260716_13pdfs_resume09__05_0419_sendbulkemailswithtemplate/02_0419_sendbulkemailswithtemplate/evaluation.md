# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs_resume09__05_0419_sendbulkemailswithtemplate`
- Case: `02_0419_sendbulkemailswithtemplate`
- Created at: ``
- Gold actions: `55`
- Prediction actions: `13`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0000`
- Canonical action multiset F1: `0.0000`
- Package multiset F1: `0.0294`
- Package family F1: `0.0588`
- Salient family F1: `0.0952`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.1818`
- WorFBench recall: `0.04`
- WorFBench F1: `0.0656`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-42`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.4127`

## First Mismatches

- `1` gold=`String.assign` prediction=`Message box.usingMessageBoxAction`
- `2` gold=`String.assign` prediction=`SharePoint.sharepointGetFolder`
- `3` gold=`MessageBox.messageBox` prediction=`Box.boxCrtFldr`
- `4` gold=`TaskBot.stopTask` prediction=`Box.boxCrtFldr`
- `5` gold=`String.assign` prediction=`Box.boxCrtFldr`
- `6` gold=`String.assign` prediction=`Box.boxCrtFldr`
- `7` gold=`String.assign` prediction=`Box.boxCrtFldr`
- `8` gold=`Folder.createFolder` prediction=`Box.boxCrtFldr`
- `9` gold=`Folder.createFolder` prediction=`Google Sheets.googleSheetsCreateWorkbook`
- `10` gold=`Folder.createFolder` prediction=`Loop.loop.commands.start`
- `11` gold=`Folder.createFolder` prediction=`Email.cloudUsingForwardAction`
- `12` gold=`Datetime.toString` prediction=`Error handler.errorHandlerCatch`
