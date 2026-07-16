# v1 vs v2 Actual Evaluation Compare

| metric | v1 | v2 | delta |
|---|---:|---:|---:|
| canonical_action_f1 | 0.0165 | 0.1466 | 0.1301 |
| canonical_sequence_f1 | 0.0165 | 0.1402 | 0.1237 |
| package_family_f1 | 0.1131 | 0.2226 | 0.1095 |
| pm4py_fitness | 0.0135 | 0.0791 | 0.0656 |
| pm4py_precision | 0.0 | 0.0385 | 0.0385 |
| worfbench_precision | 0.2354 | 0.4081 | 0.1727 |
| worfbench_recall | 0.0901 | 0.1838 | 0.0937 |
| worfbench_f1 | 0.1146 | 0.244 | 0.1294 |

| case_id | v1 status | v2 status | v1 WorF F1 | v2 WorF F1 | delta WorF F1 | v1 PM4Py fitness | v2 PM4Py fitness | delta fitness |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 01_0374_createprojectinjirausingapi | ok | ok | 0.087 | 0.4138 | 0.3268 | 0.0 | 0.0 | 0.0 |
| 02_0419_sendbulkemailswithtemplate | ok | ok | 0.0656 | 0.1905 | 0.1249 | 0.0 | 0.0571 | 0.0571 |
| 03_0131_currency-rate---oanda | skipped | ok | None | 0.2264 | None | None | 0.0588 | None |
| 04_0176_global-exchange-currency-rate | ok | ok | 0.125 | 0.3158 | 0.1908 | 0.0 | 0.1 | 0.1 |
| 05_0393_a2019-twilio-integration | ok | ok | 0.0426 | 0.1404 | 0.0978 | 0.0741 | 0.1081 | 0.034 |
| 06_0224_extracttablesfrompdftocsv | ok | ok | 0.1176 | 0.2222 | 0.1046 | 0.0 | 0.25 | 0.25 |
| 07_0225_extracttablefromwordtocsv | ok | ok | 0.1111 | 0.1905 | 0.0794 | 0.0 | 0.2105 | 0.2105 |
| 08_0137_sendemailusingsmtp-demo | ok | ok | 0.25 | 0.4 | 0.15 | 0.0 | 0.0 | 0.0 |
| 09_0245_merge-multiple-csv-files | ok | ok | 0.069 | 0.1875 | 0.1185 | 0.0741 | 0.0667 | -0.0074 |
| 10_0302_a2019-get-current-exchange-rate | ok | ok | 0.087 | 0.2581 | 0.1711 | 0.0 | 0.0 | 0.0 |
| 11_0167_outlook-email-notifier | ok | ok | 0.25 | 0.3333 | 0.0833 | 0.0 | 0.0 | 0.0 |
| 12_0338_lettergenerationbot | skipped | ok | None | 0.2 | None | None | 0.0714 | None |
| 13_0410_a2019-invoicely-assistant | ok | ok | 0.0556 | 0.093 | 0.0374 | 0.0 | 0.1053 | 0.1053 |
