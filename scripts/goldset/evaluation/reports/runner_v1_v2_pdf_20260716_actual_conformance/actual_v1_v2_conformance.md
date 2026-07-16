# Actual v1/v2 conformance

| metric | v1 | v2 |
|---|---:|---:|
| pm4py_fitness | 0.0 | 0.0 |
| pm4py_precision | 0.0 | 0.0 |
| worf_precision | 0.1407 | 0.2687 |
| worf_recall | 0.0686 | 0.16 |
| worf_f1 | 0.0783 | 0.1873 |

| source_bot | v1 status | v2 status | v1 pred | v2 pred | v1 PM4Py fit | v2 PM4Py fit | v1 PM4Py prec | v2 PM4Py prec | v1 WorF F1 | v2 WorF F1 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0374_createprojectinjirausingapi | ok | ok | 10 | 15 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0741 | 0.25 |
| 0419_sendbulkemailswithtemplate | ok | ok | 13 | 13 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0317 | 0.0952 |
| 0131_currency-rate---oanda | no_prediction | ok | 0 | 20 | None | 0.0 | None | 0.0 | 0.0 | 0.0952 |
| 0176_global-exchange-currency-rate | ok | ok | 9 | 16 | 0.0 | 0.0 | 0.0 | 0.0 | 0.1176 | 0.1951 |
| 0393_a2019-twilio-integration | ok | ok | 7 | 19 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0408 | 0.0656 |
| 0224_extracttablesfrompdftocsv | ok | ok | 5 | 6 | 0.0 | 0.0 | 0.0 | 0.0 | 0.1176 | 0.3333 |
| 0225_extracttablefromwordtocsv | ok | ok | 6 | 9 | 0.0 | 0.0 | 0.0 | 0.0 | 0.1111 | 0.2857 |
| 0137_sendemailusingsmtp-demo | ok | ok | 7 | 5 | 0.0 | 0.0 | 0.0 | 0.0 | 0.2 | 0.25 |
| 0245_merge-multiple-csv-files | ok | ok | 4 | 8 | 0.0 | 0.0 | 0.0 | 0.0 | 0.069 | 0.1818 |
| 0302_a2019-get-current-exchange-rate | ok | ok | 6 | 16 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0833 | 0.2941 |
| 0167_outlook-email-notifier | ok | ok | 8 | 14 | 0.0 | 0.0 | 0.0 | 0.0 | 0.1212 | 0.2051 |
| 0338_lettergenerationbot | no_prediction | ok | 0 | 17 | None | 0.0 | None | 0.0 | 0.0 | 0.1386 |
| 0410_a2019-invoicely-assistant | ok | ok | 9 | 14 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0513 | 0.0455 |
