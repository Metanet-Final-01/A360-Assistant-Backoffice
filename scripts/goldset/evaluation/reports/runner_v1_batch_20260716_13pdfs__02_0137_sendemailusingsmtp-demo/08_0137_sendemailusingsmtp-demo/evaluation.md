# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs__02_0137_sendemailusingsmtp-demo`
- Case: `08_0137_sendemailusingsmtp-demo`
- Created at: ``
- Gold actions: `3`
- Prediction actions: `7`

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
- WorFBench precision: `0.2`
- WorFBench recall: `0.3333`
- WorFBench F1: `0.25`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `4`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.5000`

## First Mismatches

- `1` gold=`DLL.Open` prediction=`Apple Mail.appleMailConnectDisconnect`
- `2` gold=`DLL.RunCSharpDLL_V1` prediction=`Gmail.sendGmailPkg`
- `3` gold=`DLL.Close` prediction=`Email.cloudUsingSendAction`
- `4` gold=`None` prediction=`Databricks.getjoboutDbricks`
- `5` gold=`None` prediction=`Error handler.errorHandlerCatch`
- `6` gold=`None` prediction=`Error handler.errorHandlerThrow`
- `7` gold=`None` prediction=`Email.emailDisconnectAction`
