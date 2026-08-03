# 최종 Goldset 9: 수정 업무정의서 v2/v3 평가

## 실행 범위

- 수정 업무정의서 9개를 사용했다.
- v2와 v3 모두 9개를 채점했다.
- Gold와 예측 Workflow에는 동일한 규칙기반 변환을 적용했다.
- Rule-only 결과를 재현 가능한 기준선으로 사용하고 Judge 보조 결과는 분리했다.

## 전체 결과

| Agent | Gold | Pred | Rule TP | Macro Action F1 | Macro Chain F1 | Micro Action F1 |
|---|---:|---:|---:|---:|---:|---:|
| v2 | 318 | 51 | 40 | 0.236 | 0.197 | 0.217 |
| v3 | 318 | 92 | 58 | 0.304 | 0.264 | 0.283 |

현재 실행의 Judge 보조 Macro Action F1은 v2 0.241, v3 0.308이다. Judge 판정은 실행 간 변동이 확인됐으므로 공식 Rule-only 기준선과 합치지 않는다.

## 케이스별 Rule-only 결과

| ID | v2 Action F1 | v3 Action F1 | v3 Chain F1 |
|---|---:|---:|---:|
| 0085 | 0.130 | 0.160 | 0.160 |
| 0089 | 0.000 | 0.216 | 0.216 |
| 0098 | 0.000 | 0.062 | 0.031 |
| 0112 | 0.400 | 0.381 | 0.286 |
| 0131 | 0.367 | 0.415 | 0.340 |
| 0140 | 0.510 | 0.464 | 0.464 |
| 0164 | 0.162 | 0.211 | 0.211 |
| 0376 | 0.519 | 0.625 | 0.500 |
| 0419 | 0.040 | 0.203 | 0.169 |

## 근거가 확인된 정규화

- Microsoft 365 Excel의 Connect 액션은 OAuth 연결 설정이다. 기존 Email/Gmail Connect와 같은 세션 생명주기 규칙으로 Gold와 예측 변환기 양쪽에서 제외했다.
- `Excel.GoToCell`, `Excel_MS.GoToCell`, `office365ExcelGoToCell`은 카탈로그상 모두 특정 셀로 이동하고 반환값이 없다. 동일한 canonical action으로 등록했다.
- `Excel_MS.GoToNextEmptyCell`과 Microsoft 365 Excel의 `goToNextEmptyCellMs365Excel`은 각각 커서 이동과 빈 셀 주소 반환으로 동작이 다르다. 이름은 유사하지만 동치 규칙으로 등록하지 않았다.

## 저점 사례 판정

- 0098: v3는 File/Folder 삭제 액션을 생성했지만 Gold의 다중 분기와 반복을 대부분 생성하지 못했다.
- 0164: XML 세션과 노드 조회는 생성했으나 Dictionary 병합 작업을 생성하지 못했다.
- 0419: Excel 읽기와 메일 발송은 생성했으나 템플릿 치환과 완료 파일 정리가 누락됐다.

위 세 사례는 현재 확인 범위에서 채점기 보정보다 Agent의 부분 생성이 주원인이다. 반복 액션을 하나로 간주하거나 서로 다른 구현을 임의 동치로 묶는 규칙은 추가하지 않았다.

상세 action 목록과 Judge 로그는 `reports/final_goldset_9_evaluation/corebrief_v2_v3_20260803/`에 있다.
