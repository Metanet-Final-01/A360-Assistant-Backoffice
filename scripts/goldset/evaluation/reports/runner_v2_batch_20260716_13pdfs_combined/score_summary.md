# Runner v2 13 PDF Evaluation Summary

- Cases: `13`

## Average F1
- action_seq_f1: `0.0013`
- action_bag_f1: `0.0013`
- canonical_seq_f1: `0.1402`
- canonical_bag_f1: `0.1466`
- package_bag_f1: `0.1684`
- package_family_f1: `0.2226`
- salient_family_f1: `0.2004`
- edge_f1: `0.0000`
- canonical_edge_f1: `0.0418`
- worfbench_node_f1: `0.0016`
- worfbench_edge_f1: `0.6483`

## Case Scores
| case | gold | pred | canonical bag | package family | salient family | worf node | worf edge |
|---|---:|---:|---:|---:|---:|---:|---:|
| 03_0131_currency-rate---oanda | 45 | 16 | 0.0656 | 0.1639 | 0.1176 | 0.0000 | 0.5424 |
| 08_0137_sendemailusingsmtp-demo | 3 | 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.0000 |
| 11_0167_outlook-email-notifier | 24 | 13 | 0.2162 | 0.3243 | 0.4444 | 0.0000 | 0.6842 |
| 04_0176_global-exchange-currency-rate | 24 | 13 | 0.2703 | 0.3784 | 0.1429 | 0.0000 | 0.6842 |
| 06_0224_extracttablesfrompdftocsv | 10 | 6 | 0.2500 | 0.2500 | 0.3077 | 0.0000 | 0.6667 |
| 07_0225_extracttablefromwordtocsv | 10 | 9 | 0.2105 | 0.2105 | 0.2500 | 0.0000 | 0.8571 |
| 09_0245_merge-multiple-csv-files | 29 | 7 | 0.0556 | 0.1111 | 0.0000 | 0.0000 | 0.4375 |
| 10_0302_a2019-get-current-exchange-rate | 18 | 13 | 0.3871 | 0.5161 | 0.5000 | 0.0000 | 0.8387 |
| 12_0338_lettergenerationbot | 98 | 16 | 0.0702 | 0.0702 | 0.1176 | 0.0204 | 0.3200 |
| 01_0374_createprojectinjirausingapi | 17 | 12 | 0.1379 | 0.4138 | 0.5000 | 0.0000 | 0.8276 |
| 05_0393_a2019-twilio-integration | 47 | 16 | 0.0635 | 0.1587 | 0.0000 | 0.0000 | 0.5517 |
| 13_0410_a2019-invoicely-assistant | 31 | 13 | 0.0909 | 0.0909 | 0.1000 | 0.0000 | 0.6047 |
| 02_0419_sendbulkemailswithtemplate | 55 | 13 | 0.0882 | 0.2059 | 0.1250 | 0.0000 | 0.4127 |

## Top Canonical Positional Mismatches
- `7` gold=`String.stringPackageAssignAction` pred=`Dictionary.dictionaryPut`
- `6` gold=`Folder.folderCreate` pred=`JSON utilities.jsonGetNodeValue`
- `5` gold=`DLL.dllPackageCloseAction` pred=`None`
- `4` gold=`String.stringPackageAssignAction` pred=`Prompt.promptForValue`
- `4` gold=`Folder.folderCreate` pred=`None`
- `4` gold=`Recorder.capture` pred=`Browser.browserPackageRunJavascriptAction`
- `3` gold=`String.stringPackageAssignAction` pred=`File.filePackageCreateAction`
- `3` gold=`String.stringPackageAssignAction` pred=`Folder.folderCreate`
- `3` gold=`MessageBox.messageBox` pred=`Message box.usingMessageBoxAction`
- `3` gold=`String.stringPackageAssignAction` pred=`Message box.usingMessageBoxAction`
- `2` gold=`MessageBox.messageBox` pred=`Task Bot.taskBotPackageStopAction`
- `2` gold=`TaskBot.stopTask` pred=`Folder.folderCreate`
- `2` gold=`String.stringPackageAssignAction` pred=`Datetime.getActionInDatetime`
- `2` gold=`String.stringPackageAssignAction` pred=`Microsoft 365 Excel package in Automation 360.usingOpenWorkbookAction`
- `2` gold=`LogToFile.logToFile` pred=`Message box.usingMessageBoxAction`
- `2` gold=`String.stringPackageAssignAction` pred=`Browser.launchWebsite`
- `2` gold=`Dictionary.dictionaryPut` pred=`Application.applicationPackageOpenProgramFileAction`
- `2` gold=`DLL.dllPackageOpenAction` pred=`Message box.usingMessageBoxAction`
- `2` gold=`MessageBox.messageBox` pred=`None`
- `2` gold=`String.stringPackageAssignAction` pred=`JSON utilities.jsonValidate`
- `2` gold=`XML.getSingleNode` pred=`XML.xmlGetSingleNode`
- `2` gold=`String.stringPackageAssignAction` pred=`Task Bot.taskBotPackageStopAction`
- `1` gold=`String.stringPackageAssignAction` pred=`If.ifPackageElseIfOptionalAction`
- `1` gold=`String.stringPackageAssignAction` pred=`Microsoft 365 Excel package in Automation 360.getWorksheetAsDataTableIn`
- `1` gold=`Folder.folderCreate` pred=`Error handler.errorHandlerCatch`
- `1` gold=`Folder.folderCreate` pred=`Error handler.errorHandlerFinally`
- `1` gold=`Folder.folderCreate` pred=`Browser.launchWebsite`
- `1` gold=`Folder.folderCreate` pred=`Prompt.promptForValue`
- `1` gold=`Datetime.cloudConvertDatetimeToString` pred=`Microsoft 365 Excel package in Automation 360.office365ExcelSetCell`
- `1` gold=`DLL.dllPackageOpenAction` pred=`Email.cloudUsingSendAction`