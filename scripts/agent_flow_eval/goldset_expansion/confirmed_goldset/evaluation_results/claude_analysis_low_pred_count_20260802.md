# 낮은 pred_count 원인 분석

수정 업무정의서로 다시 실행한 v2 결과 중 0085, 0089, 0098, 0112의 추천 action과 최종 채점 입력을 직접 비교했다.

## 확인 결과

| 후보 | 생성 결과 | 확인된 문제 |
|---|---|---|
| 0085 GetNextBusinessWorkingDay | Excel 열기, 시트 읽기, Excel 닫기 3개 | 다음 영업일 계산에 필요한 Datetime.add 및 날짜 변환 action을 생성하지 않았다. |
| 0089 ArchivalOfFiles | action 0개 | 분석은 완료됐지만 Folder 생성, File 복사·삭제 action을 카탈로그에서 확정하지 못하고 빈 Workflow를 반환했다. |
| 0098 Delete Junk Files | 시스템 변수 조회, 파일 목록 조회, 폴더 목록 조회 3개 | 정리 대상은 조회했지만 File.deleteFiles 및 Folder.deleteFolder를 생성하지 않았다. |
| 0112 GetPreviousWeekday | Excel 열기·닫기, List.addItem 2개씩 총 4개 | 이전 영업일 계산에 필요한 Datetime 연산은 생성하지 않았다. |

## search_kb 및 도구 예산 검증

`04_turn_recommend.events.jsonl`, 당시 RAG 관측 로그와 현재 BackendCatalog를 대조했다.

| 후보 | 도구 라운드 | MAX_TOOL_ROUNDS=6 도달 | 확인 결과 |
|---|---:|---|---|
| 0085 | 4 | 아니오 | 날짜 계산 쿼리를 반복했지만 Datetime.add를 확보하지 못한 채 스스로 탐색을 종료했다. |
| 0098 | 6 | 예 | 마지막 검색 뒤 도구 없는 최종 JSON 생성이 강제됐다. 다만 상한 도달 전에도 action 식별자를 잘못 사용했다. |
| 0112 | 3 | 아니오 | Datetime/isBefore와 Locale/stringtodate는 schema를 조회했지만 add/subtract는 확인하지 않고 탐색을 종료했다. |

현재 카탈로그를 직접 조회하면 다음 action은 모두 존재한다.

- `Datetime/add`
- `Datetime/subtract`
- `File/deleteFiles`
- `Folder/deleteFolder`

0098의 `delete folder A360 package` 검색에서는 `Folder - Delete`가 상위 후보에 나타났다. 그러나 Agent는 `get_action_schema(package="Folder", action="Delete")`를 호출했다. 카탈로그의 실제 내부 action 이름은 `deleteFolder`이므로 이 조회는 실패한다. File 삭제도 `File/deleteFiles`를 조회하지 않았다. 따라서 0098은 검색 결과 부족, 표시명과 내부 식별자 혼동, 6라운드 상한이 함께 작용했다. 상한만 늘려서는 해결된다고 볼 수 없다.

v2 검색기는 source type을 전체 후보 검색 뒤에 적용한다. 문서 페이지가 많은 현재 코퍼스에서는 action schema가 후보 창 밖으로 밀릴 수 있다. v3는 이 문제를 줄이기 위해 `pushdown=True`, `collapse_by_action=True`를 사용한다. 기존 v3 결과에서도 0085는 `Datetime.add`, 0098은 `Folder.deleteFolder`, 0112는 `Datetime.subtract`와 `Datetime.assign`을 생성했다. 이는 해당 차이가 채점기 보정보다 검색·선택 단계에 있음을 뒷받침한다.

다만 당시 `search_kb`가 LLM에 반환한 JSON 전체는 실행 로그에 저장되지 않고 쿼리와 상위 검색 진단만 남았다. 따라서 특정 action이 최종 5개 tool result에 포함됐는지까지는 단정하지 않는다.

## 판단

낮은 점수의 직접 원인은 Gold action 수가 많아서만은 아니다. Gold와 예측에 동일한 규칙기반 변환을 적용한 뒤에도 v2가 업무 핵심 action을 생성하지 못한 사례가 남는다. case별 `gold_core_actions`는 사용하지 않았다.

- 0085와 0112는 업무 단계는 인식했지만 날짜 계산 action으로 구체화하지 못했다.
- 0089와 0098은 일반적인 File/Folder 작업을 카탈로그 action으로 확정하지 못했다.
- 네 사례 모두 현재 저장된 추천 JSON과 변환 JSON의 action 수가 일치한다. 변환기 또는 채점기가 생성된 action을 누락한 증거는 없다.

따라서 이 결과를 올리기 위해 action 동치나 전처리 제외 규칙을 추가하지 않는다. Datetime 및 File/Folder 검색어, 카탈로그 alias, `search_kb`와 `get_action_schema` 호출 결과는 Agent/RAG 개선 대상으로 분리한다.

v1/v2/v3 비교가 목적이므로 v2에 v3의 검색 pushdown을 역적용하거나 `MAX_TOOL_ROUNDS`를 임의로 늘리지 않는다. 수정 업무정의서 기반 v3 재실행 후에도 동일 누락이 반복될 때 v3의 action 식별자 선택 로직을 별도로 검토한다.

0085, 0089, 0098, 0112는 재현 가능한 업무이고 실패 원인도 관찰 가능하므로 최종 후보에서 제외하지 않는다. 후보 선정과 Agent 성능 평가는 별도 판단으로 유지한다.
