# Runner v1 Evaluation Summary

- Evaluated: `12`
- Failed/excluded: `12_0338_lettergenerationbot`
- Oanda note: used `turnAnalyze` recommendation fallback because v1 returned recommendation during analysis and none during second recommend call.

## Average F1
- canonical_seq_f1: `0.0151`
- canonical_bag_f1: `0.0151`
- package_family_f1: `0.1160`
- salient_family_f1: `0.0973`
- worfbench_edge_f1: `0.4709`
- worfbench_node_f1: `0.0000`

## Case Scores
| case | gold | pred | canon seq | canon bag | pkg family | salient | worf edge | note |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 03_0131_currency-rate---oanda | 45 | 9 | 0.000 | 0.000 | 0.148 | 0.207 | 0.346 | turnAnalyze fallback |
| 08_0137_sendemailusingsmtp-demo | 3 | 7 | 0.000 | 0.000 | 0.000 | 0.000 | 0.500 |  |
| 11_0167_outlook-email-notifier | 24 | 8 | 0.000 | 0.000 | 0.250 | 0.429 | 0.485 |  |
| 04_0176_global-exchange-currency-rate | 24 | 9 | 0.000 | 0.000 | 0.182 | 0.133 | 0.529 |  |
| 06_0224_extracttablesfrompdftocsv | 10 | 5 | 0.000 | 0.000 | 0.000 | 0.000 | 0.588 |  |
| 07_0225_extracttablefromwordtocsv | 10 | 6 | 0.000 | 0.000 | 0.000 | 0.000 | 0.667 |  |
| 09_0245_merge-multiple-csv-files | 29 | 4 | 0.061 | 0.061 | 0.121 | 0.000 | 0.276 |  |
| 10_0302_a2019-get-current-exchange-rate | 18 | 6 | 0.083 | 0.083 | 0.250 | 0.143 | 0.500 |  |
| 01_0374_createprojectinjirausingapi | 17 | 8 | 0.000 | 0.000 | 0.080 | 0.100 | 0.640 |  |
| 05_0393_a2019-twilio-integration | 47 | 7 | 0.037 | 0.037 | 0.148 | 0.000 | 0.286 |  |
| 13_0410_a2019-invoicely-assistant | 31 | 8 | 0.000 | 0.000 | 0.154 | 0.061 | 0.421 |  |
| 02_0419_sendbulkemailswithtemplate | 55 | 13 | 0.000 | 0.000 | 0.059 | 0.095 | 0.413 |  |