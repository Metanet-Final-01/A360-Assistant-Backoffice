# 골드셋 확장 후보 — GPT 검토용 번들

## 선정 과정

```
470개 전체(817 워크플로우)
  -> RAG 카탈로그 정규화 완전일치 + main workflow + 액션수>=3   148개
  -> Exaone(EXAONE-4.0-32B, reasoning ON) '골드셋 가치 있음' 판정   49개
  -> 후순위 필터(정확한 package.action 매칭, DLL/Python/JS/VBScript/
     RunMacro/runApp/REST/미해결TaskBot.runTask만 제외)          34개 (Main 후보)
                                                                15개 (후순위 보존)
```

## 후순위 판정 기준 (3원칙)

1. 액션명만으로 일반적인 규칙기반 채점이 어려운가
2. 정답 여부를 판단하려면 스크립트/함수명/URL/Body/SQL 등 하위 구현까지 확인해야 하는가
3. 그 구현 방식이 업무상 반드시 필요한 선택이 아니라 다른 A360 액션으로 대체 가능한가

후순위 = 가치 없음이 아니라, 지금의 액션·구조 중심 규칙기반 Main 평가에 안 맞아 별도 보존한다는 뜻.
File/Folder/MessageBox/Screen/LogToFile/XML/JSONHandler/Dictionary/List는 이 필터에 포함되지 않음
(기능이 명확해서 별도 가치판단 대상).

## Main 후보 (34개)

| 봇 | 파일 | 액션수 |
|---|---|---|
| 0011_CareerInsightAgentMVP | Automation Anywhere__Bots__Users__jp@theevolutia.com__CareerInsightAgent__Tools__Tool_LogReviewData.goldset.json | 6 |
| 0017_CandidatePersonalityInsightsAIAgent | Automation Anywhere__Bots__Users__vatsal@hedehi.com__Candidate Personality Insights AI Agent__Tools__GetOutcomes.goldset.json | 9 |
| 0033_SupplyChainRiskAgent-by-SahilAmirov | Automation Anywhere__Bot Store__SupplyChainRiskAgent__Tools__Bot_Update_Excel_Status.goldset.json | 3 |
| 0048_invoice-analyst-agent | Automation Anywhere__Bots__AWS Solution Accelerators__AI Agent - Invoice Analyst__Automated Tasks__Setup Log Folders and Locations.goldset.json | 7 |
| 0080_readmailwithuserschoicesconnectiontype | Automation Anywhere__Bot Store__Predikly_Bots__12_Read_Mail__ReadMailWithUsersChoicesConnectionType.goldset.json | 14 |
| 0084_sub-pdfseparatorbypage | Automation Anywhere__Bot Store__Predikly_Bots__19_PDF_Split_By_Blank_Page__Sub_PDFSeparatorByPage.goldset.json | 13 |
| 0089_archivaloffiles | Automation Anywhere__Bot Store__Predikly_Bots__09_Archival_of_files_in_folder__ArchivalOfFiles.goldset.json | 14 |
| 0098_delete-junk-files-and-folder-from-system | Automation Anywhere__Bot Store__Predikly_Bots__30_Delete_Junk_file_from_the_system__Delete_Junk_Files_And_Folder_From_System.goldset.json | 12 |
| 0104_template-taskbot | Automation Anywhere__Bot Store__Template Bot - Verinext__Template_Taskbot.goldset.json | 6 |
| 0106_a360---execution-and-error-logs | Automation Anywhere__Bot Store__Execution and Error Logs__A360 - Execution and Error Logs.goldset.json | 3 |
| 0131_currency-rate---oanda | Automation Anywhere__Bot Store__Exchange Rate - Team Computers__Currency Rate - Oanda.goldset.json | 24 |
| 0140_new-user-registration-with-unique-user-id | Automation Anywhere__Bot Store__New User Registration with Unique User ID - KLOUDPAD Mobility Research Pvt Ltd__New User Registration with Unique User ID.goldset.json | 15 |
| 0147_salesforce-multi-language | Automation Anywhere__Bot Store__Salesforce Multi Language Package- Automation Anywhere__Salesforce Multi Language.goldset.json | 8 |
| 0149_control-room-audit-logs | Automation Anywhere__Bot Store__Control Room Audit Logs - Automation Anywhere__PrepareDateTime.goldset.json | 5 |
| 0161_extractcandidateinfofromportal-master | Automation Anywhere__Bot Store__ESSPL - Contribution__HR_TA_Process__Business Bots__ReadConfig.goldset.json | 11 |
| 0164_read-configuration | Automation Anywhere__Bot Store__ESSPL - Contribution__Read Config__Read Configuration.goldset.json | 20 |
| 0199_slack-integration-package | Automation Anywhere__Bot Store__Slack Integration Package.goldset.json | 7 |
| 0232_a2019---findnextworkingdate | Automation Anywhere__Bot Store__FindWorkingDate__A2019 - FindNextWorkingDate.goldset.json | 10 |
| 0239_employeebonuscalculationtask | Automation Anywhere__Bot Store__AARI_BonusCalculation-In2it__SendBonusNotificationEmailTaskBot.goldset.json | 12 |
| 0276_checkifinputisalphanumeric | Automation Anywhere__Bot Store__CheckInputisAlphaNumeric__CheckIfInputisAlphaNumeric.goldset.json | 14 |
| 0288_getquarterinformation | Automation Anywhere__Bot Store__GetQuarterInformation__GetQuarterInformation.goldset.json | 13 |
| 0307_payment-reminder-with-cross-sell | Automation Anywhere__Bot Store__Payment Reminder - Automation Anywhere__Payment Reminder with Cross Sell.goldset.json | 21 |
| 0313_sendemailwithimagehtml | Automation Anywhere__Bot Store__SendEmailWithImage-Citiustech__SendEmailWithImageHTML.goldset.json | 5 |
| 0348_a2019---microsoft-word-package | Automation Anywhere__Bot Store__A2019 - Microsoft Word Package__A2019 - Microsoft Word Package.goldset.json | 16 |
| 0359_contract-creation-using-ps-activity-guide-mekkanos | Automation Anywhere__Bot Store__Contract Creation using PS Activity Guide__SubTask_ReadUserConfig.goldset.json | 11 |
| 0376_filebackup | Automation Anywhere__Bot Store__FileBackup - Persys__FileBackup.goldset.json | 11 |
| 0381_SalesforcePackage | Automation Anywhere__Bot Store__Salesforce Package - Automation Anywhere__Salesforce Package Sample Bot.goldset.json | 17 |
| 0404_a2019-breathehr-expense-assistant | Automation Anywhere__Bot Store__A2019 - BreatheHR Expense Assistant - KLOUDPAD Mobility Research Pvt Ltd__A2019-BreatheHR Expense Assistant.goldset.json | 9 |
| 0411_check-urls-virustotal | Automation Anywhere__Bot Store__Check URLs Threats From VirusTotal Portal - Globaltek__lib__subTrazaMensajeBot.goldset.json | 6 |
| 0412_a2019-camunda-dmn-package | Automation Anywhere__Bot Store__Camunda DMN Package - Automation Anywhere__A2019 Camunda DMN Package.goldset.json | 3 |
| 0419_sendbulkemailswithtemplate | Automation Anywhere__Bot Store__SendBulkEmailsWithTemplate - FPT Software__SendBulkEmailsWithTemplate.goldset.json | 21 |
| 0425_trello-package | Automation Anywhere__Bot Store__Trello Package.goldset.json | 12 |
| 0447_sending-birthday-email | Automation Anywhere__Bot Store__Sending Birthday Email.goldset.json | 7 |
| 0461_a2019---html-parser-package | Automation Anywhere__Bot Store__HTML.goldset.json | 4 |

## 후순위 보존 (15개) — 사유 포함

| 봇 | 파일 | 액션수 | 후순위 사유 |
|---|---|---|---|
| 0154_get-last-day-of-any-month-by-year-and-month | Automation Anywhere__Bot Store__20_11_2021__Get last day of any month by year and month.goldset.json | 6 | DLL.Close, DLL.Open, DLL.Run function |
| 0203_apimaster | Automation Anywhere__Bot Store__API_UserMgmtBot__APIMaster.goldset.json | 6 | Rest.restDelete, Rest.restPost |
| 0242_formsbotai-parascript-demo | Automation Anywhere__Bot Store__FormsBot.AI-Parascript.goldset.json | 16 | DLL.Close, DLL.Open, DLL.Run function |
| 0243_maintask-pdftoexcel | Automation Anywhere__Bot Store__PDF To Excel - CitiusTech__MainTask_PDFToExcel.goldset.json | 13 | Excel_MS.RunMacro |
| 0244_maintask-pdftoword | Automation Anywhere__Bot Store__PDFToWord - CitiusTech__MainTask_PDFToWord.goldset.json | 14 | Excel_MS.RunMacro |
| 0247_AWS-S3-File-Management-06-20-2023 | Automation Anywhere__Bot Store__AWS S3 File Management - Automation Anywhere__AWS S3 File Management.goldset.json | 14 | DLL.Close, DLL.Open, DLL.RunCSharpDLL_V1 |
| 0296_global-value-deletion-via-api | Automation Anywhere__Bot Store__Global Value Deletion Through API__Global Value Deletion via API.goldset.json | 7 | Rest.restDelete, Rest.restPost |
| 0338_lettergenerationbot | Automation Anywhere__Bot Store__A2019 - AARI for Web - PoF Letter Generation__LetterGenerationBot.goldset.json | 26 | Rest.restGet |
| 0353_determinenumberofpagesinpdffile | Automation Anywhere__Bot Store__DetermineNumberOfPagesInPDFFile.goldset.json | 6 | DLL.Close, DLL.Open, DLL.Run function |
| 0357_wordtopdf-citiustech | Automation Anywhere__Bot Store__WordToPDF-Citiustech__WordToPDF-Citiustech.goldset.json | 14 | DLL.Close, DLL.Open, DLL.RunCSharpDLL_V1 |
| 0374_createprojectinjirausingapi | Automation Anywhere__Bot Store__CreateProjectInJIRAusingAPI.goldset.json | 13 | Rest.restPost |
| 0394_npiserach-citiustech | Automation Anywhere__Bot Store__NPISearch-Citiustech__NPISerach.goldset.json | 8 | Excel_MS.RunMacro, Rest.restPost |
| 0416_providersanctionvalidation-citiustech | Automation Anywhere__Bot Store__ProviderSanctionValidation-CitiusTech__GetProviderDetails.goldset.json | 6 | Excel_MS.RunMacro |
| 0427_a2019---extract-json-data | Automation Anywhere__Bot Store__A2019 - Extract JSON Data - KLOUDPAD Mobility Research Pvt Ltd__A2019 - Extract JSON Data.goldset.json | 4 | Rest.restPost |
| 0440_indian-state-wise-covid-19-day-to-day-status | Automation Anywhere__Bot Store__Indian State Wise Covid-19 Day to Day Status.goldset.json | 7 | Rest.restGet |