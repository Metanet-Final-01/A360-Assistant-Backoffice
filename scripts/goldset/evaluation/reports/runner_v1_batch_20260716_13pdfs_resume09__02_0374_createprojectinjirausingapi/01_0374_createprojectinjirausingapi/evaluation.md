# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs_resume09__02_0374_createprojectinjirausingapi`
- Case: `01_0374_createprojectinjirausingapi`
- Created at: ``
- Gold actions: `17`
- Prediction actions: `8`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0000`
- Canonical action multiset F1: `0.0000`
- Package multiset F1: `0.0800`
- Package family F1: `0.0800`
- Salient family F1: `0.1000`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.1667`
- WorFBench recall: `0.0588`
- WorFBench F1: `0.087`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-9`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.6400`

## First Mismatches

- `1` gold=`XML.startSession` prediction=`XML.xmlStartSession`
- `2` gold=`XML.getSingleNode` prediction=`Microsoft Outlook (macOS).macMsOutlookConnectDisconnect`
- `3` gold=`XML.getSingleNode` prediction=`Microsoft 365 Outlook.ms365OutlookSaveAttachments`
- `4` gold=`XML.getSingleNode` prediction=`Microsoft Outlook (macOS).macMsOutlookConnectDisconnect`
- `5` gold=`XML.endSession` prediction=`Process Composer.processComposerCreateARequest`
- `6` gold=`Email.emailConnect` prediction=`Jira.jiraCreateProject`
- `7` gold=`Email.saveAttachment` prediction=`Loop.loop.commands.start`
- `8` gold=`Email.closeEmail` prediction=`Error handler.errorHandlerCatch`
- `9` gold=`Excel_MS.OpenSpreadsheet` prediction=`None`
- `10` gold=`Excel_MS.GetMultipleCells` prediction=`None`
- `11` gold=`MessageBox.messageBox` prediction=`None`
- `12` gold=`String.assign` prediction=`None`
