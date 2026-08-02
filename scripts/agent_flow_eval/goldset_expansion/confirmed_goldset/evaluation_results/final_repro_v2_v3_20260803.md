# 최종 Goldset 9: 핵심업무 분류 + 분기 진단 반영, v2/v3 재현성 배치 (2026-08-03)

## 실행 범위

- v2, v3 에이전트를 9개 확정 케이스 전체에 대해 **실제로 새로 재실행**했다(기존
  예측 재사용 아님) - `runner_v2_batch_20260716_13pdfs` 계열이 아니라
  `final_goldset_9_repro_{v2,v3}_20260803` run-prefix, 둘 다 9/9 재시도 없이 1회
  성공.
- 채점은 `audit_final_goldset.py`로 실행했다(9개 확정 케이스 전용 배치 스크립트 -
  `run_eval_batch.py`는 레거시 13개 케이스 전용이라 이번 케이스와 무관).
- 이번 실행부터 **핵심업무 분류**(`classify_core_business_actions`)와 **분기
  진단 지표**(`score_branch_coverage`)가 처음으로 반영됐다 - 아래 "무엇이
  달라졌나" 참고.
- Rule-only 결과를 재현 가능한 기준선으로 사용하고 Judge 보조 결과는 분리했다
  (기존 관례 유지).

## 무엇이 달라졌나 (2026-08-02 corebrief 대비)

1. **핵심업무 분류(신규)**: `Folder.createFolder/deleteFolder`, `File.createFile`,
   `String.assign`, `Datetime.subtract/toString/assign`, `Number.assignToNumber`,
   `Boolean.assign`, `MessageBox.messageBox`처럼 패키지·액션명만으로는 핵심업무인지
   범용 셋업(로그 폴더 준비, 경로 조립, 검증 안내 메시지 등)인지 구별 안 되는
   액션만 대상으로, 키워드 규칙(무료) → 남은 것만 gpt-4o-mini가 업무정의서 원문과
   실제 파라미터를 보고 판단한다. **Gold와 예측 양쪽에 대칭 적용** - `gold_core_actions/
   <id>.json`(공식 채점에서 이미 제외된 Gold 전용 비대칭 UID 목록)과는 다른 별개
   시스템이다. 사람이 검토한 `gold_core_actions/0089.json`·`0098.json`과 대조한
   결과 52건의 제외 중 52건 일치(추가 2건은 검증 안내 메시지박스 - 합리적인 추가
   판정으로 확인).
2. **분기(if/elseIf/else) 진단 지표(신규, 별도 지표)**: 상호배타적 분기가
   flatten 시 하나의 목록으로 풀려서 생기는 중복 카운트 문제에 대해, 새 분기
   매칭 알고리즘 없이 이미 계산된 Rule/Judge 매칭 결과를 재사용해 분기별
   커버리지를 집계한다. **메인 Action F1/Chain F1에는 반영되지 않는다** - 순수
   참고용.
3. 위 변경으로 Gold 액션 총합이 318개(9개 합산) → **핵심업무만 130개**로
   줄었다(188개가 범용 셋업으로 분류·제외됨, v2/v3 동일 - Gold 분류는 예측과
   무관하게 결정적임을 재확인).

**주의**: 이번 실행은 에이전트를 새로 돌린 결과라 2026-08-02 corebrief 수치와의
차이에는 (a) 이 채점기 변경 효과와 (b) 에이전트 자체의 실행별 비결정성
(`HANDOFF_CODEX.md`에 이미 문서화됨, temperature=0/seed=0에도 완전한 결정성은
아님) 두 가지가 섞여 있다 - 전부 채점기 덕이라고 단정하지 않는다.

## 전체 결과 (핵심업무만, Rule-only = 재현 가능한 기준선)

| Agent | Gold(핵심) | Pred(핵심) | Rule TP | Macro Action F1 | Macro Chain F1 | Micro Action F1 |
|---|---:|---:|---:|---:|---:|---:|
| v2 | 130 | 60 | 29 | 0.260 | 0.260 | 0.305 |
| v3 | 130 | 84 | 40 | 0.412 | 0.374 | 0.374 |

Judge-assisted (참고, 실행 간 변동 있음): v2 Macro F1 0.260(Judge 미사용, TP 동일),
v3 Macro F1 0.418 / Macro Chain F1 0.380 (Judge TP 41, Rule TP 40 대비 1건 추가 매칭).

## 분기 진단 지표 (참고, 메인 점수와 별개)

| Agent | 적용 가능 케이스 | 평균 Branch Coverage | 평균 Branch Score |
|---|---:|---:|---:|
| v2 | 8/9 | 0.125 | 0.143 |
| v3 | 8/9 | 0.172 | 0.202 |

0131은 v2/v3 모두 if 분기 내부에 핵심업무 액션이 없어 "해당 없음"으로 분모에서
빠졌다(6개 if 그룹이 전부 서식/보조 로직).

## 핵심업무 분류 규모

| Agent | Gold 제외 | Pred 제외 | 분류 LLM 호출(로그 기준, 캐시 히트 포함) |
|---|---:|---:|---:|
| v2 | 188 (9개 케이스 합) | 4 | gold 128 / pred 5 |
| v3 | 188 (v2와 동일 - Gold 분류는 결정적) | 22 | gold 128(대부분 캐시 재사용) / pred 24 |

## 케이스별 Rule-only 결과 (핵심업무 기준)

| ID | v2 Gold | v2 Pred | v2 F1 | v3 Gold | v3 Pred | v3 F1 | v3 Chain F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0085 | 7 | 2 | 0.444 | 7 | 5 | 0.667 | 0.667 |
| 0089 | 4 | 1 | 0.000 | 4 | 3 | 0.286 | 0.286 |
| 0098 | 8 | 3 | 0.000 | 8 | 6 | 0.429 | 0.286 |
| 0112 | 9 | 2 | 0.182 | 9 | 9 | 0.667 | 0.667 |
| 0131 | 24 | 13 | 0.324 | 24 | 19 | 0.372 | 0.279 |
| 0140 | 26 | 8 | 0.235 | 26 | 12 | 0.263 | 0.211 |
| 0164 | 18 | 5 | 0.261 | 18 | 7 | 0.400 | 0.400 |
| 0376 | 15 | 13 | 0.643 | 15 | 7 | 0.455 | 0.455 |
| 0419 | 19 | 13 | 0.250 | 19 | 16 | 0.171 | 0.114 |

## 저점 사례 판정

- **0089/v2, 0098/v2 (F1=0.000)**: v2 예측이 핵심업무 액션을 각각 1개/3개만
  생성했고 그마저 Gold와 매칭되지 않았다(0089는 `Locale.changedate` 하나만
  생성, Gold의 실제 보관 폴더 생성/파일 이동을 만들지 못함; 0098은 삭제 대신
  폴더/파일 조회 액션만 생성). v3는 같은 두 케이스에서 F1 0.286/0.429로
  개선됐다.
- **0419/v3 (F1=0.171, 9개 중 최저)**: 이메일 발송(`ms365OutlookSend`)과 Excel
  읽기 일부는 생성했으나 Gold가 요구하는 다중 반복 처리(String.replace ×4,
  Excel.SaveSpreadSheet ×2, File.copyFiles/deleteFiles)를 만들지 못했다. Judge
  Match가 1건 추가로 잡아 Judge F1 0.229로 소폭 상승했다.
- **0131 (both)**: if 분기 자체가 서식/보조 로직뿐이라 분기 진단 지표가
  "해당 없음"으로 빠진다 - 이 케이스의 핵심업무는 조건 분기가 아니라 순차적인
  웹 자동화 + Excel 기록이라 분기 지표 미적용이 올바른 판정이다.

이번 실행 범위에서 확인한 저점 원인은 채점기 보정 여지가 아니라 에이전트의
부분 생성이다 - 반복 액션을 하나로 간주하거나 임의 동치를 추가하지 않았다.

## 산출물

- 상세 원본: `reports/final_goldset_9_evaluation/final_repro_v2_v3_20260803/`
  (`audit.json`/`summary.csv`/`summary.md`)
- 실행 로그(대용량, 재현 가능, 커밋 대상 아님): `runner/logs/final_goldset_9_repro_v2_20260803/`,
  `runner/logs/final_goldset_9_repro_v3_20260803/`
- 세부 엑셀(Overview/Aggregates/Matches/Unmatched/JudgeLog/CoreBusinessClassification/BranchCoverageDetail
  7개 시트): `final_repro_v2_v3_20260803_detail.xlsx` (이 폴더)
