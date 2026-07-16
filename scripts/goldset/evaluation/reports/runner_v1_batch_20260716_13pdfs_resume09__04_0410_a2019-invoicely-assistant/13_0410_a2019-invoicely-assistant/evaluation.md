# Evaluation Report

- Run: `runner_v1_batch_20260716_13pdfs_resume09__04_0410_a2019-invoicely-assistant`
- Case: `13_0410_a2019-invoicely-assistant`
- Created at: ``
- Gold actions: `31`
- Prediction actions: `8`

## Scores

- Action sequence LCS F1: `0.0000` (precision `0.0000`, recall `0.0000`)
- Action multiset F1: `0.0000`
- Canonical action sequence LCS F1: `0.0000`
- Canonical action multiset F1: `0.0000`
- Package multiset F1: `0.0000`
- Package family F1: `0.1538`
- Salient family F1: `0.0606`
- Adjacent edge F1: `0.0000`
- PM4Py fitness: `0.0`
- PM4Py precision: `0.0`
- WorFBench precision: `0.1667`
- WorFBench recall: `0.0333`
- WorFBench F1: `0.0556`

## Diagnostic Artifact Check

- Gold PNML readable: `True`
- Prediction PNML readable: `True`
- Tree leaf delta: `-23`
- PNML hash equal: `False`
- Diagnostic WorFBench node-label F1: `0.0000`
- Diagnostic WorFBench edge F1: `0.4211`

## First Mismatches

- `1` gold=`Browser.launchWebsite` prediction=`JavaScript.javascriptRun`
- `2` gold=`Recorder.capture` prediction=`JavaScript.javascriptRun`
- `3` gold=`Recorder.capture` prediction=`Logging.logToFileLogVariablesToFile`
- `4` gold=`Recorder.capture` prediction=`Excel advanced.excelAdvancedPackageReadRowAction`
- `5` gold=`Recorder.capture` prediction=`Loop.loop.commands.start`
- `6` gold=`Recorder.capture` prediction=`Loop.loop.commands.start`
- `7` gold=`Recorder.capture` prediction=`Logging.logToFileLogVariablesToFile`
- `8` gold=`Recorder.capture` prediction=`Task Bot.taskBotPackageStopAction`
- `9` gold=`Folder.createFolder` prediction=`None`
- `10` gold=`MessageBox.messageBox` prediction=`None`
- `11` gold=`LogToFile.logToFile` prediction=`None`
- `12` gold=`LogToFile.logToFile` prediction=`None`
