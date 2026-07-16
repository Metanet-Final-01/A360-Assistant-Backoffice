# Evaluation Report

- Run: `runner_v2_batch_20260716_13pdfs_resume04__07_0374_createprojectinjirausingapi`
- Case: `01_0374_createprojectinjirausingapi`
- Created at: ``
- Gold actions: `17`
- Prediction actions: `12`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.1379`
- Canonical action multiset F1: `0.1379`
- Package multiset F1: `0.2069`
- Package family F1: `0.4138`
- Salient family F1: `0.5000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.5`
- WorFBench recall: `0.3529`
- WorFBench F1: `0.4138`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-5`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.8276`

## First Mismatches

- `1` gold=`XML.startSession` prediction=`XML.xmlStartSession`
- `2` gold=`XML.getSingleNode` prediction=`XML.xmlGetSingleNode`
- `3` gold=`XML.getSingleNode` prediction=`XML.xmlGetSingleNode`
- `4` gold=`XML.getSingleNode` prediction=`Microsoft 365 Outlook.ms365OutlookSaveAllAttachments`
- `5` gold=`XML.endSession` prediction=`Microsoft 365 Outlook.ms365OutlookDisconnect`
- `6` gold=`Email.emailConnect` prediction=`Excel advanced.cloudExcelOpen`
- `7` gold=`Email.saveAttachment` prediction=`Excel advanced.excelAdvancedPackageReadRowAction`
- `8` gold=`Email.closeEmail` prediction=`Jira.jiraCreateIssue`
- `9` gold=`Excel_MS.OpenSpreadsheet` prediction=`JSON utilities.jsonStartSession`
- `10` gold=`Excel_MS.GetMultipleCells` prediction=`JSON utilities.convertJsonToDictionary`
- `11` gold=`MessageBox.messageBox` prediction=`Message box.usingMessageBoxAction`
- `12` gold=`String.assign` prediction=`Excel advanced.excelAdvancedPackageCloseAction`
